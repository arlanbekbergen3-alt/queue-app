#!/usr/bin/env bash
set -u

LABEL="${1:?label}"
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DIR")"
OUT="$DIR/results/inv"
mkdir -p "$OUT"

set -a
. "$ROOT/.env"
set +a

NET="ql_net"
APP="ql_app"
PG="ql_pg"
K6="ql_k6"
IMAGE="${IMAGE:-ql_app:head}"
DB="ql_$(echo "$LABEL" | tr -c 'a-zA-Z0-9\n' '_')"
SESSIONS="${SESSIONS:-1}"
STUDENTS="${STUDENTS:-30}"
ARRIVAL_SECONDS="${ARRIVAL_SECONDS:-30}"
POLL_SECONDS="${POLL_SECONDS:-60}"
APP_CPUSET="${APP_CPUSET:-}"
PG_CPUSET="${PG_CPUSET:-}"
K6_CPUSET="${K6_CPUSET:-}"
EXTRA_ENV="${EXTRA_ENV:-}"
APP_CMD="${APP_CMD:-python /scripts/inv_probe.py}"
SPY="${SPY:-0}"

free_mb() {
  powershell -NoProfile -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" | tr -d '\r'
}

FREE=$(free_mb)
echo "$LABEL free_mb_before=$FREE" > "$OUT/$LABEL-state.txt"
if [ "$FREE" -lt 2048 ]; then
  echo "free memory below 2 GB: $FREE MB" >&2
  exit 3
fi

pg_exec() { docker exec "$PG" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q -tA "$@"; }

PIDS=""
cleanup() {
  for pid in $PIDS; do kill "$pid" 2>/dev/null || true; done
  docker rm -f "$K6" > /dev/null 2>&1 || true
  docker rm -f "$APP" > /dev/null 2>&1 || true
  pg_exec -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" > /dev/null 2>&1 || true
}
trap cleanup EXIT

docker network inspect "$NET" > /dev/null 2>&1 || docker network create "$NET" > /dev/null
if ! docker ps --format '{{.Names}}' | grep -qx "$PG"; then
  docker rm -f "$PG" > /dev/null 2>&1 || true
  PG_ARGS=""
  [ -n "$PG_CPUSET" ] && PG_ARGS="--cpuset-cpus=$PG_CPUSET"
  docker run -d --name "$PG" --network "$NET" $PG_ARGS -e POSTGRES_PASSWORD=qlpass postgres:15 \
    -c max_connections=200 > /dev/null
  for _ in $(seq 1 40); do
    docker exec "$PG" pg_isready -U postgres > /dev/null 2>&1 && break
    sleep 1
  done
  sleep 2
fi

pg_exec -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" > /dev/null
pg_exec -c "CREATE DATABASE \"$DB\"" > /dev/null

APP_ARGS=""
[ -n "$APP_CPUSET" ] && APP_ARGS="--cpuset-cpus=$APP_CPUSET"
SPY_ARGS=""
[ "$SPY" = "1" ] && SPY_ARGS="--cap-add=SYS_PTRACE -u 0"

docker rm -f "$APP" > /dev/null 2>&1 || true
MSYS_NO_PATHCONV=1 docker run -d --name "$APP" --network "$NET" $APP_ARGS $SPY_ARGS \
  -e DATABASE_URL="postgresql+asyncpg://postgres:qlpass@$PG:5432/$DB" \
  -e JWT_SECRET_KEY="$JWT_SECRET_KEY" \
  -e TEACHER_INVITE_CODE="$TEACHER_INVITE_CODE" \
  -e LOG_FORMAT=json \
  -e PROBE_OUT="/out/$LABEL-probe.csv" \
  -e MAX_QUEUE_SIZE=300 \
  -e RATE_LIMIT_JOIN_PER_IP_PER_MINUTE=100000 \
  -e RATE_LIMIT_IP_PER_MINUTE=100000 \
  -e RATE_LIMIT_AUTH_PER_IP_PER_MINUTE=1000 \
  $EXTRA_ENV \
  -v "$DIR:/scripts:ro" -v "$OUT:/out" \
  --entrypoint sh "$IMAGE" -c "alembic upgrade head && cd /app && PYTHONPATH=/app $APP_CMD" > /dev/null

for _ in $(seq 1 60); do
  docker exec "$APP" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" > /dev/null 2>&1 && break
  sleep 1
done

if [ "$SPY" = "1" ]; then
  docker exec -u 0 "$APP" pip install -q py-spy > /dev/null 2>&1 || echo "py-spy install failed" >> "$OUT/$LABEL-state.txt"
fi

(
  echo "ts,active,idle_in_tx,wait_lock,wait_lwlock,wait_io,wait_client,total,ungranted_locks"
  while true; do
    docker exec "$PG" psql -U postgres -d postgres -tA -F, -c "select extract(epoch from now())::int, count(*) filter (where state='active'), count(*) filter (where state='idle in transaction'), count(*) filter (where wait_event_type='Lock'), count(*) filter (where wait_event_type='LWLock'), count(*) filter (where wait_event_type='IO'), count(*) filter (where wait_event_type='Client'), count(*), (select count(*) from pg_locks where not granted) from pg_stat_activity where datname='$DB' and pid <> pg_backend_pid()" 2>/dev/null
    sleep 1
  done
) > "$OUT/$LABEL-pg.csv" &
PIDS="$PIDS $!"

(
  echo "ts,name,cpu,mem"
  while true; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' "$APP" "$PG" 2>/dev/null | while IFS= read -r line; do
      echo "$(date +%s),$line"
    done
  done
) > "$OUT/$LABEL-docker.csv" &
PIDS="$PIDS $!"

SAMPLES=$(( (ARRIVAL_SECONDS + POLL_SECONDS + 40) / 2 ))
powershell -NoProfile -File "$DIR/host_cpu.ps1" -Samples "$SAMPLES" -Interval 2 > "$OUT/$LABEL-host.csv" 2>/dev/null &

if [ "$SPY" = "1" ]; then
  (
    sleep $((ARRIVAL_SECONDS + 10))
    docker exec -u 0 "$APP" py-spy record -d 30 -r 100 -f raw -o /out/$LABEL-spy.txt --pid 1 > /dev/null 2>&1
  ) &
  PIDS="$PIDS $!"
fi

K6_ARGS=""
[ -n "$K6_CPUSET" ] && K6_ARGS="--cpuset-cpus=$K6_CPUSET"

START=$(date +%s)
MSYS_NO_PATHCONV=1 docker run --rm -i --name "$K6" --network "$NET" $K6_ARGS \
  -e BASE_URL="http://$APP:8000" -e TEACHER_INVITE_CODE="$TEACHER_INVITE_CODE" \
  -e SESSIONS="$SESSIONS" -e STUDENTS="$STUDENTS" \
  -e ARRIVAL_SECONDS="$ARRIVAL_SECONDS" -e POLL_SECONDS="$POLL_SECONDS" \
  -v "$DIR:/scripts" \
  grafana/k6:0.57.0 run --quiet \
  --summary-export "/scripts/results/inv/$LABEL-summary.json" \
  /scripts/multi.js > "$OUT/$LABEL-k6.txt" 2>&1 || true
END=$(date +%s)
echo "start=$START end=$END" >> "$OUT/$LABEL-state.txt"

sleep 2
docker exec "$PG" psql -U postgres -d "$DB" -tA -c "select count(distinct session_id), count(*) from submissions where status='completed'" > "$OUT/$LABEL-graded.txt" 2>&1 || true
docker logs "$APP" 2>&1 | grep -c '"level": "ERROR"' > "$OUT/$LABEL-server-errors.txt" || true
docker logs "$APP" 2>&1 | grep -ci 'QueuePool' >> "$OUT/$LABEL-server-errors.txt" || true
docker logs "$APP" 2>&1 | grep -c -- '-> 5[0-9][0-9]' >> "$OUT/$LABEL-server-errors.txt" || true
echo "$LABEL done in $((END - START)) s"
