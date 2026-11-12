#!/usr/bin/env bash
set -eu

SCENARIO="${1:?scenario: peak|salvo|sse|ssesalvo}"
VARIANT="${2:?variant: base|new}"
LABEL="${3:?label}"
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DIR")"
RESULTS="$DIR/results/series"
mkdir -p "$RESULTS"

set -a
. "$ROOT/.env"
set +a

case "$VARIANT" in
  base) IMAGE="queue_tmp_baseline:step1" ;;
  new) IMAGE="queue_tmp_new:latest" ;;
  *) echo "unknown variant" >&2; exit 2 ;;
esac

NET="queue-app_default"
APP="queue_tmp_app"
DB="queue_tmp_$(echo "$LABEL" | tr -c 'a-zA-Z0-9\n' '_')"

psql_admin() { docker exec queue_db psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q -c "$1" > /dev/null; }

cleanup() {
  kill "${MONITOR_PID:-0}" 2>/dev/null || true
  docker stop "$APP" > /dev/null 2>&1 || true
  docker rm "$APP" > /dev/null 2>&1 || true
  psql_admin "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" 2>/dev/null || true
}
trap cleanup EXIT

docker rm "$APP" > /dev/null 2>&1 || true
psql_admin "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)"
psql_admin "CREATE DATABASE \"$DB\""

docker run -d --name "$APP" --network "$NET" \
  -e DATABASE_URL="postgresql+asyncpg://postgres:${DB_PASSWORD}@postgres:5432/${DB}" \
  -e JWT_SECRET_KEY="$JWT_SECRET_KEY" \
  -e TEACHER_INVITE_CODE="$TEACHER_INVITE_CODE" \
  -e LOG_FORMAT=json \
  "$IMAGE" sh -c "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000" > /dev/null

for _ in $(seq 1 60); do
  docker exec "$APP" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" > /dev/null 2>&1 && break
  sleep 1
done

DB_NAME="$DB" WEB_CONTAINER="$APP" DB_CONTAINER=queue_db \
  bash "$DIR/monitor.sh" "$RESULTS/$LABEL-system.csv" 2 &
MONITOR_PID=$!

BASE_URL="http://$APP:8000"
case "$SCENARIO" in
  peak|salvo)
    if [ "$SCENARIO" = peak ]; then ARRIVAL=60; POLL=240; else ARRIVAL=5; POLL=55; fi
    MSYS_NO_PATHCONV=1 docker run --rm -i --network "$NET" \
      -e BASE_URL="$BASE_URL" -e TEACHER_INVITE_CODE="$TEACHER_INVITE_CODE" \
      -e STUDENTS=300 -e ARRIVAL_SECONDS="$ARRIVAL" -e POLL_SECONDS="$POLL" \
      -v "$DIR:/scripts" \
      grafana/k6:0.57.0 run --quiet \
      --summary-export "/scripts/results/series/$LABEL-summary.json" \
      /scripts/peak.js > "$RESULTS/$LABEL-k6.txt" 2>&1 || true
    ;;
  sse|ssesalvo)
    if [ "$SCENARIO" = sse ]; then ARRIVAL=60; DURATION=240; else ARRIVAL=5; DURATION=55; fi
    MSYS_NO_PATHCONV=1 docker run --rm -i --network "$NET" \
      -e BASE_URL="$BASE_URL" -e TEACHER_INVITE_CODE="$TEACHER_INVITE_CODE" \
      -e STUDENTS=300 -e ARRIVAL_SECONDS="$ARRIVAL" -e DURATION_SECONDS="$DURATION" \
      -e OUT="/scripts/results/series/$LABEL-sse.json" \
      -v "$DIR:/scripts" \
      python:3.12 sh -c "pip install -q httpx 2>/dev/null && python /scripts/sse_peak.py" \
      > "$RESULTS/$LABEL-sse.txt" 2>&1 || true
    ;;
esac

docker logs "$APP" 2>&1 | grep -c '"level": "ERROR"' > "$RESULTS/$LABEL-server-errors.txt" || true
docker logs "$APP" 2>&1 | grep -ci 'QueuePool' >> "$RESULTS/$LABEL-server-errors.txt" || true
docker logs "$APP" 2>&1 | grep -c -- '-> 5[0-9][0-9]' >> "$RESULTS/$LABEL-server-errors.txt" || true
echo "$LABEL done"
