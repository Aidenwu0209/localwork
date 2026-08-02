#!/bin/bash
# Safe local product lifecycle for topology A. Capture remains foreground-only.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="${DEJAVIEW_RUNTIME_DIR:-${TMPDIR:-/tmp}/dejaview-product-${UID}}"
SERVICE_TIMEOUT="${DEJAVIEW_SERVICE_TIMEOUT:-90}"
STOP_TIMEOUT="${DEJAVIEW_STOP_TIMEOUT:-10}"
KILL_TIMEOUT="${DEJAVIEW_KILL_TIMEOUT:-2}"
POLL_SECONDS="${DEJAVIEW_POLL_SECONDS:-0.5}"
SKIP_INFRA="${DEJAVIEW_SKIP_INFRA:-0}"
SKIP_GATEWAY="${DEJAVIEW_SKIP_GATEWAY_CHECK:-0}"
SKIP_PRIVACY_STACK="${DEJAVIEW_SKIP_PRIVACY_STACK:-0}"
DEV_STACK_SCRIPT="${DEJAVIEW_DEV_STACK_SCRIPT:-$ROOT/deploy/mac/llama-launch/dev-stack.sh}"
DATA_COMPOSE=(docker compose -f "$ROOT/deploy/mac/compose.data.yml")
HONCHO_COMPOSE=(docker compose -f "$ROOT/deploy/mac/compose.honcho.yml")
STARTED=()
STARTED_DATA=0
STARTED_HONCHO=0
PRIVACY_STARTED=0

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

pidfile() { printf '%s/%s.pid\n' "$RUNTIME_DIR" "$1"; }
logfile() { printf '%s/%s.log\n' "$RUNTIME_DIR" "$1"; }
project_path() { printf '%s/services/%s\n' "$ROOT" "$1"; }
privacy_runtime_dir() { printf '%s/privacy\n' "$RUNTIME_DIR"; }
privacy_marker() { printf '%s/privacy.product-owned\n' "$RUNTIME_DIR"; }

service_tree_revision() {
  git -C "$ROOT" rev-parse --verify "HEAD:services/$1" 2>/dev/null
}

process_command() { ps -p "$1" -o command= 2>/dev/null || true; }

process_fingerprint() {
  local pid="$1" start
  if [[ -r "/proc/$pid/stat" ]]; then
    start="$(sed 's/^[^)]*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}')"
    [[ -n "$start" ]] && { printf 'proc:%s\n' "$start"; return 0; }
  fi
  start="$(ps -p "$pid" -o lstart= 2>/dev/null | awk '{$1=$1; print}')"
  [[ -n "$start" ]] && printf 'ps:%s\n' "$start"
}

process_is_zombie() {
  local state
  state="$(ps -p "$1" -o stat= 2>/dev/null | awk '{$1=$1; print}')"
  case "$state" in Z*) return 0 ;; *) return 1 ;; esac
}

raw_pid() {
  local value
  [[ -f "$1" ]] || return 1
  IFS='|' read -r value _ < "$1" || return 1
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$value"
}

read_owned_record() {
  local pf="$1" expected="$2" pid fingerprint service token revision current command current_revision
  [[ -f "$pf" ]] || return 1
  IFS='|' read -r pid fingerprint service token revision < "$pf" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ "$service" == "$expected" && -n "$fingerprint" && -n "$token" && -n "$revision" ]] || return 1
  current_revision="$(service_tree_revision "$service")"
  [[ -n "$current_revision" && "$current_revision" == "$revision" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  process_is_zombie "$pid" && return 1
  current="$(process_fingerprint "$pid")"
  [[ -n "$current" && "$current" == "$fingerprint" ]] || return 1
  command="$(process_command "$pid")"
  [[ "$command" == *"$(project_path "$service")"* && "$command" == *"python -m $service"* ]] || return 1
  OWNED_PID="$pid"
  OWNED_TOKEN="$token"
}

write_pid_record() {
  local pf="$1" service="$2" pid="$3" fingerprint token revision i=0
  fingerprint="$(process_fingerprint "$pid")"
  while [[ -z "$fingerprint" && "$i" -lt 20 ]]; do
    sleep 0.01
    fingerprint="$(process_fingerprint "$pid")"
    i=$((i + 1))
  done
  [[ -n "$fingerprint" ]] || return 1
  revision="$(service_tree_revision "$service")"
  [[ -n "$revision" ]] || return 1
  token="$service-$$-$(date +%s)-$RANDOM"
  printf '%s|%s|%s|%s|%s\n' "$pid" "$fingerprint" "$service" "$token" "$revision" > "$pf"
  chmod 600 "$pf"
}

service_state() {
  local service="$1" pf
  pf="$(pidfile "$service")"
  read_owned_record "$pf" "$service"
}

health_contract() {
  local service="$1" url="$2" payload
  payload="$(curl -fsS --max-time 1 "$url" 2>/dev/null)" || return 1
  printf '%s' "$payload" | python3 -c '
import json
import sys

service = sys.argv[1]
try:
    body = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(1)
if not isinstance(body, dict) or body.get("status") != "ok":
    raise SystemExit(1)
if service == "ocrd":
    valid = isinstance(body.get("backend"), str) and bool(body["backend"])
else:
    valid = body.get("service") == service
raise SystemExit(0 if valid else 1)
' "$service"
}

wait_service_health() {
  local service="$1" url="$2" timeout="$3" deadline
  deadline=$((SECONDS + timeout))
  while (( SECONDS <= deadline )); do
    if health_contract "$service" "$url"; then return 0; fi
    sleep "$POLL_SECONDS"
  done
  return 1
}

gateway_models_url() {
  local base="${1%/}"
  base="${base%/v1}"
  printf '%s/v1/models\n' "$base"
}

gateway_has_owned_sentinel() {
  local payload
  payload="$(curl -fsS --max-time 2 "$(gateway_models_url "$SENTINEL_GATEWAY_URL")" 2>/dev/null)" || return 1
  printf '%s' "$payload" | python3 -c '
import json
import sys
try:
    body = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(1)
models = body.get("data") if isinstance(body, dict) else None
valid = isinstance(models, list) and any(
    isinstance(model, dict)
    and model.get("id") == "sentinel"
    and isinstance(model.get("owned_by"), str)
    and bool(model["owned_by"])
    for model in models
)
raise SystemExit(0 if valid else 1)
'
}

port_is_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

preflight_service_ports() {
  local service port
  if ! command -v lsof >/dev/null 2>&1; then
    echo "error: lsof is required to verify fixed service ports safely" >&2
    return 1
  fi
  for pair in "ocrd:8006" "memoryd:8090" "agentd:8101"; do
    service="${pair%%:*}"; port="${pair#*:}"
    if ! service_state "$service" && port_is_listening "$port"; then
      echo "error: port $port is occupied by an unowned process; refusing to adopt or signal it" >&2
      return 1
    fi
  done
}

start_privacy_stack() {
  [[ "$SKIP_PRIVACY_STACK" == "1" ]] && return 0
  if ! command -v lsof >/dev/null 2>&1; then
    echo "error: lsof is required to verify the privacy gateway port safely" >&2
    return 1
  fi
  export SENTINEL_GATEWAY_URL="${SENTINEL_GATEWAY_URL:-${LOCAL_GATEWAY_URL:-http://127.0.0.1:4000/v1}}"
  if gateway_has_owned_sentinel; then
    return 0
  fi
  if port_is_listening 4000; then
    echo "error: privacy gateway port 4000 is occupied by an unowned process" >&2
    return 1
  fi
  [[ -x "$DEV_STACK_SCRIPT" ]] || {
    echo "error: privacy stack launcher is not executable: $DEV_STACK_SCRIPT" >&2
    return 1
  }
  mkdir -p "$(privacy_runtime_dir)"
  chmod 700 "$(privacy_runtime_dir)"
  if ! DEJAVIEW_RUNTIME_DIR="$(privacy_runtime_dir)" "$DEV_STACK_SCRIPT" up sentinel; then
    return 1
  fi
  PRIVACY_STARTED=1
  if ! gateway_has_owned_sentinel; then
    echo "error: owned privacy stack did not expose an owned sentinel role" >&2
    stop_privacy_stack_current || true
    return 1
  fi
  printf '%s\n' 'product-owned privacy runtime' > "$(privacy_marker)"
  chmod 600 "$(privacy_marker)"
}

stop_privacy_stack_current() {
  [[ "$SKIP_PRIVACY_STACK" == "1" ]] && return 0
  [[ -f "$(privacy_marker)" || "$PRIVACY_STARTED" -eq 1 ]] || return 0
  DEJAVIEW_RUNTIME_DIR="$(privacy_runtime_dir)" "$DEV_STACK_SCRIPT" down
  rm -f "$(privacy_marker)"
  PRIVACY_STARTED=0
}

stop_service() {
  local service="$1" pf pid deadline kill_deadline
  pf="$(pidfile "$service")"
  [[ -f "$pf" ]] || return 0
  if ! read_owned_record "$pf" "$service"; then
    pid="$(raw_pid "$pf" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && ! process_is_zombie "$pid"; then
      echo "error: refusing to stop unowned PID $pid from $pf" >&2
      return 1
    fi
    rm -f "$pf"
    return 0
  fi
  pid="$OWNED_PID"
  deadline=$((SECONDS + STOP_TIMEOUT))
  kill -TERM "$pid" 2>/dev/null || true
  while read_owned_record "$pf" "$service" && (( SECONDS < deadline )); do
    sleep "$POLL_SECONDS"
  done
  if read_owned_record "$pf" "$service"; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  kill_deadline=$((SECONDS + KILL_TIMEOUT))
  while read_owned_record "$pf" "$service" && (( SECONDS < kill_deadline )); do
    sleep "$POLL_SECONDS"
  done
  if read_owned_record "$pf" "$service"; then
    echo "error: $service did not stop within ${STOP_TIMEOUT}s" >&2
    return 1
  fi
  rm -f "$pf"
  echo "stopped $service"
}

start_service() {
  local service="$1" port="$2" pf pid project
  pf="$(pidfile "$service")"
  if service_state "$service"; then
    echo "$service already running (pid $OWNED_PID)"
    return 0
  fi
  if [[ -f "$pf" ]]; then
    pid="$(raw_pid "$pf" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && ! process_is_zombie "$pid"; then
      echo "error: $pf points to an unowned live process; refusing to overwrite it" >&2
      return 1
    fi
    rm -f "$pf"
  fi

  project="$(project_path "$service")"
  nohup uv run --project "$project" python -m "$service" >"$(logfile "$service")" 2>&1 &
  pid=$!
  if ! write_pid_record "$pf" "$service" "$pid"; then
    kill -TERM "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    echo "error: could not fingerprint $service process $pid" >&2
    return 1
  fi
  STARTED+=("$service")
  if ! wait_service_health "$service" "http://127.0.0.1:${port}/health" "$SERVICE_TIMEOUT" || \
    ! service_state "$service"; then
    echo "error: $service failed readiness; see $(logfile "$service")" >&2
    stop_service "$service" || true
    return 1
  fi
  echo "$service ready on 127.0.0.1:$port"
}

rollback_started() {
  local index
  for ((index=${#STARTED[@]}-1; index>=0; index--)); do
    stop_service "${STARTED[$index]}" || true
  done
}

compose_state() {
  local output
  if ! output="$("${COMPOSE_COMMAND[@]}" ps -q 2>/dev/null)"; then
    echo "error: could not inspect $COMPOSE_LABEL compose state" >&2
    return 1
  fi
  if [[ -n "$output" ]]; then COMPOSE_ACTIVE=1; else COMPOSE_ACTIVE=0; fi
}

rollback_infra() {
  local result=0
  if [[ "$STARTED_HONCHO" -eq 1 ]]; then
    "${HONCHO_COMPOSE[@]}" down || result=1
    STARTED_HONCHO=0
  fi
  if [[ "$STARTED_DATA" -eq 1 ]]; then
    "${DATA_COMPOSE[@]}" down || result=1
    STARTED_DATA=0
  fi
  return "$result"
}

start_infra() {
  COMPOSE_COMMAND=("${DATA_COMPOSE[@]}")
  COMPOSE_LABEL=data
  compose_state || return 1
  [[ "$COMPOSE_ACTIVE" -eq 1 ]] || STARTED_DATA=1
  if ! "${DATA_COMPOSE[@]}" up -d --wait; then
    rollback_infra || true
    return 1
  fi

  COMPOSE_COMMAND=("${HONCHO_COMPOSE[@]}")
  COMPOSE_LABEL=Honcho
  if ! compose_state; then
    rollback_infra || true
    return 1
  fi
  [[ "$COMPOSE_ACTIVE" -eq 1 ]] || STARTED_HONCHO=1
  if ! "${HONCHO_COMPOSE[@]}" up -d --wait; then
    rollback_infra || true
    return 1
  fi
}

preflight() {
  "$ROOT/deploy/mac/setup-honcho.sh" --check >/dev/null
  preflight_service_ports
  if [[ "$SKIP_GATEWAY" != "1" ]]; then
    export GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:14000/v1}"
    export RADEON_GATEWAY_URL="${RADEON_GATEWAY_URL:-$GATEWAY_URL}"
    export LOCAL_GATEWAY_URL="${LOCAL_GATEWAY_URL:-http://127.0.0.1:4000/v1}"
    export SENTINEL_GATEWAY_URL="${SENTINEL_GATEWAY_URL:-$LOCAL_GATEWAY_URL}"
    if ! gateway_has_owned_sentinel; then
      echo "error: local Sentinel gateway lacks an owned sentinel role" >&2
      return 1
    fi
    if ! curl -fsS --max-time 2 "$(gateway_models_url "$GATEWAY_URL")" >/dev/null; then
      echo "error: allowed-stage gateway is unavailable; establish the Radeon tunnel or select Local Metal" >&2
      return 1
    fi
  fi
}

cmd_up() {
  preflight_service_ports
  start_privacy_stack
  if ! preflight; then
    stop_privacy_stack_current || true
    return 1
  fi
  if [[ "$SKIP_INFRA" != "1" ]]; then
    if ! start_infra; then
      stop_privacy_stack_current || true
      return 1
    fi
  fi
  if ! start_service ocrd 8006 || ! start_service memoryd 8090 || ! start_service agentd 8101; then
    rollback_started
    rollback_infra || true
    stop_privacy_stack_current || true
    return 1
  fi
  echo "DejaView product ready: http://127.0.0.1:8101/"
  echo "Capture stays foreground and permission-visible: make capture"
}

cmd_down() {
  local failures=0
  stop_service agentd || failures=$((failures + 1))
  stop_service memoryd || failures=$((failures + 1))
  stop_service ocrd || failures=$((failures + 1))
  if [[ "$SKIP_INFRA" != "1" ]]; then
    "${HONCHO_COMPOSE[@]}" down || failures=$((failures + 1))
    "${DATA_COMPOSE[@]}" down || failures=$((failures + 1))
  fi
  stop_privacy_stack_current || failures=$((failures + 1))
  (( failures == 0 )) || return 1
}

cmd_status() {
  local service port failures=0
  for pair in "ocrd:8006" "memoryd:8090" "agentd:8101"; do
    service="${pair%%:*}"; port="${pair#*:}"
    if service_state "$service"; then
      if health_contract "$service" "http://127.0.0.1:${port}/health"; then
        echo "$service: managed and ready"
      else
        echo "$service: managed but not ready"
        failures=$((failures + 1))
      fi
    else
      echo "$service: down or unowned"
      failures=$((failures + 1))
    fi
  done
  if [[ "$SKIP_PRIVACY_STACK" != "1" ]]; then
    export SENTINEL_GATEWAY_URL="${SENTINEL_GATEWAY_URL:-${LOCAL_GATEWAY_URL:-http://127.0.0.1:4000/v1}}"
    if gateway_has_owned_sentinel; then
      echo "privacy gateway: owned sentinel role ready"
    else
      echo "privacy gateway: missing or invalid sentinel role"
      failures=$((failures + 1))
    fi
  fi
  if [[ "$SKIP_INFRA" != "1" ]]; then
    "${DATA_COMPOSE[@]}" ps
    "${HONCHO_COMPOSE[@]}" ps
  fi
  if (( failures == 0 )); then
    echo "READY: product runtime contracts verified"
    return 0
  fi
  echo "NOT_READY: product runtime contracts failed"
  return 1
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  *) echo "usage: $0 {up|down|status}" >&2; exit 2 ;;
esac
