from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MEMORY_PROCESSOR_VERSION = "v1"
MEMORY_PROMPT_VERSION = "v1"
MEMORY_SCHEMA_VERSION = "v1"


async def lock_memory_user(session: AsyncSession, user_id: object) -> None:
    """Serialize consent/deletion boundaries with provider dispatch and memory mutation."""

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 0))"),
        {"user_id": str(user_id)},
    )


@dataclass(frozen=True)
class ExtractionCandidate:
    """Validated worker output shape; extraction execution is intentionally not in this slice."""

    operation: str
    kind: str
    content: str
    confidence: float
    evidence_reason: str
    target_memory_id: str | None = None
