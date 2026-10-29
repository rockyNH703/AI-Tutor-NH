#!/usr/bin/env bash
#
# What production runs, compared with GitHub main. Read-only:
#
#   bun run status:netsec        (exits 1 unless everything is healthy and "Status: MATCH")
#
# Runs deploy-netsec/server/status.sh on the host over ssh (units, health, what each part runs, the
# public URL, the last deploys), then prints GitHub main, the production checkout, the commit
# the backend and the frontend run, and MATCH or MISMATCH. GitHub CI's verdict on the checkout
# is shown as information only. --host <user@host>, or $NETSEC_DEPLOY_HOST (one of them is required).
set -uo pipefail
HOST=${NETSEC_DEPLOY_HOST:-}
[ "${1:-}" = --host ] && HOST=${2:?--host needs a value}
[ -n "$HOST" ] || { echo "STOP: set NETSEC_DEPLOY_HOST=user@host or pass --host" >&2; exit 1; }
cd "$(git rev-parse --show-toplevel)" || exit 1
. deploy-netsec/summary.sh
SOURCE=https://github.com/rockyNH703/AI-Tutor-NH.git
[ "${DEPLOY_REHEARSAL:-0}" = 1 ] && SOURCE=${DEPLOY_REHEARSAL_SOURCE:?}

OUT=$(ssh "$HOST" 'bash /opt/netsecai/app/deploy-netsec/server/status.sh')
RC=$?
printf '%s\n' "$OUT" | grep -v '^@@'
MAIN=""
git fetch -q "$SOURCE" main 2> /dev/null && MAIN=$(git rev-parse FETCH_HEAD)
echo
print_summary "$MAIN" "$(printf '%s\n' "$OUT" | grep '^@@STATUS ' | tail -1)"
CHECKOUT=$(printf '%s\n' "$OUT" | sed -n 's/^@@STATUS checkout=\([0-9a-f]*\).*/\1/p' | tail -1)
if [ -n "$CHECKOUT" ] && [ "${DEPLOY_REHEARSAL:-0}" != 1 ]; then
  echo "GitHub CI (advisory): $(timeout 20 bun deploy-netsec/ci-gate.mjs --report "$CHECKOUT" 2> /dev/null || echo unavailable)"
fi
[ "$RC" = 0 ] && [ "$SUMMARY_MATCH" = 1 ]
