from functools import cache
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])

# Production runs the checkout /opt/aichat/app, with a RELEASE file at its root (written by
# deploy/server/update.sh on every deploy). It is read once, at start: the value is the commit
# this process started from.
RELEASE_FILE = Path(__file__).resolve().parents[2] / "RELEASE"


def read_release(path: Path = RELEASE_FILE) -> str:
    """The commit this code was released as, or "development" outside a release."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("commit="):
                return line.split("=", 1)[1].strip() or "development"
    except OSError:
        pass
    return "development"


RELEASE = read_release()  # read once: a running process never changes release


@router.get("/health", summary="Liveness: the process is up, the release it runs, and whether files work")
async def health(request: Request) -> dict:
    worker = getattr(request.app.state, "file_worker", None)
    files = "available" if worker is not None and worker.available else "unavailable"
    return {"status": "ok", "release": RELEASE, "files": files}


@cache
def code_migrations() -> tuple[str, frozenset[str]]:
    """This code's migration head and every revision it knows, read once from backend/alembic."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(config)
    (head,) = script.get_heads()
    return head, frozenset(revision.revision for revision in script.walk_revisions())


def schema_state(revision: str | None) -> str:
    """current: at this code's head. behind: an older revision this code still has to apply (not ready).
    ahead: a revision this code does not know, from a newer release (a rollback; compatible by rule)."""
    head, known = code_migrations()
    if revision == head:
        return "current"
    return "behind" if revision is None or revision in known else "ahead"


async def database_revision(request: Request) -> str | None:
    async with request.app.state.db.engine.connect() as connection:
        return await connection.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))


@router.get("/ready", summary="Readiness: the database is reachable and its schema is not behind this code")
async def ready(request: Request) -> JSONResponse:
    database_ok = await request.app.state.db.ping()
    schema = "unknown"
    if database_ok:
        try:
            schema = schema_state(await database_revision(request))
        except Exception:  # noqa: BLE001 - readiness reports, never raises (no alembic_version: not migrated)
            schema = "behind"
    ok = database_ok and schema != "behind"
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "unavailable", "database": database_ok, "schema": schema},
    )
