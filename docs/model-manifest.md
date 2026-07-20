# Model Manifest (task D8)

Weights live on the compute server (not in git). Rebuild everything with one command:

```bash
bash /workspace/dejaview-models/download-models.sh   # persistent copy; also at deploy/server/download-models.sh
```

- Server model root: `/root/dejaview-models/` (overlay disk — wiped on container rebuild, hence the bootstrap script)
- Persistent volume: `/workspace/dejaview-models/` holds `download-models.sh` + `sha256.txt` (only 10 GB, scripts/manifests only)
- Download route: `hf-mirror.com` resolve URLs via plain `wget -c` (hf CLI hits Xet CAS 401 through the mirror for newer repos)
- Downloaded & verified: 2026-07-20 · 10 files · ≈41 GB

| Logical | Source repo | File | Size | sha256 |
|---|---|---|---|---|
| brain | `bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF` | `ThinkingCap-Qwen3.6-27B-Q8_0.gguf` | 28G | `efcb358ef86f07cf24bfd617a66bb0baa7220e9dd1c31b7d7beacd7b49e67d93` |
| brain (vision) | 〃 | `mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf` | 889M | `81a714ac5e8e15687371fc95a180953a29b732962f6616f791063ff127559412` |
| perceive | `ggml-org/gemma-4-E4B-it-GGUF` | `gemma-4-E4B-it-Q8_0.gguf` | 7.5G | `34be82b17b4942d389b9b527170c4b058027abdd32531fda063d3d97dd8ce80a` |
| perceive (vision+audio, required BF16) | 〃 | `mmproj-gemma-4-E4B-it-BF16.gguf` | 946M | `f77995e4b6a569ab8f0d1bfdb7e8da4a0fa5b9e6f309b9bf3bdb76164d75e29f` |
| perceive (mmproj alt) | 〃 | `mmproj-gemma-4-E4B-it-Q8_0.gguf` | 534M | `197f49a93027f9843772bd24a6a9e0be2a32a788de5a3def330e9c585d86edd1` |
| perceive (MTP head — speculative decoding candidate, bench in S window) | 〃 | `mtp-gemma-4-E4B-it-Q8_0.gguf` | 95M | `f38ae62962657c7a6303c49bbb147e9ae23634e911cfa532fac0818c2e18b665` |
| sentinel | `openbmb/MiniCPM-V-4.6-gguf` | `MiniCPM-V-4_6-Q4_K_M.gguf` | 505M | `6b0c74962c44bc6bf4b655b9b02c13eda9d5a0491543ae976d1ac18e4b7892e2` |
| sentinel (vision) | 〃 | `mmproj-model-f16.gguf` | 1.1G | `ca931d861d0801d9003e50697cd764721a334107c0e0415a51168ee1938462de` |
| fast | `openbmb/MiniCPM5-1B-GGUF` | `MiniCPM5-1B-Q8_0.gguf` | 1.1G | `0dc7638539067268774c275a14a6ec9c7e01f7eeb2cff606c8590361fa527e4c` |
| embed | `Qwen/Qwen3-Embedding-0.6B-GGUF` | `Qwen3-Embedding-0.6B-Q8_0.gguf` | 610M | `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439` |

Mac-side dev copies (task M2.5, downloaded separately, lower quants where noted):
sentinel Q4_K_M + mmproj-f16 · fast Q8_0 · embed Q8_0 · perceive `gemma-4-E4B-it-Q4_K_M.gguf` + mmproj-BF16.

Licenses: Apache-2.0 (ThinkingCap, MiniCPM-V, MiniCPM5, Qwen3-Embedding) · Gemma License (E4B — flag separately in `docs/licenses.md`, T3.4).
