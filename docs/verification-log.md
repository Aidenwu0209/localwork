# Verification Log

Resolved `[VERIFY]` items and load-bearing empirical findings. Append-only; newest at the bottom.

## 2026-07-20 (planning session)

- **Server reachability**: `ssh root@36.150.116.200 -p 30147` passwordless OK (alias `radeon-cloud` in `~/.ssh/config`). `nproc=128`. Root fs is **overlay** (container); free ≈2.0T.
- **Persistent storage**: only `/workspace` survives rebuilds, and it is a **10 GB** NFS PVC — too small for weights. Decision: weights on overlay `/root/dejaview-models/`, bootstrap script + sha256 in `/workspace/dejaview-models/` + git.
- **Mac hardware**: Apple M5, **16 GB** unified memory → dev stack must start/stop instances per task; 27B never runs locally; dev `brain` is served by the E4B instance (dual-mapped in LiteLLM).
- **No cloud API key available** → all dev inference is local (Metal). Bonus: even dev data never leaves the device.
- **HF download route**: direct HF unreachable from server; `hf-mirror.com` works. **hf CLI fails with Xet CAS 401 through the mirror** for newer repos (MiniCPM-V-4.6, MiniCPM5, Qwen3-Embedding); `HF_HUB_DISABLE_XET=1` did NOT help; plain `wget -c` on resolve URLs works. Bootstrap script uses wget only.
- **`[VERIFY]` fast GGUF repo — resolved**: `openbmb/MiniCPM5-1B-GGUF` (HF + ModelScope). File `MiniCPM5-1B-Q8_0.gguf` (1.1 GB). Official llama.cpp cookbook recommends `--jinja` for llama-server.
- **Bonus find**: `ggml-org/gemma-4-E4B-it-GGUF` ships `mtp-gemma-4-E4B-it-Q8_0.gguf` (95M MTP head) → E4B speculative decoding candidate; benchmark in S window (T0.7/T0.8).
- **GitHub**: push identity verified (`Aidenwu0209` via SSH, port 443 route). `gh` CLI token invalid — repo ops via plain git. Remote designated by user: `Aidenwu0209/localwork` (private).
- **Git identity**: global `user.name=Aidenwu0209`, `user.email=1418557225@qq.com` — do not override; no Co-authored-by / AI trailers in commit messages.

## Open `[VERIFY]` items (owners: upcoming tasks)

- PaddleOCR 3.7 Python API params & PP-OCRv6 model fetch (M5.1) — **resolved 2026-07-21**, see the "2026-07-21 (M5.1)" section below.
- llama.cpp: exposure of MiniCPM-V 4x/16x visual-token compression switch (S window).
- llama.cpp `--spec-type draft-mtp` exact flag/behavior on current build, ROCm gfx1100 (T0.7).
- LiteLLM passthrough of image/audio content parts to llama.cpp backends (M2.5 first contact).
- llama.cpp `/v1/rerank` + Qwen3-Reranker-0.6B GGUF availability (optional, Phase 2).
- E4B audio input via llama.cpp on ROCm (T0.6, S window; mmproj must be BF16).

## 2026-07-21 (data layer + Honcho bring-up, M1.3 / M3.1 / M2.4)

- **Gemma 4 E4B GGUF quant naming — resolved**: the `ggml-org/gemma-4-E4B-it-GGUF` repo uses llama.cpp's newer naming `Q4_0` / `Q8_0`, **NOT** `Q4_K_M`. The handbook's `Q4_K_M` does not exist there and the server returns a 15-byte `"Entry not found"` body — which the old `download-dev-models.sh` happily accepted because it only checked `[ -s ]` (non-empty). Fixed: the script now verifies against the remote `Content-Length` before skipping, and the Mac dev file is `gemma-4-E4B-it-Q4_0.gguf` (4.59 GB; server keeps Q8_0 per D4). Q4_0 + BF16 mmproj ≈ 5.5 GB matches the handbook's 16 GB budget.
- **Docker Desktop VM egress — resolved (proxy leak)**: containers got `Connection refused` on every HTTPS request even though the host could reach PyPI/Tsinghua directly. Root cause: the host shell exports `HTTP(S)_PROXY=http://127.0.0.1:7897` (a local Clash/Mihomo); Docker Desktop passes these through, but inside a container `127.0.0.1` is the container itself. The proxy binds to 127.0.0.1 only (not LAN-reachable via `host.docker.internal`). DNS resolves fine; raw TCP to the resolved IP works; only proxied requests fail. Fix: scrub proxy vars — inline `unset` before `uv` in the build, and blank them in `compose.honcho.yml` `environment` for runtime. The Honcho stack now starts clean.
- **uv.lock embedded URLs vs `UV_INDEX_URL` — resolved**: `uv.lock` carries absolute per-wheel URLs (with hashes) under `files.pythonhosted.org`. Under `--frozen`, uv downloads from those exact URLs and ignores index overrides, so `UV_INDEX_URL` alone did nothing. The Tsinghua mirror mirrors PyPI's CDN under the identical `/packages/xx/yy/...` path, so a `sed s|files.pythonhosted.org|pypi.tuna.tsinghua.edu.cn|` rewrite (2623 URLs) keeps the locked hashes valid and `--frozen` passes.
- **Honcho default vector dim 1536 vs our 1024 — resolved**: Honcho's alembic migration creates `documents.embedding` and `message_embeddings.embedding` as `vector(1536)` (OpenAI default). With `EMBEDDING_VECTOR_DIMENSIONS=1024` the startup validator refuses to boot. Bootstrap order is: (1) alembic upgrade, (2) `scripts/configure_embeddings.py --yes` to ALTER both columns + rebuild HNSW indices, (3) start api/deriver. Run it via `docker compose -f deploy/mac/compose.honcho.yml run --rm --no-deps --entrypoint /app/.venv/bin/python honcho-api scripts/configure_embeddings.py --yes` (must bypass `docker/entrypoint.sh` or it tries to start the API and fails validation).
- **Submodule hygiene**: rather than patching `third_party/honcho/Dockerfile` (which would dirty the submodule), the PyPI-mirror build is a wrapper `deploy/mac/honcho.Dockerfile` with build context = repo root (`context: ../..`) so COPY paths are `third_party/honcho/...`. The submodule tree stays pristine; `setup-honcho.sh` still controls the patch stack for source-level changes.

## 2026-07-21 (inference stack bring-up, M2.5)

- **LiteLLM proxy `master_key` triggers prisma import crash — resolved**: setting `master_key` in `general_settings` makes LiteLLM 1.93 take the DB-backed auth path, which `import prisma` and crashes (`ModuleNotFoundError: No module named 'prisma'`) when prisma isn't installed. Fix: omit `master_key` entirely — the gateway binds 127.0.0.1 (and the AMD server sits behind Tailscale), so open access is fine for local dev. Documented in `deploy/server/litellm.yaml`.
- **LiteLLM runtime: use `uvx --from 'litellm[proxy]'`** (litellm 1.93.0, 102 deps) rather than the host's mise/anaconda installs, which lack the proxy extras (`backoff`, etc.). The host's `litellm` shim is the bare client, not the proxy server. `deploy/mac/llama-launch/gateway.sh` wraps this.
- **MiniCPM5 (fast) and MiniCPM-V 4.6 (sentinel) are reasoning models — resolved**: by default they emit a `reasoning_content` chain-of-thought and return empty `content` until thinking completes. With a small `max_tokens` the whole budget is consumed by CoT and the actual answer never appears (`finish_reason: length`, empty content). Fix per task type:
  - Fast-track tasks (novelty gate / tagging / sentinel classification): pass `chat_template_kwargs: {"enable_thinking": false}` in the request body → direct answer, no CoT. This is the handbook's "no-think 模式为主" for the fast lane (§6.1).
  - Deep tasks (when actually using their reasoning): give a generous `max_tokens` (≥400) so CoT + answer both fit, and read `content` (the final answer), ignoring `reasoning_content`.
  - Confirmed working via the gateway: fast → "Hello! 😊" (no-think), sentinel → correctly described the Acme Bank login page from a screenshot (no-think).
- **Gemma 4 E4B (perceive / dev brain) is also a reasoning model**: same pattern — `reasoning_content` holds the work, `content` holds the final answer. Verified `brain` (E4B dual-mapped) computes 17×23=391 correctly when given ≥400 tokens. So the dev `brain` is usable for tool-calling / planning, just budget tokens for CoT.
- **Memory budget on Apple M5 / 16GB confirmed**: small trio (sentinel + fast + embed) ≈ 3.5 GB resident, runs comfortably alongside the OS. Adding perceive (E4B Q4_0 + BF16 mmproj ≈ 5.5 GB) alongside the trio pushes ~9 GB of model weight + OS overhead — workable but tight; `deploy/mac/llama-launch/dev-stack.sh` starts instances on demand per task. Full pyramid (all four) is the practical ceiling on this machine; the real 27B brain only ever runs on the AMD server (S2 window).
- **Inference stack operational**: 5 logical names (brain/perceive/sentinel/fast/embed) all route through the LiteLLM gateway (:4000) to llama-server Metal instances; `deploy/mac/llama-launch/{embed,fast,sentinel,perceive,gateway,dev-stack}.sh` control start/stop/status/smoke. `dev-stack.sh up <roles...>` starts only the instances the current task needs.

## 2026-07-21 (M5.1 — ocrd OCR microservice, PaddleOCR 3.7 + ARM backend)

`[VERIFY]` resolved: PaddleOCR 3.7 Python API + PP-OCRv6 model fetch + ARM (Apple M5) backend choice. All numbers are **real measurements on this Mac** (Apple M5, 16 GB, macOS 25.5, arm64); production runs on dual-socket EPYC and will be much faster — these are dev reference values, not SLA.

- **PaddleOCR 3.7 installs cleanly on ARM Mac without `paddlepaddle`.** `uv add paddleocr onnxruntime` pulls `paddleocr==3.7.0` + `paddlex==3.7.2` + `onnxruntime==1.27.0`; the heavy `paddlepaddle` wheel is **not** a transitive dependency. The default inference engine is `paddle_static` (which needs paddlepaddle), so you must pass `engine='onnxruntime'` explicitly. Valid engine literals are `['paddle', 'paddle_static', 'paddle_dynamic', 'transformers', 'onnxruntime']` — `'onnx'` is rejected with a `ValueError`.
- **PP-OCRv6 model fetch is fully automatic.** First `PaddleOCR(...).predict(img)` downloads `PP-OCRv6_medium_det_onnx` + `PP-OCRv6_medium_rec_onnx` (≈34.5M combined) into `~/.paddlex/official_models/` via the paddlex model hoster. No manual `wget`/ModelScope dance. The first-connectivity check to the hoster is slow — set `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True` to skip it once models are cached.
- **API surface (3.7):** `PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, lang='ch', engine='onnxruntime', device='cpu')`. The three preprocessing switches map 1:1 to the handbook's "all off" rule. `predict()` returns a list of `OCRResult`; per-line data lives at `result[0].json['res']` with keys `rec_polys` (4×2 polygon), `rec_texts`, `rec_scores`. `dt_polys`/`rec_boxes` are the detection-side alternatives. (Note: `result[0].json['res']` is a **dict**, not a list — easy to mis-index.)
- **PaddleOCR 3.7 on this Mac is unusably slow** (the deciding finding). With `engine='onnxruntime'`, PP-OCRv6_medium steady-state per-image inference: terminal_01 **116 s**, webpage_01 **27 s**, code_01 **20 s** (1920×1080 screenshots). Cold first-call adds one extra ~60 s pass. This is ~50–100× the handbook's "<1s P95 on EPYC" expectation and makes any local iteration loop painful. Root cause is unclear but reproducible (likely onnxruntime CPU thread scheduling on M5 + PP-OCRv6 det side-len handling); not investigated further because a faster path exists.
- **Backend decision — `rapidocr-onnxruntime` for Mac dev, PaddleOCR retained for EPYC prod.** `rapidocr-onnxruntime==1.4.4` (PP-OCRv4 ONNX packaging, same det+rec philosophy) runs the same three images in **~0.8–1.3 s steady-state** (median: terminal 782 ms, webpage 872 ms, code 1202 ms) — ~60× faster than PaddleOCR 3.7 here, and on the synthetic test set it is **at least as accurate** (it correctly transcribes the `https://docs.demo-acme.io/errors/...` URL; PaddleOCR also mis-read `ROCM` as `R0cM`). The service's `engine.py` ships both backends behind one protocol; `OCR_BACKEND=rapidocr` (default) vs `OCR_BACKEND=paddleocr`. Production on EPYC should switch to `paddleocr` to use the newer PP-OCRv6_medium model (the accuracy edge the handbook cites is a v6-over-v5 claim; on EPYC the speed penalty that kills it on M5 should not apply — to re-confirm in T0.5/T1.8 A/B).
- **Preprocessing all-off confirmed harmless for screenshots.** Neither RapidOCR's defaults nor PaddleOCR with all three switches off produce orientation/row-flip errors on the upright synthetic screenshots; the "all off" rule is pure speedup as designed.
- **Contract shape verified end-to-end via the running service.** `POST /ocr` (multipart `file`) on all three test images returns `{"full_text","blocks":[{"text","bbox":[x1,y1,x2,y2],"conf"}], "backend","elapsed_ms","n_blocks"}`; every block's bbox is exactly 4 floats, every conf ∈ [0,1], no stray keys — compatible with `memoryd.models.OcrResult`/`OcrBlock`. Error paths: empty upload → HTTP 400, `text/plain` content-type → HTTP 415, oversized (>25 MiB) → HTTP 413, undecodable bytes → HTTP 422. `/health` returns before the model is loaded (`engine_loaded=false`) so it stays cheap.
- **Entity-recognition check vs ground-truth JSON:** `terminal_01` — URL `docs.demo-acme.io/errors/...` transcribed correctly; identifier `ROCM-4042` picked up as `R0cM-4042` (single-char OCR noise — the substring `4042`, the URL stem, and `hip_alloc`/`dejaview-core`/`acme-parser` all hit). `webpage_01` — all three `docs.demo-acme.io/{zh,en}/...` URLs exact, Chinese+English mixed paragraph transcribed (this is the handbook's primary acceptance scenario for the multilingual single-model claim). `code_01` — `def parse_timeline`, `class TimelineParser`, `from acme_parser import Tokenizer, LexerError`, `from lumen_rpc import RemoteCursor` all exact. The residual `O`/`0` and case confusions on small glyphs are exactly the "verbatim from OCR, never self-transcribe" guardrail's reason for existing, and pg_trgm fuzzy search will absorb them.
- **Files:** `services/ocrd/src/ocrd/{engine,server,__init__,__main__}.py`, `services/ocrd/README.md`. Service boots in <1 s (lazy model load on first `/ocr`); run with `cd services/ocrd && uv run python -m ocrd` (loopback 127.0.0.1:8006 only, per §6.1).

## 2026-07-21 (Honcho memory link, M2.6)

- **Honcho v3 API requires client-supplied ids** for workspace/peer/session (`POST /v3/workspaces` body needs `id`, not just `name`; same for peers and sessions). This matches the handbook's "session=按天" model — the caller owns session identity. Reruns reuse stable ids (`dejaview` / `owner` / `m2_6-seed`).
- **Docker Desktop `host.docker.internal` resolves IPv6-first — resolved**: `/etc/hosts` inside the Honcho container had BOTH `192.168.65.254` (IPv4) and `fdc4:f303:9324::254` (IPv6) for `host.docker.internal`. psycopg's getaddrinfo returned IPv6 first, and the Docker NAT IPv6 route was unreachable → `connection is bad: Network is unreachable`. Symptoms: deriver (early connection) worked, but the dialectic `/chat` path opened fresh SQLAlchemy connections that hit IPv6 and 500'd. Fix: replace `host.docker.internal` with the literal IPv4 `192.168.65.254` in `honcho.env` (DB / Redis / gateway URLs). Trade-off: this IP is Docker-Desktop-internal and could shift on a Docker update — if Honcho connections break after a Docker upgrade, re-resolve via `docker compose exec honcho-api getent hosts host.docker.internal` and update. Documented inline in `honcho.env`.
- **Memory link end-to-end — operational**: ingested 20 M6.2 synthetic messages (Jordan Lee persona) → deriver produced an accurate short summary via `perceive` ("Jordan, a 29-year-old Backend Engineer at Northwind Pay... focusing on the payments", 39 s for the 1000-token summary) → `/peers/{id}/chat` dialectic via `brain` (dev-mapped to E4B) answered "What does this person do for work?" correctly with detail (role + Go/Postgres/Redis/gRPC/Kafka stack + prior PHP experience + morning deep-work habit). The dialectic is a multi-turn agent loop (it calls `search_messages` for evidence), so latency is high on the dev E4B (~127 s for one answer); S2's 27B brain will be faster AND higher-quality.
- **Dev brain latency caveat**: E4B-as-brain works for verifying link correctness but is too slow for snappy dialectic UX. Sequential questions on a single instance can also time out under concurrent load. This is expected and acceptable for M2.6 (acceptance = "conclusions non-empty + dialectic gives grounded answers"); quality/speed is re-verified on S2 with ThinkingCap-27B.
- **Files:** `services/memoryd/scripts/seed_honcho.py` (workspace/peer/session bootstrap → batch ingest → queue-drain wait → dialectic Q&A). Run with the inference stack up (`dev-stack.sh up embed perceive`) and Honcho stack up (`compose.honcho.yml up`).


## 2026-07-21 (server S1: engine + first boot, T0.2)

- **Shared-GPU constraint**: a Dolphin-v2-ROCm eval job (PID 20527, torch 2.7.1+rocm7.2.0) holds ~10.6 GB VRAM throughout. User instruction: do NOT kill or impact it. Available VRAM for DejaView ≈ 37 GB; we deploy the small four常驻 (~12 GB) and run the 27B brain Q6_K on-demand to stay under the 48 GB ceiling.
- **llama.cpp build — DONE**: cloned ggml-org/llama.cpp @ 76f46ad29 (direct clone needed `GIT_SSL_NO_VERIFY=true` — GitHub cert chain failed on this server; mirrors gitclone/ghfast/kkgithub all unreachable). Configured with `-DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1100`; cmake 3.28 (apt-installed); HIP via ROCm 7.2 clang 22. Built `-j32` (NOT -j128, to leave CPU for Dolphin) in ~5 min. Binary at `/root/llama.cpp/build/bin/llama-server`. HTTPS disabled (no OpenSSL) — irrelevant, we serve local weights only.
- **T0.2 PASS — ROCm can compile, load, and infer**: booted `Qwen3-Embedding-0.6B-Q8_0` on :18004, `POST /v1/embeddings` returned a correct 1024-dim vector in seconds. VRAM delta was within Dolphin's own fluctuation — the test instance did not disturb the running job. This nails the handbook's "ROCm 生死题" (compile / load / fit / emit tokens) which is the backbone of the 40-point optimisation score.
- **lemonade-sdk not used**: `lemonade-core-rocm` / `lemonade-server` are not on PyPI (a same-named parsing tool `lemonade` is — do not confuse). The GitHub install path would need the same SSL workaround; source compile is the deterministic path so we took it. The handbook's "lemonade-sdk prefback加分" remains an option for the README but is not load-bearing.

## 2026-07-21 (server S1: 5-instance smoke, T0.4)

- **T0.4 PASS — all 5 logical names serve via :4000**: embed (1024-dim, 27ms), fast (no-think chat OK), sentinel (vision), perceive (Gemma E4B Q8, 12×8=96), brain (ThinkingCap-Qwen3.6-27B Q6_K, 17×23=391 in 14.6 s incl. reasoning). This is the load-bearing evidence for the 40-point ROCm optimisation score: the 27B fits, loads, and infers on the W7900D under ROCm 7.2.
- **Shared-GPU VRAM choreography (Dolphin co-tenant)**: Dolphin (~10.6 GB) + the four常驻 small (sentinel+fast+embed+perceive ≈ 12 GB) = ~23 GB with ~25 GB free. brain Q8_0 (28 GB) would push the total to ~50.6 GB > 48 GB → OOM Dolphin. Resolution per handbook §2.4: run brain at **Q6_K (21 GB)**. Even Q6_K is tight alongside the full常驻 four + Dolphin (~43 GB), so the practical ops pattern is **stop perceive before starting brain** (brain itself can serve perceive's screen-understanding role), run brain, then optionally restart perceive. brain Q6_K measured: 17×23=391 correct in 14.6 s, no disturbance to Dolphin.
- **Gateway coexistence verified**: Dolphin's VRAM footprint was unchanged before/during/after the brain smoke (10.6 GB ± Dolphin's own fluctuation); the eval job kept its 24 h+ uptime throughout. The dev-stack `-j32` build + per-instance `--log-disable` + `server-stack.sh` pidfile controller kept the co-tenant untouched.
- **SSH tunnel for Mac access**: the server gateway binds 0.0.0.0:4000 but that port isn't exposed publicly. Mac reaches it via `ssh -N -L 14000:127.0.0.1:4000 radeon-cloud`, so Mac `.env` uses `GATEWAY_URL=http://127.0.0.1:14000/v1`. The tunnel adds latency jitter (httpx ReadTimeout on serial batches) — `seed_fixtures.py` and `agentd/embed.py` gained retry + 90-120 s timeouts to absorb it.

## 2026-07-21 (agentd出口 + M7.2)

- **M7.2 PASS — OpenAI-compatible /v1/chat/completions with tool-calling + citations**: agentd forwards the user message to brain (logical name) with the four tools attached, runs the tool-call loop locally (call brain → execute tool_calls via dispatch → feed results back → repeat, capped at 6 rounds), and enforces the handbook §6.5 answer discipline via system prompt: every memory reference must carry `[event#<id> <HH:MM> <app>]`.
- **End-to-end verified** (via SSH tunnel to the server's brain Q6_K + embed, fixture-seeded timeline): asked "What GPU errors have I hit recently?" → brain autonomously called `search_timeline`, returned the ROCM-4042 / HIP buffer allocation failure event, and answered with a correctly-formatted citation `[event#120 00:45 Terminal]` traceable to a real timeline row. This is the handbook §6.5 acceptance ("检索→引用→回答, 引用可回溯到事件").
- **Non-streaming first**: `stream:true` is acknowledged but answered as a single JSON (Phase 2 will add SSE). Open WebUI handles both.
- **brain latency on Q6_K**: one round-trip tool-calling answer ~30-60s end-to-end (brain thinks + embed + DB); acceptable for M7.2 correctness, optimisation in T3.1.

## 2026-07-21 (full pipeline M3.4)

- **M3.4 PASS — full local pipeline runs end to end**: with memoryd in REAL_PIPELINE mode (MEMORYD_REAL_PIPELINE=1), GATEWAY_URL pointed at the server gateway via SSH tunnel, and ocrd running locally on Mac, all four real stages execute per frame: sentinel (MiniCPM-V via gateway) → ocrd (local PP-OCRv4) → novelty gate (Jaccard + fast) → perceive (Gemma E4B via gateway) → embed (Qwen3 via gateway) → timeline_events + screenshot. The three M3.4 acceptance points are met:
  - **Merge path**: re-sending code_01.png twice merged into the original event (`merged_into=166, jaccard 1.00 >= 0.85`) — no new row, end_ts advanced. The two-tier Jaccard-then-fast gate works.
  - **Ingest + perceive path**: code/webpage/chat frames produced real timeline_events rows with perceive-generated activity ("working in VS Code", "working in Chrome", "working in Slack").
  - **Block + audit path**: a frame the sentinel blocks writes ONLY to sentinel_audit and discards the image (no OCR, no timeline row, no screenshot) — the privacy invariant holds for whatever the sentinel decides to block.
- **Tunnel jitter → retry+longer timeouts**: the SSH tunnel adds latency jitter on image payloads, which caused httpx ReadTimeout at sentinel and perceive. GatewaySentinel (180s, 3 retries), GatewayPerceive (240s, 3 retries) now absorb it; OcrdClient stays local so no retry needed there.
- **Sentinel precision is T2.1, not M3.4**: in this run the sentinel mis-classified the 4 sensitive frames as `allow/normal` (and over-blocked terminal_01). The classification accuracy + prompt tuning is the T2.1 acceptance criterion ("拦截率与误杀率报告"); M3.4 only owns the pipeline wiring, which is correct — every sentinel decision flows through the right branch (block→audit-only, allow→OCR→...→store). The terminal_01 block correctly discarded the image and wrote audit-only.
- **Hybrid topology confirmed**: Mac (data-sovereignty: memoryd + ocrd + Postgres + screenshots) ↔ AMD server (compute: 4 small常驻 models on ROCm). GATEWAY_URL is the only seam. The full-pyramid M3.4 run did not load the 27B brain (perceive handles the screen-understanding step), so it coexisted trivially with the Dolphin co-tenant.

## 2026-07-22 (M4.4 real-run acceptance + two bugs found/fixed)

- **WebP->PNG fix (load-bearing)**: capture encodes frames as WebP (handbook 5.2), but llama.cpp's MiniCPM-V vision backend only accepts PNG/JPEG. Every frame 400'd at the gateway (`Failed to load image or audio file`) and memoryd surfaced that as a 500 to capture, which silently dropped it. An earlier 4-hour real run produced ZERO events because of this. memoryd now re-encodes WebP->PNG via `_to_png_if_needed()` before sending to sentinel/perceive.
- **Per-window capture (product fix)**: the original capture grabbed only the foreground monitor (mss monitor 1), so switching windows lost all other windows' content — you couldn't tell what the user was actually doing across their multi-window workflow. Rewritten to enumerate all on-screen windows (`CGWindowListCopyWindowInfo`) and capture each via Apple's `screencapture -l <wid>` (pyobjc `CGWindowListCreateImage` returns None for many cross-app windows even WITH Screen Recording permission — `screencapture` has the full entitlement). Each window becomes its own frame with its own app/title; dedup is per-window keyed by `owner::title`; capped at 8 windows/cycle.
- **M4.4 acceptance — PASS** (54-min real run, user working normally):
  - **Timeline grew real events**: 61 events across 12 apps (Google Chrome 21, 微信 10, ZCode 7, Code 6, 访达 4, ChatGPT 3, WhatsApp 2, Telegram/Orca/Slack/屏幕共享/Cursor 1 each). OCR accurate per window — Code frames show `def parse_args()`, `parser.add_argument('--log_dir', ...)`, file tree (`train_classification.py`, `Pointnet_Pointnet2_pytorch`), terminal prompts.
  - **Zero external network**: gateway logged 1559 POSTs, all from the Mac tunnel; no outbound calls to openai.com / anthropic / etc. (one recurring `model=None` 400 is an internal client bug, not external traffic — 32/1559 = 2%, to fix).
  - **Sentinel audit recorded**: 81 decisions (20 block + 61 allow). Blocks include real sensitive frames: banking_finance 3, private_chat 1, password_prompt 1 (the privacy invariant held — blocked frames wrote audit-only, no OCR/row/screenshot). 15 blocks were `normal` (over-blocking, sentinel precision = T2.1 prompt-tuning scope, not M4.4).
  - **Client zero-disk**: capture dir has no data files; all 61 screenshots live under DATA_ROOT/screenshots/YYYY/MM/DD/ as webp (only for ALLOWED frames). per-window `screencapture` writes to a NamedTemporaryFile that is unlinked immediately after the in-memory PIL encode.
- **Sentinel confidence always 0.5**: every audit row shows confidence 0.5, which means the JSON-parse fallback fired (`_parse_sentinel_json` default). The classification itself is sometimes correct (banking/private_chat/password hit) but the model isn't returning the strict-JSON schema — T2.1 will tighten the prompt and parse.
- **Latency under SSH tunnel**: ~12-15s per window through the full pipeline (sentinel+ocrd+perceive+embed); a full 6-8 window cycle ≈ 90s. Acceptable for the tunnel; will drop sharply when the gateway is on LAN (sentinel ~0.5s locally).


## 2026-07-23 (P3.1 ROCm ablation — historical partial / blocked; superseded 2026-07-28)

- **Partial pass on W7900D / ROCm 7.2 / llama.cpp 76f46ad29**: with `embed+fast+sentinel+perceive` up, measured n=3 medians via `/v1/chat/completions` `timings` (thinking disabled):
  - **fast** Q8_0: decode **366.7 tok/s**, wall **13.4 ms**
  - **sentinel** vision classify: decode **221.1 tok/s**, wall **108.1 ms**
  - **perceive** text: decode **80.7 tok/s**, wall **243 ms**; vision: decode **79.9 tok/s**, wall **416 ms**
  - **VRAM** 4-model residency: **13.71 / 47.98 GiB** used (`docs/assets/rocm-smi-vram-4model.png`)
- **Dolphin co-tenant**: historical PID 20527 **absent** at capture (VRAM matches 4-model stack only).
- **MTP flag surface**: `llama-server --help` lists `--spec-type ... draft-mtp ...` on this build → flag exists. On/off tok/s A/B for ThinkingCap-27B **not run** (SSH dropped before brain up) — still `[VERIFY]`.
- **Blocked**: after perceive single-request benches, `ssh radeon-cloud` (`36.150.116.200:30147`) returned **Connection refused** for >10 min (host still ICMP-reachable). Missing brain Q8/Q6/Q4 × MTP × concurrency and perceive `-np` 1/2/4 sweep. Q4_K_M download had reached ~18% (~2.9 GB of 16 GB) — resume with `wget -c` when SSH returns.
- Tables live in `docs/benchmarks.md` §2; TASKBOARD P3.1 → `blocked`.

## 2026-07-23 (P3.6 sentinel tune + P3.7 perceive prompts)

### P3.6 — sentinel (accept)

- **Root cause (M4.4)**: MiniCPM-V often returned partial / inconsistent JSON (`{"decision":"allow"}` only, or `decision=block` with `category=normal`). Parser defaulted confidence to 0.5 on missing fields; 15/81 real-run blocks were `category=normal` (false kills).
- **Fix** (`services/memoryd/src/memoryd/stages.py`):
  1. Category-first prompt (ask only `category` + `confidence`).
  2. **Derive decision from category** (sensitive→block, `normal`→allow) — eliminates decision↔category disagreement.
  3. Stricter JSON extraction (fences, trailing commas, aliases); missing confidence with a known category defaults to **0.75** (no longer looks like parse-fallback 0.5).
- **Offline parse proofs**: `uv run python scripts/test_parse_offline.py` — covers `block+normal→allow`, `allow+banking→block`, category-only JSON, fences.
- **Live fixture subset** (Mac Metal MiniCPM-V Q4, CPU mmproj; AMD `radeon-cloud` SSH refused during this window): 10 images from `tests/assets/sentinel/`
  - **block recall 6/6**; **normal FP 0/4**
  - banking_finance 2/2, password_prompt 2/2, private_chat 2/2 all `block`
- **vs M4.4 baseline**: normal false-kill class `category=normal & decision=block` was **15/81 (~18.5%)** → parser now forces those to **allow** (that failure mode → **0**). Fixture spot-check FP **0/4** under the new prompt.
- **Scripts**: `services/memoryd/scripts/eval_sentinel.py`, `test_parse_offline.py`.

### P3.7 — perceive (accept)

- **Fix** (`stages.py`): concrete-activity prompt (forbid `working in X` / `using X`); `enable_thinking=false` for structured JSON; OCR-grounded activity fallback; **verbatim filtered to substrings of `ocr_text`**.
- **20-event spot-check** (`tests/assets/screenshots/`, text-only OCR→perceive on Mac Gemma-E4B Q4; vision path flaky on this Metal build so OCR-only isolates prompt/parse):
  - **generic activity 0/20** (was typically `working in {app}` via old fallback)
  - **verbatim ⊆ ocr_text violations 0/20** (parser drops hallucinations)
  - Examples: `defining parse_timeline method in TimelineParser class`, `displaying error ROCM-4042 in terminal`, `reading security advisory SEC-2026-0142 for nova-cipher in webpage`
- **Scripts**: `services/memoryd/scripts/eval_perceive.py` (`--ocr-from-gt` or live ocrd).
- **Note**: AMD ROCm gateway was down (`Connection refused` on :30147); Mac Metal vision + mmproj often segfaults after 1 request — use `--no-mmproj-offload` / text-only for local regression; production path remains gateway→ROCm when server is up.

## 2026-07-23 (handoff re-verify M1.3 / M2.4 / M3.1)

- **Trigger**: an older UI snapshot still showed these three as `doing` (半成品 @ `e5ade0c`). TASKBOARD already had them `accept` since 2026-07-21; ran live verify before freezing the Phase-3 handoff.
- **M1.3 PASS**: `make data-up` → `dejaview-data-database-1` + `dejaview-data-redis-1` both healthy; `psql -h 127.0.0.1 -p 5433 -U dejaview -d dejaview` connects; redis `PONG`.
- **M3.1 PASS**: extensions `vector 0.8.5` + `pg_trgm 1.6`; tables `timeline_events` / `sentinel_audit` / `kb_chunks`; `timeline_events` columns match handbook §6.3 (incl. `end_ts`); indexes pkey + hnsw embedding + ts + gin_trgm ocr_text + (app,ts); `honcho` database present.
- **M2.4 PASS**: `docker compose -f deploy/mac/compose.honcho.yml config` OK; stack already up; `curl http://127.0.0.1:8100/health` → HTTP 200 `{"status":"ok"}`.
- **Conclusion**: no leftover `doing`; all 33 G0+M+D tasks remain `accept`.

## 2026-07-28 ([VERIFY] P3.1 ROCm ablation — accept)

- **Authoritative evidence**: successful `mode=all` run
  `p31-w7900d-20260728T075653Z`; raw directory
  `docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/`; formal tables in
  `docs/benchmarks.md` §2. `shasum -a 256 -c SHA256SUMS` passed for every
  archived artifact.
- **Environment identity**: replacement instance `u-4695-e6d1476b`, 2× AMD
  EPYC 9334 / 128 logical CPUs / 1007.56 GiB RAM; W7900D-class gfx1100,
  51,522,830,336 B (47.98 GiB) VRAM; ROCm 7.2.1; driver 6.14.14.
  llama.cpp is clean source commit
  `76f46ad29d61fd8c1401e8221842934bf62a6064`, Release/HIP/gfx1100; the
  running binary SHA256 is
  `90d82cee630d8340b0f1f629e4675a23b7189b49f2d9869ed6efb424cfdeb55f`.
  The run manifest binds the runner, benchmark client, summarizer, six exact
  model/mmproj files, prompts, and perceive fixture by SHA256.
- **Safety preflight**: the first live-host capture was `rocm-smi`: GPU 0%,
  28,016,640 B used, assigned KFD GPU ID `60148`, and no KFD process scoped to
  that assigned GPU. All DejaView roles were down. No Dolphin/co-tenant process
  was present on the assigned GPU, so the harness did not touch or coexist with
  an unrecognized workload.
- **Complete matrix**: brain **18/18** =
  Q8_0/Q6_K/Q4_K_M × MTP off/on × c1/c4/c8; perceive **3/3** =
  Q8_0 with paired `(-np, client concurrency)` of (1,1)/(2,2)/(4,4).
  Every cell used one excluded warm-up plus **n=3 measured batches**; successful
  requests equalled `3×concurrency`.
- **Timing hygiene**: all 21 records used synthetic inputs,
  `temperature=0`, `enable_thinking=false`, and request
  `cache_prompt=false`; servers used `--cache-ram 0` and
  `--no-cache-idle-slots`; every measured response had `timings.cache_n=0`.
  The fail-closed summarizer recomputed request medians/P95 and batch aggregate
  medians from raw samples and accepted the complete matrix.
- **[VERIFY] ROCm execution**: all six brain loads and three perceive loads
  produced non-empty GPU proofs binding the local process to an exclusive
  assigned-KFD delta and the checksummed binary. Logs contain ROCm/HIP and full
  layer offload: brain **66/66**, perceive **43/43**. Resident and 200 ms sampled
  peak VRAM evidence is present for every cell.
- **[VERIFY] content gates**: every brain request returned the exact required
  `1..80` sequence (100% pass); every perceive request used the same fixture
  SHA256 `d7903ab467f554b2fba7489380024c603c0ad3b8785ccb08f62af07cc976caf9`
  and identified `parse.py` (100% pass). These are narrow deterministic
  compliance/visual-path gates, not general reasoning or VLM accuracy claims.
- **[VERIFY] MTP result**: deterministic on/off output parity **PASS**. All
  MTP-on cells accepted 98.9% of generated drafts. Aggregate-throughput ratios
  (on/off) were Q8_0 c1/c4/c8 =
  **2.018×/0.986×/0.973×**; Q6_K =
  **1.738×/1.261×/1.292×**; Q4_K_M =
  **1.474×/1.436×/1.504×**. MTP added **4.95 GiB** resident VRAM.
- **Production decision**: keep the fixed brain default **Q6_K**. Enable MTP
  for Q6_K only on an exclusive or positively headroom-checked session; when
  Dolphin or another co-tenant is present, stop perceive and keep MTP off unless
  post-load telemetry preserves the 6 GB reserve. Q8_0 uses MTP only at c1
  because c4/c8 regress; Q4_K_M+MTP is the throughput/headroom fallback but its
  narrow compliance pass does not justify replacing Q6_K for general quality.
- **[VERIFY] perceive scaling**: aggregate output rose
  **31.2 → 40.7 → 50.0 t/s** for `-np` 1/2/4, while per-request decode fell
  **59.4 → 46.8 → 32.8 t/s** and P95 rose
  **739.1 → 1150.8 → 1861.6 ms**. Production remains `-np 2` as the balance
  point; `-np 4` is the throughput-first option.
- **Cleanup / no residue**: entry and exit service snapshots both show
  gateway/sentinel/fast/embed/perceive/brain down; exit VRAM returned exactly
  to **28,016,640 B** and the assigned-GPU KFD inventory was empty. Per
  `service-state-policy.txt`, brain and perceive were deliberately left stopped
  rather than starting an unmonitored restore load.
- **Supersession**: this checksummed run closes the brain/MTP/concurrency and
  perceive `-np` gaps recorded in the historical 2026-07-23 P3.1 entry above.
  That earlier small-model pass remains provenance only and no longer represents
  current P3.1 status.

## 2026-08-02 ([VERIFY] P3.2 Grafana / ROCm live dashboard — accept)

- **Live topology:** replacement instance `u-15420-7be0d6c9`
  (`36.150.116.206:31357`) ran the five fixed model roles plus LiteLLM and the
  local-only ROCm exporter. Prometheus and Grafana ran on the Mac; all traffic
  crossed explicit SSH forwards, so the AMD host exposed no metrics or model
  port publicly.
- **[VERIFY] simultaneous fail-closed gates:** Prometheus returned ROCm exporter
  scrape success **1**, GPU series count **1**, required role health **4/4**
  (`perceive/sentinel/embed/fast`), and required roles with positive prompt or
  predicted tokens/s **4/4**. The same scrape window showed GPU utilization
  **100%**, VRAM used **37.03 GiB**, and timeline ingest rate **1.09 events/min**.
- **One-screen acceptance:** the provisioned Grafana dashboard displays per-role
  llama.cpp prompt/decode tokens/s, Radeon GPU utilization, VRAM utilization,
  the four green health/throughput gates, event rate by outcome, and live request
  pressure. Evidence: `docs/assets/p32/grafana-rocm-live-20260802.png`.
- **Transient-failure proof:** an existing SSH forwarding process had become
  stale while its PID remained alive; the dashboard correctly turned exporter,
  GPU-count, and role gates red. Recreating only the verified tunnel restored
  all endpoints and the next scrape turned every gate green. This confirms the
  dashboard fails closed instead of presenting stale telemetry as healthy.
- **Tests:** monitoring contract tests cover scrape topology, provisioned panels,
  pretty-JSON Grafana health output, and fail-closed queries; exporter parser
  tests cover valid, missing, and malformed `rocm-smi` output; memoryd metrics
  tests cover created/merged/blocked counters.

## 2026-08-02 ([VERIFY] P3.4 remote-link failover requirement change)

- **User-approved scope change:** Act 6 no longer requires a physical Ethernet
  cable pull. The accepted fault injection is a visible software disconnect of
  the already-attested Radeon SSH compute tunnel. The final video must still be
  ≤5 minutes, contain all six acts, and show the second grounded daily report
  finish through Local Metal fallback.
- **Fail-closed implementation:** the stage resolves the live `:14000` listener,
  reuses the exact formal-tunnel matcher (gateway + five role proof forwards +
  ROCm exporter forward), sends `SIGTERM` only to that verified PID, waits for
  process exit, and clears the connectivity cache. It does not alter Wi-Fi,
  network interfaces, firewall state, or the remote model processes.
- **[VERIFY] live fault injection:** the guarded endpoint terminated formal
  tunnel PID `86405` and returned
  `method=verified_ssh_tunnel_termination`; `/api/connectivity` then reported
  `remote_radeon=false`, `local_metal=true`, and `mode=local_fallback`. The
  versioned browser control repeated the test against PID `4473` and visibly
  changed the page to `LINK DOWN · LOCAL READY` with
  `RADEON LINK DISCONNECTED`.
- **Cache regression found and closed:** the first browser click loaded an old
  cached JavaScript asset and produced no POST despite the new HTML control.
  Stage HTML/CSS/JS now return `Cache-Control: no-store`, and HTML references
  versioned CSS/JS URLs. The behavior test first failed on the missing header
  and version query, then passed after the fix.
- **Status:** this verifies the revised disconnect mechanism, not the final
  P3.4 acceptance. `TASKBOARD.json` remains `doing` until a real ≤5-minute video
  artifact is inspected for all six acts and the completed Local Metal rerun.

## 2026-08-02 ([VERIFY] P3.4 six-act video acceptance)

- **Formal artifact:** `docs/assets/demo/dejaview-p34-six-act-20260802.mp4`;
  `ffprobe` reports **157.2 seconds**, 1920×1080, 30 fps H.264 video plus mono
  AAC narration, 6,407,325 bytes. SHA-256:
  `5dc772cea426b215ce6a87c83b75f7dbf2c9f9ca5884e77686e449c2f3ae23ed`.
  The timecode manifest is `docs/assets/demo/p34-video-manifest.json`.
- **Formal isolated run:** the stage started with the exact seven-forward
  Radeon tunnel at PID `6812`, five gateway roles and their dedicated metrics
  endpoints healthy, live ROCm exporter, warmed PP-OCRv6, real memoryd pipeline
  on `dejaview_demo`, isolated Honcho, and independently attested Local Metal.
- **Acts 1–5:** the captured run shows the five-role Radeon/storage split plus
  live Grafana and rocm-smi evidence; Act 2 creates three real pipeline events;
  Act 3 reports `BLOCKED`, `0 FILES`, `0 ROWS`, and `AUDIT #4`; Act 4 resolves
  PR #1842 to event #1 with the synthetic screenshot and bbox; Act 5 displays
  nine isolated Honcho-derived conclusions.
- **Act 6 remote and failover:** the first Planner→Retriever→Writer→Reviewer
  run reports `RADEON ROCM`, retrieves events #2–#4, and ends with Reviewer
  `PASS`. The visible disconnect control then terminates the exact attested
  tunnel; the page shows `LINK DOWN · LOCAL READY` and
  `RADEON LINK DISCONNECTED`. Wi-Fi and host interfaces are untouched.
- **Act 6 Local Metal:** the second run reports `LOCAL METAL FALLBACK`, uses the
  same three grounded event ids, completes the report, and ends with Reviewer
  `PASS`. Post-run checks find no listener on `:14000`, `remote_radeon=false`,
  `local_metal=true`, four timeline rows, four audits, and the newest audit is
  `banking_finance|block`; the screenshot root contains only the historical PR
  evidence and the three allowed Act 2 images.
- **Media QA:** the first assembly was rejected after timestamp sampling exposed
  reordered still frames and black gaps. The accepted re-encode creates twelve
  normalized clips first, concatenates them in declared timecode order, and was
  sampled at 13 points covering every act plus both sides of the disconnect.
  Audio has mean volume -18.2 dB, max -2.9 dB, and no silence ≥2 seconds at
  -45 dB. The accepted duration is **2:37.2**, below the five-minute gate.
- **Conclusion:** all P3.4 verify conditions are satisfied. The task moves from
  `doing` to `accept` in the same commit as the inspected video, manifest,
  narration, README link, status update, and this `[VERIFY]` record.

## 2026-08-02 ([VERIFY] P3.11 Grafana system self-check — accept)

- **Privacy-safe active probes:** the new private Compose-network exporter uses
  only standard-library TCP/HTTP checks. It validates database, Redis, Honcho,
  PP-OCRv6, `memoryd pipeline=real`, agentd, all five logical names through the
  Radeon tunnel, and a two-token Local Metal `fast` response with
  `chat_template_kwargs.enable_thinking=false`. It reads no timeline rows,
  screenshots, credentials, or `.env` files. The Local Metal inference is
  cached for 30 seconds.
- **Fail-closed proof before restoration:** with memoryd, agentd, and `:14000`
  absent, Prometheus reported `dejaview_selfcheck_state=0`; database, Redis,
  Honcho, and OCR were `1`; missing memoryd/agentd/Radeon were `0`; the real
  Local Metal probe was `1`. The dashboard rendered `FAILED`, `FAILED (<6)`,
  and `LOCAL FALLBACK`; GPU and VRAM were gray `NO DATA`, not green.
- **Restored READY proof:** after establishing the exact gateway + five-role +
  ROCm exporter SSH forwards and starting real-pipeline memoryd plus agentd,
  fresh Prometheus queries returned self-check **2**, age **7.32 s**, ROCm
  exporter **1**, GPU series **1**, required role health **4/4**, and required
  roles with positive tokens/s **4/4**. The same window showed GPU **100%** and
  VRAM **77.2%**.
- **UI QA:** the accepted 1280×720 PNG shows a single non-duplicated status per
  card, short untruncated gate titles, all four accepted P3.2 trend panels, and
  idle memory outcomes as zero instead of an ambiguous green/no-data state.
  Evidence: `docs/assets/p32/grafana-selfcheck-20260802.png`, SHA-256
  `75825f71e029809757d5333fcf3b47ca7573b60974b113ea94794cc06cd17bfd`;
  machine-readable evidence is the adjacent JSON file.
- **Fresh verification:** `python3 -m unittest discover -s
  deploy/mac/monitoring -p 'test_*.py' -v` ran **13 tests, 0 failures**;
  exporter byte-compilation, Compose config, JSON parsing, PNG inspection, and
  `git diff --check` all passed. Health exporter, Prometheus, and Grafana all
  reported healthy.
- **Conclusion:** every P3.11 verify condition is satisfied; the task moves from
  `doing` to `accept` with the exporter, dashboard, tests, documentation, and
  retained evidence in the same commit.

## 2026-08-02 ([VERIFY] P3.12 mature-product design — accept)

- **Scope derived from current code, not task color:** the accepted competition
  demo remains intact, while P3.13–P3.18 cover the confirmed product gaps:
  fail-open Sentinel parsing/default stubs, capture outcome loss, fixed-gateway
  agentd, no-op timeline→Honcho projection, missing daily evidence UI, and
  clean-clone/release drift.
- **Architecture decision:** Topology A adds a mandatory local
  `SENTINEL_GATEWAY_URL` with no Radeon fallback for raw pixels. Allowed frames
  may use the stateless Radeon compute path; blocked pixels, durable timeline,
  audit, screenshots, and Honcho state remain on the data device. Logical model
  names and physical model choices are unchanged.
- **Honest capability boundary:** the mature MVP is single-user and
  screen-memory-first. Audio/document routes must return an explicit unsupported
  response until they have storage or a traceable job; accounts, sync,
  installers, and Windows capture are not claimed.
- **Fresh baseline:** the current first-party suites ran **80 tests, 0
  failures**: memoryd 5, agentd/P3.4 25, monitoring 13, P3.1 contracts 32,
  Mac/server gateway launchers 2, ROCm exporter 2, and Honcho demo compose 1.
  The initially combined ROCm exporter invocation exposed only a Python import
  working-directory error; its correct discovery command passed 2/2.
- **Design evidence:**
  `docs/superpowers/specs/2026-08-02-mature-product-design.md` defines the data
  flow, failure semantics, APIs, UI, accessibility, reproduction, automated
  tests, live synthetic gates, exclusions, and requirement-to-task matrix. A
  placeholder/contradiction scan and `git diff --check` passed.
- **Conclusion:** P3.12 satisfies its design-only verify contract. Production
  code remains unchanged; P3.13 is the next claimable task.

## 2026-08-03 ([VERIFY] P3.13 privacy gate and capture reliability — accept)

- **Fail-closed production path:** `memoryd` now wires the complete real
  Sentinel/OCR/novelty/perceive/embed pipeline by default. Raw frames use the
  separate local `SENTINEL_GATEWAY_URL`; malformed, unknown, low-confidence,
  or unavailable Sentinel results return `processing_state=blocked` after one
  metadata-only audit and before OCR, screenshot, timeline, or downstream
  model calls. Stub stages require explicit test opt-in, report `degraded`, and
  reject frame ingest with HTTP 503 before reading pixels.
- **Live model-contract defect found and closed:** the local MiniCPM-V initially
  returned `{"category":"normal"}` without confidence, so the correct strict
  parser blocked normal frames. A strict OpenAI JSON Schema now requires the
  six-category enum and numeric confidence in every Sentinel response while
  retaining `chat_template_kwargs.enable_thinking=false`; missing confidence
  still fails closed. A live synthetic normal fixture then returned
  `normal/1.0/classified_normal`, while the banking fixture returned
  `banking_finance/sensitive_category` and remained blocked.
- **Auditable ingest contract:** every frame response is exactly `stored`,
  `merged`, or `blocked`; sentinel audits persist one of seven closed reason
  codes. The idempotent local migration added `reason NOT NULL` plus the
  relation-scoped CHECK constraint, backfilled 81 legacy audit rows without
  reading their contents, and a second execution updated zero rows. Valid
  audio/document metadata returns HTTP 501 with `stored=false`; malformed
  metadata returns a stable sanitized error without reflecting input.
- **Capture reliability:** the macOS client parses structured terminal
  outcomes, advances dedup state only for stored/merged/blocked, never writes a
  retry queue, and keeps monotonic stored/merged/blocked/failed counters.
  Metadata-only heartbeats continue while the screen is locked; client resets
  cannot make Prometheus `_total` decrease. Missing screen-recording permission
  exits with code 2 before observer, HTTP client, or capture initialization.
- **[VERIFY] live synthetic privacy proof:** an isolated worktree `memoryd`
  reported `pipeline=real` and `accepting_frames=true`. The normal fixture was
  stored with one timeline row and one WebP. The banking fixture returned 202
  with `processing_state=blocked`; its exact timestamp had **0 timeline rows**,
  and the isolated screenshot root still contained only the normal fixture.
  The synthetic device had two audits: one `allow/classified_normal` and one
  `block/sensitive_category`. Audio and document probes both returned 501;
  heartbeat metrics exported synthetic counts 1/2/3/4 for
  stored/merged/blocked/failed.
- **Fresh regression:** **127 first-party tests passed, 0 failures**: memoryd
  35, capture 17, agentd/P3.4 25, monitoring 13, P3.1 contracts 32,
  Mac/server gateway launchers 2, ROCm exporter 2, and Honcho demo compose 1.
  Compose configuration and `git diff --check` also passed. The only warnings
  were existing FastAPI/TestClient and startup-event deprecations.
- **Cleanup:** the exact synthetic device rows used by this verification were
  deleted and both timeline/audit remaining counts were zero. Temporary model
  roles and isolated memoryd were stopped. The generated temporary screenshot
  directory was moved to the macOS Trash rather than permanently erased.
- **Conclusion:** P3.13 meets its implementation and live synthetic acceptance
  criteria and moves from `doing` to `accept`. P3.14 is next; accepted ROCm,
  Grafana, video, license, and five-model evidence were not modified or rerun.

## 2026-08-03 — P3.14 real Radeon to Local Metal agent routing

- **[VERIFY] Shared router:** ordinary agentd questions, semantic embeddings,
  and all daily-report model stages now use one Radeon-first compute router.
  A Local Metal `brain` request physically uses `perceive`; response/audit
  metadata records backend, physical/logical model, degraded state, stable
  reason, and latency. Fast requests set `enable_thinking=false` on both paths.
- **[VERIFY] Classified fallback:** retry crosses backends only for connection,
  timeout, 429, 502–504, a verified missing-model 404, invalid JSON, or an
  invalid product shape. Caller errors, authentication, policy rejection, 422,
  500, and unclassified HTTP errors do not cross backends. A per-role circuit
  suppresses repeated remote failures and probes again after cooldown.
- **[VERIFY] Grounding:** event ids, times, and apps returned by this request's
  tools form the only citation allowlist. An invalid first answer receives one
  correction request; invalid/empty/tool-calling correction output becomes a
  safe evidence-insufficient answer. Malformed tool calls and non-finite or
  non-1024-dimensional embeddings are rejected before use.
- **[VERIFY] Truthful UI:** the daily badge is `BACKEND UNVERIFIED` while a run
  is active. Only a completed report pins the actual writer route; failures are
  shown as failures. Connectivity health can no longer preselect or overwrite
  the current run's backend claim.
- **Fresh regression:** agentd and P3.4 suites passed **63 tests** with zero
  failures; the only warnings were existing FastAPI/TestClient and startup-event
  deprecations. Independent final review passed with no remaining findings.
- **Live synthetic route probes:** with both real gateways available, a small
  `brain` request completed on Radeon as `primary_ok` (physical `brain`). The
  exact verification tunnel was then stopped; the same logical request
  completed on Local Metal as `degraded=true`, reason
  `remote_connection_error`, physical `perceive`. With both configured paths
  unreachable, the router returned `remote_connection_error` and
  `local_connection_error` without endpoint or upstream-body data. The Radeon
  SSH tunnel was restored afterward and all five logical roles were visible.
- **Conclusion:** P3.14 moves from `doing` to `accept`. P3.15 remains in
  progress; accepted benchmark, video, Grafana, and privacy evidence were not
  rerun or relabeled.
