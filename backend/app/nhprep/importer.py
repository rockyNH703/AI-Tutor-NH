"""Importing an NHprep export v1 into the nhprep_* tables (plan rev 4 §8.2; spec r2-data-search §3-§4).

Order, with no transaction ever held across a provider call:
0. Plan (pure): validated records -> normalised rows, items and chunk texts (app/nhprep/normalize.py).
1. Lock: pg_try_advisory_lock on an AUTOCOMMIT connection of its own, pinged before every write and embedding batch.
2. T0 (read only): what changes, which texts need vectors, what a complete export removes; then the guards
   (mass removal, capacity projection, egress basis, precomputed vectors present). A dry run stops here: it writes
   nothing, records nothing and calls no provider.
3. T1: stale 'running' runs become 'interrupted'; this run is recorded as 'running'.
4. One transaction per course: the course, its trainer link, changed lessons and their items.
5. Index: batches of missing texts -> reuse a vector the model already made, else embed; then one short
   transaction per lesson writes its chunks.
6. Removals (complete exports only), last, so an interrupted run never leaves "removed but not replaced".
7. The run is 'succeeded' with its report (counts and codes only), or 'failed' with an error code.
Everything is keyed by content, so a rerun resumes, and an unchanged export writes 0 rows and embeds 0 texts.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, TextIO

from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.session import Database
from app.errors import ServiceUnavailable
from app.files.service import DB_CEILING_BYTES, MIN_FREE_DISK_BYTES, OWN_DATA_BYTES, check_capacity
from app.nhprep import repository
from app.nhprep.embedding import Embedder, embed_texts
from app.nhprep.normalize import ChunkText, CoursePlan, LessonPlan, lesson_chunks, plan_course
from app.nhprep.repository import ImportBusy, LockLost
from app.nhprep.validate import SCHEMA, Export

__all__ = ["ImportBusy", "ImportOptions", "ImportRefused", "purge", "run_import"]

log = logging.getLogger(__name__)

NHPREP_RESERVE_BYTES = 512 * 1024**2  # left under files.service.DB_CEILING_BYTES for students' uploads (G12)
BYTES_PER_CHUNK = 17_500  # projection; replace with pg_total_relation_size / count(*) measured on the WSL import
MASS_REMOVAL_SHARE = 0.05  # removing more than this share of lessons, or any whole course, needs --allow-removals
ACCESS_BASIS = "owner-authorised; NetworkersHome operates nhprep.com; robots.txt disallow overridden"
NORMALISATIONS = ("invisible", "fence_info", "directive", "nhprep_url")


class ImportRefused(Exception):
    """Refused before (or instead of) writing; `code` is safe to log and record."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class ImportOptions:
    apply: bool
    operator: str = "local"  # a label, ^[a-z0-9][a-z0-9_.-]{0,79}$
    access_basis: str = ACCESS_BASIS
    egress_basis: str | None = None  # required when any text will be embedded
    allow_removals: int = 0
    batch_size: int = 64  # settings.embedding_batch_size
    emit_texts: TextIO | None = None  # dry run only: {"k","t"} lines for scripts/nhprep_embed_offline.py
    backup: str | None = None  # the backup or waiver, recorded in the report only


@dataclass(frozen=True)
class _Lesson:
    plan: LessonPlan
    course: CoursePlan
    chunks: tuple[ChunkText, ...]


@dataclass
class _Work:
    lessons: list[_Lesson]
    report: dict[str, Any] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)  # text key -> text, needing a vector
    to_index: list[_Lesson] = field(default_factory=list)
    removed_courses: list[uuid.UUID] = field(default_factory=list)
    removed_lessons: list[uuid.UUID] = field(default_factory=list)


def _plan(export: Export) -> tuple[list[CoursePlan], Counter[str]]:
    counts: Counter[str] = Counter()
    plans = []
    for slug, course in export.courses.items():
        plan = plan_course(course, [lesson for lesson in export.lessons if lesson.course == slug], counts)
        if plan is None:
            export.complete = False
        else:
            plans.append(plan)
    return plans, counts


def _texts(plans: list[CoursePlan]) -> list[_Lesson]:
    return [
        _Lesson(lesson, course, tuple(lesson_chunks(lesson, course.values["title"], (course.trainer or (None,))[0])))
        for course in plans
        for lesson in course.lessons
    ]


def _managed(lesson: LessonPlan) -> frozenset[str]:
    return lesson.authoritative | {"lesson"}


def _diff(export: Export, plans: list[CoursePlan], work: _Work, state: repository.State, model: str) -> None:
    """T0: what this export changes against the stored state (pure over `state`)."""
    for item in work.lessons:
        stored = state.lessons.get(item.plan.external_id)
        existing = state.chunks.get(stored.id, {}) if stored else {}
        current = {key for key, (kind, used) in existing.items() if used == model and kind in _managed(item.plan)}
        wanted = {chunk.key for chunk in item.chunks}
        changed = stored is None or stored.content_hash != item.plan.content_hash or stored.removed_at is not None
        stale = any(kind in _managed(item.plan) and key not in wanted for key, (kind, _) in existing.items())
        if changed or stale or wanted != current:
            work.to_index.append(item)
        for chunk in item.chunks:
            if chunk.key not in current:
                work.missing[chunk.key] = chunk.text
    if export.complete:
        listed = {plan.slug for plan in plans}
        work.removed_courses = [
            row.id for slug, row in state.courses.items() if slug not in listed and row.removed_at is None
        ]
        gone = set(work.removed_courses)
        planned = {item.plan.external_id for item in work.lessons}  # a lesson moving out of a removed course stays
        work.removed_lessons = [
            row.id
            for external_id, row in state.lessons.items()
            if row.removed_at is None
            and external_id not in planned
            and (external_id not in export.present_lesson_ids or row.course_id in gone)
        ]


def _guard_removals(work: _Work, state: repository.State, allow: int) -> None:
    active = sum(1 for row in state.lessons.values() if row.removed_at is None)
    count = len(work.removed_lessons) + len(work.removed_courses)
    if count and (work.removed_courses or len(work.removed_lessons) > MASS_REMOVAL_SHARE * active) and allow < count:
        raise ImportRefused("mass_removal", f"{count} removals; rerun with --allow-removals {count} if intended")


async def _guard(db: Database, lock: AsyncConnection) -> None:
    """Before every write transaction and every embedding batch: the lock is held and there is room."""
    await repository.ping(lock)
    async with db.sessionmaker() as session:
        try:
            await check_capacity(session)  # free disk >= 5 GB and the public schema under the 4 GiB ceiling
        except ServiceUnavailable as error:
            raise ImportRefused("capacity", "the files capacity check refused") from error
        if int(await session.scalar(OWN_DATA_BYTES) or 0) > DB_CEILING_BYTES - NHPREP_RESERVE_BYTES:
            raise ImportRefused("capacity", "NHprep would use the headroom students' uploads need")


def _free_disk(database: str | None) -> int | None:
    if database and database.endswith("_test"):
        return None  # as check_capacity: a test database never stands for the shared disk
    try:
        return shutil.disk_usage("/").free
    except OSError:
        return 0


def _project(work: _Work, own: int, database: str | None) -> None:
    catalogue = sum(len(json.dumps(item.plan.values, default=str)) for item in work.to_index)
    catalogue += sum(len(json.dumps(row.values(), default=str)) for item in work.to_index for row in item.plan.items)
    projected = len(work.missing) * BYTES_PER_CHUNK + 2 * catalogue
    work.report.update(projected_bytes=projected, own_bytes_before=own)
    if own + projected > DB_CEILING_BYTES - NHPREP_RESERVE_BYTES:
        raise ImportRefused("capacity", "the projected size passes the NHprep reserve")
    free = _free_disk(database)
    if free is not None and free < MIN_FREE_DISK_BYTES + 2 * projected + 500_000_000:
        raise ImportRefused("capacity", "not enough free disk for the projected import and its backups")


async def run_import(db: Database, export: Export, embedder: Embedder, options: ImportOptions) -> dict[str, Any]:
    if export.errors:
        raise ImportRefused("invalid_export", "; ".join(export.errors))
    if options.emit_texts is not None and options.apply:
        raise ImportRefused("emit_texts_dry_run_only")
    plans, counts = _plan(export)
    work = _Work(_texts(plans))
    work.report.update(
        applied=False,
        complete=export.complete,
        manifest_sha256=export.manifest_sha256,
        rejects=dict(export.rejects + Counter({k: v for k, v in counts.items() if k not in NORMALISATIONS})),
        ignored=dict(export.ignored),
        normalised={k: v for k, v in counts.items() if k in NORMALISATIONS},
        courses=len(plans),
        lessons=len(work.lessons),
        chunks=sum(len(item.chunks) for item in work.lessons),
    )
    if options.emit_texts is not None:
        seen: set[str] = set()
        for chunk in (chunk for item in work.lessons for chunk in item.chunks):
            if chunk.key not in seen:
                seen.add(chunk.key)
                options.emit_texts.write(json.dumps({"k": chunk.key, "t": chunk.text}, ensure_ascii=False) + "\n")
        work.report["emitted_texts"] = len(seen)
    model = embedder.model
    async with repository.exclusive(db) as lock:
        async with db.sessionmaker() as session:
            state = await repository.load_state(session)
            own = int(await session.scalar(OWN_DATA_BYTES) or 0)
        _diff(export, plans, work, state, model)
        work.report.update(
            to_index=len(work.to_index),
            texts_missing=len(work.missing),
            removals={"courses": len(work.removed_courses), "lessons": len(work.removed_lessons)},
        )
        _guard_removals(work, state, options.allow_removals)
        _project(work, own, db.engine.url.database)
        if work.missing and not options.egress_basis:
            raise ImportRefused("egress_basis_required", f"{len(work.missing)} texts would go to {model}")
        if hasattr(embedder, "has") and not all(embedder.has(text) for text in work.missing.values()):
            raise ImportRefused("vectors_missing", "the --vectors file lacks texts this export needs")
        log.info("nhprep.import planned", extra={"apply": options.apply, **_numbers(work.report)})
        if not options.apply:
            return work.report
        return await _apply(db, lock, export, plans, state, work, embedder, options)


def _numbers(report: dict[str, Any]) -> dict[str, int]:
    return {key: value for key, value in report.items() if isinstance(value, int) and not isinstance(value, bool)}


async def _apply(
    db: Database,
    lock: AsyncConnection,
    export: Export,
    plans: list[CoursePlan],
    state: repository.State,
    work: _Work,
    embedder: Embedder,
    options: ImportOptions,
) -> dict[str, Any]:
    await _guard(db, lock)
    async with db.sessionmaker() as session:
        run_id = await repository.start_run(
            session,
            {
                "kind": "import",
                "operator": options.operator,
                "complete": export.complete,
                "manifest_sha256": export.manifest_sha256,
                "exporter_version": export.exporter_version,
                "schema_version": SCHEMA,
                "access_basis": options.access_basis,
                "egress_basis": options.egress_basis,
            },
        )
        await session.commit()
    report = work.report
    report["backup"] = options.backup
    try:
        ids = await _write_catalogue(db, lock, plans, state, report)
        await _index(db, lock, work, ids, embedder, options.batch_size, report)
        if work.removed_courses or work.removed_lessons:
            await _guard(db, lock)
            async with db.sessionmaker() as session:
                await repository.remove(session, work.removed_courses, work.removed_lessons)
                await session.commit()
        async with db.sessionmaker() as session:
            report["own_bytes_after"] = int(await session.scalar(OWN_DATA_BYTES) or 0)
            report["applied"] = True
            model = embedder.model if report["embedded"] else None
            await repository.finish_run(session, run_id, "succeeded", report, embedding_model=model)
            await session.commit()
    except BaseException as error:
        code = getattr(error, "code", None) or ("lock_lost" if isinstance(error, LockLost) else type(error).__name__)
        log.warning("nhprep.import failed", extra={"reason": code})
        try:
            async with db.sessionmaker() as session:
                await repository.finish_run(session, run_id, "failed", {**report, "error": code})
                await session.commit()
        except Exception as recording:  # noqa: BLE001 - the original error is what matters; it is re-raised
            log.warning("nhprep.import could not record the failure", extra={"error_type": type(recording).__name__})
        raise
    log.info("nhprep.import succeeded", extra=_numbers(report))
    return report


async def _write_catalogue(
    db: Database, lock: AsyncConnection, plans: list[CoursePlan], state: repository.State, report: dict[str, Any]
) -> dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]]:
    """One transaction per course; returns lesson external_id -> (lesson id, course id)."""
    outcomes: dict[str, Counter[str]] = {
        "course_writes": Counter(),
        "lesson_writes": Counter(),
        "item_writes": Counter(),
    }
    ids: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}
    for plan in plans:
        await _guard(db, lock)
        async with db.sessionmaker() as session:
            course_id, outcome = await repository.write_course(session, plan, state.courses.get(plan.slug))
            outcomes["course_writes"][outcome] += 1
            for lesson in plan.lessons:
                lesson_id, outcome = await repository.write_lesson(
                    session, course_id, lesson, state.lessons.get(lesson.external_id), outcomes["item_writes"]
                )
                outcomes["lesson_writes"][outcome] += 1
                ids[lesson.external_id] = (lesson_id, course_id)
            await session.commit()
    report.update({name: dict(counter) for name, counter in outcomes.items()})
    return ids


async def _index(
    db: Database,
    lock: AsyncConnection,
    work: _Work,
    ids: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]],
    embedder: Embedder,
    batch_size: int,
    report: dict[str, Any],
) -> None:
    totals: Counter[str] = Counter()
    pending: list[_Lesson] = []
    vectors: dict[str, Any] = {}

    async def flush() -> None:
        # Read now, not from T0: an item deleted by the catalogue phase takes its chunks with it (FK cascade).
        texts = {chunk.key: chunk.text for item in pending for chunk in item.chunks}
        async with db.sessionmaker() as session:
            present = await repository.chunk_keys(
                session, [ids[i.plan.external_id][0] for i in pending], embedder.model
            )
        needed = (c.key for i in pending for c in i.chunks if (ids[i.plan.external_id][0], c.key) not in present)
        keys = [key for key in dict.fromkeys(needed) if key not in vectors]
        if keys:
            await _guard(db, lock)
            async with db.sessionmaker() as session:
                reused = await repository.reusable_vectors(session, embedder.model, keys)
            vectors.update(reused)
            totals["reused"] += len(reused)
            fresh = [key for key in keys if key not in reused]
            if fresh:
                made = await embed_texts(embedder, [texts[key] for key in fresh], lambda: _guard(db, lock))
                vectors.update(zip(fresh, made, strict=True))
                totals["embedded"] += len(fresh)
        for item in pending:
            await _guard(db, lock)
            lesson_id, course_id = ids[item.plan.external_id]
            async with db.sessionmaker() as session:
                written = await repository.write_chunks(
                    session, lesson_id, course_id, list(item.chunks), _managed(item.plan), embedder.model, vectors
                )
                await session.commit()
            totals.update({f"chunks_{name}": count for name, count in written.items()})
        pending.clear()

    waiting = 0
    for item in work.to_index:
        pending.append(item)
        waiting += sum(1 for chunk in item.chunks if chunk.key in work.missing and chunk.key not in vectors)
        if waiting >= batch_size:
            await flush()
            waiting = 0
    await flush()
    report.update(embedded=totals["embedded"], reused=totals["reused"])
    report["chunk_writes"] = {name: totals[f"chunks_{name}"] for name in ("inserted", "updated", "deleted")}


async def purge(db: Database, *, operator: str, reason: str, apply: bool) -> dict[str, int]:
    """Delete the whole catalogue (rollback = purge); nhprep_sync_runs stays as the audit, with this purge in it."""
    async with repository.exclusive(db) as lock:
        async with db.sessionmaker() as session:
            if not apply:
                return await repository.catalogue_counts(session)
        await repository.ping(lock)
        async with db.sessionmaker() as session:
            counts = await repository.purge(session)
            record = {"kind": "purge", "operator": operator, "reason": reason, "report": {"deleted": counts}}
            await repository.start_run(session, {**record, "status": "succeeded", "finished_at": repository.NOW})
            await session.commit()
    log.info("nhprep.import purged", extra=counts)
    return counts
