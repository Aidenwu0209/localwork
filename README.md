# DejaView

A fully local "digital memory" system built for the AMD AI DevMaster Hackathon (Track 2: Agentic AI).

It continuously perceives your screen, filters what should never be remembered through a privacy
sentinel model, extracts verbatim text with deterministic OCR, builds a psychological user model
with Honcho, and answers questions with screenshot evidence — with 100% of AI inference running on
an AMD Radeon PRO W7900D (ROCm). Your data never leaves your own devices.

> Product codename: **DejaView** (déjà vu + view: your machine remembers what it has "seen").
> 中文叙事名:**全本地数字记忆体**。中文文档见 `README.zh.md`。

---

## Why this exists

Microsoft Recall died on privacy; Rewind.ai pivoted away. This product form was sentenced to death
by the cloud. We resurrect it safely with a single 48 GB Radeon, and add two layers they lacked:
**user psychology modeling** (Honcho — it understands, not just remembers) and a **model-level
privacy sentinel** (local memory has internal permission tiers: sensitive screens are blocked before
they ever reach OCR or storage).

The four pillars (never cut): **privacy sentinel · evidence-backed Q&A · daily-report multi-agent
flow · ROCm optimisation report**.

## Architecture (two-plane, data-sovereignty split)

```
┌─ Sensor (Mac/Win) ─┐   ┌─ Data sovereignty (Mac, stateful) ────────────┐   ┌─ Compute (AMD server, stateless) ──┐
│ capture client     │   │ memoryd (orchestrator)   agentd (brain出口)   │   │ LiteLLM gateway :4000              │
│ per-window capture │──▶│ ocrd (PP-OCR, CPU)       Honcho (user model)  │──▶│ brain :8001 (ThinkingCap-27B)       │
│ dhash dedup        │   │ Postgres+pgvector        timeline+kb+audit    │   │ perceive :8002 (Gemma E4B)          │
│ zero-disk          │   │ DATA_ROOT (screenshots)                      │   │ sentinel :8003 (MiniCPM-V 4.6)     │
└────────────────────┘   └───────────────────────────────────────────────┘   │ fast :8005 (MiniCPM5-1B)            │
                              GATEWAY_URL is the only seam ◀──────────────── │ embed :8004 (Qwen3-Embedding-0.6B)  │
                                                                                └─────────────────────────────────────┘
```

- **Stateful stays on Mac**: Postgres, Redis, screenshots/audio/docs, audit logs. One portable
  `DATA_ROOT` (`~/dejaview-data`). Single LiteLLM `GATEWAY_URL` is the only Mac↔server seam.
- **Server is pure compute**: model services + OCR + gateway + monitoring. No prompt logging
  (`--log-disable`); weights on an overlay, rebuilt from manifest+sha256.
- **Three-tier inference pyramid**: each request routes to the cheapest sufficient tier — high-freq
  shallow tasks to the fast lane (~1B), mid-freq understanding to perceive (8B-class), low-freq deep
  reasoning to brain (27B). This pyramid *is* the "inference speed optimisation" score narrative.

## Quick start (dev topology: Mac + AMD server via SSH tunnel)

Prereqs: Docker Desktop running; `uv` installed; SSH alias `radeon-cloud` to the AMD server.

```bash
# 1. Data layer + Honcho (Mac)
make data-up                                                    # pgvector :5433 + redis :6380
docker compose -f deploy/mac/compose.honcho.yml up -d           # Honcho api/deriver :8100
docker compose -f deploy/mac/compose.honcho.yml run --rm --no-deps \
  --entrypoint /app/.venv/bin/python honcho-api scripts/configure_embeddings.py --yes  # pgvector dim 1024 (once)

# 2. AMD server inference stack (see deploy/server/DEPLOY.md for full guide)
ssh radeon-cloud "cd /root/dejaview-launch && ./server-stack.sh up embed fast sentinel perceive"

# 3. Bridge Mac to the server gateway (server port not public)
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud                   # Mac :14000 -> server :4000

# 4. Mac services
cd services/ocrd && nohup uv run python -m ocrd > /tmp/ocrd.log 2>&1 &           # :8006
MEMORYD_REAL_PIPELINE=1 GATEWAY_URL=http://127.0.0.1:14000/v1 \
  nohup uv run --project services/memoryd python -m memoryd > /tmp/memoryd.log 2>&1 &   # :8090
cd clients/capture && CAPTURE_DEVICE_ID=dev uv run python -m capture             # per-window capture

# 5. Ask your memory (start brain on the server first: ./server-stack.sh up brain)
GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/agentd python -m agentd   # :8101
curl -X POST http://127.0.0.1:8101/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What GPU errors have I hit recently? Cite the events."}]}'
# → "You hit ROCM-4042 ... [event#120 00:45 Terminal]"
```

> The full real-run recipe and all "known issues" are in **`STATUS.md`** (human-readable snapshot)
> and **`docs/EXECUTION_HANDBOOK.md`** (single source of truth).

## Score alignment (Track 2)

| Score dimension | Weight | Where it's earned in DejaView |
|---|---|---|
| Functional completeness & value | 60 | Per-window capture → sentinel → OCR → understand → timeline → Honcho model → evidence-backed Q&A. Four pillars + multi-window awareness. |
| Radeon GPU / ROCm optimisation | 40 | 5 models常驻 48 GB (Q8/Q6 tiered); three-tier inference pyramid; llama.cpp HIP/gfx1100; ablation report (T3.1, `docs/benchmarks.md`); storage/compute split. |

## Status

**TASKBOARD: 33/33 accept.** Full pipeline verified end-to-end (M3.4); 54-min real working run
passed all four M4.4 acceptance points (real events, zero external network, sentinel audit,
zero-disk). Remaining work is Phase 3 (ROCm ablation report, Grafana, demo video, bilingual README
polish) — see `STATUS.md` → "还没做的".

## Where to start reading

- **`STATUS.md`** — human-readable snapshot: what runs, known issues, next steps. **Read this first.**
- `docs/EXECUTION_HANDBOOK.md` — single source of truth: architecture, specs, WBS, handoff (§12).
- `docs/verification-log.md` — every resolved `[VERIFY]` + load-bearing empirical finding (pitfalls).
- `docs/benchmarks.md` — OCR accuracy A/B (rapidocr vs paddleocr); T3.1 ROCm ablation goes here.
- `deploy/server/DEPLOY.md` — AMD server deployment (build, VRAM budget, Dolphin coexistence, tunnel).
- `TASKBOARD.json` — authoritative task state machine.

## Layout

```
docs/             handbook, kickoff, manifests, verification log, benchmarks, model manifest
deploy/server/    GPU-side: llama-launch scripts, gateway, DEPLOY.md, download-models.sh, sha256
deploy/mac/       data-side: postgres/redis/honcho compose, timeline schema, llama-launch (Metal)
services/         memoryd (ingest+search) · ocrd (deterministic OCR) · agentd (brain出口)
clients/capture/  per-window screen capture (macOS; Windows adapter TODO)
third_party/      pinned Honcho fork (submodule @ 340175ad)
tests/assets/     synthetic fixtures (screenshots, messages, frame-pairs, sentinel set) — zero PII
STATUS.md         human-readable project snapshot (read first)
```

## License notes (for `docs/licenses.md`, T3.6)

Apache-2.0: ThinkingCap / MiniCPM / Honcho / llama.cpp / PaddleOCR. **Gemma License separately
noted.** Qwen3-Embedding: Apache-2.0. LiteLLM: MIT. MarkItDown: MIT. Open WebUI: MIT.
