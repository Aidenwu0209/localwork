#!/bin/bash
# DejaView dev llama-server launcher — embed (Qwen3-Embedding-0.6B Q8_0, Metal).
# Handbook §6.1 / §2.3: embeddings are 1024-dim; instruction prefix added on the
# query side (agentd), ingest side embeds plain text. Pooling last per Qwen card.
# Port 8004 matches deploy/server/litellm.yaml.
#
# Apple M5 / 16GB: start only the instances the current task needs (this one is
# ~0.7GB). Kill with: pkill -f "alias embed".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-$HOME/Projects/Aidenwu0209/models/dejaview}"
MODEL="$MODELS_DIR/embed/Qwen3-Embedding-0.6B-Q8_0.gguf"

exec llama-server \
  -m "$MODEL" \
  --alias embed \
  -ngl 99 \
  -c 8192 \
  --embedding --pooling last \
  --host 127.0.0.1 --port 8004 \
  --log-disable \
  --jinja
