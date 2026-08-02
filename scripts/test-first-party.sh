#!/bin/bash
# Single offline entry point shared by developers and CI. No cloud/GPU/PII required.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m unittest discover -s tests/release -p 'test_*.py' -v

uv run --project services/agentd --with pytest pytest -q \
  services/agentd/tests services/agentd/scripts/test_demo_p34.py
uv run --project services/memoryd --with pytest pytest -q services/memoryd/tests
uv run --project services/memoryd python services/memoryd/scripts/test_parse_offline.py
uv run --project services/ocrd --locked python -c \
  'from ocrd import create_app, main; app = create_app(); assert callable(main); assert {route.path for route in app.routes} >= {"/health", "/ocr"}'

if [[ "$(uname -s)" == "Darwin" ]]; then
  uv run --project clients/capture --with pytest pytest -q clients/capture/tests
else
  echo "SKIP capture runtime suite: macOS frameworks unavailable"
fi

node --test services/agentd/tests/test_product_focus.mjs
uv run --with pytest pytest -q \
  deploy/mac/llama-launch/test_gateway_launcher.py \
  deploy/mac/monitoring/test_health_exporter.py \
  deploy/mac/monitoring/test_monitoring_contract.py \
  deploy/mac/test_honcho_demo_compose.py \
  deploy/mac/tests
uv run --with pytest --with pyyaml pytest -q \
  deploy/server/bench/test_p31_bench.py \
  deploy/server/llama-launch/test_gateway_launcher.py \
  deploy/server/monitoring/test_rocm_smi_exporter.py \
  deploy/server/tests

for script in \
  deploy/mac/setup-honcho.sh \
  deploy/mac/product-stack.sh \
  deploy/mac/llama-launch/dev-stack.sh \
  deploy/server/llama-launch/server-stack.sh \
  deploy/server/llama-launch/gateway.sh \
  scripts/doctor.sh \
  scripts/test-first-party.sh; do
  /bin/bash -n "$script"
done

git diff --check
echo "PASS: offline first-party release suite"
