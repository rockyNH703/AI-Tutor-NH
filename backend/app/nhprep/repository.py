"""NHprep's tables: every read and write the importer makes (SQLAlchemy Core, bound parameters only).

Rows are written only when their content_hash differs (or a removed row comes back), so an unchanged export writes
nothing. Chunks are keyed by the sha256 of their embedded text: kept when unchanged, deleted when their kind is
re-stated without them or their model changed, inserted with a vector otherwise.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.models import (
    NhprepCourse,
    NhprepCourseTrainer,
    NhprepLesson,
    NhprepLessonItem,
    NhprepSearchChunk,
    NhprepSyncRun,
    NhprepTrainer,
)
from app.db.session import Database
from app.nhprep.normalize import ChunkText, CoursePlan, LessonPlan, content_hash, trainer_aliases

log = logging.getLogger(__name__)

NHPREP_LOCK_KEY = 7_216_094_260  # pg advisory lock: one NHprep import or purge at a time (handoff: reserved)
NOW = func.clock_timestamp()


class ImportBusy(Exception):
    """Another NHprep import or purge holds the lock."""


class LockLost(Exception):
    """The lock connection died: another run could start, so this one stops before its next write."""


@asynccontextmanager
async def exclusive(db: Database) -> AsyncIterator[AsyncConnection]:
    """pg_try_advisory_lock on a connection of its own; refuses rather than waits. AUTOCOMMIT, so no transaction
    stays open on it while the run waits on the embedding provider (session-level locks survive commits)."""
    async with db.engine.connect() as lock:
        await lock.execution_options(isolation_level="AUTOCOMMIT")
        if not await lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": NHPREP_LOCK_KEY}):
            raise ImportBusy
        try:
            yield lock
        finally:
            try:
                await lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": NHPREP_LOCK_KEY})
            except Exception as error:  # noqa: BLE001 - a dead connection already released its lock
                log.warning("nhprep.import unlock failed", extra={"error_type": type(error).__name__})


async def ping(lock: AsyncConnection) -> None:
    """Alive means the session lock is still held."""
    try:
        await lock.scalar(text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 - any failure means the lock may be gone
        raise LockLost from error


# ------------------------------------------------------------------ state
@dataclass(frozen=True)
class Stored:
    id: uuid.UUID
    content_hash: str
    removed_at: Any
    course_id: uuid.UUID | None = None


@dataclass
class State:
    courses: dict[str, Stored]
    lessons: dict[uuid.UUID, Stored]
    chunks: dict[uuid.UUID, dict[str, tuple[str, str]]]  # lesson id -> content_hash -> (kind, embedding_model)


async def load_state(session: AsyncSession) -> State:
    courses = await session.execute(
        select(NhprepCourse.external_id, NhprepCourse.id, NhprepCourse.content_hash, NhprepCourse.removed_at)
    )
    lessons = await session.execute(
        select(
            NhprepLesson.external_id,
            NhprepLesson.id,
            NhprepLesson.content_hash,
            NhprepLesson.removed_at,
            NhprepLesson.course_id,
        )
    )
    chunks: dict[uuid.UUID, dict[str, tuple[str, str]]] = {}
    rows = await session.execute(
        select(
            NhprepSearchChunk.lesson_id,
            NhprepSearchChunk.content_hash,
            NhprepSearchChunk.kind,
            NhprepSearchChunk.embedding_model,
        )
    )
    for lesson_id, key, kind, model in rows:
        chunks.setdefault(lesson_id, {})[key] = (kind, model)
    return State(
        courses={row[0]: Stored(*row[1:]) for row in courses},
        lessons={row[0]: Stored(*row[1:]) for row in lessons},
        chunks=chunks,
    )


# ------------------------------------------------------------------ runs
async def start_run(session: AsyncSession, values: dict[str, Any]) -> uuid.UUID:
    """Under the lock any 'running' row is stale (its process died): marked interrupted first."""
    await session.execute(
        update(NhprepSyncRun).where(NhprepSyncRun.status == "running").values(status="interrupted", finished_at=NOW)
    )
    return await session.scalar(insert(NhprepSyncRun).values(**values).returning(NhprepSyncRun.id))


async def finish_run(session: AsyncSession, run_id: uuid.UUID, status: str, report: dict[str, Any], **values) -> None:
    await session.execute(
        update(NhprepSyncRun)
        .where(NhprepSyncRun.id == run_id)
        .values(status=status, finished_at=NOW, report=report, **values)
    )


# ------------------------------------------------------------------ catalogue
async def _trainer_id(session: AsyncSession, display: str) -> uuid.UUID:
    name, aliases = display.lower(), trainer_aliases(display)
    found = (
        await session.execute(
            select(NhprepTrainer.id, NhprepTrainer.display_name, NhprepTrainer.aliases).where(
                NhprepTrainer.normalized_name == name
            )
        )
    ).first()
    if found is None:
        return await session.scalar(
            insert(NhprepTrainer)
            .values(normalized_name=name, display_name=display, aliases=aliases)
            .returning(NhprepTrainer.id)
        )
    if (found.display_name, list(found.aliases)) != (display, aliases):
        await session.execute(
            update(NhprepTrainer)
            .where(NhprepTrainer.id == found.id)
            .values(display_name=display, aliases=aliases, updated_at=NOW)
        )
    return found.id


async def write_course(session: AsyncSession, plan: CoursePlan, stored: Stored | None) -> tuple[uuid.UUID, str]:
    """(course id, 'inserted' | 'updated' | 'unchanged'); its trainer link follows the course."""
    values = {**plan.values, "content_hash": plan.content_hash}
    if stored is None:
        course_id = await session.scalar(insert(NhprepCourse).values(**values).returning(NhprepCourse.id))
        outcome = "inserted"
    elif stored.content_hash != plan.content_hash or stored.removed_at is not None:
        course_id = stored.id
        await session.execute(
            update(NhprepCourse).where(NhprepCourse.id == course_id).values(**values, removed_at=None, updated_at=NOW)
        )
        outcome = "updated"
    else:
        return stored.id, "unchanged"
    await session.execute(delete(NhprepCourseTrainer).where(NhprepCourseTrainer.course_id == course_id))
    if plan.trainer is not None:
        display, provenance = plan.trainer
        trainer_id = await _trainer_id(session, display)
        await session.execute(
            insert(NhprepCourseTrainer).values(course_id=course_id, trainer_id=trainer_id, provenance=provenance)
        )
    return course_id, outcome


async def write_lesson(
    session: AsyncSession, course_id: uuid.UUID, plan: LessonPlan, stored: Stored | None, item_counts: dict[str, int]
) -> tuple[uuid.UUID, str]:
    """(lesson id, outcome); a changed lesson's items are synced for the kinds the export states completely."""
    values = {**plan.values, "course_id": course_id, "content_hash": plan.content_hash}
    if stored is None:
        lesson_id = await session.scalar(
            insert(NhprepLesson).values(external_id=plan.external_id, **values).returning(NhprepLesson.id)
        )
        outcome = "inserted"
    elif stored.content_hash != plan.content_hash or stored.removed_at is not None or stored.course_id != course_id:
        lesson_id = stored.id
        await session.execute(
            update(NhprepLesson).where(NhprepLesson.id == lesson_id).values(**values, removed_at=None, updated_at=NOW)
        )
        outcome = "updated"
    else:
        return stored.id, "unchanged"
    await _sync_items(session, lesson_id, plan, item_counts)
    return lesson_id, outcome


async def _sync_items(session: AsyncSession, lesson_id: uuid.UUID, plan: LessonPlan, counts: dict[str, int]) -> None:
    rows = await session.execute(
        select(
            NhprepLessonItem.id, NhprepLessonItem.kind, NhprepLessonItem.item_key, NhprepLessonItem.content_hash
        ).where(NhprepLessonItem.lesson_id == lesson_id)
    )
    stored = {(kind, key): (item_id, digest) for item_id, kind, key, digest in rows}
    wanted = {(item.kind, item.item_key): item for item in plan.items}
    gone = [item_id for pair, (item_id, _) in stored.items() if pair[0] in plan.authoritative and pair not in wanted]
    if gone:  # their chunks go with them (FK cascade); the lesson's chunks are rebuilt in the index phase
        await session.execute(delete(NhprepLessonItem).where(NhprepLessonItem.id.in_(gone)))
        counts["deleted"] += len(gone)
    for pair, item in wanted.items():
        values = item.values()
        digest = content_hash(values)
        if pair not in stored:
            await session.execute(insert(NhprepLessonItem).values(lesson_id=lesson_id, content_hash=digest, **values))
            counts["inserted"] += 1
        elif stored[pair][1] != digest:
            await session.execute(
                update(NhprepLessonItem)
                .where(NhprepLessonItem.id == stored[pair][0])
                .values(content_hash=digest, updated_at=NOW, **values)
            )
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1


async def remove(session: AsyncSession, course_ids: list[uuid.UUID], lesson_ids: list[uuid.UUID]) -> None:
    """Soft removal (a complete export no longer lists them); their search chunks are deleted."""
    if course_ids:
        await session.execute(
            update(NhprepCourse).where(NhprepCourse.id.in_(course_ids)).values(removed_at=NOW, updated_at=NOW)
        )
    if lesson_ids:
        await session.execute(
            update(NhprepLesson).where(NhprepLesson.id.in_(lesson_ids)).values(removed_at=NOW, updated_at=NOW)
        )
        await session.execute(delete(NhprepSearchChunk).where(NhprepSearchChunk.lesson_id.in_(lesson_ids)))


# ------------------------------------------------------------------ search chunks
async def reusable_vectors(session: AsyncSession, model: str, keys: Iterable[str]) -> dict[str, Any]:
    """Vectors this model already made for the same text (another lesson, or a moved one)."""
    rows = await session.execute(
        select(NhprepSearchChunk.content_hash, NhprepSearchChunk.embedding)
        .where(NhprepSearchChunk.embedding_model == model, NhprepSearchChunk.content_hash.in_(list(keys)))
        .distinct(NhprepSearchChunk.content_hash)
    )
    return {key: vector for key, vector in rows}


async def chunk_keys(session: AsyncSession, lesson_ids: list[uuid.UUID], model: str) -> set[tuple[uuid.UUID, str]]:
    rows = await session.execute(
        select(NhprepSearchChunk.lesson_id, NhprepSearchChunk.content_hash).where(
            NhprepSearchChunk.lesson_id.in_(lesson_ids), NhprepSearchChunk.embedding_model == model
        )
    )
    return {(lesson_id, key) for lesson_id, key in rows}


async def write_chunks(
    session: AsyncSession,
    lesson_id: uuid.UUID,
    course_id: uuid.UUID,
    chunks: list[ChunkText],
    managed: frozenset[str],
    model: str,
    vectors: dict[str, Any],
) -> dict[str, int]:
    rows = await session.execute(
        select(
            NhprepSearchChunk.content_hash,
            NhprepSearchChunk.kind,
            NhprepSearchChunk.embedding_model,
            NhprepSearchChunk.ordinal,
            NhprepSearchChunk.item_id,
        ).where(NhprepSearchChunk.lesson_id == lesson_id)
    )
    stored = {row.content_hash: row for row in rows}
    items = {
        (kind, key): item_id
        for item_id, kind, key in await session.execute(
            select(NhprepLessonItem.id, NhprepLessonItem.kind, NhprepLessonItem.item_key).where(
                NhprepLessonItem.lesson_id == lesson_id
            )
        )
    }
    wanted = {chunk.key: chunk for chunk in chunks}
    stale = [
        key
        for key, row in stored.items()
        if row.kind in managed and (key not in wanted or row.embedding_model != model)
    ]
    if stale:
        await session.execute(
            delete(NhprepSearchChunk).where(
                NhprepSearchChunk.lesson_id == lesson_id, NhprepSearchChunk.content_hash.in_(stale)
            )
        )
    counts = {"deleted": len(stale), "inserted": 0, "updated": 0}
    for key, chunk in wanted.items():
        item_id = items[chunk.item_key] if chunk.item_key else None
        if key in stored and key not in stale:
            if (stored[key].ordinal, stored[key].item_id) != (chunk.ordinal, item_id):
                await session.execute(
                    update(NhprepSearchChunk)
                    .where(NhprepSearchChunk.lesson_id == lesson_id, NhprepSearchChunk.content_hash == key)
                    .values(ordinal=chunk.ordinal, item_id=item_id)
                )
                counts["updated"] += 1
            continue
        await session.execute(
            insert(NhprepSearchChunk).values(
                lesson_id=lesson_id,
                course_id=course_id,
                item_id=item_id,
                kind=chunk.kind,
                ordinal=chunk.ordinal,
                title=chunk.title,
                content=chunk.content,
                embedding=vectors[key],
                embedding_model=model,
                content_hash=key,
            )
        )
        counts["inserted"] += 1
    return counts


# ------------------------------------------------------------------ purge
PURGED = (NhprepCourse, NhprepLesson, NhprepLessonItem, NhprepSearchChunk, NhprepCourseTrainer, NhprepTrainer)


async def catalogue_counts(session: AsyncSession) -> dict[str, int]:
    return {model.__tablename__: int(await session.scalar(select(func.count()).select_from(model))) for model in PURGED}


async def purge(session: AsyncSession) -> dict[str, int]:
    """DELETE (the runtime role has no TRUNCATE): courses cascade to lessons, items, chunks and trainer links."""
    counts = await catalogue_counts(session)
    await session.execute(delete(NhprepCourse))
    await session.execute(delete(NhprepTrainer))
    return counts
