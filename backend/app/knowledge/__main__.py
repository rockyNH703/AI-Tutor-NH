"""Knowledge worker entry point."""

from __future__ import annotations

import asyncio
from functools import partial

from app.db.session import Database
from app.knowledge.processing import process_revision, queue_ready_files
from app.knowledge.worker import run_once
from app.logging import configure_logging
from app.rag.embeddings import build_embeddings, is_configured
from app.settings import Settings


async def _main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    embeddings = build_embeddings(settings)
    if not settings.files_runner or not is_configured(embeddings):
        raise RuntimeError("knowledge_processing_not_configured")
    try:
        await queue_ready_files(database, settings, embeddings)
        await run_once(database, partial(process_revision, database, settings, embeddings))
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
