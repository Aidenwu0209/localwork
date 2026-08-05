# DejaView

> Continuously perceives your screen and turns digital life into **queryable memory with evidence**; uses Honcho psychological modeling to understand *who you are*; a privacy sentinel gates *what must never be remembered*. **Primary agentic compute runs on Radeon PRO W7900D (ROCm) after a device-local Sentinel privacy gate**; data stays on your own devices, with Local Metal available for verified fallback. Audio and document ingest are not supported in the current service.

Product codename: **DejaView** (déjà vu + view — your machine has “seen this before”).
中文叙事名:**全本地数字记忆体** · [中文 README](README.zh.md)

Built for the [AMD AI DevMaster Hackathon](https://luma.com/amd-4dhi) · **Track 2 · Agentic AI**.

---

## Why this exists (award narrative)

Microsoft Recall nearly crashed on privacy; Rewind.ai pivoted away — this product form was sentenced to death by the cloud. We resurrect it safely with a single **48 GB Radeon**, and add two layers they lacked:

1. **User psychology modeling** (Honcho reasoning-first profile + dialectic Q&A — understands, not just remembers)
2. **Model-level privacy sentinel** (local memory has internal permission tiers; sensitive frames are blocked before OCR or disk)

**Precedents we differentiate from:** Microsoft Recall (cloud trust crisis), Rewind.ai (pivoted), OpenRecall (open-source AGPL — screenshot + OCR + search, no understanding layer).

**Our edges:** ① Honcho user model · ② pre-ingest privacy sentinel · ③ Agent closed loop (tool calling, multi-agent daily report) · ④ five logical model roles with measured VRAM-aware residency + ROCm report · ⑤ storage/compute split for data sovereignty.

**Four pillars (never cut):** privacy sentinel · evidence-backed Q&A · daily-report multi-agent flow · ROCm optimisation report.

---

## Dual topology

Same codebase and compose stack. Stateful services use `GATEWAY_URL`; agentd
uses `RADEON_GATEWAY_URL` first and `LOCAL_GATEWAY_URL` for a verified fallback
(see `docs/EXECUTION_HANDBOOK.md` §2.2).
**Topology A** below is the path a stranger can smoke today. **Topology B** is the all-in-one AMD box for judge reproduction / demo day.

### Topology A — Mac data sovereignty + AMD stateless compute

*Primary / day-to-day topology. Stateful memory on the user’s Mac; GPU is pure compute.*

```
┌─ Sensor (Mac/Win) ─┐   ┌─ Data sovereignty (Mac, stateful) ────────────┐   ┌─ Compute (AMD, stateless) ──────────┐
│ capture client     │   │ memoryd (orchestrator)   agentd (brain出口)   │   │ LiteLLM gateway :4000               │
│ per-window capture │──▶│ local Sentinel → ocrd (PP-OCR, CPU)           │──▶│ brain :8001 · perceive :8002         │
│ dhash · zero-disk  │   │ Postgres+pgvector        timeline+kb+audit    │   │ fast :8005                           │
└────────────────────┘   │ DATA_ROOT (~/dejaview-data)                   │   │ embed :8004 · (ocrd EPYC optional)  │
                         └───────────────────────────────────────────────┘   └─────────────────────────────────────┘
                          SENTINEL_GATEWAY_URL stays local; only allowed frames use GATEWAY_URL
```

- **Stateful on the sovereignty device:** Postgres, Redis, screenshots, and audit logs. One portable `DATA_ROOT`; audio/document ingest is currently unsupported.
- **Capture clients:** macOS remains the contest-verified client; P3.19 now includes a Windows Win32/mss backend with in-memory pixels and secure-desktop pause. The full Windows product stack remains an in-progress gate and is documented in [`deploy/windows/README.md`](deploy/windows/README.md).
- **Server is stateless:** model services + gateway (+ optional EPYC OCR). No user data, no prompt logs on disk.
- **Privacy order:** memoryd sends raw frames to the local Sentinel configured by
  `SENTINEL_GATEWAY_URL` before OCR, disk, or any allowed Radeon request through
  `GATEWAY_URL`.
- **Network:** LAN or Tailscale/WireGuard; SSH tunnel is fine for smoke (see below).

### Topology B — Single-box AMD (judge / demo)

*All services on one AMD machine (handbook §2.2「单机」). Same images; point `GATEWAY_URL` at localhost. Use when a judge must reproduce without a Mac data plane.*

```
┌──────────────────────────── AMD single box (stateful + compute) ────────────────────────────┐
│  capture ─▶ memoryd / local Sentinel / ocrd / Honcho / Postgres / DATA_ROOT                 │
│                    │                                                                         │
│                    └──▶ LiteLLM :4000 ─▶ brain / perceive / sentinel / fast / embed (ROCm) │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Details for server bring-up, VRAM budget, and model download: [`deploy/server/DEPLOY.md`](deploy/server/DEPLOY.md).
Day-to-day port map and known issues: [`STATUS.md`](STATUS.md).

---

## Score alignment (Track 2)

| Dimension | Weight | How DejaView earns it |
|---|---|---|
| Functional completeness & application value | **60** | Per-window capture → sentinel → OCR → novelty → perceive → timeline → Honcho model → evidence-backed Q&A (`[event#id HH:MM app]`). Four pillars + multi-window awareness. |
| AMD Radeon GPU & ROCm optimisation | **40** | Five logical models on W7900D 48 GB; three-tier inference pyramid; llama.cpp HIP / gfx1100; storage/compute split. **Evidence:** [`docs/benchmarks.md`](docs/benchmarks.md) (OCR A/B already in; **ROCm ablation chapter filled by P3.1**). |

These are the **100 base points** for Track 2. The optional cloud-model
optimisation bonus is not claimed by this all-local contest path.

---

## Demo video

[**Watch the 3:15 English-primary submission demo**](docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.mp4) — authentic submission slides lead into the unchanged live isolated run: synthetic data, evidence-backed recall, Honcho, privacy blocking, Radeon daily-report agents, visible verified SSH-compute-link disconnect, and the completed Local Metal fallback report. Wi-Fi remains untouched. The accepted [original evidence cut](docs/assets/demo/dejaview-p34-six-act-20260802.mp4) remains unchanged; the submission cut replaces the live-run audio with English narration and supplies complete English captions.

Integrity, caption hash, and timecodes: [`docs/assets/demo/p34-video-manifest.json`](docs/assets/demo/p34-video-manifest.json). Editable captions: [`dejaview-p34-six-act-20260802-en-3m.srt`](docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.srt). The submission cut is 3:15.2, within the official 3–5 minute window.

### Submission package

- Project specification: [Markdown](docs/submission/PROJECT_SPECIFICATION.md) · [editable DOCX](docs/submission/DejaView-Project-Specification.docx)
- Supplementary presentation: [editable PPTX](docs/submission/DejaView-Track2-Presentation.pptx)
- Demo: [English-primary MP4](docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.mp4) · [editable SRT](docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.srt) · [manifest](docs/assets/demo/p34-video-manifest.json)
- Radeon/ROCm optimisation: [benchmark report](docs/benchmarks.md) · [checksummed P3.1 evidence](docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/)

**Human-only submission step:** before the final upload, fork the official
competition repository and open the required English pull request from that
fork. This checkout does not claim that account-owned contest step is complete;
the repository checker keeps it as an explicit human boundary.

---

## Topology A smoke (clean machine)

Prereqs: Docker Desktop · [`uv`](https://github.com/astral-sh/uv) · `llama-server`
plus the downloaded local Sentinel weights · SSH host alias `radeon-cloud`
to an authorised AMD box with the inference stack (see
[`DEPLOY.md`](deploy/server/DEPLOY.md)). Run every command below from the clone
root; `REPO_ROOT` keeps background launches independent of later `cd` calls.

On Windows, use `deploy\\windows\\dejaview.cmd doctor`, then `tunnel-up`,
`product-up`, `product-status`, and `capture`. The local Sentinel requirement is
unchanged; raw frames never use the Radeon tunnel.

```bash
git submodule update --init --recursive
make setup
cp .env.example .env
cp deploy/mac/honcho.env.example deploy/mac/honcho.env   # edit if needed; no secrets required for local smoke
set -a; source .env; set +a   # memoryd reads exported environment variables directly
make doctor                    # read-only; runtime endpoints may still be WARN before start
make test                      # offline first-party contracts; same command runs in CI
```

Minimal commands (full recipe + troubleshooting: [`STATUS.md`](STATUS.md) · handbook §12.5):

```bash
# 1. Device-local Sentinel. Unfiltered pixels must use this gateway.
./deploy/mac/llama-launch/dev-stack.sh up sentinel

# 2. AMD inference for allowed stages (brain is on demand — check VRAM first)
ssh radeon-cloud "cd /root/dejaview-launch && ./server-stack.sh up embed fast perceive"

# 3. Tunnel (server gateway is not public)
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud

# 4. Start data, Honcho, OCR, memoryd, and agentd with readiness checks.
#    The exported URLs make Sentinel local, Radeon primary, and Metal fallback.
make product-up
# Once per fresh Honcho database: align pgvector to the shipped 1024-d embed role.
docker compose -f deploy/mac/compose.honcho.yml run --rm --no-deps \
  --entrypoint /app/.venv/bin/python honcho-api scripts/configure_embeddings.py --yes
make product-status

# 5. Capture stays foreground so Screen Recording permission is visible.
make capture

# Daily product: http://127.0.0.1:8101/ (start brain on demand for deep Q&A)
curl -s http://127.0.0.1:8101/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What GPU errors have I hit recently? Cite the events."}]}'
```

| Layer | How to start | Port |
|---|---|---|
| Product lifecycle | `make product-up/status/down` | managed local services |
| Data | started by `product-up` | pg `:5433` · redis `:6380` |
| Honcho | started by `product-up` | `:8100` |
| Local Sentinel | `dev-stack.sh up sentinel` | gateway `:4000` · model `:8003` |
| Tunnel | `ssh -L 14000:…:4000` | Mac `:14000` → server `:4000` |
| ocrd | started by `product-up` | `:8006` |
| memoryd | started by `product-up` | `:8090` |
| agentd | started by `product-up` | `:8101` |
| capture | `make capture` | — |

---

## Logical model names

Application code uses these logical names. `sentinel` is reached through the
separate local `SENTINEL_GATEWAY_URL`; the other gateway-backed stages use
`GATEWAY_URL`. Physical routing lives in `deploy/server/litellm.yaml`.

| Logical name | Role | Physical model | Port |
|---|---|---|---|
| `brain` | Deep: reasoning / planning / vision / writing | ThinkingCap-Qwen3.6-27B (+ mmproj) | 8001 |
| `perceive` | Mid: screen understanding and Honcho deriver baseline | Gemma 4 E4B (+ mmproj) | 8002 |
| `sentinel` | Fast-lane vision: privacy classify | MiniCPM-V 4.6 Q4_K_M (+ mmproj) | 8003 |
| `fast` | Fast-lane text: novelty / merge / tags | MiniCPM5-1B | 8005 |
| `embed` | All embeddings (query side adds instruction prefix) | Qwen3-Embedding-0.6B (1024-d) | 8004 |
| `ocrd` *(not LLM)* | Deterministic verbatim OCR | PP-OCRv6 / rapidocr (CPU) | 8006 |

**Cloud-swap rules (dev only):** (1) **`sentinel` stays local forever** — it sees unfiltered screens; configure it separately with `SENTINEL_GATEWAY_URL`. (2) Switching `embed` requires a full re-index. (3) Contest demo / submission video must be **fully local**.

The table defines five logical roles, not a promise that all five weight sets are
resident in every topology. The split daily topology keeps Sentinel on the data
plane and starts `brain` on demand; the all-in-one/demo topology can place all
roles on Radeon subject to the measured VRAM policy.

---

## Privacy & data sovereignty

- User memory (Postgres, Redis, `DATA_ROOT` screenshots, audit logs) lives on **your device** — never on the AMD compute node. Audio and document ingest currently return `501 unsupported_media`.
- Capture client: **zero local disk** (in-memory → POST → discard). Sentinel `block` frames write audit only — no OCR, no screenshot file.
- Capture sends a metadata-only heartbeat every 30 seconds, including while the
  screen is locked. Frame replies report `processing_state`: `stored`,
  `merged`, or `blocked`; a missing Screen Recording permission exits capture
  with status `2` before frame capture begins.
- Stored events automatically enter Honcho through an atomic local outbox. The
  projection is restricted to activity/topics/app/time/event provenance;
  OCR, window titles, URLs, screenshots, and blocked frames never enter it.
  Delivery is retryable, idempotent, observable, and explicitly pausable.
- Repo contains **synthetic fixtures only** (no real PII, no API keys). Clear the timeline DB before public demos if you ran real capture.
- SearXNG stays **disabled** by default (conflicts with “data never leaves the device”).

---

## License

Third-party licenses, Gemma callout, user-data-never-uploaded statement, and §10 readiness notes: [`docs/licenses.md`](docs/licenses.md).

- **Apache-2.0:** ThinkingCap · MiniCPM · Honcho · PaddleOCR · Qwen3-Embedding · Gemma 4 (see callout)
- **Gemma (separate callout):** Gemma 4 E4B perceive — details in `docs/licenses.md`
- **MIT:** llama.cpp · LiteLLM · MarkItDown · Open WebUI

Do not copy AGPL code (OpenRecall is reference-only).

---

## Status & further reading

**TASKBOARD:** **48/49 accept**; P3.12–P3.18 mature-product hardening, release
reproducibility, and the evidence-bound end-to-end audit are accepted. P3.19 is
the user-requested final contest-risk polish and is currently `doing`.
The accepted evidence includes the end-to-end
pipeline, ROCm ablation, Grafana, privacy/perceive gates, and the ≤5-minute
remote-link failover demo — see [`STATUS.md`](STATUS.md).

| Doc | Purpose |
|---|---|
| [`STATUS.md`](STATUS.md) | Human snapshot: start table, known issues, next steps — **read first** |
| [`docs/EXECUTION_HANDBOOK.md`](docs/EXECUTION_HANDBOOK.md) | Single source of truth (architecture, specs, handoff §12) |
| [`docs/verification-log.md`](docs/verification-log.md) | Resolved `[VERIFY]` + pitfalls |
| [`docs/benchmarks.md`](docs/benchmarks.md) | OCR A/B + ROCm ablation (P3.1) |
| [`deploy/server/DEPLOY.md`](deploy/server/DEPLOY.md) | AMD server ops / VRAM / tunnel |
| [`docs/submission/PROJECT_SPECIFICATION.md`](docs/submission/PROJECT_SPECIFICATION.md) | English Track 2 project specification (editable DOCX is beside it) |
| [`TASKBOARD.json`](TASKBOARD.json) | Authoritative task state machine |

## Layout

```
docs/             handbook, verification log, benchmarks, model manifest
deploy/server/    GPU-side launch scripts, gateway, DEPLOY.md, download-models.sh
deploy/mac/       data-side compose (postgres/redis/honcho), Metal llama-launch
services/         memoryd · ocrd · agentd
clients/capture/  per-window screen capture (macOS MVP)
third_party/      Honcho submodule @ 340175ad
tests/assets/     synthetic fixtures — zero real PII
```
