# shellcheck shell=bash
# shellcheck disable=SC2034  # the constants are used by the scripts that source this file
# Shared helpers for deploy-netsec/server/update.sh, status.sh, install-units.sh, deploy-netsec/infra-check.sh
# and deploy-netsec/install.sh. Sourced, never run. Runs on the production host as root.
# Never prints secret values.
#
# Production runs the checkout /opt/netsecai/app (the commit last pushed to /opt/netsecai/app.git by
# `bun run deploy`). The database, secrets and Node runtimes live outside it.

ROOT=/opt/netsecai
APP="$ROOT/app"            # the production checkout: the code that runs
APP_GIT="$ROOT/app.git"    # the bare repository deploys push to
STATE="$ROOT/app-state"    # what each part runs (content keys), and the deploy history
SHARED="$ROOT/shared"
SECRETS=/etc/netsecai
HISTORY="$STATE/HISTORY"
BUN=/root/.bun/bin/bun
API_PORT=4400
WEB_PORT=4300

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\nSTOP: %s\n' "$*" >&2; exit 1; }

is_commit() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }

# Wait until a URL answers with a 2xx/3xx status.
wait_http() {
  local url=$1 seconds=${2:-60} i
  for ((i = 0; i < seconds * 4; i++)); do
    curl -fsS -o /dev/null --max-time 5 "$url" 2>/dev/null && return 0
    sleep 0.25
  done
  return 1
}

# The commit a running app reports on its health endpoint ("" if it does not answer).
reported_release() {
  curl -fsS --max-time 5 "$1" 2>/dev/null \
    | python3.12 -c 'import json,sys; print(json.load(sys.stdin).get("release", ""))' 2>/dev/null || true
}

# One value from one of the secrets files, never printed by itself.
env_value() { sed -n "s/^$1=//p" "$SECRETS/$2" 2> /dev/null | tail -1 | tr -d '\r"'"'"''; }

DB_COMPOSE_PROJECT=netsecai-postgres
DB_CONTAINER=netsecai-postgres-postgres-1
DB_VOLUME=netsecai_postgres_data
DB_NETWORK=netsecai_postgres_network
DB_NAME=netsecai
DB_MARKER_PREFIX=netsecai-db-identity:

db_compose() {
  docker compose --env-file "$SECRETS/postgres.env" -f "$SHARED/postgres/docker-compose.yml" "$@"
}

validate_database_environment() {
  local configured_db marker
  [ -s "$SECRETS/postgres.env" ] || die "missing $SECRETS/postgres.env"
  configured_db=$(env_value POSTGRES_DB postgres.env)
  marker=$(env_value NETSECAI_DB_IDENTITY postgres.env)
  [ "$configured_db" = "$DB_NAME" ] || die "POSTGRES_DB must be the isolated database $DB_NAME"
  [ "$(env_value POSTGRES_USER postgres.env)" = netsecai_migrator ] || die "POSTGRES_USER must be netsecai_migrator"
  [ "$(env_value POSTGRES_RUNTIME_USER postgres.env)" = netsecai_runtime ] || die "POSTGRES_RUNTIME_USER must be netsecai_runtime"
  [ "$(env_value POSTGRES_AUTH_USER postgres.env)" = netsecai_auth ] || die "POSTGRES_AUTH_USER must be netsecai_auth"
  [ "$(env_value POSTGRES_HOST_PORT postgres.env)" = 55432 ] || die "POSTGRES_HOST_PORT must be the isolated port 55432"
  [ "$(env_value BACKUP_DIR postgres.env)" = /var/backups/netsecai ] || die "BACKUP_DIR must be /var/backups/netsecai"
  [[ "$marker" =~ ^[A-Za-z0-9_-]{32,128}$ ]] \
    || die "NETSECAI_DB_IDENTITY must be a dedicated random 32-128 character marker in $SECRETS/postgres.env"
}

assert_database_url() {
  local file=$1 key=$2 expected_user=$3 port
  port=$(env_value POSTGRES_HOST_PORT postgres.env)
  port=${port:-55432}
  python3.12 - "$SECRETS/$file" "$key" "$expected_user" "$port" "$DB_NAME" <<'PY' \
    || die "$key in $SECRETS/$file must target only the isolated NetSec database"
import sys
from urllib.parse import urlsplit

path, key, expected_user, expected_port, expected_db = sys.argv[1:]
value = ""
with open(path, encoding="utf-8") as stream:
    for raw in stream:
        if raw.startswith(f"{key}="):
            value = raw.split("=", 1)[1].strip().strip('"\'')
url = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
valid = (
    url.scheme == "postgresql"
    and url.hostname == "127.0.0.1"
    and url.port == int(expected_port)
    and url.username == expected_user
    and url.path == f"/{expected_db}"
)
raise SystemExit(0 if valid else 1)
PY
}

assert_netsec_database_urls() {
  assert_database_url migration.env MIGRATION_DATABASE_URL "$(env_value POSTGRES_USER postgres.env)"
  assert_database_url api.env DATABASE_URL "$(env_value POSTGRES_RUNTIME_USER postgres.env)"
  assert_database_url web.env AUTH_DATABASE_URL "$(env_value POSTGRES_AUTH_USER postgres.env)"
}

# Prove that every Docker object is the explicit NetSec database resource before touching the DB.
assert_database_resources() {
  local project service name mounts networks volume_project volume_key network_project network_key database
  validate_database_environment
  [ -f "$SHARED/postgres/docker-compose.yml" ] || die "missing $SHARED/postgres/docker-compose.yml"
  docker container inspect "$DB_CONTAINER" >/dev/null 2>&1 || die "missing expected database container $DB_CONTAINER"
  name=$(docker inspect -f '{{.Name}}' "$DB_CONTAINER" 2>/dev/null)
  project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$DB_CONTAINER" 2>/dev/null)
  service=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$DB_CONTAINER" 2>/dev/null)
  [ "$name" = "/$DB_CONTAINER" ] && [ "$project" = "$DB_COMPOSE_PROJECT" ] && [ "$service" = postgres ] \
    || die "database container identity mismatch"

  docker volume inspect "$DB_VOLUME" >/dev/null 2>&1 || die "missing expected database volume $DB_VOLUME"
  volume_project=$(docker volume inspect -f '{{index .Labels "com.docker.compose.project"}}' "$DB_VOLUME" 2>/dev/null)
  volume_key=$(docker volume inspect -f '{{index .Labels "com.docker.compose.volume"}}' "$DB_VOLUME" 2>/dev/null)
  [ "$volume_project" = "$DB_COMPOSE_PROJECT" ] && [ "$volume_key" = pgdata ] \
    || die "database volume identity mismatch"

  docker network inspect "$DB_NETWORK" >/dev/null 2>&1 || die "missing expected database network $DB_NETWORK"
  network_project=$(docker network inspect -f '{{index .Labels "com.docker.compose.project"}}' "$DB_NETWORK" 2>/dev/null)
  network_key=$(docker network inspect -f '{{index .Labels "com.docker.compose.network"}}' "$DB_NETWORK" 2>/dev/null)
  [ "$network_project" = "$DB_COMPOSE_PROJECT" ] && [ "$network_key" = postgres ] \
    || die "database network identity mismatch"

  mounts=$(docker inspect -f '{{range .Mounts}}{{printf "%s:%s\n" .Name .Destination}}{{end}}' "$DB_CONTAINER" 2>/dev/null)
  networks=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{printf "%s\n" $name}}{{end}}' "$DB_CONTAINER" 2>/dev/null)
  grep -Fx "$DB_VOLUME:/var/lib/postgresql/data" <<<"$mounts" >/dev/null \
    || die "database container is not mounted from $DB_VOLUME"
  grep -Fx "$DB_NETWORK" <<<"$networks" >/dev/null \
    || die "database container is not attached to $DB_NETWORK"

  database=$(db_compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$(env_value POSTGRES_USER postgres.env)" \
    -d "$DB_NAME" -tAc 'SELECT current_database()' 2>/dev/null | tr -d '[:space:]')
  [ "$database" = "$DB_NAME" ] || die "expected database $DB_NAME is unavailable"
}

assert_database_identity() {
  local expected actual
  assert_database_resources
  expected="$DB_MARKER_PREFIX$(env_value NETSECAI_DB_IDENTITY postgres.env)"
  actual=$(db_compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$(env_value POSTGRES_USER postgres.env)" \
    -d "$DB_NAME" -tAc "SELECT COALESCE(shobj_description(oid, 'pg_database'), '') FROM pg_database WHERE datname = current_database()" \
    2>/dev/null | tr -d '\r\n')
  [ "$actual" = "$expected" ] || die "database identity marker missing or mismatched; refusing the operation"
}

# Called only by install.sh after it proved the expected container, volume and network did not
# exist before startup. A marker is never adopted into an existing or non-empty database.
initialize_database_identity() {
  local existing relation_count marker
  [ "${NETSECAI_BRAND_NEW_DATABASE:-0}" = 1 ] \
    || die "database identity initialization requires a proven brand-new NetSec database"
  assert_database_resources
  existing=$(db_compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$(env_value POSTGRES_USER postgres.env)" \
    -d "$DB_NAME" -tAc "SELECT COALESCE(shobj_description(oid, 'pg_database'), '') FROM pg_database WHERE datname = current_database()" \
    2>/dev/null | tr -d '\r\n')
  [ -z "$existing" ] || die "brand-new database already has an identity marker; refusing adoption"
  relation_count=$(db_compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$(env_value POSTGRES_USER postgres.env)" \
    -d "$DB_NAME" -tAc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')" \
    2>/dev/null | tr -dc '0-9')
  [ "$relation_count" = 0 ] || die "brand-new database is not empty; refusing identity initialization"
  marker=$(env_value NETSECAI_DB_IDENTITY postgres.env)
  db_compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$(env_value POSTGRES_USER postgres.env)" -d "$DB_NAME" \
    -v marker="$marker" -v marker_prefix="$DB_MARKER_PREFIX" <<'SQL' >/dev/null
SELECT format('COMMENT ON DATABASE %I IS %L', current_database(), :'marker_prefix' || :'marker') \gexec
SQL
  assert_database_identity
}

# The session cookie as the web app's Better Auth signs it (lib/server/cookies.ts):
# urlencode(token + "." + base64(HMAC-SHA256(BETTER_AUTH_SECRET, token))). Made here for a token
# that is no session, so the request passes the BFF's signature check and reaches the API.
signed_session_cookie() {
  local token=$1 secret signature
  secret=$(env_value BETTER_AUTH_SECRET web.env)
  [ -n "$secret" ] || return 1
  signature=$(printf '%s' "$token" | openssl dgst -sha256 -hmac "$secret" -binary | base64 | tr -d '\n')
  python3.12 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$token.$signature"
}

# The web app's database role reaches its identity tables (AUTH_DATABASE_URL, from web.env).
auth_database_ok() {
  systemd-run --pipe --wait --collect --quiet -p EnvironmentFile="$SECRETS/web.env" \
    "$APP/backend/.venv/bin/python" -c '
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["AUTH_DATABASE_URL"], timeout=10)
    try:
        await conn.fetchval("SELECT count(*) FROM auth_accounts")
    finally:
        await conn.close()
asyncio.run(main())' > /dev/null 2>&1
}

# The web app's own check of the same (its /api/health, local requests only): Better Auth
# started (the first check starts it) and its tables answer. A web build from before Better Auth
# reports no "auth" at all: nothing to check then (a rollback).
web_auth_ok() {
  local body i
  for i in 1 2 3 4 5; do
    body=$(curl -fsS --max-time 10 "http://127.0.0.1:$1/api/health" 2> /dev/null) || body=""
    case "$body" in
      '') ;;
      *'"auth":"ok"'*) return 0 ;;
      *'"auth":'*) ;;
      *) return 0 ;;
    esac
    sleep 2
  done
  return 1
}

# Prove browser -> web -> API -> database on a web port: a request with a made-up session
# must travel through the BFF to the API (which looks the session up in PostgreSQL) and
# come back 401, and the API's own log must show the same request id. It carries both the
# Better Auth cookie (signed, so the BFF forwards it) and the cookie of the release before it,
# so it holds for either web build: a rollback runs today's tools against an older web app.
check_chain() {
  local port=$1 api_unit=$2 headers body status rid i signed
  headers=$(mktemp) body=$(mktemp)
  signed=$(signed_session_cookie "deploychaincheck$(date +%s)") || signed=""
  status=$(curl -s -o "$body" -D "$headers" -w '%{http_code}' --max-time 20 \
    -H "Cookie: padhai_session=deploy-chain-check; padhai_session=$signed" "http://127.0.0.1:$port/api/v1/conversations" || true)
  rid=$(grep -i '^x-request-id:' "$headers" | tr -d '\r' | awk '{print $2}')
  if [ "$status" != 401 ] || ! grep -q '"unauthorized"' "$body" || [ -z "$rid" ]; then
    rm -f "$headers" "$body"
    note "chain check: expected 401 unauthorized with a request id, got HTTP $status"
    return 1
  fi
  rm -f "$headers" "$body"
  for ((i = 0; i < 20; i++)); do
    if journalctl -u "$api_unit" --since "-5 min" -o cat --no-pager 2>/dev/null | grep -F "$rid" >/dev/null; then
      note "chain ok: web :$port -> $api_unit -> database (request $rid)"
      return 0
    fi
    sleep 0.5
  done
  note "chain check: request $rid never reached $api_unit"
  return 1
}

json_field() { python3.12 -c "import json,sys; print(json.loads(sys.stdin.read())[sys.argv[1]])" "$1"; }

# ------------------------------------------------------------------ content keys
# What each part of the app is made of, as a short hash of the git objects of its files in the
# checkout's HEAD (tests excluded: tests/, e2e/, *.test.*, and the production smoke, smoke/ and
# its playwright.smoke.config.ts). A part is rebuilt or restarted only when its key differs
# from the key it runs with ($STATE/<part>: "<key> <commit it came from>").
TEST_FILES='(^|/)(tests|e2e|smoke)/|\.test\.[a-z]+$|/playwright\.smoke\.config\.ts$'
tree_key() { # <path>...
  git -C "$APP" ls-tree -r HEAD -- "$@" | grep -vE "$TEST_FILES" | sha256sum | cut -c1-16
}
key_migrations() { tree_key backend/alembic/versions; }
key_backend_deps() { tree_key backend/requirements.lock; }
key_backend() { tree_key backend deploy-netsec/systemd/netsecai-api.service; }
key_frontend_deps() {
  git -C "$APP" ls-tree -r HEAD -- frontend .bun-version | grep -E '(/package\.json|/bun\.lock|\.bun-version)$' | sha256sum | cut -c1-16
}
# NEXT_PUBLIC_* values are baked into the build, so they are part of the frontend.
key_frontend() {
  { tree_key frontend .node-version .bun-version deploy-netsec/systemd/netsecai-web.service; grep -E '^NEXT_PUBLIC_' "$SECRETS/web.env"; } | sha256sum | cut -c1-16
}
running_key() { cut -d' ' -f1 "$STATE/$1" 2>/dev/null; }
running_commit() { cut -d' ' -f2 "$STATE/$1" 2>/dev/null; }

# Enough disk and memory to install or build without starving production or PostgreSQL.
preflight_resources() {
  local free_mb avail_mb min_free=${NETSECAI_MIN_FREE_MB:-3000} min_mem=${NETSECAI_MIN_MEMORY_MB:-1200}
  free_mb=$(df -Pm "$ROOT" | awk 'NR==2 {print $4}')
  avail_mb=$(awk '/^MemAvailable:|^SwapFree:/ {sum += $2} END {print int(sum / 1024)}' /proc/meminfo)
  [ "$free_mb" -ge "$min_free" ] || die "only ${free_mb} MB free on $ROOT (need ${min_free} MB); the database shares this disk"
  [ "$avail_mb" -ge "$min_mem" ] || die "only ${avail_mb} MB memory+swap available (need ${min_mem} MB)"
}

# The Node runtime the web app is built and served with: the checkout's .node-version (the same
# file CI's setup-node reads). Each version is installed once from nodejs.org, verified against
# the published SHA-256 list, into /opt/netsecai/runtime.
RUNTIMES="$ROOT/runtime"
node_bin_of() { printf '%s/node-v%s-linux-x64/bin' "$RUNTIMES" "$(tr -d '[:space:]' < "$1/.node-version")"; }
ensure_node() {
  local dir=$1 version tarball tmp
  version=$(tr -d '[:space:]' < "$dir/.node-version")
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid .node-version in $dir: '$version'"
  [ -x "$RUNTIMES/node-v$version-linux-x64/bin/node" ] && return 0
  tarball="node-v$version-linux-x64.tar.xz"
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/$tarball" "https://nodejs.org/dist/v$version/$tarball" || die "could not download Node $version"
  curl -fsSL -o "$tmp/SHASUMS256.txt" "https://nodejs.org/dist/v$version/SHASUMS256.txt" || die "could not download Node's checksums"
  (cd "$tmp" && grep -E " $tarball\$" SHASUMS256.txt | sha256sum -c --status) || die "Node $version failed its SHA-256 check"
  mkdir -p "$RUNTIMES" && tar -xJf "$tmp/$tarball" -C "$tmp" && mv "$tmp/node-v$version-linux-x64" "$RUNTIMES/"
  rm -rf "$tmp"
  note "installed Node $version (checksum verified)"
}

# The pinned Bun (.bun-version), installed when it changes.
ensure_bun() {
  local want
  want=$(tr -d '[:space:]' < "$1/.bun-version")
  [ -x "$BUN" ] && [ "$("$BUN" --version)" = "$want" ] && return 0
  curl -fsSL https://bun.sh/install | BUN_INSTALL=/root/.bun bash -s "bun-v$want" >/dev/null || die "could not install bun $want"
  [ "$("$BUN" --version)" = "$want" ] || die "bun $want could not be installed"
  note "installed bun $want"
}

# Run an install or build capped in memory and at low priority, so the kernel kills the build,
# never the live services or PostgreSQL.
build_step() {
  systemd-run --scope --quiet --nice=10 -p MemoryMax="${NETSECAI_BUILD_MEMORY_MAX:-2G}" -- "$@"
}
