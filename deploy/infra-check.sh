#!/usr/bin/env bash
#
# Compare the host against INFRASTRUCTURE.md. Read-only.
#   bash /opt/aichat/app/deploy/infra-check.sh
#
# First what production runs (deploy/server/status.sh: the checkout, the code each part runs,
# health), then the host.
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/server/lib.sh"
fail=0
ok()  { printf '  ok    %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; fail=1; }
API="http://127.0.0.1:$API_PORT"
POSTGRES_HOST_PORT=$(env_value POSTGRES_HOST_PORT postgres.env)
POSTGRES_HOST_PORT=${POSTGRES_HOST_PORT:-55432}

bash "$(dirname "$(readlink -f "$0")")/server/status.sh" || fail=1

echo
echo "services"
for u in aichat-postgres.service aichat-api.service aichat-web.service aichat-pg-backup.timer; do
  systemctl is-active --quiet "$u" && ok "$u active" || bad "$u not active"
done

echo "network exposure (loopback only)"
for port in "$WEB_PORT" "$API_PORT" "$POSTGRES_HOST_PORT"; do
  if ss -ltnH "( sport = :$port )" | awk '{print $4}' | grep -vE '^(127\.0\.0\.1|\[::1\]):' >/dev/null; then
    bad "port $port listens beyond loopback"
  else
    ok "port $port bound to loopback"
  fi
done

echo "health"
curl -fsS "$API/ready" >/dev/null && ok "api /ready (database reachable)" || bad "api /ready"
curl -sS -o /dev/null -w '%{http_code}' "$API/docs" | grep -q 404 && ok "api docs hidden" || bad "api docs exposed"
curl -fsS -o /dev/null "http://127.0.0.1:$WEB_PORT/login" && ok "web app serves /login" || bad "web app not answering on :$WEB_PORT"
check_chain "$WEB_PORT" aichat-api >/dev/null && ok "web -> API -> database" || bad "web app cannot reach the API"

echo "database isolation"
[ -f "$SHARED/postgres/docker-compose.yml" ] && ok "compose file in $SHARED/postgres" || bad "missing $SHARED/postgres/docker-compose.yml"
project=$(docker compose --env-file "$SECRETS/postgres.env" -f "$SHARED/postgres/docker-compose.yml" config --format json 2>/dev/null \
  | python3.12 -c 'import json,sys; print(json.load(sys.stdin).get("name", ""))' 2>/dev/null)
[ "$project" = padhai-postgres ] && ok "dedicated Compose project padhai-postgres" || bad "unexpected Compose project ${project:-unknown}"
docker volume inspect padhai_postgres_data >/dev/null 2>&1 && ok "dedicated volume padhai_postgres_data" || bad "missing dedicated volume padhai_postgres_data"
docker network inspect padhai_postgres_network >/dev/null 2>&1 && ok "dedicated network padhai_postgres_network" || bad "missing dedicated network padhai_postgres_network"
unexpected_schemas=$(docker compose --env-file "$SECRETS/postgres.env" -f "$SHARED/postgres/docker-compose.yml" \
  exec -T postgres psql -U "$(env_value POSTGRES_USER postgres.env)" -d "$(env_value POSTGRES_DB postgres.env)" -tAc \
  "SELECT string_agg(schema_name, ',' ORDER BY schema_name) FROM information_schema.schemata WHERE schema_name NOT IN ('public', 'information_schema', 'pg_catalog', 'pg_toast') AND schema_name NOT LIKE 'pg_temp_%' AND schema_name NOT LIKE 'pg_toast_temp_%'" 2>/dev/null | tr -d '[:space:]')
[ -z "$unexpected_schemas" ] && ok "database contains only the PadhAI public schema" || bad "unexpected database schemas: $unexpected_schemas"
latest=$(ls -1t /var/backups/padhai/padhai-*.dump 2>/dev/null | head -1)
if [ -n "$latest" ] && [ "$(find "$latest" -mtime -2 | wc -l)" -eq 1 ]; then ok "recent backup $latest"; else bad "no backup in the last 2 days"; fi
free_gb=$(( $(df --output=avail -B1 / | tail -1 | tr -dc '0-9') / 1000000000 ))
[ "$free_gb" -ge 5 ] && ok "disk: ${free_gb} GB free (uploads stop under 5 GB)" || bad "disk: only ${free_gb} GB free: file uploads are refused and backups may fail"

echo "files (app/files)"
langs=$(tesseract --list-langs 2>/dev/null | tail -n +2)
if grep -qx eng <<<"$langs" && grep -qx hin <<<"$langs"; then ok "tesseract with eng and hin (OCR)"; else bad "tesseract with eng and hin is missing (apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin)"; fi
keep=$(sed -n 's/^KEEP_DAYS=//p' /etc/aichat/postgres.env 2>/dev/null)
[ "${keep:-7}" -le 7 ] 2>/dev/null && ok "backups kept ${keep:-7} days" || bad "KEEP_DAYS=${keep} in postgres.env: keep it at 7 (dumps share the database's disk)"
files=$(curl -fsS --max-time 5 "$API/health" 2>/dev/null | grep -o '"files":"[a-z]*"' | cut -d'"' -f4)
[ "$files" = available ] && ok "files available (sandbox self-test passed)" || bad "files ${files:-unknown}: see journalctl -u aichat-api (files self-test)"

exit $fail
