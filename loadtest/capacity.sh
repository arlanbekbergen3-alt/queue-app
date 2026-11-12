#!/usr/bin/env bash
set -eu

NAME="${1:?usage: capacity.sh <container-name> [env KEY=VALUE ...] -- <uvicorn args>}"
shift
DIR="$(cd "$(dirname "$0")" && pwd)"
NETWORK="${K6_NETWORK:-queue-app_default}"
IMAGE="${IMAGE:-$(docker inspect queue_api --format '{{.Config.Image}}')}"
: "${TEACHER_INVITE_CODE:?set TEACHER_INVITE_CODE to the value from .env}"
: "${DB_PASSWORD:?set DB_PASSWORD to the value from .env}"
: "${JWT_SECRET_KEY:?set JWT_SECRET_KEY to the value from .env}"

ENVS=()
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
  ENVS+=(-e "$1")
  shift
done
[ "${1:-}" = "--" ] && shift

docker run -d --name "$NAME" --network "$NETWORK" \
  -e DATABASE_URL="postgresql+asyncpg://postgres:${DB_PASSWORD}@postgres:5432/queue_db" \
  -e JWT_SECRET_KEY="${JWT_SECRET_KEY}" \
  -e TEACHER_INVITE_CODE="$TEACHER_INVITE_CODE" \
  -e LOG_FORMAT=json \
  "${ENVS[@]}" \
  "$IMAGE" uvicorn main:app --host 0.0.0.0 --port 8000 "$@" > /dev/null

for _ in $(seq 1 30); do
  docker exec "$NAME" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" 2>/dev/null && break
  sleep 1
done

MSYS_NO_PATHCONV=1 docker run --rm -i --network "$NETWORK" \
  -e BASE_URL="http://$NAME:8000" \
  -e TEACHER_INVITE_CODE="$TEACHER_INVITE_CODE" \
  -e VUS="${VUS:-64}" -e DURATION="${DURATION:-20s}" -e WAITING="${WAITING:-200}" \
  -v "$DIR:/scripts" \
  grafana/k6:0.57.0 run --quiet /scripts/capacity.js 2>&1 \
  | grep -E "http_reqs|http_req_duration\.\.|http_req_failed|checks" || true

docker stop "$NAME" > /dev/null
