#!/usr/bin/env bash
set -eu

LABEL="${1:?usage: run.sh <label>}"
DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS="$DIR/results"
mkdir -p "$RESULTS"

: "${TEACHER_INVITE_CODE:?set TEACHER_INVITE_CODE to the value from .env}"

bash "$DIR/monitor.sh" "$RESULTS/$LABEL-system.csv" 2 &
MONITOR_PID=$!
trap 'kill $MONITOR_PID 2>/dev/null || true' EXIT

MSYS_NO_PATHCONV=1 docker run --rm -i \
  --add-host=host.docker.internal:host-gateway \
  ${K6_NETWORK:+--network "$K6_NETWORK"} \
  -e BASE_URL="${BASE_URL:-http://host.docker.internal:8000}" \
  -e TEACHER_INVITE_CODE="$TEACHER_INVITE_CODE" \
  -e STUDENTS="${STUDENTS:-300}" \
  -e ARRIVAL_SECONDS="${ARRIVAL_SECONDS:-60}" \
  -e POLL_SECONDS="${POLL_SECONDS:-240}" \
  -e ORDER_MODE="${ORDER_MODE:-join}" \
  -e PRESENCE="${PRESENCE:-0}" \
  -e GRADE_INTERVAL_SECONDS="${GRADE_INTERVAL_SECONDS:-15}" \
  -v "$DIR:/scripts" \
  grafana/k6:0.57.0 run --quiet \
  --summary-export "/scripts/results/$LABEL-summary.json" \
  ${K6_OUT:+--out "json=/scripts/results/$LABEL-points.json"} \
  "/scripts/${SCRIPT:-peak.js}" 2>&1 | tee "$RESULTS/$LABEL-k6.txt"
