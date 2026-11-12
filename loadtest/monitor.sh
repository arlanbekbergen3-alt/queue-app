#!/usr/bin/env bash
set -u

OUT="${1:?usage: monitor.sh <output.csv>}"
INTERVAL="${2:-2}"
DB_CONTAINER="${DB_CONTAINER:-queue_db}"
WEB_CONTAINER="${WEB_CONTAINER:-queue_api}"
DB_NAME="${DB_NAME:-queue_db}"

echo "ts,db_total,db_active,db_idle,db_idle_in_tx,db_waiting_lock,web_cpu_percent,web_mem" > "$OUT"

while true; do
  ts=$(date +%s)
  db=$(docker exec "$DB_CONTAINER" psql -U postgres -d postgres -tAF, -c \
    "select count(*), count(*) filter (where state='active'), count(*) filter (where state='idle'), count(*) filter (where state='idle in transaction'), count(*) filter (where wait_event_type='Lock') from pg_stat_activity where datname='$DB_NAME' and pid <> pg_backend_pid()" 2>/dev/null || echo ",,,,")
  web=$(docker stats --no-stream --format '{{.CPUPerc}},{{.MemUsage}}' "$WEB_CONTAINER" 2>/dev/null | tr -d '%' | sed 's/ \/ .*//' || echo ",")
  echo "$ts,$db,$web" >> "$OUT"
  sleep "$INTERVAL"
done
