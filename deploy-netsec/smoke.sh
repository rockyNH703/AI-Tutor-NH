#!/usr/bin/env bash
set -euo pipefail
SAFETY_LIB=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/server/safety.sh
. "$SAFETY_LIB"
safety_preflight
HOST=${NETSEC_DEPLOY_HOST:-}
[ -n "$HOST" ] || ns_die "set NETSEC_DEPLOY_HOST=user@host"
exec bash deploy-netsec/status.sh --host "$HOST"
