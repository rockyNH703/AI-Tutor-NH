"""Deterministic structure-aware chunks and atomic revision publication."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select, update

from app.db.models import DocumentChunk, DocumentRevision, DocumentRevisionChunk, KnowledgeSource
from app.errors import Conflict, NotFound
from app.knowledge.protocol import ExtractedDocument

MAX_CHUNK_CHARS = 2_400
CHUNK_OVERLAP_CHARS = 240


@dataclass(frozen=True)
class StagedChunk:
    stable_key: str
    ordinal: int
    content: str
    content_sha256: str
    section_path: tuple[str, ...]
    physical_page_start: int
    physical_page_end: int
    printed_page_label: str | None
    metadata: dict[str, Any]


def _split_text(value: str) -> list[str]:
    value = value.strip()
    if len(value) <= MAX_CHUNK_CHARS:
        return [value] if value else []
    chunks: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + MAX_CHUNK_CHARS)
        if end < len(value):
            boundary = value.rfind("\n", start, end)
            if boundary <= start:
                boundary = value.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
    return chunks


def build_chunks(extracted: ExtractedDocument) -> list[StagedChunk]:
    staged: list[StagedChunk] = []
    for element in extracted.elements:
        section = tuple(element.get("section_path") or ())
        page = int(element["physical_page"])
        label = element.get("printed_page_label")
        for part_index, content in enumerate(_split_text(element["content"])):
            content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            stable = hashlib.sha256(
                f"{element['sequence']}\0{part_index}\0{page}\0{'/'.join(section)}\0{content_sha}".encode()
            ).hexdigest()
            staged.append(
                StagedChunk(
                    stable_key=stable,
                    ordinal=len(staged),
                    content=content,
                    content_sha256=content_sha,
                    section_path=section,
                    physical_page_start=page,
                    physical_page_end=page,
                    printed_page_label=label,
                    metadata={"element_type": element["element_type"], **dict(element.get("metadata") or {})},
                )
            )
    return staged


async def stage_chunks(session, revision: DocumentRevision, chunks: list[StagedChunk]) -> None:
    await session.execute(delete(DocumentRevisionChunk).where(DocumentRevisionChunk.revision_id == revision.id))
    if chunks:
        await session.execute(
            insert(DocumentRevisionChunk),
            [
                {
                    "revision_id": revision.id,
                    "document_id": revision.document_id,
                    "source_id": revision.source_id,
                    "stable_chunk_key": chunk.stable_key,
                    "ordinal": chunk.ordinal,
                    "content": chunk.content,
                    "content_sha256": chunk.content_sha256,
                    "section_path": list(chunk.section_path),
                    "physical_page_start": chunk.physical_page_start,
                    "physical_page_end": chunk.physical_page_end,
                    "printed_page_label": chunk.printed_page_label,
                    "metadata": chunk.metadata,
                }
                for chunk in chunks
            ],
        )


async def publish_revision(session, revision_id: uuid.UUID, actor_id: uuid.UUID) -> int:
    revision = await session.scalar(
        select(DocumentRevision).where(DocumentRevision.id == revision_id).with_for_update()
    )
    if revision is None:
        raise NotFound()
    if revision.status != "ready_to_publish":
        raise Conflict("This revision is not ready to publish.", code="revision_not_publishable")
    source = await session.get(KnowledgeSource, revision.source_id)
    if source is None or source.rights_revoked_at is not None:
        raise Conflict("The source rights are not active.", code="rights_not_active")
    staged = list(
        await session.scalars(
            select(DocumentRevisionChunk)
            .where(DocumentRevisionChunk.revision_id == revision.id)
            .order_by(DocumentRevisionChunk.ordinal)
        )
    )
    if not staged or any(
        chunk.embedding is None or chunk.embedding_model != revision.embedding_model for chunk in staged
    ):
        raise Conflict("The staged chunks are incomplete.", code="revision_incomplete")
    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == revision.document_id))
    await session.execute(
        insert(DocumentChunk),
        [
            {
                "document_id": revision.document_id,
                "source_id": revision.source_id,
                "ordinal": chunk.ordinal,
                "content": chunk.content,
                "metadata": {
                    **chunk.metadata_,
                    "revision_id": str(revision.id),
                    "stable_chunk_key": chunk.stable_chunk_key,
                    "section_path": chunk.section_path,
                    "physical_page": chunk.physical_page_start,
                    "physical_page_end": chunk.physical_page_end,
                    "printed_page_label": chunk.printed_page_label,
                },
                "embedding": chunk.embedding,
                "embedding_model": chunk.embedding_model,
            }
            for chunk in staged
        ],
    )
    await session.execute(
        update(DocumentRevision)
        .where(
            DocumentRevision.document_id == revision.document_id,
            DocumentRevision.status == "published",
            DocumentRevision.id != revision.id,
        )
        .values(status="retired")
    )
    revision.status = "published"
    revision.published_by_user_id = actor_id
    revision.published_at = revision.updated_at = datetime.now(UTC)
    return len(staged)
