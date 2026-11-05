#!/usr/bin/env bash
#
# Install the systemd units and the shared PostgreSQL files from a copy of this repository.
# Called by deploy-netsec/server/update.sh on every deploy and by install.sh on a new host. Idempotent.
#
#   bash deploy-netsec/server/install-units.sh <directory with deploy-netsec/>
#
# - Units run the production checkout /opt/netsecai/app. They are only rewritten (and systemd
#   reloaded) when their content changes; each rewrite prints "updated <unit>", which is how
#   update.sh knows which service to restart. Replaced files are kept as <unit>$BACKUP_SUFFIX.
# - The database is NOT part of app deploys: its compose file lives in
#   /opt/netsecai/shared/postgres and is installed once. A release with a different compose
#   file prints a warning instead of replacing it, because recreating the database container
#   must be a deliberate step. Its NetSec AI-specific project, network and volume names are pinned.
# - Never restarts anything.
set -euo pipefail
SAFETY_LIB=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/safety.sh
. "$SAFETY_LIB"
safety_preflight
. "$(dirname "$(readlink -f "$0")")/lib.sh"

SRC=${1:?usage: install-units.sh <directory with deploy-netsec/>}
[ "$(id -u)" -eq 0 ] || die "run as root"

SUFFIX=${BACKUP_SUFFIX:-.before-$(date -u +%Y%m%d%H%M%S)}  # replaced units are kept as <unit>$SUFFIX
changed=0
for unit in "$SRC"/deploy-netsec/systemd/netsecai-*.service "$SRC"/deploy-netsec/systemd/netsecai-*.timer \
  "$SRC"/deploy-netsec/systemd/netsecai-*.slice; do
  target="/etc/systemd/system/$(basename "$unit")"
  if ! cmp -s "$unit" "$target"; then
    [ -f "$target" ] && cp -p "$target" "$target$SUFFIX"
    install -m 644 "$unit" "$target"
    note "updated $(basename "$unit")"
    changed=1
  fi
done
if [ "$changed" = 1 ]; then systemctl daemon-reload; fi
systemctl enable --quiet netsecai-postgres.service netsecai-pg-backup.timer
# The app units start at boot once production has a checkout (a fresh host has none yet).
if [ -d "$APP/.git" ]; then
  systemctl enable --quiet netsecai-api.service netsecai-web.service
  # The owner's lasting off switch for the Knowledge Library worker (docs/operations.md): while the file exists,
  # every deploy keeps the timer disabled and stopped instead of enabling it again.
  if [ -e "$SECRETS/knowledge.disabled" ]; then systemctl disable --now --quiet netsecai-knowledge.timer; else systemctl enable --quiet netsecai-knowledge.timer; fi
fi

install -d -m 755 "$SHARED/postgres"
compose="$SHARED/postgres/docker-compose.yml"
if [ ! -f "$compose" ]; then
  install -m 644 "$SRC/deploy-netsec/compose/docker-compose.yml" "$compose"
  note "installed $compose"
elif ! cmp -s "$SRC/deploy-netsec/compose/docker-compose.yml" "$compose"; then
  note "WARNING: this release's deploy-netsec/compose/docker-compose.yml differs from $compose."
  note "         The database container was left alone. To apply it deliberately, see docs/deployment.md."
fi
for helper in lib.sh safety.sh; do
  if ! cmp -s "$SRC/deploy-netsec/server/$helper" "$SHARED/postgres/server/$helper"; then
    install -d -m 755 "$SHARED/postgres/server"
    install -m 644 "$SRC/deploy-netsec/server/$helper" "$SHARED/postgres/server/$helper"
    note "installed $SHARED/postgres/server/$helper"
  fi
done
if ! cmp -s "$SRC/deploy-netsec/backup.sh" "$SHARED/postgres/backup.sh"; then
  install -m 755 "$SRC/deploy-netsec/backup.sh" "$SHARED/postgres/backup.sh"
  note "installed $SHARED/postgres/backup.sh"
fi
if ! cmp -s "$SRC/deploy-netsec/database-grants.sh" "$SHARED/postgres/database-grants.sh"; then
  install -m 755 "$SRC/deploy-netsec/database-grants.sh" "$SHARED/postgres/database-grants.sh"
  note "installed $SHARED/postgres/database-grants.sh"
fi
