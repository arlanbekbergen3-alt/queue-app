#!/usr/bin/env bash
set -euo pipefail

PROJECT="${COMPOSE_PROJECT_NAME:-queue-prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/queue}"
KEEP="${BACKUP_KEEP:-14}"

case "$KEEP" in
  ''|*[!0-9]*|0) echo "BACKUP_KEEP must be a positive number" >&2; exit 2 ;;
esac

umask 077
mkdir -p "$BACKUP_DIR"

stamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/queue_${stamp}.dump"
partial="${target}.part"
trap 'rm -f "$partial"' EXIT

compose() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }

compose exec -T postgres pg_dump -U postgres -Fc queue_db > "$partial"
compose exec -T postgres pg_restore --list < "$partial" > /dev/null
mv "$partial" "$target"

position=0
while IFS= read -r old; do
  position=$((position + 1))
  if [ "$position" -gt "$KEEP" ]; then
    rm -f -- "$old"
  fi
done < <(ls -1t "$BACKUP_DIR"/queue_*.dump)

echo "backup ok: $target ($(wc -c < "$target") bytes), kept: $(ls -1 "$BACKUP_DIR"/queue_*.dump | wc -l)"
