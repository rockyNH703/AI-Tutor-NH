"""`python -m app.jobs`: one run of the jobs worker (app/jobs/runner.py), then exit.

Exit status 0 when every requested step succeeded, the run was disabled, or another run held the
lock. Exit status 1 when the run could not start or any requested step failed. Failed steps remain
independent: later steps still run, and only their bounded names are added to the summary.
"""

from __future__ import annotations

import asyncio
import sys

from app.auth.emails import build_email_sender
from app.db.session import Database
from app.jobs.runner import run_once
from app.llm.factory import build_llm
from app.logging import configure_logging, get_logger
from app.settings import Settings


async def _run(settings: Settings) -> dict:
    db = Database(settings)
    try:
        return await run_once(settings, db, build_email_sender(settings), llm=build_llm(settings))
    finally:
        await db.dispose()


def main() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    log = get_logger("app.jobs")
    try:
        settings.validate_for_env()
        report = asyncio.run(_run(settings))
    except Exception as error:  # noqa: BLE001 - reported, then a non-zero exit for systemd
        log.exception("jobs run could not run", extra={"error_type": type(error).__name__})
        return 1
    failed_steps = tuple(name for name, result in report.items() if result == "failed")
    log.info("jobs run finished", extra={"report": report, "failed_steps": failed_steps})
    return 1 if failed_steps else 0


if __name__ == "__main__":
    sys.exit(main())
