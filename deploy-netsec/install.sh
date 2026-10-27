#!/usr/bin/env bash
#
# Prepare an Ubuntu 24.04 host for the application. Run once per host (and again to repair it),
# from any copy of this repository, for example an extracted release:
#
#   sudo bash deploy-netsec/install.sh
#
# Installs system packages (git included) and Bun, creates /opt/netsecai/{app-state,shared,runtime},
# checks that the secret files exist, installs the systemd units and the shared PostgreSQL files,
# and starts PostgreSQL and the backup timer. It never builds or starts the app (the first
# `bun run deploy` does), never writes secrets and never imports data.
#
# Safe on a host that runs other services: collision checks run before package installation or
# any host mutation. Existing Docker is used as is (never replaced, upgraded or restarted).
set -euo pipefail
SAFETY_LIB=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/server/safety.sh
. "$SAFETY_LIB"
safety_preflight
SRC=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
. "$SRC/deploy-netsec/server/lib.sh"
BUN_VERSION=$(tr -d '[:space:]' < "$SRC/.bun-version")   # one pinned version everywhere (CI reads the same file)

[ "$(id -u)" -eq 0 ] || die "run as root"

# Read and validate only the isolation settings before the first apt/filesystem change.
[ -s "$SECRETS/postgres.env" ] || die "missing $SECRETS/postgres.env (template: deploy-netsec/env/postgres.env.example)"
validate_database_environment
POSTGRES_HOST_PORT=$(env_value POSTGRES_HOST_PORT postgres.env)
POSTGRES_HOST_PORT=${POSTGRES_HOST_PORT:-55432}
[[ "$POSTGRES_HOST_PORT" =~ ^[0-9]+$ ]] && [ "$POSTGRES_HOST_PORT" -ge 1024 ] && [ "$POSTGRES_HOST_PORT" -le 65535 ] \
  || die "POSTGRES_HOST_PORT in $SECRETS/postgres.env must be an unprivileged TCP port"
for reserved in "$API_PORT" "$WEB_PORT"; do
  [ "$POSTGRES_HOST_PORT" != "$reserved" ] || die "POSTGRES_HOST_PORT=$POSTGRES_HOST_PORT collides with a NetSec AI application port"
done

# All proposed host ports are checked before apt or filesystem mutation. A listener is accepted
# only when the corresponding NetSec unit is already active (an idempotent repair install).
for binding in "$WEB_PORT:netsecai-web.service" "$API_PORT:netsecai-api.service" "$POSTGRES_HOST_PORT:netsecai-postgres.service"; do
  port=${binding%%:*}
  unit=${binding#*:}
  if ss -ltnH "( sport = :$port )" | grep . >/dev/null && ! systemctl is-active --quiet "$unit" 2>/dev/null; then
    ss -ltnpH "( sport = :$port )" >&2
    die "port $port is already in use and is not owned by active $unit"
  fi
done

NETSECAI_BRAND_NEW_DATABASE=0
if command -v docker >/dev/null 2>&1; then
  existing=0
  for resource in \
    "container $DB_CONTAINER" \
    "network $DB_NETWORK" \
    "volume $DB_VOLUME"; do
    set -- $resource
    if docker "$1" inspect "$2" >/dev/null 2>&1; then existing=$((existing + 1)); fi
  done
  if [ "$existing" -eq 0 ]; then
    NETSECAI_BRAND_NEW_DATABASE=1
  elif [ "$existing" -ne 3 ] || ! systemctl is-active --quiet netsecai-postgres.service 2>/dev/null; then
    die "partial or inactive NetSec database resources exist; inventory ownership before installing"
  else
    assert_database_identity
  fi
else
  NETSECAI_BRAND_NEW_DATABASE=1
fi

log "1/5 packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates unzip xz-utils git python3.12 python3.12-venv >/dev/null
# OCR for students' scanned PDFs and photos (app/files/extract.py), English and Hindi.
apt-get install -y -qq tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin >/dev/null
if command -v docker >/dev/null 2>&1; then
  note "docker already installed ($(docker --version)); leaving it untouched"
else
  apt-get install -y -qq docker.io docker-compose-v2 >/dev/null
  systemctl enable --now docker
fi
docker info >/dev/null 2>&1 || die "the docker daemon is not reachable"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is missing; install the compose plugin that matches this Docker"

log "2/5 bun $BUN_VERSION"
if [ ! -x "$BUN" ] || [ "$("$BUN" --version)" != "$BUN_VERSION" ]; then
  curl -fsSL https://bun.sh/install | BUN_INSTALL=/root/.bun bash -s "bun-v$BUN_VERSION" >/dev/null
fi
[ "$("$BUN" --version)" = "$BUN_VERSION" ] || die "bun $BUN_VERSION could not be installed"

log "3/5 directories and the pinned Node runtime"
install -d -m 755 "$STATE" "$SHARED" "$RUNTIMES"
ensure_node "$SRC"
note "Node $(tr -d '[:space:]' < "$SRC/.node-version") ready in $RUNTIMES"

log "4/5 secrets"
install -d -m 700 "$SECRETS"
missing=0
for f in postgres.env migration.env api.env web.env; do
  if [ ! -s "$SECRETS/$f" ]; then note "missing $SECRETS/$f (template: deploy-netsec/env/$f.example)"; missing=1; fi
done
[ "$missing" -eq 0 ] || die "fill the files above (docs/deployment.md), then re-run"
chmod 600 "$SECRETS"/*.env

log "5/5 units and database"
bash "$SRC/deploy-netsec/server/install-units.sh" "$SRC"
systemctl enable --now netsecai-postgres.service netsecai-pg-backup.timer
if [ "$NETSECAI_BRAND_NEW_DATABASE" = 1 ]; then
  export NETSECAI_BRAND_NEW_DATABASE
  initialize_database_identity
else
  assert_database_identity
fi
note "PostgreSQL and the nightly backup are running."
log "host ready. From a workstation: git push origin main && bun run deploy (docs/deployment.md)"
