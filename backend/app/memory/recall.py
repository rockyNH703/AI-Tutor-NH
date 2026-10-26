from __future__ import annotations

import asyncio
import time
import uuid

from langchain_core.embeddings import Embeddings

from app.db.session import Database
from app.logging import get_logger
from app.memory.repository import MemoryRepository
from app.memory.service import RecalledMemory, rank_candidates
from app.rag.embeddings import embedding_model_id
from app.settings import Settings

log = get_logger("app.memory")


class MemoryRecall:
    """Optional chat recall with its own short-lived database session and hard failure boundary."""

    def __init__(self, db: Database, settings: Settings, embeddings: Embeddings) -> None:
        self.db = db
        self.settings = settings
        self.embeddings = embeddings

    async def recall(self, user_id: uuid.UUID, query: str) -> tuple[RecalledMemory, ...]:
        if not self.settings.memory_recall_enabled or not query.strip():
            return ()
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.memory_recall_timeout_s):
                embedding = await self.embeddings.aembed_query(query)
                async with self.db.sessionmaker() as session:
                    candidates = await MemoryRepository(session).recall_candidates(
                        user_id,
                        embedding,
                        embedding_model_id(self.embeddings),
                        limit=self.settings.memory_recall_candidates,
                    )
            recalled = rank_candidates(candidates, self.settings)
        except Exception as error:  # noqa: BLE001 - optional recall must never fail tutoring
            log.warning("memory recall failed", extra={"error_type": type(error).__name__})
            return ()
        log.info(
            "memory recall",
            extra={"items": len(recalled), "total_ms": int((time.monotonic() - started) * 1000)},
        )
        return tuple(recalled)
