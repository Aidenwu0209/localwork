#!/bin/bash
# DejaView AMD-server launcher — brain (ThinkingCap-Qwen3.6-27B).
# Deep tier: reasoning / planning / deep vision / writing. Resident ~28 GB Q8_0
# or ~21 GB Q6_K. Handbook §6.1.
#
# SHARED-GPU MODE: this server also runs another job (Dolphin ~10.6 GB VRAM).
# Q8_0 (28 GB) + Dolphin + the常驻 four (~12 GB) = ~50.6 GB > 48 GB → OOM.
# Default to Q6_K (~21 GB) so total stays ~43 GB (4.6 GB headroom). Quant
# selection is fail-closed: a missing file never falls back to a larger model.
# Override with BRAIN_QUANT=Q8_0 only when the GPU is free.
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-/root/dejaview-models}"
BIN="${LLAMA_BIN:-/root/llama.cpp/build/bin/llama-server}"
QUANT="${BRAIN_QUANT:-Q6_K}"

case "$QUANT" in
  Q8_0)
    MODEL="$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q8_0.gguf"
    MODEL_SIZE=29047082976
    MODEL_SHA=efcb358ef86f07cf24bfd617a66bb0baa7220e9dd1c31b7d7beacd7b49e67d93
    ;;
  Q6_K)
    MODEL="$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q6_K.gguf"
    MODEL_SIZE=22430998496
    MODEL_SHA=37d93cb02a08e42a2b8e917d79efc340709b90546cac1fa655121ccadf4aa791
    ;;
  Q4_K_M)
    MODEL="$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q4_K_M.gguf"
    MODEL_SIZE=16810713056
    MODEL_SHA=b0651e28555bde7d2459ce99f091319b1a547143463e8d49f2aa7f572675fe67
    ;;
  *) echo "brain: unsupported BRAIN_QUANT=$QUANT" >&2; exit 2 ;;
esac
CTX=32768
MMPROJ="$MODELS_DIR/brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf"

check_weight() {
  local path="$1" size="$2" sha="$3" actual
  [[ -f "$path" ]] || { echo "brain: missing weight: $path" >&2; exit 2; }
  [[ "$(stat -c %s "$path")" == "$size" ]] ||
    { echo "brain: size mismatch: $path" >&2; exit 2; }
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$sha" ]] ||
    { echo "brain: sha256 mismatch: $path" >&2; exit 2; }
}

check_weight "$MODEL" "$MODEL_SIZE" "$MODEL_SHA"
check_weight \
  "$MMPROJ" 931145888 \
  81a714ac5e8e15687371fc95a180953a29b732962f6616f791063ff127559412

echo "brain: serving $QUANT ($MODEL)" >&2
exec "$BIN" \
  -m "$MODEL" --mmproj "$MMPROJ" \
  --alias brain \
  -ngl 99 \
  -c "$CTX" -np 2 \
  --host 127.0.0.1 --port 8001 \
  --metrics \
  --log-disable \
  --jinja
