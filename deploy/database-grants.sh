#!/usr/bin/env bash
# Apply PadhAI runtime privileges after migrations. The login roles must already exist; this
# script never creates credentials. Run by deploy/server/update.sh through the migration owner.
set -euo pipefail

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}" "${POSTGRES_RUNTIME_USER:?}"
[[ "$POSTGRES_RUNTIME_USER" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "invalid POSTGRES_RUNTIME_USER" >&2; exit 1; }
COMPOSE=(docker compose --env-file /etc/aichat/postgres.env -f /opt/aichat/shared/postgres/docker-compose.yml)

"${COMPOSE[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v runtime_role="$POSTGRES_RUNTIME_USER" <<'SQL'
SELECT CASE WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = :'runtime_role') THEN 1 ELSE 0 END AS role_exists \gset
\if :role_exists
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'runtime_role') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'runtime_role') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'runtime_role') \gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', :'runtime_role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', current_user, :'runtime_role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I', current_user, :'runtime_role') \gexec
\else
\echo 'runtime role does not exist: create the LOGIN role before deploying'
\quit 3
\endif
SQL
