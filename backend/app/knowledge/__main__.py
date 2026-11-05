"""Knowledge worker entry point: one bounded tick (systemd oneshot, netsecai-knowledge.service, every minute).

Each tick queues ready files, asks admission whether the shared host has room for a book now (and for how many
OCR processes), processes at most one revision, and logs one JSON line saying what it did (counts and reasons,
never book text). SIGTERM (a stop or a deploy) cancels the revision in flight: it is recorded as retryable and a
later tick resumes it, its vectors already stored are reused.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import asdict
from functools import partial

from app.db.session import Database
from app.knowledge.admission import admit
from app.knowledge.processing import process_revision, queue_ready_files
from app.knowledge.worker import run_once
from app.logging import configure_logging, get_logger
from app.rag.embeddings import build_embeddings, is_configured
from app.settings import Settings

log = get_logger(__name__)


async def _main() -> dict:
    settings = Settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    embeddings = build_embeddings(settings)
    if not settings.files_runner or not is_configured(embeddings):
        raise RuntimeError("knowledge_processing_not_configured")
    report: dict[str, object] = {}
    try:
        report["queued"] = await queue_ready_files(database, settings, embeddings)
        admission = await admit(database)
        report["admission"] = asdict(admission)
        if admission.admitted:
            processor = partial(process_revision, database, settings, embeddings, ocr_workers=admission.ocr_workers)
            report.update(await run_once(database, processor))
        return report
    finally:
        log.info("knowledge.worker", extra={"report": report})
        await database.dispose()


def main() -> None:
    loop = asyncio.new_event_loop()
    task = loop.create_task(_main())
    with contextlib.suppress(NotImplementedError):  # not on Windows; the host is Linux
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        log.warning("knowledge.worker stopped by SIGTERM; the revision in flight is retryable")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
