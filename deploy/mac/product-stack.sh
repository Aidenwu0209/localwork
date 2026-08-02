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
DATA_COMPOSE=(docker compose -f "$ROOT/deploy/mac/compose.data.yml")
HONCHO_COMPOSE=(docker compose -f "$ROOT/deploy/mac/compose.honcho.yml")
STARTED=()
STARTED_DATA=0
STARTED_HONCHO=0

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

pidfile() { printf '%s/%s.pid\n' "$RUNTIME_DIR" "$1"; }
logfile() { printf '%s/%s.log\n' "$RUNTIME_DIR" "$1"; }
project_path() { printf '%s/services/%s\n' "$ROOT" "$1"; }

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
  local pf="$1" expected="$2" pid fingerprint service token current command
  [[ -f "$pf" ]] || return 1
  IFS='|' read -r pid fingerprint service token < "$pf" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ "$service" == "$expected" && -n "$fingerprint" && -n "$token" ]] || return 1
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
  local pf="$1" service="$2" pid="$3" fingerprint token i=0
  fingerprint="$(process_fingerprint "$pid")"
  while [[ -z "$fingerprint" && "$i" -lt 20 ]]; do
    sleep 0.01
    fingerprint="$(process_fingerprint "$pid")"
    i=$((i + 1))
  done
  [[ -n "$fingerprint" ]] || return 1
  token="$service-$$-$(date +%s)-$RANDOM"
  printf '%s|%s|%s|%s\n' "$pid" "$fingerprint" "$service" "$token" > "$pf"
  chmod 600 "$pf"
}

service_state() {
  local service="$1" pf
  pf="$(pidfile "$service")"
  read_owned_record "$pf" "$service"
}

wait_url() {
  local url="$1" timeout="$2" deadline
  deadline=$((SECONDS + timeout))
  while (( SECONDS <= deadline )); do
    if curl -fsS --max-time 1 "$url" >/dev/null 2>&1; then return 0; fi
    sleep "$POLL_SECONDS"
  done
  return 1
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
  if ! wait_url "http://127.0.0.1:${port}/health" "$SERVICE_TIMEOUT" || \
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

gateway_models_url() {
  local base="${1%/}"
  base="${base%/v1}"
  printf '%s/v1/models\n' "$base"
}

preflight() {
  "$ROOT/deploy/mac/setup-honcho.sh" --check >/dev/null
  if [[ "$SKIP_GATEWAY" != "1" ]]; then
    export GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:14000/v1}"
    export RADEON_GATEWAY_URL="${RADEON_GATEWAY_URL:-$GATEWAY_URL}"
    export LOCAL_GATEWAY_URL="${LOCAL_GATEWAY_URL:-http://127.0.0.1:4000/v1}"
    export SENTINEL_GATEWAY_URL="${SENTINEL_GATEWAY_URL:-$LOCAL_GATEWAY_URL}"
    if ! curl -fsS --max-time 2 "$(gateway_models_url "$SENTINEL_GATEWAY_URL")" >/dev/null; then
      echo "error: local Sentinel gateway is unavailable; start the local sentinel stack first" >&2
      return 1
    fi
    if ! curl -fsS --max-time 2 "$(gateway_models_url "$GATEWAY_URL")" >/dev/null; then
      echo "error: allowed-stage gateway is unavailable; establish the Radeon tunnel or select Local Metal" >&2
      return 1
    fi
  fi
}

cmd_up() {
  preflight
  if [[ "$SKIP_INFRA" != "1" ]]; then
    start_infra
  fi
  if ! start_service ocrd 8006 || ! start_service memoryd 8090 || ! start_service agentd 8101; then
    rollback_started
    rollback_infra || true
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
    "${HONCHO_COMPOSE[@]}" down
    "${DATA_COMPOSE[@]}" down
  fi
  (( failures == 0 )) || return 1
}

cmd_status() {
  local service port
  for pair in "ocrd:8006" "memoryd:8090" "agentd:8101"; do
    service="${pair%%:*}"; port="${pair#*:}"
    if service_state "$service"; then
      if curl -fsS --max-time 1 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
        echo "$service: managed and ready"
      else
        echo "$service: managed but not ready"
      fi
    else
      echo "$service: down or unowned"
    fi
  done
  if [[ "$SKIP_INFRA" != "1" ]]; then
    "${DATA_COMPOSE[@]}" ps
    "${HONCHO_COMPOSE[@]}" ps
  fi
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  *) echo "usage: $0 {up|down|status}" >&2; exit 2 ;;
esac
