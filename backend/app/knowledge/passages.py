"""Canonical passages (migration 0025): a text is stored and indexed once, wherever and however often it appears.

- stage_passages: a revision's chunks become passages (INSERT ... ON CONFLICT on the normalized-text key, so
  concurrent revisions converge on one row) and occurrences (the revision, pages and section of each appearance).
- missing_embeddings / store_embeddings: only passages without a vector for the model are embedded (reuse).
- fingerprint_file / classify_file: a file's page identities, and its duplicate reviews against every other file
  of the library, across sources, courses and import batches. Nothing is ever deleted by a classification.
- publish: under one advisory lock (publications are serialized), re-check what blocks a book (rights, pending
  duplicate reviews, missing vectors), make its passages live in its source, retire the document's previous
  revision, and drop liveness that no published revision of the source still carries.
- purge_orphans: passages no occurrence refers to (after a deletion) go, with their vectors and graph rows.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DocumentFile,
    DocumentRevision,
    KnowledgeDuplicateReview,
    KnowledgeEdition,
    KnowledgePageFingerprint,
    KnowledgePassage,
    KnowledgePassageEmbedding,
    KnowledgePassageOccurrence,
    KnowledgePassageSource,
    KnowledgeSource,
)
from app.errors import Conflict, NotFound
from app.knowledge.canonical import content_key, simhash
from app.knowledge.duplicates import DuplicateClass, normalize_page
from app.knowledge.publication import StagedChunk

PUBLISH_LOCK_KEY = 7_216_094_025  # pg_advisory_xact_lock: one publication at a time sees every other one
PURGE_LOCK_KEY = 7_216_094_026  # staging holds it shared, the orphan purge exclusive: no purge mid-staging
# A pending review of one of these blocks the file's publication until the operator decides it.
BLOCKING = frozenset(
    {
        DuplicateClass.EXACT,
        DuplicateClass.CONFIRMED_CONTENT,
        DuplicateClass.SAME_EDITION_REPRESENTATION,
        DuplicateClass.PROBABLE,
        DuplicateClass.DIFFERENT_EDITION,
        "subset",
        "superset",
    }
)
WITHHELD = ("reuse", "reject")  # decisions that keep a copy unpublished (the library already has the book)
MIN_PAGE_CHARS = 80  # shorter pages (blank, part titles, "This page intentionally left blank") prove nothing
MIN_SHARED_PAGES = 3
KEY_BATCH = 5_000  # IN-lists stay far below asyncpg's 32,767 bind parameters
_EDITION_WORDS = re.compile(
    r"\b(\d+(st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+edition\b"
    r"|\bedition\b|\bvol(ume)?\.?\s*\w+\b|\bpart\s+\w+\b|[^\w\s]",
    re.IGNORECASE,
)


def _batches(items: Sequence[Any], size: int = KEY_BATCH) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ----------------------------------------------------------------------------- staging
async def stage_passages(session: AsyncSession, revision: DocumentRevision, chunks: Sequence[StagedChunk]) -> int:
    """The revision's chunks as passages and occurrences, in chunk order; idempotent. Returns distinct passages."""
    await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": PURGE_LOCK_KEY})
    await session.execute(
        delete(KnowledgePassageOccurrence).where(KnowledgePassageOccurrence.revision_id == revision.id)
    )
    if not chunks:
        return 0
    keys = [content_key(chunk.content) for chunk in chunks]
    first: dict[str, str] = {}
    for key, chunk in zip(keys, chunks, strict=True):
        first.setdefault(key, chunk.content)
    ordered = sorted(first)  # one lock order for every writer: concurrent books cannot deadlock on shared text
    await session.execute(
        pg_insert(KnowledgePassage).on_conflict_do_nothing(index_elements=["content_key"]),
        [
            {"content_key": key, "content": first[key], "simhash": simhash(first[key]), "char_count": len(first[key])}
            for key in ordered
        ],
    )
    ids: dict[str, uuid.UUID] = {}
    for batch in _batches(ordered):
        rows = await session.execute(
            select(KnowledgePassage.content_key, KnowledgePassage.id).where(KnowledgePassage.content_key.in_(batch))
        )
        ids.update(rows.tuples().all())
    await session.execute(
        insert(KnowledgePassageOccurrence),
        [
            {
                "passage_id": ids[key],
                "revision_id": revision.id,
                "document_id": revision.document_id,
                "source_id": revision.source_id,
                "ordinal": chunk.ordinal,
                "stable_chunk_key": chunk.stable_key,
                "physical_page_start": chunk.physical_page_start,
                "physical_page_end": chunk.physical_page_end,
                "printed_page_label": chunk.printed_page_label,
                "section_path": list(chunk.section_path),
                "element_type": str(chunk.metadata.get("element_type") or "paragraph")[:20],
            }
            for key, chunk in zip(keys, chunks, strict=True)
        ],
    )
    return len(first)


async def missing_embeddings(session: AsyncSession, revision_id: uuid.UUID, model: str) -> list[tuple[uuid.UUID, str]]:
    """The revision's passages without a vector for `model`: (passage id, text), each once, in a stable order."""
    has_vector = (
        select(KnowledgePassageEmbedding.passage_id)
        .where(
            KnowledgePassageEmbedding.passage_id == KnowledgePassage.id,
            KnowledgePassageEmbedding.embedding_model == model,
        )
        .exists()
    )
    revision_passages = select(KnowledgePassageOccurrence.passage_id).where(
        KnowledgePassageOccurrence.revision_id == revision_id
    )
    rows = await session.execute(
        select(KnowledgePassage.id, KnowledgePassage.content)
        .where(KnowledgePassage.id.in_(revision_passages), ~has_vector)
        .order_by(KnowledgePassage.id)
    )
    return [(row.id, row.content) for row in rows]


async def store_embeddings(session: AsyncSession, vectors: Sequence[tuple[uuid.UUID, list[float]]], model: str) -> None:
    """Content-addressed and idempotent: two workers embedding the same passage keep the first vector."""
    if vectors:
        await session.execute(
            pg_insert(KnowledgePassageEmbedding).on_conflict_do_nothing(
                index_elements=["passage_id", "embedding_model"]
            ),
            [{"passage_id": pid, "embedding_model": model, "embedding": vector} for pid, vector in vectors],
        )


# ----------------------------------------------------------------------------- editions
def work_key(title: str) -> str:
    """A book's identity across its editions and volumes: the title without edition, volume and part words."""
    return " ".join(_EDITION_WORDS.sub(" ", title.casefold()).split())[:200] or "untitled"


async def record_edition(
    session: AsyncSession, revision: DocumentRevision, fallback_title: str, metadata: dict[str, Any]
) -> KnowledgeEdition:
    """What the book says about itself (protocol v3), kept per document; an operator's correction is not undone."""
    edition = await session.get(KnowledgeEdition, revision.document_id)
    if edition is not None and edition.metadata_source == "operator":
        return edition
    title = str(metadata.get("title") or fallback_title or "untitled")[:300]
    values = {
        "source_id": revision.source_id,
        "work_key": work_key(title),
        "title": title,
        "edition_number": metadata.get("edition_number"),
        "edition_statement": metadata.get("edition_statement"),
        "volume": metadata.get("volume"),
        "isbn13": list(metadata.get("isbn13") or []),
        "year": metadata.get("year"),
        "exam_codes": list(metadata.get("exam_codes") or []),
        "metadata_source": "pdf",
        "updated_at": datetime.now(UTC),
    }
    if edition is None:
        edition = KnowledgeEdition(document_id=revision.document_id, **values)
        session.add(edition)
    else:
        for name, value in values.items():
            setattr(edition, name, value)
    await session.flush()
    return edition


# ----------------------------------------------------------------------------- duplicates
def pages_of(elements: Sequence[dict[str, Any]]) -> list[tuple[int, str]]:
    """The extracted text of each physical page, in page order."""
    pages: dict[int, list[str]] = {}
    for element in elements:
        pages.setdefault(int(element["physical_page"]), []).append(str(element["content"]))
    return [(page, "\n".join(parts)) for page, parts in sorted(pages.items())]


def page_keys(pages: Sequence[tuple[int, str]]) -> list[tuple[int, str, int]]:
    """(page, key, characters) of the pages long enough to identify a book."""
    keyed = []
    for page, page_text in pages:
        normalized = normalize_page(page_text)
        if len(normalized) >= MIN_PAGE_CHARS:
            keyed.append((page, hashlib.sha256(normalized.encode("utf-8")).hexdigest(), len(normalized)))
    return keyed


async def fingerprint_file(session: AsyncSession, file_id: uuid.UUID, pages: Sequence[tuple[int, str]]) -> int:
    await session.execute(delete(KnowledgePageFingerprint).where(KnowledgePageFingerprint.file_id == file_id))
    keyed = page_keys(pages)
    if keyed:
        await session.execute(
            insert(KnowledgePageFingerprint),
            [{"file_id": file_id, "physical_page": p, "page_key": k, "char_count": c} for p, k, c in keyed],
        )
    return len(keyed)


def classify_pages(mine: Sequence[str], theirs: Sequence[str], same_bytes: bool) -> tuple[str, int, dict[str, Any]]:
    """A duplicate class for two files from their ordered page keys (evidence for an operator, never a deletion)."""
    if same_bytes:
        return DuplicateClass.EXACT, 100, {"sha256": True}
    left, right = set(mine), set(theirs)
    shared = len(left & right)
    ordered = sum(a == b for a, b in zip(mine, theirs, strict=False)) / max(len(mine), len(theirs), 1)
    signals = {"sharedPages": shared, "pages": [len(mine), len(theirs)], "orderedMatch": round(ordered, 3)}
    if mine and list(mine) == list(theirs):
        return DuplicateClass.CONFIRMED_CONTENT, 100, signals
    if ordered >= 0.95 and abs(len(mine) - len(theirs)) <= 1:
        return DuplicateClass.SAME_EDITION_REPRESENTATION, 95, signals
    if left and shared >= 0.9 * len(left) and len(right) > len(left):
        return "subset", 90, signals  # this file is (nearly) contained in the other one: a volume of a combined book
    if right and shared >= 0.9 * len(right) and len(left) > len(right):
        return "superset", 90, signals
    smaller = max(min(len(left), len(right)), 1)
    if shared >= MIN_SHARED_PAGES and shared / smaller >= 0.8:
        return DuplicateClass.PROBABLE, 85, signals
    if shared >= MIN_SHARED_PAGES and shared / smaller >= 0.3:
        return DuplicateClass.DIFFERENT_EDITION, 70, signals
    if shared >= MIN_SHARED_PAGES:
        return DuplicateClass.RELATED, 60, signals
    return DuplicateClass.INCONCLUSIVE, 20, signals


async def _keys(session: AsyncSession, file_id: uuid.UUID) -> list[str]:
    return list(
        await session.scalars(
            select(KnowledgePageFingerprint.page_key)
            .where(KnowledgePageFingerprint.file_id == file_id)
            .order_by(KnowledgePageFingerprint.physical_page)
        )
    )


async def classify_file(session: AsyncSession, file: DocumentFile) -> list[tuple[uuid.UUID, str]]:
    """Reviews of `file` against every live file sharing bytes or pages with it, in any source or course.

    A review the operator already decided keeps its decision; only the evidence is refreshed."""
    mine = await _keys(session, file.id)
    live = DocumentFile.status.in_(("ready",))
    candidates = set(
        await session.scalars(
            select(DocumentFile.id).where(DocumentFile.sha256 == file.sha256, DocumentFile.id != file.id, live)
        )
    )
    if mine:  # one IN-list: a book has at most extract.MAX_PAGES (3,000) pages, below KEY_BATCH
        candidates |= set(
            await session.scalars(
                select(KnowledgePageFingerprint.file_id)
                .join(DocumentFile, DocumentFile.id == KnowledgePageFingerprint.file_id)
                .where(
                    KnowledgePageFingerprint.page_key.in_(sorted(set(mine))),
                    KnowledgePageFingerprint.file_id != file.id,
                    live,
                )
                .group_by(KnowledgePageFingerprint.file_id)
                .having(func.count() >= MIN_SHARED_PAGES)
            )
        )
    found: list[tuple[uuid.UUID, str]] = []
    for candidate_id in sorted(candidates):
        candidate = await session.get(DocumentFile, candidate_id)
        if candidate is None:
            continue
        classification, confidence, signals = classify_pages(
            mine, await _keys(session, candidate_id), candidate.sha256 == file.sha256
        )
        if classification == DuplicateClass.INCONCLUSIVE:
            continue
        signals = {**signals, "candidateSource": str(candidate.source_id)}
        await session.execute(
            pg_insert(KnowledgeDuplicateReview)
            .values(
                file_id=file.id,
                candidate_file_id=candidate_id,
                classification=str(classification),
                confidence=confidence,
                signals=signals,
            )
            .on_conflict_do_update(
                constraint="uq_knowledge_duplicate_reviews_pair",
                set_={"classification": str(classification), "confidence": confidence, "signals": signals},
            )
        )
        found.append((candidate_id, str(classification)))
    return found


async def blocking_reviews(session: AsyncSession, file_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeDuplicateReview)
            .where(
                KnowledgeDuplicateReview.file_id == file_id,
                KnowledgeDuplicateReview.decision == "pending",
                KnowledgeDuplicateReview.classification.in_(sorted(str(value) for value in BLOCKING)),
            )
        )
        or 0
    )


# ----------------------------------------------------------------------------- publication
async def publish(session: AsyncSession, revision_id: uuid.UUID, actor_id: uuid.UUID | None) -> int:
    """Make a ready revision's passages live in its source; returns how many distinct passages it carries.

    0 means held, not published: another copy reached the library meanwhile, so the revision is back in
    needs_review and its new duplicate reviews are written. The caller commits that (the operator must see them).
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PUBLISH_LOCK_KEY})
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
    file = await session.get(DocumentFile, revision.file_id)
    if file is not None:  # re-checked under the lock: two copies processed at the same time see each other now
        await classify_file(session, file)
        if await blocking_reviews(session, file.id):
            report = dict(revision.quality_report or {})
            report["warnings"] = sorted({*report.get("warnings", ()), "duplicate_review_pending"})
            revision.quality_report = report
            revision.status = "needs_review"
            revision.updated_at = datetime.now(UTC)
            await session.flush()
            return 0
        withheld = await session.scalar(
            select(func.count())
            .select_from(KnowledgeDuplicateReview)
            .where(KnowledgeDuplicateReview.file_id == file.id, KnowledgeDuplicateReview.decision.in_(WITHHELD))
        )
        if withheld:
            raise Conflict("The operator decided this copy is not published.", code="duplicate_withheld")
    passages = list(
        await session.scalars(
            select(KnowledgePassageOccurrence.passage_id)
            .where(KnowledgePassageOccurrence.revision_id == revision.id)
            .distinct()
        )
    )
    if not passages or await missing_embeddings(session, revision.id, revision.embedding_model):
        raise Conflict("The staged passages are incomplete.", code="revision_incomplete")
    now = datetime.now(UTC)
    await session.execute(
        update(DocumentRevision)
        .where(
            DocumentRevision.document_id == revision.document_id,
            DocumentRevision.status == "published",
            DocumentRevision.id != revision.id,
        )
        .values(status="retired", retired_at=now, updated_at=now)
    )
    await session.execute(
        pg_insert(KnowledgePassageSource).on_conflict_do_nothing(),
        [{"source_id": revision.source_id, "passage_id": passage_id} for passage_id in passages],
    )
    revision.status = "published"
    revision.published_by_user_id = actor_id
    revision.published_at = revision.updated_at = now
    await session.flush()
    await refresh_source_liveness(session, revision.source_id)
    return len(passages)


async def refresh_source_liveness(session: AsyncSession, source_id: uuid.UUID) -> int:
    """Drop liveness no published revision of the source carries any more (a retired or withdrawn edition's text)."""
    carried = (
        select(KnowledgePassageOccurrence.id)
        .join(DocumentRevision, DocumentRevision.id == KnowledgePassageOccurrence.revision_id)
        .where(
            KnowledgePassageOccurrence.passage_id == KnowledgePassageSource.passage_id,
            KnowledgePassageOccurrence.source_id == source_id,
            DocumentRevision.status == "published",
        )
        .correlate(KnowledgePassageSource)
        .exists()
    )
    result = await session.execute(
        delete(KnowledgePassageSource).where(KnowledgePassageSource.source_id == source_id, ~carried)
    )
    return int(result.rowcount or 0)


async def purge_orphans(session: AsyncSession) -> int:
    """Remove passages no occurrence refers to; their vectors, liveness and graph rows go by cascade."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PURGE_LOCK_KEY})
    referenced = (
        select(KnowledgePassageOccurrence.id)
        .where(KnowledgePassageOccurrence.passage_id == KnowledgePassage.id)
        .correlate(KnowledgePassage)
        .exists()
    )
    result = await session.execute(delete(KnowledgePassage).where(~referenced))
    return int(result.rowcount or 0)
