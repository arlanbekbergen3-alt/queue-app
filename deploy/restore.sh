#!/usr/bin/env bash
set -euo pipefail

DUMP="${1:?usage: restore.sh <dump-file> [database-name]}"
TARGET_DB="${2:-queue_restore}"
PROJECT="${COMPOSE_PROJECT_NAME:-queue-prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

case "$TARGET_DB" in
  queue_db) echo "refusing to overwrite the live database: pick another name" >&2; exit 2 ;;
  *[!a-zA-Z0-9_]*) echo "database name may contain only letters, digits and _" >&2; exit 2 ;;
esac

compose() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }

compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$TARGET_DB\""
compose exec -T postgres pg_restore -U postgres -d "$TARGET_DB" --no-owner < "$DUMP"
compose exec -T postgres psql -U postgres -d "$TARGET_DB" -At -c "SELECT 'sessions', count(*) FROM queue_sessions UNION ALL SELECT 'entries', count(*) FROM entries UNION ALL SELECT 'revision', 0 FROM alembic_version"
compose exec -T postgres psql -U postgres -d "$TARGET_DB" -At -c "SELECT version_num FROM alembic_version"

echo "restored into database $TARGET_DB (the live database was not touched)"
