#!/bin/bash
# DejaView model bootstrap (server side, tasks D3-D7). Verified working 2026-07-20.
# Persistent copy: /workspace/dejaview-models/download-models.sh (survives container rebuild)
# Models land on overlay disk: /root/dejaview-models/ (rebuildable by re-running this script)
# Method: plain wget via hf-mirror resolve URLs (hf CLI hits Xet CAS 401 through the mirror
# for newer repos; wget -c is resumable and dependency-free).
set -ex
M=https://hf-mirror.com
DIR=/root/dejaview-models
mkdir -p "$DIR"/{brain,perceive,sentinel,fast,embed}
cd "$DIR"

dl() { wget -q -c -O "$1" "$2" && echo "OK $1"; }

# D3 brain: ThinkingCap-Qwen3.6-27B Q8_0 + f16 mmproj (~28.9 GB)
dl brain/ThinkingCap-Qwen3.6-27B-Q8_0.gguf        "$M/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/resolve/main/ThinkingCap-Qwen3.6-27B-Q8_0.gguf"
dl brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf  "$M/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/resolve/main/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf"

# D4 perceive: Gemma 4 E4B Q8_0 + BF16 mmproj (audio-capable) + Q8 mmproj + MTP head (~9.1 GB)
dl perceive/gemma-4-E4B-it-Q8_0.gguf              "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q8_0.gguf"
dl perceive/mmproj-gemma-4-E4B-it-BF16.gguf       "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/mmproj-gemma-4-E4B-it-BF16.gguf"
dl perceive/mmproj-gemma-4-E4B-it-Q8_0.gguf       "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/mmproj-gemma-4-E4B-it-Q8_0.gguf"
dl perceive/mtp-gemma-4-E4B-it-Q8_0.gguf          "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/mtp-gemma-4-E4B-it-Q8_0.gguf"

# D5 sentinel: MiniCPM-V 4.6 Q4_K_M + f16 mmproj (~1.6 GB)
dl sentinel/MiniCPM-V-4_6-Q4_K_M.gguf             "$M/openbmb/MiniCPM-V-4.6-gguf/resolve/main/MiniCPM-V-4_6-Q4_K_M.gguf"
dl sentinel/mmproj-model-f16.gguf                 "$M/openbmb/MiniCPM-V-4.6-gguf/resolve/main/mmproj-model-f16.gguf"

# D6 fast: MiniCPM5-1B Q8_0 (~1.1 GB)
dl fast/MiniCPM5-1B-Q8_0.gguf                     "$M/openbmb/MiniCPM5-1B-GGUF/resolve/main/MiniCPM5-1B-Q8_0.gguf"

# D7 embed: Qwen3-Embedding-0.6B Q8_0 (~0.6 GB)
dl embed/Qwen3-Embedding-0.6B-Q8_0.gguf           "$M/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf"

echo "=== downloads finished, hashing ==="
find "$DIR" -name "*.gguf" -exec sha256sum {} \; | tee "$DIR/sha256.txt"
cp "$DIR/sha256.txt" /workspace/dejaview-models/sha256.txt 2>/dev/null || true
df -h / | tail -1
echo "=== BOOTSTRAP COMPLETE ==="
