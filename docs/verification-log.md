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

- PaddleOCR 3.7 Python API params & PP-OCRv6 model fetch (M5.1) — incl. ARM backend choice (onnxruntime vs paddle native).
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
