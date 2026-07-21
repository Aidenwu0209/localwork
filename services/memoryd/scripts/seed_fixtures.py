#!/usr/bin/env python3
"""M3.3: seed 50 synthetic timeline events, each embedded with the real
Qwen3-Embedding-0.6B via the gateway, so the search endpoints (semantic /
exact / hybrid) have data to retrieve. Every event plants at least one
exact-substring target (error code / URL / identifier) so the pg_trgm lane
can be verified, plus natural-language activity so the semantic lane works.

All content is fictional (dejaview-demo / acme-api / ERR-xxxx / demo-acme.io).
device_id='fixture' so the seeder's rows are trivially cleaned:
    DELETE FROM timeline_events WHERE device_id='fixture';

Run with the dev inference stack up: dev-stack.sh up embed
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg

# Connection info (host, port, user, password, dbname) — keep it simple.
DSN = "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview"
GATEWAY = "http://127.0.0.1:4000/v1"

# 50 synthetic events. Each: (hours_ago, app, title, activity, ocr_text).
# ocr_text plants the exact-substring targets (ERR-xxxx codes, URLs).
BASE = datetime.now(timezone.utc)
FIXTURE_EVENTS = [
    # ---- coding: dejaview-core / acme-api (10) ----
    (1, "VS Code", "dejaview-core/src/memoryd/pipeline.py",
     "Editing the ingest pipeline to wire the real embed stage",
     "src/memoryd/pipeline.py\nfrom memoryd.stages import GatewayEmbed\nasync def ingest_frame(): embed = await self.embed.embed(activity)\n# TODO M3.3 wire GatewayEmbed into _default_pipeline"),
    (2, "VS Code", "acme-api/cmd/server/main.go",
     "Writing the Go server entrypoint for acme-api",
     "package main\nimport 'net/http'\nfunc main() { http.ListenAndServe(':8080', nil) }\n// acme-api v0.4.1 entrypoint"),
    (3, "Terminal", "cargo build --release",
     "Building dejaview-core in release mode, hit a linker error",
     "error: linker `cc` failed\nnote: undefined symbol: hip_alloc\nnote: see HIP buffer alloc at hip_alloc.rs:142\nerror ERR-4101: linker failed on dejaview-core"),
    (4, "VS Code", "acme-api/internal/payments/charge.go",
     "Implementing the charge flow for the payments core",
     "func Charge(ctx, amt) error {\n  tx := db.Begin(ctx)\n  defer tx.Rollback()\n  // payments core: amount in cents\n}"),
    (5, "Chrome", "github.com/acme/api/pull/142",
     "Reviewing PR #142 on the acme-api repo",
     "github.com/acme/api/pull/142\nfeat(payments): idempotent charge\n+12 -3\napproved by morgan_dev"),
    (6, "VS Code", "dejaview-core/src/agentd/tools.py",
     "Defining the search_timeline tool for the agent",
     "def search_timeline(query, mode='hybrid', k=5):\n  '''semantic + exact + time filter'''\n  # tool calling via brain"),
    (7, "Terminal", "git log --oneline",
     "Checking recent commits on dejaview-core",
     "24f9c6f M2.6 Honcho memory link lit\n1144e9c M5.1 ocrd OCR microservice\n0942396 M4.2 dhash dedup"),
    (8, "VS Code", "acme-api/internal/payments/refund.go",
     "Adding refund support to the payments module",
     "func Refund(ctx, charge_id) error {\n  // reverse the original ERR-4101 if it partially applied\n  return nil\n}"),
    (10, "Chrome", "docs.demo-acme.io/errors/ERR-4101",
     "Reading the docs for error ERR-4101",
     "Error ERR-4101\nThe HIP buffer allocator failed to reserve device memory.\nSee https://docs.demo-acme.io/errors/ERR-4101\nResolution: free GPU memory or reduce batch size."),
    (12, "VS Code", "dejaview-core/bench/bench_embed.py",
     "Benchmarking the embedding throughput",
     "# bench_embed.py\nresults = []\nfor text in corpus: results.append(embed(text))\nmedian = statistics.median(latencies)"),
    # ---- terminal / errors (10) ----
    (14, "Terminal", "make bench",
     "Running the benchmark suite, got ROCM out of memory",
     "make bench\nerror: ROCM-4042: HIP buffer alloc failed (requested 8192 MiB)\nhttps://docs.demo-acme.io/errors/ROCM-4042\naborted after 12s"),
    (16, "Terminal", "docker compose logs",
     "Inspecting container logs for a crash",
     "dejaview-honcho-honcho-api-1 | ERROR Application startup failed\npsycopg.OperationalError connection refused at 127.0.0.1:5433"),
    (18, "Terminal", "kubectl get pods",
     "Checking the kubernetes pods status",
     "NAME                     READY   STATUS\nacme-api-7d4f            1/1     Running\nacme-worker-b2c1         0/1     CrashLoopBackOff"),
    (20, "Terminal", "pytest tests/",
     "Running the test suite, 2 failures",
     "FAILED tests/test_pipeline.py::test_embed_called - AssertionError\nFAILED tests/test_search.py::test_exact_hit - assert 0 == 1\n2 failed, 48 passed"),
    (22, "Terminal", "psql -c 'SELECT count(*)'",
     "Querying the timeline_events count",
     "psql (16.4)\n count \n-------\n   483\n(1 row)"),
    (24, "Terminal", "curl localhost:8090/v1/search",
     "Testing the search endpoint manually",
     "$ curl -X POST localhost:8090/v1/search -d '{\"query\":\"ROCM-4042\",\"mode\":\"exact\"}'\n{\"hits\":[{\"id\":142,\"score\":0.92}]}"),
    (26, "Terminal", "make data-reset",
     "Resetting the dev database",
     "make data-reset\n[+] Running 0/2  ✔ Container dejaview-data-database-1 Removed\nVolume dejaview-data_dejaview-pgdata Removed"),
    (28, "Terminal", "git push origin main",
     "Pushing commits to GitHub",
     "To github.com:Aidenwu0209/localwork.git\n   0942396..24f9c6f  main -> main"),
    (30, "Terminal", "uv add imagehash",
     "Adding the imagehash dependency for dhash dedup",
     "+ imagehash==4.3.1\n+ numpy==2.5.1\ninstalled in 1.2s"),
    (32, "Terminal", "ssh radeon-cloud",
     "SSHing into the AMD server to check GPU status",
     "ssh root@36.150.116.200 -p 30147\nLast login: ...\nrocm-smi\nGPU 0: W7900D  48GB  util 12%"),
    (34, "Terminal", "tail -f /tmp/dejaview-perceive.log",
     "Tailing the perceive model server log",
     "llama-server b10050\nllama_model_loader: loaded gemma-4-e4b-it Q4_0 (4590 MB)\nserver listening on 127.0.0.1:8002"),
    # ---- browser / docs (15) ----
    (36, "Chrome", "docs.demo-acme.io/architecture",
     "Reading the DejaView architecture overview",
     "DejaView Architecture\nThe system splits into sensor / data-sovereignty / compute planes.\nAll inference on Radeon PRO W7900D via ROCm."),
    (38, "Chrome", "blog.demo-acme.io/local-first",
     "Reading a blog post about local-first software",
     "Why local-first matters\nLocal-first keeps user data on the user's device.\n本地优先 (local-first) 把数据主权还给用户。"),
    (40, "Chrome", "github.com/ggml-org/llama.cpp",
     "Browsing the llama.cpp repo for the latest release",
     "ggml-org/llama.cpp\nRelease b10050\nHIP backend improvements for gfx1100"),
    (42, "Chrome", "huggingface.co/Qwen/Qwen3-Embedding-0.6B",
     "Checking the Qwen embedding model card",
     "Qwen3-Embedding-0.6B\n1024 dimensions, 32k context, Apache-2.0\nInstruction-aware: add 'Instruct: ...' prefix on queries"),
    (44, "Chrome", "luma.com/amd-4dhi",
     "Reading the AMD hackathon page",
     "AMD AI DevMaster Hackathon\nTrack 2: Agentic AI\nDeadline 2026-08-06\nPrize pool TBD"),
    (46, "Chrome", "docs.demo-acme.io/rocm",
     "Reading the ROCm optimization guide",
     "ROCm optimization for W7900D\nUse -DAMDGPU_TARGETS=gfx1100\nMTP speculative decoding via --spec-type draft-mtp"),
    (48, "Chrome", "stackoverflow.com/questions/89234",
     "Searching for a psycopg connection pool answer",
     "psycopg connection pool with async FastAPI\nanswered by user dev_sage\nuse psycopg_pool.ConnectionPool with asyncio.to_thread"),
    (50, "Chrome", "github.com/plastic-labs/honcho",
     "Reading the Honcho repository README",
     "plastic-labs/honcho\nUser psychology modeling for agents\nDialectic Q&A over a peer representation"),
    (52, "Chrome", "docs.pydantic.dev/latest/",
     "Reading the pydantic v2 migration guide",
     "Pydantic V2\nmodel_validate replaces parse_obj\nmodel_dump replaces dict()\nField(min_length=...) for validation"),
    (54, "Chrome", "fastapi.tiangolo.com/tutorial/body",
     "Reading the FastAPI request body tutorial",
     "FastAPI request body\nUse Pydantic models for JSON bodies\nAnnotated[type, Form()] for form fields"),
    (56, "Chrome", "grafana.demo-acme.io/d/timeline",
     "Looking at the Grafana timeline dashboard",
     "Grafana | timeline\nevents/sec: 0.3\nP95 latency: 847ms\nGPU util: 12%"),
    (58, "Chrome", "mail.google.com",
     "Reading an email about the hackathon team registration",
     "Subject: AMD DevMaster registration\nTeam Aidenwu0209 confirmed for Track 2\nPlease register at AMD AI Developer Program"),
    (60, "Chrome", "docs.demo-acme.io/benchmarks",
     "Reviewing the ROCm benchmark numbers",
     "DejaView benchmarks\nbrain Q8 prefill 312 tok/s decode 47 tok/s\nperceive single frame P95 847ms\nVRAM 43.5/48 GB"),
    (62, "Chrome", "notion.so/acme/roadmap",
     "Reviewing the team roadmap in Notion",
     "acme-api roadmap Q3\n- payments idempotency\n- webhooks v2\n- migrate to Go 1.22"),
    (64, "Chrome", "github.com/Aidenwu0209/localwork",
     "Browsing the localwork repo on GitHub",
     "Aidenwu0209/localwork\nDejaView: 全本地数字记忆体\n27 tasks accepted / 33 total"),
    # ---- chat (5) ----
    (66, "Slack", "#dejaview-demo",
     "Discussing the deriver drift with the team in Slack",
     "#dejaview-demo\njordan: deriver is drifting again\nmorgan_dev: regression test?\nsam_qa: I'll add a fixture"),
    (68, "Slack", "#payments-team",
     "Talking about the kafka consumer lag in payments",
     "#payments-team\nalex_w: kafka lag 24000\npriya_eng: scaling consumers\ncheckout p95 340ms"),
    (70, "Slack", "#rocm-bench",
     "Sharing ROCm benchmark results",
     "#rocm-bench\njordan: brain Q8 47 tok/s decode\nmorgan_dev: nice, MTP helps?"),
    (72, "Discord", "amd-hackathon",
     "Chatting in the AMD hackathon Discord",
     "amd-hackathon\nparticipant_42: anyone using gfx1100?\nmentor: yes, W7900D is gfx1100"),
    (74, "Slack", "#dejaview-demo",
     "Discussing the privacy sentinel accuracy",
     "#dejaview-demo\njordan: sentinel caught 19/20 sensitive frames\nmorgan_dev: 1 false negative, which category?\nsam_qa: id_document, edge case"),
    # ---- docs / notes (10) ----
    (76, "Obsidian", "dejaview-notes/rocm-tuning.md",
     "Writing notes on ROCm tuning",
     "# ROCm tuning\n- DGPU_TARGETS=gfx1100\n- -ngl 99 for full offload\n- MTP draft head: --spec-type draft-mtp\n- VRAM budget: 43.5/48 GB"),
    (78, "Obsidian", "dejaview-notes/sentinel-prompts.md",
     "Drafting the sentinel classification prompt",
     "# Sentinel prompt\nClassify screenshot as one of:\npassword_prompt | banking_finance | private_chat | id_document | adult | normal\nReply JSON: {decision, category, confidence}"),
    (80, "Obsidian", "dejaview-notes/weekly-2026-29.md",
     "Writing the weekly summary",
     "# Week 29 2026\n- M2.6 Honcho memory link lit\n- M5.1 ocrd service\n- M4.x capture client MVP\nNext: M3.3 search, M3.4 full pipeline"),
    (82, "Obsidian", "dejaview-notes/architecture.md",
     "Updating the architecture doc",
     "# DejaView architecture\nThree planes: sensor (Mac/Win), data (Mac), compute (AMD).\n5 models: brain/perceive/sentinel/fast/embed."),
    (84, "Obsidian", "acme-api/design/payments.md",
     "Documenting the payments charge design",
     "# payments charge\nIdempotency key on charge_id\nRetry safe: ERR-4101 reverses partial state\nPostgres 15, SERIALIZABLE isolation"),
    (86, "Obsidian", "dejaview-notes/embed-ablation.md",
     "Notes on the embedding model selection",
     "# embed ablation\nQwen3-Embedding-0.6B vs bge-m3\nMMTEB 64.33 vs 59.56\n1024 dim, instruction-aware query prefix"),
    (88, "TextEdit", "meeting-notes-2026-07-21.txt",
     "Typing meeting notes",
     "Meeting 2026-07-21\nAttendees: jordan, morgan_dev, sam_qa\nDecisions: ship M3.3 by EOD, defer MCP to phase 3"),
    (90, "Obsidian", "dejaview-notes/demo-script.md",
     "Writing the six-act demo script",
     "# Demo (six acts)\n1. GPU full (Grafana)\n2. timeline grows\n3. sentinel blocks bank page\n4. ask 'which ROCm PR'\n5. Honcho preference\n6. unplug network"),
    (92, "Obsidian", "dejaview-notes/risks.md",
     "Updating the risk register",
     "# Risks\n- OCR small-glyph noise (R0cM vs ROCM) -> pg_trgm absorbs\n- E4B-as-brain slow -> S2 swaps 27B\n- IPv6 host.docker.internal -> IPv4 literal"),
    (94, "Obsidian", "acme-api/incidents/2026-07-15.md",
     "Writing the postmortem for the kafka lag incident",
     "# Incident 2026-07-15\nkafka consumer lag 24000 in payments\nRoot cause: consumer rebalance storm\nFix: sticky assignor, ERR-4101 backoff"),
]


def embed(text: str) -> list[float]:
    import httpx
    with httpx.Client(timeout=30.0) as c:
        r = c.post(f"{GATEWAY}/embeddings", json={"model": "embed", "input": text})
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


def main() -> int:
    print(f"embedding {len(FIXTURE_EVENTS)} fixture events via {GATEWAY}...")
    rows = []
    t0 = time.monotonic()
    for i, (hours_ago, app, title, activity, ocr) in enumerate(FIXTURE_EVENTS):
        ts = BASE - timedelta(hours=hours_ago)
        # Embed the activity + topics text (ingest side: no instruction prefix).
        vec = embed(activity)
        rows.append((ts, app, title, activity, ocr, vec))
        if (i + 1) % 10 == 0:
            print(f"  embedded {i+1}/{len(FIXTURE_EVENTS)} ({time.monotonic()-t0:.1f}s)")
    print(f"embedding done in {time.monotonic()-t0:.1f}s; inserting into DB...")

    inserted = 0
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for ts, app, title, activity, ocr, vec in rows:
            cur.execute(
                """INSERT INTO timeline_events
                   (ts, device_id, kind, app, window_title, activity,
                    topics, verbatim, ocr_text, ocr_blocks, embedding)
                   VALUES (%s, 'fixture', 'frame', %s, %s, %s,
                           %s, %s, %s, %s, %s)""",
                (ts, app, title, activity,
                 ["dev"], psycopg.types.json.Jsonb({}), ocr,
                 psycopg.types.json.Jsonb([]), vec),
            )
            inserted += 1
        conn.commit()
    print(f"inserted {inserted} fixture rows (device_id='fixture')")
    print("\ncleanup: DELETE FROM timeline_events WHERE device_id='fixture';")
    return 0


if __name__ == "__main__":
    sys.exit(main())
