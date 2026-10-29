#!/usr/bin/env bash
# Apply NetSec AI least-privilege database roles after migrations. On a clean isolated cluster,
# it creates the runtime/auth LOGIN roles with the passwords in postgres.env. It also absorbs the
# historical migrations' NOLOGIN aichat_auth compatibility role, then removes that role after
# transferring only the intended identity-table privileges to netsecai_auth.
set -euo pipefail
SAFETY_LIB=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/server/safety.sh
. "$SAFETY_LIB"
safety_preflight
. "$(dirname "$(readlink -f "$0")")/server/lib.sh"

POSTGRES_USER=$(env_value POSTGRES_USER postgres.env)
POSTGRES_DB=$(env_value POSTGRES_DB postgres.env)
POSTGRES_RUNTIME_USER=$(env_value POSTGRES_RUNTIME_USER postgres.env)
POSTGRES_RUNTIME_PASSWORD=$(env_value POSTGRES_RUNTIME_PASSWORD postgres.env)
POSTGRES_AUTH_USER=$(env_value POSTGRES_AUTH_USER postgres.env)
POSTGRES_AUTH_PASSWORD=$(env_value POSTGRES_AUTH_PASSWORD postgres.env)
: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}" "${POSTGRES_RUNTIME_USER:?}" "${POSTGRES_RUNTIME_PASSWORD:?}" \
  "${POSTGRES_AUTH_USER:?}" "${POSTGRES_AUTH_PASSWORD:?}"
for role in "$POSTGRES_RUNTIME_USER" "$POSTGRES_AUTH_USER"; do
  [[ "$role" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "invalid NetSec database role: $role" >&2; exit 1; }
done
[ "$POSTGRES_RUNTIME_USER" = netsecai_runtime ] || die "POSTGRES_RUNTIME_USER must be netsecai_runtime"
[ "$POSTGRES_AUTH_USER" = netsecai_auth ] || die "POSTGRES_AUTH_USER must be netsecai_auth"
[ "$POSTGRES_RUNTIME_USER" != "$POSTGRES_USER" ] && [ "$POSTGRES_AUTH_USER" != "$POSTGRES_USER" ] \
  && [ "$POSTGRES_RUNTIME_USER" != "$POSTGRES_AUTH_USER" ] || die "database roles must be distinct"
assert_database_identity

# The passwords travel only in environments (`-e NAME` takes the value from this shell's environment;
# psql reads it with \getenv), never in arguments, which any local user can read in ps or
# /proc/*/cmdline. They are never interpolated into shell commands either.
NETSEC_RUNTIME_PW="$POSTGRES_RUNTIME_PASSWORD" NETSEC_AUTH_PW="$POSTGRES_AUTH_PASSWORD" \
  db_compose exec -T -e NETSEC_RUNTIME_PW -e NETSEC_AUTH_PW postgres psql -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v runtime_role="$POSTGRES_RUNTIME_USER" -v auth_role="$POSTGRES_AUTH_USER" <<'SQL'
\getenv runtime_password NETSEC_RUNTIME_PW
\getenv auth_password NETSEC_AUTH_PW
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'runtime_role', :'runtime_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'runtime_role') \gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', :'runtime_role', :'runtime_password') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'auth_role', :'auth_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'auth_role') \gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', :'auth_role', :'auth_password') \gexec

SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database()) \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'runtime_role') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'runtime_role') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'runtime_role') \gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', :'runtime_role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', current_user, :'runtime_role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I', current_user, :'runtime_role') \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'auth_role') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'auth_role') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE ON TABLE users TO %I', :'auth_role')
WHERE to_regclass('public.users') IS NOT NULL \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I TO %I', tablename, :'auth_role')
FROM (VALUES ('auth_sessions'), ('auth_accounts'), ('auth_verifications'), ('auth_two_factors')) AS identity(tablename)
WHERE to_regclass('public.' || tablename) IS NOT NULL \gexec
SELECT format('GRANT SELECT, INSERT ON TABLE auth_attempts TO %I', :'auth_role')
WHERE to_regclass('public.auth_attempts') IS NOT NULL \gexec

-- Historical migration compatibility is deployment-only. Explicit NetSec grants above are the
-- complete intended privilege set; remove every legacy grant and the NOLOGIN role afterwards.
SELECT format('REASSIGN OWNED BY %I TO %I', 'aichat_auth', current_user)
WHERE EXISTS (SELECT FROM pg_roles WHERE rolname = 'aichat_auth') \gexec
SELECT format('DROP OWNED BY %I', 'aichat_auth')
WHERE EXISTS (SELECT FROM pg_roles WHERE rolname = 'aichat_auth') \gexec
SELECT 'DROP ROLE aichat_auth' WHERE EXISTS (SELECT FROM pg_roles WHERE rolname = 'aichat_auth') \gexec
SQL
