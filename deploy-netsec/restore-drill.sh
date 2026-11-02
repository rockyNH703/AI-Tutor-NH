#!/usr/bin/env bash
#
# Local-only recovery rehearsal for an explicit PostgreSQL custom-format archive.
# Restores into a freshly created disposable *_test database, applies this checkout's
# migrations, runs offline application-readiness checks, and drops the database on exit.
#
# No .env file or default target is read. Supply TEST_DATABASE_URL explicitly and put
# the password in PGPASSWORD_FILE (preferred) or an already-exported PGPASSWORD.
set -euo pipefail
umask 077

usage() {
  cat >&2 <<'EOF'
Usage: TEST_DATABASE_URL=postgresql+asyncpg://USER@127.0.0.1:TEST_PORT/NAME_test \
       PGPASSWORD_FILE=/path/to/password deploy-netsec/restore-drill.sh ARCHIVE.dump

The archive must be a readable PostgreSQL custom-format archive. The target must be an
explicit loopback *_test database. It is dropped before the drill and again on exit.
EOF
  exit 2
}

die() {
  printf 'restore-drill: %s\n' "$*" >&2
  exit 1
}

[ "$#" -eq 1 ] || usage
ARCHIVE=$1
[ -f "$ARCHIVE" ] && [ -r "$ARCHIVE" ] || die "archive must be a readable regular file"
[ ! -L "$ARCHIVE" ] || die "archive must not be a symlink"

: "${TEST_DATABASE_URL:?TEST_DATABASE_URL must be set explicitly}"
case "$TEST_DATABASE_URL" in
  postgresql://*|postgresql+asyncpg://*) ;;
  *) die "TEST_DATABASE_URL must use postgresql:// or postgresql+asyncpg://" ;;
esac
case "$TEST_DATABASE_URL" in
  *\?*|*\#*) die "TEST_DATABASE_URL must not contain query parameters or a fragment" ;;
esac

URL_REST=${TEST_DATABASE_URL#*://}
AUTHORITY=${URL_REST%%/*}
DB_NAME=${URL_REST#*/}
[ "$AUTHORITY" != "$URL_REST" ] && [ -n "$DB_NAME" ] && [ "$DB_NAME" != "$URL_REST" ] \
  || die "TEST_DATABASE_URL must include an explicit database name"
case "$AUTHORITY" in
  *@*) ;;
  *) die "TEST_DATABASE_URL must include an explicit database user" ;;
esac
USERINFO=${AUTHORITY%@*}
HOSTPORT=${AUTHORITY##*@}
[ -n "$USERINFO" ] && [ -n "$HOSTPORT" ] || die "TEST_DATABASE_URL is incomplete"
case "$USERINFO" in
  *:*) die "do not put a password in TEST_DATABASE_URL; use PGPASSWORD_FILE or PGPASSWORD" ;;
esac
case "$DB_NAME" in
  *_test) ;;
  *) die "database name must end in _test" ;;
esac
case "$DB_NAME" in
  *[!a-z0-9_]*) die "database name must contain only lowercase letters, digits, and underscores" ;;
esac
case "$DB_NAME" in
  netsecai*|aichat*|padhai*|*production*|*prod*|*staging*|*stage*|*live*)
    die "refusing a production-looking database name"
    ;;
esac

case "$HOSTPORT" in
  127.0.0.1|localhost|::1|127.0.0.1:[0-9]*|localhost:[0-9]*|'[::1]'|'[::1]':[0-9]*) ;;
  *) die "TEST_DATABASE_URL host must be loopback (127.0.0.1, localhost, or ::1)" ;;
esac

case "$TEST_DATABASE_URL" in
  *@localhost/*|*@localhost:[0-9]*/*)
    die "localhost must include an explicit port so the drill cannot fall back to a local socket"
    ;;
esac
case "$HOSTPORT" in
  *:*) ;;
  *) die "TEST_DATABASE_URL must include an explicit port" ;;
esac

if [ -n "${PGPASSWORD_FILE:-}" ]; then
  [ -r "$PGPASSWORD_FILE" ] || die "PGPASSWORD_FILE must be a readable password file"
  [ ! -L "$PGPASSWORD_FILE" ] || die "PGPASSWORD_FILE must not be a symlink"
  [ -f "$PGPASSWORD_FILE" ] || die "PGPASSWORD_FILE must be a regular file"
  PGPASSWORD=$(cat -- "$PGPASSWORD_FILE")
  export PGPASSWORD
elif [ -z "${PGPASSWORD:-}" ]; then
  die "set PGPASSWORD_FILE (preferred) or an already-exported PGPASSWORD"
fi
[ -n "$PGPASSWORD" ] || die "database password must not be empty"

for command in pg_restore psql dropdb createdb; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done

SCRIPT_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
BACKEND="$ROOT/backend"
PYTHON=${RESTORE_DRILL_PYTHON:-$BACKEND/.venv/bin/python}
[ -x "$PYTHON" ] || die "backend Python not found at $PYTHON (set RESTORE_DRILL_PYTHON explicitly)"

PSQL_URL=${TEST_DATABASE_URL/postgresql+asyncpg:\/\//postgresql:\/\/}
BASE_URL=${PSQL_URL%/*}
ADMIN_URL="$BASE_URL/postgres"
DISPOSABLE_CREATED=0
cleanup() {
  rc=$?
  if [ "$DISPOSABLE_CREATED" = 1 ]; then
    dropdb --if-exists --force --maintenance-db="$ADMIN_URL" "$DB_NAME" >/dev/null 2>&1 \
      || printf 'restore-drill: cleanup failed; drop disposable database %s manually\n' "$DB_NAME" >&2
  fi
  unset PGPASSWORD
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

printf 'restore-drill: validating custom-format archive\n'
pg_restore --list "$ARCHIVE" >/dev/null || die "archive is not a readable PostgreSQL custom-format archive"

printf 'restore-drill: creating disposable test database %s\n' "$DB_NAME"
dropdb --if-exists --force --maintenance-db="$ADMIN_URL" "$DB_NAME"
createdb --maintenance-db="$ADMIN_URL" --template=template0 "$DB_NAME"
DISPOSABLE_CREATED=1

printf 'restore-drill: restoring archive\n'
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$PSQL_URL" "$ARCHIVE"

printf 'restore-drill: applying current migrations\n'
(
  cd "$BACKEND"
  TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  APP_ENV=test \
  DATABASE_URL="$TEST_DATABASE_URL" \
  MIGRATION_DATABASE_URL="$TEST_DATABASE_URL" \
  LLM_PROVIDER=fake \
  EMBEDDING_PROVIDER=fake \
  EMAIL_BACKEND=memory \
  INTENT_ROUTER=off \
  STUDY_SCOPE=off \
  VOICE_MODE=off \
  NCERT_KB_BASE_URL= \
  NCERT_KB_API_KEY= \
  "$PYTHON" -m alembic upgrade head
)

printf 'restore-drill: checking schema and offline application readiness\n'
RESTORE_DRILL_PYTHON="$PYTHON" \
RESTORE_DRILL_BACKEND="$BACKEND" \
RESTORE_DRILL_DATABASE_URL="$TEST_DATABASE_URL" \
"$PYTHON" - <<'PY'
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

backend = Path(os.environ["RESTORE_DRILL_BACKEND"])
sys.path.insert(0, str(backend))

from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.emails import MemoryEmailSender
from app.llm.fake import FakeProvider
from app.main import create_app
from app.settings import Settings

url = os.environ["RESTORE_DRILL_DATABASE_URL"]
config = Config(str(backend / "alembic.ini"))
config.set_main_option("script_location", str(backend / "alembic"))
heads = ScriptDirectory.from_config(config).get_heads()
if len(heads) != 1:
    raise RuntimeError(f"expected one migration head, found {len(heads)}")


async def check() -> None:
    engine = create_async_engine(url, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("select version_num from alembic_version limit 1"))
            vector = await connection.scalar(text("select extname from pg_extension where extname = 'vector'"))
            users = await connection.scalar(text("select count(*) from users"))
        if revision != heads[0]:
            raise RuntimeError(f"database revision {revision!r} does not match current head {heads[0]!r}")
        if vector != "vector":
            raise RuntimeError("pgvector extension is unavailable after restore")

        settings = Settings(
            _env_file=None,
            app_env="test",
            app_name="NetSec AI",
            database_url=url,
            email_backend="memory",
            llm_provider="fake",
            embedding_provider="fake",
            intent_router="off",
            study_scope="off",
            voice_mode="off",
            secret_key="restore-drill-secret-key-that-is-long-enough",
            auth_internal_token="restore-drill-internal-token-that-is-long-enough",
            question_bank_tests=False,
            files_enabled=False,
            rag_enabled=False,
            memory_recall_enabled=False,
            memory_automatic_proposals="off",
            jobs_enabled=False,
        )
        app = create_app(settings, llm=FakeProvider(), email_sender=MemoryEmailSender())
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://restore-drill") as client:
                health = await client.get("/health")
                ready = await client.get("/ready")
        if health.status_code != 200 or health.json().get("status") != "ok":
            raise RuntimeError("application health check failed")
        if ready.status_code != 200 or ready.json().get("database") is not True:
            raise RuntimeError("application readiness check failed")
        print(json.dumps({"database": "ready", "migration": revision, "users_restored": users}))
    finally:
        await engine.dispose()


asyncio.run(check())
PY

printf 'restore-drill: PASS; cleanup will drop %s\n' "$DB_NAME"
