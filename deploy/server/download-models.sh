#!/bin/bash
# DejaView model bootstrap (server side, tasks D3-D7). Verified working 2026-07-20.
# Persistent copy: /workspace/dejaview-models/download-models.sh (survives container rebuild)
# Models land on overlay disk: /root/dejaview-models/ (rebuildable by re-running this script)
# Method: plain wget via hf-mirror resolve URLs (hf CLI hits Xet CAS 401 through the mirror
# for newer repos; wget -c is resumable and dependency-free).
set -ex
M=https://hf-mirror.com
DIR=/root/dejaview-models
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHA_FILE="${SHA_FILE:-$SCRIPT_DIR/sha256.txt}"
BRAIN_REV=0982db1be5e1e6cf7163ba89d7f63a9f18d2f4f0
PERCEIVE_REV=b8093469224f83f5c38f691eb906c380e9e63114
SENTINEL_REV=78e02f066e9819a60573b78a4275df8a0c27f698
FAST_REV=87007042419d30c1d8f38ef065424ee33870831e
EMBED_REV=370f27d7550e0def9b39c1f16d3fbaa13aa67728
mkdir -p "$DIR"/{brain,perceive,sentinel,fast,embed}
cd "$DIR"

dl() { wget -q -c -O "$1" "$2" && echo "OK $1"; }

# D3 brain: exact benchmark quantizations in the safe run order
# Q6_K -> Q4_K_M -> Q8_0, followed by the shared f16 mmproj.
dl brain/ThinkingCap-Qwen3.6-27B-Q6_K.gguf        "$M/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/resolve/$BRAIN_REV/ThinkingCap-Qwen3.6-27B-Q6_K.gguf"
dl brain/ThinkingCap-Qwen3.6-27B-Q4_K_M.gguf      "$M/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/resolve/$BRAIN_REV/ThinkingCap-Qwen3.6-27B-Q4_K_M.gguf"
dl brain/ThinkingCap-Qwen3.6-27B-Q8_0.gguf        "$M/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/resolve/$BRAIN_REV/ThinkingCap-Qwen3.6-27B-Q8_0.gguf"
dl brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf  "$M/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/resolve/$BRAIN_REV/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf"

# D4 perceive: Gemma 4 E4B Q8_0 + BF16 mmproj (audio-capable) + Q8 mmproj + MTP head (~9.1 GB)
dl perceive/gemma-4-E4B-it-Q8_0.gguf              "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/$PERCEIVE_REV/gemma-4-E4B-it-Q8_0.gguf"
dl perceive/mmproj-gemma-4-E4B-it-BF16.gguf       "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/$PERCEIVE_REV/mmproj-gemma-4-E4B-it-BF16.gguf"
dl perceive/mmproj-gemma-4-E4B-it-Q8_0.gguf       "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/$PERCEIVE_REV/mmproj-gemma-4-E4B-it-Q8_0.gguf"
dl perceive/mtp-gemma-4-E4B-it-Q8_0.gguf          "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/$PERCEIVE_REV/mtp-gemma-4-E4B-it-Q8_0.gguf"

# D5 sentinel: MiniCPM-V 4.6 Q4_K_M + f16 mmproj (~1.6 GB)
dl sentinel/MiniCPM-V-4_6-Q4_K_M.gguf             "$M/openbmb/MiniCPM-V-4.6-gguf/resolve/$SENTINEL_REV/MiniCPM-V-4_6-Q4_K_M.gguf"
dl sentinel/mmproj-model-f16.gguf                 "$M/openbmb/MiniCPM-V-4.6-gguf/resolve/$SENTINEL_REV/mmproj-model-f16.gguf"

# D6 fast: MiniCPM5-1B Q8_0 (~1.1 GB)
dl fast/MiniCPM5-1B-Q8_0.gguf                     "$M/openbmb/MiniCPM5-1B-GGUF/resolve/$FAST_REV/MiniCPM5-1B-Q8_0.gguf"

# D7 embed: Qwen3-Embedding-0.6B Q8_0 (~0.6 GB)
dl embed/Qwen3-Embedding-0.6B-Q8_0.gguf           "$M/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/$EMBED_REV/Qwen3-Embedding-0.6B-Q8_0.gguf"

echo "=== downloads finished, verifying pinned checksums ==="
[[ -f "$SHA_FILE" ]] || { echo "missing checksum manifest: $SHA_FILE" >&2; exit 2; }
sha256sum -c "$SHA_FILE"
df -h / | tail -1
echo "=== BOOTSTRAP COMPLETE AND VERIFIED ==="
