#!/usr/bin/env bash
#
# Nightly logical backup of the isolated PadhAI PostgreSQL database.
# Fails closed if an unexpected non-system schema appears.
#
#   pg_dump custom format, compressed; restore with:
#   docker compose --env-file /etc/aichat/postgres.env -f /opt/aichat/shared/postgres/docker-compose.yml \
#     exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < <file>.dump
#
# file_chunks (the text and embeddings of students' files) is derived data and is not dumped:
# the files themselves are. After a restore, have every ready file read again (the API does it):
#   ... exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
#     "UPDATE user_files SET status='processing', attempts=0, error_code=NULL WHERE status='ready'"
#
# auth_sessions and auth_verifications are not dumped either: Better Auth keeps live session tokens
# and sign-in codes in them (raw tokens, unlike the hashes before 0012), and a copy on disk must
# never be a way into an account. After a restore everyone simply signs in again.
#
# This cluster contains PadhAI only. If an unrelated schema appears, stop and investigate rather
# than silently excluding it: isolation has been violated.
#
# A copy of the docker volume is not a backup: it can catch the database mid-write.
set -euo pipefail

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/padhai}"
KEEP_DAYS="${KEEP_DAYS:-7}"
COMPOSE=(docker compose --env-file /etc/aichat/postgres.env -f /opt/aichat/shared/postgres/docker-compose.yml)

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
out="$BACKUP_DIR/padhai-$(date +%Y-%m-%d-%H%M%S).dump"

unexpected_schemas=$("${COMPOSE[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT string_agg(schema_name, ',' ORDER BY schema_name) FROM information_schema.schemata WHERE schema_name NOT IN ('public', 'information_schema', 'pg_catalog', 'pg_toast') AND schema_name NOT LIKE 'pg_temp_%' AND schema_name NOT LIKE 'pg_toast_temp_%'" \
  | tr -d '[:space:]')
if [ -n "$unexpected_schemas" ]; then
  echo "backup: unexpected non-system schemas in the isolated PadhAI database: $unexpected_schemas" >&2
  exit 1
fi

# The dumps share the database's disk: never write one that could fill it (a full disk stops
# PostgreSQL). Refuse, loudly, when less than twice the size of what is dumped is free.
db_bytes=$("${COMPOSE[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT pg_database_size(current_database())" | tr -dc '0-9')
free_bytes=$(df --output=avail -B1 "$BACKUP_DIR" | tail -1 | tr -dc '0-9')
if [ -n "$db_bytes" ] && [ -n "$free_bytes" ] && [ "$free_bytes" -lt $((2 * db_bytes)) ]; then
  echo "backup: only $free_bytes bytes free for a $db_bytes-byte database; not dumping (free disk space first)" >&2
  exit 1
fi

"${COMPOSE[@]}" exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom \
  --exclude-table-data=file_chunks --exclude-table-data=auth_sessions --exclude-table-data=auth_verifications > "$out"

# Fail loudly on an empty dump rather than rotating away the last good copy.
if [ ! -s "$out" ]; then
  echo "backup: pg_dump produced no data at $out" >&2
  exit 1
fi
echo "backup: wrote $out ($(du -h "$out" | cut -f1))"
find "$BACKUP_DIR" -name 'padhai-*.dump' -type f -mtime "+$KEEP_DAYS" -print -delete

# Copy off the host for a real backup (a dump on the same disk does not survive the disk).
# rclone copy "$out" remote:aichat-backups/
