#!/bin/bash
# Mac-side dev model downloads (task M2.5 prerequisite).
# Dev stack on Apple M5 / 16GB: small trio at full quant + E4B at Q4 (serves both
# `perceive` and dev `brain`). Downloads via hf-mirror resolve URLs (wget -c resumable).
# Target dir is outside the repo; *.gguf is gitignored anyway.
set -ex
M=https://hf-mirror.com
DIR="${1:-$HOME/Projects/Aidenwu0209/models/dejaview}"
mkdir -p "$DIR"/{sentinel,fast,embed,perceive}
cd "$DIR"

dl() { [ -s "$1" ] || curl -L --retry 3 -C - -o "$1" "$2"; echo "OK $1"; }

dl sentinel/MiniCPM-V-4_6-Q4_K_M.gguf  "$M/openbmb/MiniCPM-V-4.6-gguf/resolve/main/MiniCPM-V-4_6-Q4_K_M.gguf"
dl sentinel/mmproj-model-f16.gguf      "$M/openbmb/MiniCPM-V-4.6-gguf/resolve/main/mmproj-model-f16.gguf"
dl fast/MiniCPM5-1B-Q8_0.gguf          "$M/openbmb/MiniCPM5-1B-GGUF/resolve/main/MiniCPM5-1B-Q8_0.gguf"
dl embed/Qwen3-Embedding-0.6B-Q8_0.gguf "$M/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf"
dl perceive/gemma-4-E4B-it-Q4_K_M.gguf "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf"
dl perceive/mmproj-gemma-4-E4B-it-BF16.gguf "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/mmproj-gemma-4-E4B-it-BF16.gguf"

find "$DIR" -name "*.gguf" -exec shasum -a 256 {} \; > "$DIR/sha256-mac.txt"
echo "=== MAC DEV MODELS COMPLETE ==="
