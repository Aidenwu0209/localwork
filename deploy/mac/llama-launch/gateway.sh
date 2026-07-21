#!/bin/bash
# DejaView dev LiteLLM gateway (port 4000). Routes logical names
# (brain/perceive/sentinel/fast/embed) to the llama-server instances started by
# the sibling *.sh scripts. Config: ../../server/litellm.yaml (shared with the
# AMD server; only the api_base host differs there).
#
# We use `uvx --from 'litellm[proxy]'` so the proxy's extra deps (backoff, etc.)
# come from an isolated uv env, independent of the host's mise/anaconda pythons.
#
# Kill: pkill -f "litellm --config".
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$ROOT/server/litellm.yaml"

exec uvx --from 'litellm[proxy]' litellm \
  --config "$CONFIG" \
  --host 127.0.0.1 --port 4000 \
  --detailed_debug
