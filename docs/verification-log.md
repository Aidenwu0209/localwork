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
