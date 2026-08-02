#!/bin/bash
# Read-only local release and prerequisite checks. Never prints environment values.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failures=0

ok() { printf 'OK    %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

check_command() {
  local command="$1" label="${2:-$1}"
  if command -v "$command" >/dev/null 2>&1; then ok "$label available"; else fail "$label missing"; fi
}

echo "DejaView doctor (read-only)"
check_command git
check_command uv
check_command curl
check_command make
check_command python3
check_command node
check_command docker "Docker CLI"
check_command ssh "SSH client"

if command -v uv >/dev/null 2>&1 && uv python find '>=3.12' >/dev/null 2>&1; then
  ok "Python 3.12+ runtime available to uv"
else
  fail "Python 3.12+ runtime unavailable to uv"
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "Docker engine reachable"
else
  fail "Docker engine is not reachable"
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  ok "Docker Compose plugin available"
else
  fail "Docker Compose plugin unavailable"
fi

if "$ROOT/deploy/mac/setup-honcho.sh" --check >/dev/null 2>&1; then
  ok "Honcho pin and patch stack exact"
else
  fail "Honcho is uninitialized or differs from the exact patch stack; run make setup"
fi

for template in "$ROOT/.env.example" "$ROOT/deploy/mac/honcho.env.example"; do
  if [[ -f "$template" ]]; then ok "template present: ${template#"$ROOT/"}"; else fail "missing template: ${template#"$ROOT/"}"; fi
done

for local_file in "$ROOT/.env" "$ROOT/deploy/mac/honcho.env"; do
  if [[ -f "$local_file" ]]; then
    ok "local configuration file present: ${local_file#"$ROOT/"} (contents not inspected)"
  else
    warn "local configuration file absent: ${local_file#"$ROOT/"}"
  fi
done

if [[ "$(uname -s)" == "Darwin" ]]; then
  ok "macOS capture platform"
else
  warn "capture client is macOS-only; core tests remain portable"
fi

for endpoint in \
  "local Sentinel gateway|http://127.0.0.1:4000/v1/models" \
  "Radeon SSH tunnel|http://127.0.0.1:14000/v1/models" \
  "memoryd|http://127.0.0.1:8090/health" \
  "agentd product|http://127.0.0.1:8101/health"; do
  label="${endpoint%%|*}"; url="${endpoint#*|}"
  if curl -fsS --max-time 1 "$url" >/dev/null 2>&1; then ok "$label reachable"; else warn "$label not currently reachable"; fi
done

if (( failures > 0 )); then
  printf '\nDoctor found %d blocking prerequisite issue(s).\n' "$failures" >&2
  exit 1
fi
printf '\nDoctor passed. WARN items are runtime state, not release corruption.\n'
