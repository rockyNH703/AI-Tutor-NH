#!/usr/bin/env bash
#
# Roll production back to an earlier commit, by the deploy's own path (deploy-netsec/deploy.sh): the
# commit is pushed to production Git, the checkout moves to it, and only the parts that differ
# are rebuilt or restarted. A frontend going back to the build the last deploy replaced is
# swapped back without building.
#
#   bun run rollback:netsec                  the commit that was live before the last deploy
#   bun run rollback:netsec -- <commit>      any earlier commit (see the host's HISTORY or git log)
#   (--host <user@host>, or $NETSEC_DEPLOY_HOST: one of them is required)
#
# The database is never rolled back: migrations are backward compatible (docs/deployment.md).
# Afterwards `bun run status:netsec` says MISMATCH (production is not GitHub main) until main is
# deployed again, usually with a fix.
set -euo pipefail
SAFETY_LIB=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/server/safety.sh
. "$SAFETY_LIB"
safety_preflight
HOST=${NETSEC_DEPLOY_HOST:-}
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST=${2:?--host needs a value}; shift 2 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) TARGET=$1; shift ;;
  esac
done
[ -n "$HOST" ] || ns_die "set NETSEC_DEPLOY_HOST=user@host or pass --host"
cd "$(git rev-parse --show-toplevel)"
if [ -z "$TARGET" ]; then
  TARGET=$(ssh "$HOST" 'cat /opt/netsecai/app-state/previous 2>/dev/null' || true)
  [ -n "$TARGET" ] || { echo "STOP: $HOST has no previous deploy recorded; name a commit: bun run rollback:netsec -- <commit>" >&2; exit 1; }
fi
exec bash deploy-netsec/deploy.sh --host "$HOST" --rollback-to "$TARGET"
