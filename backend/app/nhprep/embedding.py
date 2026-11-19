"""Embedding calls for NHprep text: the provider is injected (built by the scripts with build_embeddings, which masks
secrets, or PrecomputedEmbeddings), so this package never imports langchain or app.rag.embeddings."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.db.models import EMBEDDING_DIMENSIONS
from app.rag.errors import EmbeddingError

log = logging.getLogger(__name__)

# A bulk import backs off longer than a student's upload (429s, timeouts); sequential, one batch at a time.
EMBED_RETRY_DELAYS_S = (2, 5, 15)
RETRYABLE = ("embedding_failed", "embedding_timeout")


class Embedder(Protocol):
    model: str

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


async def embed_texts(embedder: Embedder, texts: list[str], before: Callable[[], Awaitable[None]]) -> list[list[float]]:
    """One batch, retried on transient failures only; `before` (the lock and capacity guard) runs before any text
    leaves, every attempt. Vectors are checked for count, size and finiteness."""
    for attempt, delay in enumerate((*EMBED_RETRY_DELAYS_S, None)):
        await before()
        try:
            vectors = await embedder.aembed_documents(texts)
        except EmbeddingError as error:
            if delay is None or error.code not in RETRYABLE:
                raise
            log.warning("nhprep.import embedding retry", extra={"attempt": attempt + 1, "reason": error.code})
            await asyncio.sleep(delay + random.random())  # noqa: S311 - jitter, not security
            continue
        if len(vectors) != len(texts) or any(
            len(vector) != EMBEDDING_DIMENSIONS or not all(map(math.isfinite, vector)) for vector in vectors
        ):
            raise EmbeddingError("embedding_dimension_mismatch", "unexpected vectors")
        return vectors
    raise EmbeddingError("embedding_failed", "retries exhausted")  # pragma: no cover - the loop returns or raises
