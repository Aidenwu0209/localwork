#!/bin/bash
# Minimal, checksummed model bootstrap for the five-role DejaView demo stack.
# The full P3.1 rebuild remains download-models.sh; this profile deliberately
# omits benchmark-only brain Q8/Q4 and optional perceive Q8/MTP projections.
set -euo pipefail

MIRROR="${HF_MIRROR:-https://hf-mirror.com}"
MODEL_ROOT="${DEV_MODELS_DIR:-/root/dejaview-models}"

BRAIN_REV=0982db1be5e1e6cf7163ba89d7f63a9f18d2f4f0
PERCEIVE_REV=b8093469224f83f5c38f691eb906c380e9e63114
SENTINEL_REV=78e02f066e9819a60573b78a4275df8a0c27f698
FAST_REV=87007042419d30c1d8f38ef065424ee33870831e
EMBED_REV=370f27d7550e0def9b39c1f16d3fbaa13aa67728

mkdir -p "$MODEL_ROOT"/{brain,perceive,sentinel,fast,embed}
cd "$MODEL_ROOT"

download() {
  local output="$1" url="$2"
  if command -v aria2c >/dev/null 2>&1; then
    aria2c --continue=true --max-connection-per-server=8 --split=8 \
      --min-split-size=16M --file-allocation=none \
      --dir="$(dirname "$output")" --out="$(basename "$output")" "$url"
  else
    wget -c --progress=dot:giga -O "$output" "$url"
  fi
}

verify() {
  local expected="$1" path="$2" actual
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "sha256 mismatch: $path" >&2
    exit 2
  }
  echo "OK $path"
}

download brain/ThinkingCap-Qwen3.6-27B-Q6_K.gguf \
  "$MIRROR/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/resolve/$BRAIN_REV/ThinkingCap-Qwen3.6-27B-Q6_K.gguf"
download brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf \
  "$MIRROR/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/resolve/$BRAIN_REV/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf"
download perceive/gemma-4-E4B-it-Q8_0.gguf \
  "$MIRROR/ggml-org/gemma-4-E4B-it-GGUF/resolve/$PERCEIVE_REV/gemma-4-E4B-it-Q8_0.gguf"
download perceive/mmproj-gemma-4-E4B-it-BF16.gguf \
  "$MIRROR/ggml-org/gemma-4-E4B-it-GGUF/resolve/$PERCEIVE_REV/mmproj-gemma-4-E4B-it-BF16.gguf"
download sentinel/MiniCPM-V-4_6-Q4_K_M.gguf \
  "$MIRROR/openbmb/MiniCPM-V-4.6-gguf/resolve/$SENTINEL_REV/MiniCPM-V-4_6-Q4_K_M.gguf"
download sentinel/mmproj-model-f16.gguf \
  "$MIRROR/openbmb/MiniCPM-V-4.6-gguf/resolve/$SENTINEL_REV/mmproj-model-f16.gguf"
download fast/MiniCPM5-1B-Q8_0.gguf \
  "$MIRROR/openbmb/MiniCPM5-1B-GGUF/resolve/$FAST_REV/MiniCPM5-1B-Q8_0.gguf"
download embed/Qwen3-Embedding-0.6B-Q8_0.gguf \
  "$MIRROR/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/$EMBED_REV/Qwen3-Embedding-0.6B-Q8_0.gguf"

verify 37d93cb02a08e42a2b8e917d79efc340709b90546cac1fa655121ccadf4aa791 \
  brain/ThinkingCap-Qwen3.6-27B-Q6_K.gguf
verify 81a714ac5e8e15687371fc95a180953a29b732962f6616f791063ff127559412 \
  brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf
verify 34be82b17b4942d389b9b527170c4b058027abdd32531fda063d3d97dd8ce80a \
  perceive/gemma-4-E4B-it-Q8_0.gguf
verify f77995e4b6a569ab8f0d1bfdb7e8da4a0fa5b9e6f309b9bf3bdb76164d75e29f \
  perceive/mmproj-gemma-4-E4B-it-BF16.gguf
verify 6b0c74962c44bc6bf4b655b9b02c13eda9d5a0491543ae976d1ac18e4b7892e2 \
  sentinel/MiniCPM-V-4_6-Q4_K_M.gguf
verify ca931d861d0801d9003e50697cd764721a334107c0e0415a51168ee1938462de \
  sentinel/mmproj-model-f16.gguf
verify 0dc7638539067268774c275a14a6ec9c7e01f7eeb2cff606c8590361fa527e4c \
  fast/MiniCPM5-1B-Q8_0.gguf
verify 06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439 \
  embed/Qwen3-Embedding-0.6B-Q8_0.gguf

echo "DEMO MODEL BOOTSTRAP COMPLETE"
