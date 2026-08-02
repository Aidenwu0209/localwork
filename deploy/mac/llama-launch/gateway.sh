#!/bin/bash
# DejaView dev LiteLLM gateway (port 4000). Routes logical names
# (brain/perceive/sentinel/fast/embed) to the llama-server instances started by
# the sibling *.sh scripts. Config: ../../server/litellm.yaml (shared with the
# AMD server; only the api_base host differs there).
#
# We use the same verified LiteLLM release as the Radeon host so the proxy's
# dependencies come from a reproducible isolated uv environment.
#
# Kill: pkill -f "litellm --config".
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$ROOT/server/litellm.yaml"

exec uvx --from 'litellm[proxy]==1.93.0' litellm \
  --config "$CONFIG" \
  --host 127.0.0.1 --port 4000
