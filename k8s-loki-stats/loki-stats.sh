#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="admin"
HOURS=6
LOCAL_API_PORT=3100
LOCAL_METRICS_PORT=3103
REMOTE_HTTP_PORT=3100
API_REMOTE_PORT=""
METRICS_REMOTE_PORT=""
OUT_DIR=""
COLOR=1
DEBUG=0

usage() {
  cat <<'USAGE'
Usage: loki-stats.sh [options]

Options:
  -n, --namespace <ns>     Kubernetes namespace (default: admin)
  -H, --hours <n>          Lookback window in hours (default: 6)
  --api-port <port>        Local port for Loki API (default: 3100)
  --metrics-port <port>    Local port for Loki metrics (default: 3103)
  --out-dir <dir>          Write raw outputs to a directory
  --no-color               Disable ANSI colors
  --debug                  Enable debug logs and command trace
  -h, --help               Show help

Examples:
  ./loki-stats.sh
  ./loki-stats.sh -n admin -H 12 --out-dir ./reports/run-1
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace) NAMESPACE="$2"; shift 2;;
    -H|--hours) HOURS="$2"; shift 2;;
    --api-port) LOCAL_API_PORT="$2"; shift 2;;
    --metrics-port) LOCAL_METRICS_PORT="$2"; shift 2;;
    --out-dir) OUT_DIR="$2"; shift 2;;
    --no-color) COLOR=0; shift;;
    --debug) DEBUG=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown option: $1"; usage; exit 1;;
  esac
done

if [[ -n "${NO_COLOR:-}" ]]; then
  COLOR=0
fi
if [[ -n "${DEBUG:-}" && "$DEBUG" -eq 1 ]]; then
  export PS4='+ [${BASH_SOURCE##*/}:${LINENO}] '
  set -x
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

if [[ "$COLOR" -eq 1 ]]; then
  C_RESET="\033[0m"
  C_DIM="\033[2m"
  C_BOLD="\033[1m"
  C_GREEN="\033[32m"
  C_YELLOW="\033[33m"
  C_CYAN="\033[36m"
  C_RED="\033[31m"
else
  C_RESET=""
  C_DIM=""
  C_BOLD=""
  C_GREEN=""
  C_YELLOW=""
  C_CYAN=""
  C_RED=""
fi

say() {
  printf "%b\n" "$*"
}

log() {
  if [[ "$DEBUG" -eq 1 ]]; then
    printf "%b\n" "${C_DIM}debug:${C_RESET} $*" >&2
  fi
}

require_ns() {
  if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "Namespace not found: $NAMESPACE" >&2
    exit 1
  fi
}

pick_api_target() {
  local svc
  for svc in loki-simple-scalable-gateway loki-read loki-backend; do
    if kubectl -n "$NAMESPACE" get svc "$svc" >/dev/null 2>&1; then
      API_REMOTE_PORT=$(kubectl -n "$NAMESPACE" get svc "$svc" -o jsonpath='{.spec.ports[0].port}')
      API_TARGET="svc/$svc"
      return 0
    fi
  done
  return 1
}

pick_write_target() {
  local pod
  pod=$(kubectl -n "$NAMESPACE" get pods -o json | jq -r '.items[] | select(.metadata.name|startswith("loki-write-")) | select(.status.phase=="Running") | .metadata.name' | head -n 1)
  if [[ -n "$pod" ]]; then
    METRICS_REMOTE_PORT="$REMOTE_HTTP_PORT"
    WRITE_TARGET="pod/$pod"
    return 0
  fi
  if kubectl -n "$NAMESPACE" get svc loki-write >/dev/null 2>&1; then
    METRICS_REMOTE_PORT=$(kubectl -n "$NAMESPACE" get svc loki-write -o jsonpath='{.spec.ports[0].port}')
    WRITE_TARGET="svc/loki-write"
    return 0
  fi
  return 1
}

wait_port() {
  local port="$1"
  local attempts=40
  local i
  for i in $(seq 1 "$attempts"); do
    if (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

PF_PIDS=()
cleanup() {
  local pid
  for pid in "${PF_PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
  if [[ -n "${TMP_DIR:-}" && -d "${TMP_DIR:-}" && -z "${OUT_DIR:-}" ]]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

require_ns

API_TARGET=""
pick_api_target || true
if [[ -z "$API_TARGET" ]]; then
  echo "Could not find Loki API service (tried gateway, read, backend)" >&2
  exit 1
fi
API_REMOTE_PORT="${API_REMOTE_PORT:-$REMOTE_HTTP_PORT}"

WRITE_TARGET=""
pick_write_target || true
if [[ -z "$WRITE_TARGET" ]]; then
  echo "Could not find Loki write target (pod or service)" >&2
  exit 1
fi
METRICS_REMOTE_PORT="${METRICS_REMOTE_PORT:-$REMOTE_HTTP_PORT}"

API_LOG=$(mktemp)
METRICS_LOG=$(mktemp)

kubectl -n "$NAMESPACE" port-forward "$API_TARGET" "${LOCAL_API_PORT}:${API_REMOTE_PORT}" >"$API_LOG" 2>&1 &
PF_PIDS+=("$!")

kubectl -n "$NAMESPACE" port-forward "$WRITE_TARGET" "${LOCAL_METRICS_PORT}:${METRICS_REMOTE_PORT}" >"$METRICS_LOG" 2>&1 &
PF_PIDS+=("$!")

if ! wait_port "$LOCAL_API_PORT"; then
  echo "API port-forward did not become ready" >&2
  cat "$API_LOG" >&2 || true
  exit 1
fi
if ! wait_port "$LOCAL_METRICS_PORT"; then
  echo "Metrics port-forward did not become ready" >&2
  cat "$METRICS_LOG" >&2 || true
  exit 1
fi

read -r START_NS END_NS < <(python3 - <<PY
import time
hours = float("$HOURS")
end_ns = int(time.time() * 1e9)
start_ns = int(end_ns - hours * 3600 * 1e9)
print(start_ns, end_ns)
PY
)

TS_UTC=$(python3 - <<PY
import datetime
print(datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)

CONTEXT=$(kubectl config current-context)

TMP_DIR=$(mktemp -d)

LABELS_JSON="$TMP_DIR/labels.json"
POD_VALUES_JSON="$TMP_DIR/pod_values.json"
STATS_JSON="$TMP_DIR/stats.json"
SERIES_JSON="$TMP_DIR/series.json"
METRICS_TXT="$TMP_DIR/metrics.txt"

fetch_json() {
  local url="$1"
  local outfile="$2"
  local fallback="$3"
  shift 3
  local args=("$@")
  local code
  log "GET $url ${args[*]}"
  code=$(curl -sS -o "$outfile" -w '%{http_code}' "${args[@]}" "$url" || true)
  if [[ "$code" != "200" ]]; then
    log "non-200 response ($code) from $url, using fallback"
    printf "%s" "$fallback" > "$outfile"
  fi
}

fetch_text() {
  local url="$1"
  local outfile="$2"
  local code
  log "GET $url"
  code=$(curl -sS -o "$outfile" -w '%{http_code}' "$url" || true)
  if [[ "$code" != "200" ]]; then
    log "non-200 response ($code) from $url, leaving empty output"
    : > "$outfile"
  fi
}

fetch_json "http://127.0.0.1:${LOCAL_API_PORT}/loki/api/v1/labels" \
  "$LABELS_JSON" '{"data":[]}'

fetch_json "http://127.0.0.1:${LOCAL_API_PORT}/loki/api/v1/label/pod/values" \
  "$POD_VALUES_JSON" '{"data":[]}' \
  --get --data-urlencode "start=$START_NS" --data-urlencode "end=$END_NS"

fetch_json "http://127.0.0.1:${LOCAL_API_PORT}/loki/api/v1/stats" \
  "$STATS_JSON" '{"data":null}' \
  --get --data-urlencode 'query={namespace=~".+"}' --data-urlencode "start=$START_NS" --data-urlencode "end=$END_NS"

fetch_json "http://127.0.0.1:${LOCAL_API_PORT}/loki/api/v1/series" \
  "$SERIES_JSON" '{"data":[]}' \
  --get --data-urlencode 'match[]={namespace=~".+"}' --data-urlencode "start=$START_NS" --data-urlencode "end=$END_NS"

fetch_text "http://127.0.0.1:${LOCAL_METRICS_PORT}/metrics" "$METRICS_TXT"
grep -E 'loki_.*(stream|chunk|flush|discard)' "$METRICS_TXT" > "${METRICS_TXT}.filtered"
mv "${METRICS_TXT}.filtered" "$METRICS_TXT"

LABEL_COUNT=$(jq -r '.data | length' "$LABELS_JSON")
POD_VALUE_COUNT=$(jq -r '.data | length' "$POD_VALUES_JSON")
SERIES_COUNT=$(jq -r '.data | length' "$SERIES_JSON")
UNIQ_INSTANCE_COUNT=$(jq -r '.data[].instance // empty' "$SERIES_JSON" | sort -u | wc -l | tr -d ' ')
UNIQ_POD_COUNT=$(jq -r '.data[].pod // empty' "$SERIES_JSON" | sort -u | wc -l | tr -d ' ')

CHUNK_CREATED=$(gawk '$1=="loki_ingester_chunks_created_total" {print $2}' "$METRICS_TXT" | tail -n 1)
CHUNK_FLUSH_TOTAL=$(gawk '$1=="loki_ingester_chunks_flush_requests_total" {print $2}' "$METRICS_TXT" | tail -n 1)
CHUNK_UTIL_SUM=$(gawk '$1=="loki_ingester_chunk_utilization_sum" {print $2}' "$METRICS_TXT" | tail -n 1)
CHUNK_UTIL_COUNT=$(gawk '$1=="loki_ingester_chunk_utilization_count" {print $2}' "$METRICS_TXT" | tail -n 1)

AVG_UTIL=""
if [[ -n "$CHUNK_UTIL_SUM" && -n "$CHUNK_UTIL_COUNT" && "$CHUNK_UTIL_COUNT" != "0" ]]; then
  AVG_UTIL=$(python3 - <<PY
s = float("$CHUNK_UTIL_SUM")
c = float("$CHUNK_UTIL_COUNT")
print(f"{s/c:.4f}")
PY
)
fi

FLUSH_REASON_LINES=$(gawk 'match($0,/reason="([^"]+)"/,m){print m[1]"=" $NF}' "$METRICS_TXT" | sort | paste -sd ", " -)

if [[ -n "$OUT_DIR" ]]; then
  mkdir -p "$OUT_DIR"
  cp "$LABELS_JSON" "$OUT_DIR/labels.json"
  cp "$POD_VALUES_JSON" "$OUT_DIR/pod_values.json"
  cp "$STATS_JSON" "$OUT_DIR/stats.json"
  cp "$SERIES_JSON" "$OUT_DIR/series.json"
  cp "$METRICS_TXT" "$OUT_DIR/metrics.txt"
fi

say "${C_BOLD}${C_CYAN}Loki Stats Report${C_RESET}"
say "${C_DIM}timestamp:${C_RESET} $TS_UTC"
say "${C_DIM}context:${C_RESET} $CONTEXT"
say "${C_DIM}namespace:${C_RESET} $NAMESPACE"
say "${C_DIM}lookback:${C_RESET} ${HOURS}h"
say "${C_DIM}api target:${C_RESET} $API_TARGET"
say "${C_DIM}metrics target:${C_RESET} $WRITE_TARGET"
if [[ -n "$OUT_DIR" ]]; then
  say "${C_DIM}output dir:${C_RESET} $OUT_DIR"
fi
say ""

say "${C_BOLD}${C_GREEN}Label Summary${C_RESET}"
say "labels total: ${C_YELLOW}${LABEL_COUNT}${C_RESET}"
say "pod label values (last ${HOURS}h): ${C_YELLOW}${POD_VALUE_COUNT}${C_RESET}"

say ""

say "${C_BOLD}${C_GREEN}Series Summary${C_RESET}"
say "series total (last ${HOURS}h): ${C_YELLOW}${SERIES_COUNT}${C_RESET}"
say "unique instance labels: ${C_YELLOW}${UNIQ_INSTANCE_COUNT}${C_RESET}"
say "unique pod labels: ${C_YELLOW}${UNIQ_POD_COUNT}${C_RESET}"

say ""

say "${C_BOLD}${C_GREEN}Chunk Metrics (ingester)${C_RESET}"
if [[ -n "$CHUNK_CREATED" ]]; then
  say "chunks created total: ${C_YELLOW}${CHUNK_CREATED}${C_RESET}"
fi
if [[ -n "$CHUNK_FLUSH_TOTAL" ]]; then
  say "flush requests total: ${C_YELLOW}${CHUNK_FLUSH_TOTAL}${C_RESET}"
fi
if [[ -n "$AVG_UTIL" ]]; then
  say "avg chunk utilization: ${C_YELLOW}${AVG_UTIL}${C_RESET}"
fi
if [[ -n "$FLUSH_REASON_LINES" ]]; then
  say "flush reasons (present in metrics): ${C_YELLOW}${FLUSH_REASON_LINES}${C_RESET}"
fi

say ""

say "${C_BOLD}${C_GREEN}Raw JSON Paths${C_RESET}"
if [[ -n "$OUT_DIR" ]]; then
  say "labels.json: $OUT_DIR/labels.json"
  say "pod_values.json: $OUT_DIR/pod_values.json"
  say "stats.json: $OUT_DIR/stats.json"
  say "series.json: $OUT_DIR/series.json"
  say "metrics.txt: $OUT_DIR/metrics.txt"
else
  say "(use --out-dir to save raw JSON and metrics)"
fi

say ""

say "${C_BOLD}${C_GREEN}Stats JSON (summary)${C_RESET}"
jq -c '.data' "$STATS_JSON" | sed "s/^/${C_DIM}/"; true

say ""

say "${C_DIM}done${C_RESET}"
