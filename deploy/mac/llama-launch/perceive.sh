#!/bin/bash
# DejaView dev llama-server launcher — perceive (Gemma 4 E4B Q4_0, Metal).
# Handbook §6.1 / §2.3: mid-tier — screen understanding, Honcho deriver baseline.
# In dev, this same instance ALSO serves `brain` (litellm.yaml dual-maps brain ->
# perceive:8002) until S2 swaps in ThinkingCap-27B on the AMD server.
# mmproj MUST be BF16 (quantised projector crashes / degrades vision quality).
# Port 8002 matches deploy/server/litellm.yaml. ~5.5GB resident (Q4_0 + mmproj).
# Kill: pkill -f "alias perceive".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-$HOME/Projects/Aidenwu0209/models/dejaview}"
MODEL="$MODELS_DIR/perceive/gemma-4-E4B-it-Q4_0.gguf"
MMPROJ="$MODELS_DIR/perceive/mmproj-gemma-4-E4B-it-BF16.gguf"

exec llama-server \
  -m "$MODEL" --mmproj "$MMPROJ" \
  --alias perceive \
  -ngl 99 \
  -c 16384 -np 2 \
  --host 127.0.0.1 --port 8002 \
  --log-disable \
  --jinja
