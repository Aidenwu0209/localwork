# DejaView

> Continuously perceives your screen and voice, turns digital life into **queryable memory with evidence**; uses Honcho psychological modeling to understand *who you are*; a privacy sentinel gates *what must never be remembered*. **AI inference runs 100% on Radeon PRO W7900D (ROCm)**; data stays on your own devices.

Product codename: **DejaView** (déjà vu + view — your machine has “seen this before”).  
中文叙事名:**全本地数字记忆体** · [中文 README](README.zh.md)

Built for the [AMD AI DevMaster Hackathon](https://luma.com/amd-4dhi) · **Track 2 · Agentic AI**.

---

## Why this exists (award narrative)

Microsoft Recall nearly crashed on privacy; Rewind.ai pivoted away — this product form was sentenced to death by the cloud. We resurrect it safely with a single **48 GB Radeon**, and add two layers they lacked:

1. **User psychology modeling** (Honcho reasoning-first profile + dialectic Q&A — understands, not just remembers)
2. **Model-level privacy sentinel** (local memory has internal permission tiers; sensitive frames are blocked before OCR or disk)

**Precedents we differentiate from:** Microsoft Recall (cloud trust crisis), Rewind.ai (pivoted), OpenRecall (open-source AGPL — screenshot + OCR + search, no understanding layer).

**Our edges:** ① Honcho user model · ② pre-ingest privacy sentinel · ③ Agent closed loop (tool calling, multi-agent daily report) · ④ five-model tiered residency on 48 GB + ROCm report · ⑤ storage/compute split for data sovereignty.

**Four pillars (never cut):** privacy sentinel · evidence-backed Q&A · daily-report multi-agent flow · ROCm optimisation report.

---

## Dual topology

Same codebase and compose stack; switch with `GATEWAY_URL` / profiles (see `docs/EXECUTION_HANDBOOK.md` §2.2).  
**Topology A** below is the path a stranger can smoke today. **Topology B** is the all-in-one AMD box for judge reproduction / demo day.

### Topology A — Mac data sovereignty + AMD stateless compute

*Primary / day-to-day topology. Stateful memory on the user’s Mac; GPU is pure compute.*

```
┌─ Sensor (Mac/Win) ─┐   ┌─ Data sovereignty (Mac, stateful) ────────────┐   ┌─ Compute (AMD, stateless) ──────────┐
│ capture client     │   │ memoryd (orchestrator)   agentd (brain出口)   │   │ LiteLLM gateway :4000               │
│ per-window capture │──▶│ ocrd (PP-OCR, CPU)       Honcho (user model)  │──▶│ brain :8001 · perceive :8002         │
│ dhash · zero-disk  │   │ Postgres+pgvector        timeline+kb+audit    │   │ sentinel :8003 · fast :8005         │
└────────────────────┘   │ DATA_ROOT (~/dejaview-data)                   │   │ embed :8004 · (ocrd EPYC optional)  │
                         └───────────────────────────────────────────────┘   └─────────────────────────────────────┘
                                      GATEWAY_URL is the only Mac↔server seam
                                      (dev: SSH tunnel Mac :14000 → server :4000)
```

- **Stateful on Mac only:** Postgres, Redis, screenshots/audio/docs, audit logs. One portable `DATA_ROOT`.
- **Server is stateless:** model services + gateway (+ optional EPYC OCR). No user data, no prompt logs on disk.
- **Network:** LAN or Tailscale/WireGuard; SSH tunnel is fine for smoke (see below).

### Topology B — Single-box AMD (judge / demo)

*All services on one AMD machine (handbook §2.2「单机」). Same images; point `GATEWAY_URL` at localhost. Use when a judge must reproduce without a Mac data plane.*

```
┌──────────────────────────── AMD single box (stateful + compute) ────────────────────────────┐
│  capture ─▶ memoryd / ocrd / Honcho / Postgres / DATA_ROOT                                  │
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

---

## Topology A smoke (clean machine)

Prereqs: Docker Desktop · [`uv`](https://github.com/astral-sh/uv) · SSH host alias `radeon-cloud` to an AMD box with the inference stack (see [`DEPLOY.md`](deploy/server/DEPLOY.md)). Copy env templates first:

```bash
cp .env.example .env
cp deploy/mac/honcho.env.example deploy/mac/honcho.env   # edit if needed; no secrets required for local smoke
```

Minimal commands (full recipe + troubleshooting: [`STATUS.md`](STATUS.md) · handbook §12.5):

```bash
# 1. Data layer + Honcho (Mac)
make data-up
docker compose -f deploy/mac/compose.honcho.yml up -d
# once: align Honcho pgvector dim to 1024
docker compose -f deploy/mac/compose.honcho.yml run --rm --no-deps \
  --entrypoint /app/.venv/bin/python honcho-api scripts/configure_embeddings.py --yes

# 2. AMD inference (4 small models; brain on demand — check VRAM first)
ssh radeon-cloud "cd /root/dejaview-launch && ./server-stack.sh up embed fast sentinel perceive"

# 3. Tunnel (server gateway is not public)
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud

# 4. ocrd · memoryd · capture
cd services/ocrd && nohup uv run python -m ocrd > /tmp/ocrd.log 2>&1 &
MEMORYD_REAL_PIPELINE=1 GATEWAY_URL=http://127.0.0.1:14000/v1 \
  nohup uv run --project services/memoryd python -m memoryd > /tmp/memoryd.log 2>&1 &
cd clients/capture && CAPTURE_DEVICE_ID=dev uv run python -m capture

# 5. agentd Q&A (start brain on server first if needed: ./server-stack.sh up brain)
GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/agentd python -m agentd
curl -s http://127.0.0.1:8101/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What GPU errors have I hit recently? Cite the events."}]}'
```

| Layer | How to start | Port |
|---|---|---|
| Data | `make data-up` | pg `:5433` · redis `:6380` |
| Honcho | `compose.honcho.yml up -d` | `:8100` |
| Tunnel | `ssh -L 14000:…:4000` | Mac `:14000` → server `:4000` |
| ocrd | `uv run python -m ocrd` | `:8006` |
| memoryd | `MEMORYD_REAL_PIPELINE=1 … python -m memoryd` | `:8090` |
| agentd | `python -m agentd` | `:8101` |
| capture | `python -m capture` | — |

---

## Logical model names

Application code may only use these names (via `GATEWAY_URL`). Physical routing lives in `deploy/server/litellm.yaml`.

| Logical name | Role | Physical model | Port |
|---|---|---|---|
| `brain` | Deep: reasoning / planning / vision / writing | ThinkingCap-Qwen3.6-27B (+ mmproj) | 8001 |
| `perceive` | Mid: screen understanding, ASR, Honcho deriver baseline | Gemma 4 E4B (+ mmproj) | 8002 |
| `sentinel` | Fast-lane vision: privacy classify | MiniCPM-V 4.6 Q4_K_M (+ mmproj) | 8003 |
| `fast` | Fast-lane text: novelty / merge / tags | MiniCPM5-1B | 8005 |
| `embed` | All embeddings (query side adds instruction prefix) | Qwen3-Embedding-0.6B (1024-d) | 8004 |
| `ocrd` *(not LLM)* | Deterministic verbatim OCR | PP-OCRv6 / rapidocr (CPU) | 8006 |

**Cloud-swap rules (dev only):** (1) **`sentinel` stays local forever** — it sees unfiltered screens. (2) Switching `embed` requires a full re-index. (3) Contest demo / submission video must be **fully local**.

---

## Privacy & data sovereignty

- User memory (Postgres, Redis, `DATA_ROOT` screenshots/audio/docs, audit logs) lives on **your device** — never on the AMD compute node.
- Capture client: **zero local disk** (in-memory → POST → discard). Sentinel `block` frames write audit only — no OCR, no screenshot file.
- Repo contains **synthetic fixtures only** (no real PII, no API keys). Clear the timeline DB before public demos if you ran real capture.
- SearXNG stays **disabled** by default (conflicts with “data never leaves the device”).

---

## License

Third-party licenses will be collected in [`docs/licenses.md`](docs/licenses.md) (P3.5). Preview:

- **Apache-2.0:** ThinkingCap · MiniCPM · Honcho · llama.cpp · PaddleOCR · Qwen3-Embedding  
- **Gemma License:** Gemma 4 E4B — **called out separately**  
- **MIT:** LiteLLM · MarkItDown · Open WebUI  

Do not copy AGPL code (OpenRecall is reference-only).

---

## Status & further reading

**TASKBOARD:** G0+M+D **33/33 accept**. End-to-end pipeline verified; 54-min real-run acceptance passed. Phase 3 materials (ROCm ablation, Grafana, demo video, licenses) in flight — see [`STATUS.md`](STATUS.md).

| Doc | Purpose |
|---|---|
| [`STATUS.md`](STATUS.md) | Human snapshot: start table, known issues, next steps — **read first** |
| [`docs/EXECUTION_HANDBOOK.md`](docs/EXECUTION_HANDBOOK.md) | Single source of truth (architecture, specs, handoff §12) |
| [`docs/verification-log.md`](docs/verification-log.md) | Resolved `[VERIFY]` + pitfalls |
| [`docs/benchmarks.md`](docs/benchmarks.md) | OCR A/B + ROCm ablation (P3.1) |
| [`deploy/server/DEPLOY.md`](deploy/server/DEPLOY.md) | AMD server ops / VRAM / tunnel |
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
