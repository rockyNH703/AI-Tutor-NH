#!/usr/bin/env bash
#
# Roll production back to an earlier commit, by the deploy's own path (deploy-netsec/deploy.sh): the
# commit is pushed to production Git, the checkout moves to it, and only the parts that differ
# are rebuilt or restarted. The deploy tooling is materialized from an immutable fetch of canonical
# main (or the explicit rehearsal repository), never from the workstation checkout's mutable HEAD.
# A frontend going back to the build the last deploy replaced is swapped back without building.
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
CANONICAL=https://github.com/rockyNH703/AI-Tutor-NH.git
PRODUCTION=10.1.50.94
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

if [ "${DEPLOY_REHEARSAL:-0}" = 1 ]; then
  SOURCE=${DEPLOY_REHEARSAL_SOURCE:?DEPLOY_REHEARSAL needs DEPLOY_REHEARSAL_SOURCE (a local repository)}
  case "$HOST" in *"$PRODUCTION"*) ns_die "rehearsal mode never rolls back production ($HOST)" ;; esac
  TOOL_SHA=$(git -C "$SOURCE" rev-parse --verify main^{commit}) \
    || ns_die "cannot resolve immutable rollback tooling from $SOURCE main"
else
  SOURCE=$CANONICAL
  TOOL_SHA=$(git ls-remote --exit-code "$SOURCE" refs/heads/main | awk 'NR == 1 { print $1 }') \
    || ns_die "cannot resolve immutable rollback tooling from $SOURCE main"
  [ -n "$TOOL_SHA" ] || ns_die "$SOURCE main did not resolve to a commit"
  case "$TOOL_SHA" in
    *[!0-9a-f]*|'') ns_die "$SOURCE main resolved to an invalid commit: $TOOL_SHA" ;;
  esac
fi

TOOLING=$(git rev-parse --git-path "netsec-rollback-tooling.$$")
case "$TOOLING" in
  /*|[A-Za-z]:[\\/]*) ;;
  *) TOOLING="$(git rev-parse --show-toplevel)/$TOOLING" ;;
esac
cleanup() { rm -rf -- "$TOOLING"; }
trap cleanup EXIT HUP INT TERM
(umask 077 && mkdir "$TOOLING") || ns_die "cannot create isolated rollback tooling directory: $TOOLING"
git -C "$TOOLING" init -q
git -C "$TOOLING" fetch -q --no-tags "$SOURCE" "$TOOL_SHA" \
  || ns_die "cannot fetch rollback tooling $TOOL_SHA from $SOURCE"
git -C "$TOOLING" checkout -q --detach FETCH_HEAD
[ "$(git -C "$TOOLING" rev-parse HEAD)" = "$TOOL_SHA" ] \
  || ns_die "fetched rollback tooling does not match $TOOL_SHA"
[ -f "$TOOLING/deploy-netsec/deploy.sh" ] \
  || ns_die "$SOURCE main ($TOOL_SHA) has no NetSec deploy tooling"

if [ -z "$TARGET" ]; then
  TARGET=$(ssh "$HOST" 'cat /opt/netsecai/app-state/previous 2>/dev/null' || true)
  [ -n "$TARGET" ] || { echo "STOP: $HOST has no previous deploy recorded; name a commit: bun run rollback:netsec -- <commit>" >&2; exit 1; }
fi
TARGET_SHA=$(git -C "$TOOLING" rev-parse --verify --quiet "$TARGET^{commit}") \
  || ns_die "$TARGET is not a commit in $SOURCE main history"
git -C "$TOOLING" merge-base --is-ancestor "$TARGET_SHA" "$TOOL_SHA" \
  || ns_die "$TARGET_SHA is not an earlier commit of $SOURCE main"

echo "==> rollback tooling: $TOOL_SHA from $SOURCE main"
(cd "$TOOLING" && bash deploy-netsec/deploy.sh --host "$HOST" --rollback-to "$TARGET_SHA")
