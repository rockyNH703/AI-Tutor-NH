"""/v1/nhprep's reads (SQLAlchemy Core, bound parameters only): lesson summaries, a lesson's detail and its study
aids, and the video reference a playback signs. The catalog is the same for every student it is for; who that is
was decided before any of this runs (app/nhprep/router.py)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NhprepCourse, NhprepCourseTrainer, NhprepLesson, NhprepLessonItem, NhprepTrainer
from app.errors import NotFound
from app.nhprep.playback import PROVIDER
from app.nhprep.schemas import (
    NhprepChapterOut,
    NhprepCourseOut,
    NhprepItemOut,
    NhprepLessonOut,
    NhprepLessonSummary,
)

AID_KINDS = ("note", "flashcard", "cheat_sheet", "interview_question")
STATED = ("available", "not_applicable")  # NHprep stated what the kind holds (app/nhprep/normalize.py AUTHORITATIVE)
FAILED = ("failed", "error")
MAX_ITEMS = 500  # far above any lesson's study aids (flashcards are drawn ≤100); a bound, not a page

_LESSON = (
    NhprepLesson.id,
    NhprepLesson.title,
    NhprepLesson.position,
    NhprepLesson.duration_seconds,
    NhprepLesson.course_id,
    NhprepLesson.video_provider,
    NhprepLesson.availability,
    NhprepLesson.removed_at,
    NhprepCourse.title.label("course_title"),
    NhprepCourse.description.label("course_description"),
    NhprepCourse.removed_at.label("course_removed_at"),
)


def _live(model) -> Any:
    return model.removed_at.is_(None)


async def _trainers(db: AsyncSession, course_ids: set[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    rows = await db.execute(
        select(NhprepCourseTrainer.course_id, NhprepTrainer.display_name)
        .join(NhprepTrainer, NhprepTrainer.id == NhprepCourseTrainer.trainer_id)
        .where(NhprepCourseTrainer.course_id.in_(course_ids))
        .order_by(NhprepTrainer.display_name)
    )
    found: dict[uuid.UUID, list[str]] = {}
    for course_id, name in rows:
        found.setdefault(course_id, []).append(name)
    return found


async def _lesson_counts(db: AsyncSession, course_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(NhprepLesson.course_id, func.count())
        .where(NhprepLesson.course_id.in_(course_ids), _live(NhprepLesson))
        .group_by(NhprepLesson.course_id)
    )
    return dict(rows.all())


def _summary(row: Any, trainers: dict, counts: dict) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "course": NhprepCourseOut(id=row.course_id, title=row.course_title),
        "trainers": trainers.get(row.course_id, []),
        "position": row.position,
        "lesson_count": counts.get(row.course_id, 0),
        "duration_seconds": row.duration_seconds,
    }


async def lesson_summaries(db: AsyncSession, ids: list[uuid.UUID]) -> list[NhprepLessonSummary]:
    """In the order asked; a missing or removed lesson (or course) is left out."""
    rows = (
        await db.execute(
            select(*_LESSON)
            .join(NhprepCourse, NhprepCourse.id == NhprepLesson.course_id)
            .where(NhprepLesson.id.in_(ids), _live(NhprepLesson), _live(NhprepCourse))
        )
    ).all()
    courses = {row.course_id for row in rows}
    trainers, counts = await _trainers(db, courses), await _lesson_counts(db, courses)
    by_id = {row.id: row for row in rows}
    return [NhprepLessonSummary(**_summary(by_id[i], trainers, counts)) for i in ids if i in by_id]


async def _row(db: AsyncSession, lesson_id: uuid.UUID) -> Any:
    row = (
        await db.execute(
            select(*_LESSON, NhprepLesson.video_id)
            .join(NhprepCourse, NhprepCourse.id == NhprepLesson.course_id)
            .where(NhprepLesson.id == lesson_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFound("Lesson not found.")
    return row


def _removed(row: Any) -> bool:
    return row.removed_at is not None or row.course_removed_at is not None


async def _neighbour(db: AsyncSession, row: Any, *, before: bool) -> uuid.UUID | None:
    here = tuple_(NhprepLesson.position, NhprepLesson.id)
    point = tuple_(row.position, row.id)
    lesson = NhprepLesson
    order = (lesson.position.desc(), lesson.id.desc()) if before else (lesson.position, lesson.id)
    return await db.scalar(
        select(NhprepLesson.id)
        .where(NhprepLesson.course_id == row.course_id, _live(NhprepLesson), here < point if before else here > point)
        .order_by(*order)
        .limit(1)
    )


def _availability(stated: dict[str, Any], counts: dict[str, int]) -> dict[str, str]:
    def one(kind: str) -> str:
        if counts.get(kind):
            return "present"
        state = stated.get(kind)
        return "absent" if state in STATED else "error" if state in FAILED else "unavailable"

    return {kind: one(kind) for kind in AID_KINDS}


async def lesson_detail(db: AsyncSession, lesson_id: uuid.UUID, *, playable: bool) -> NhprepLessonOut:
    """`playable`: a video signer is configured. A removed lesson keeps its title and says it was removed."""
    row = await _row(db, lesson_id)
    trainers, counts = await _trainers(db, {row.course_id}), await _lesson_counts(db, {row.course_id})
    removed = _removed(row)
    chapters: list[NhprepChapterOut] = []
    kinds: dict[str, int] = {}
    if not removed:
        items = NhprepLessonItem
        chapters = [
            NhprepChapterOut(id=chapter.id, title=chapter.title, start_seconds=chapter.start_seconds)
            for chapter in await db.execute(
                select(items.id, items.title, items.start_seconds)
                .where(items.lesson_id == lesson_id, items.kind == "chapter", items.start_seconds.is_not(None))
                .order_by(items.start_seconds, items.position)
                .limit(MAX_ITEMS)
            )
        ]
        kinds = dict(
            (
                await db.execute(
                    select(items.kind, func.count()).where(items.lesson_id == lesson_id).group_by(items.kind)
                )
            ).all()
        )
    if row.video_provider is None:
        video = "no_video"
    else:
        video = "ready" if playable and row.video_provider == PROVIDER and not removed else "not_configured"
    return NhprepLessonOut(
        **_summary(row, trainers, counts),
        description=row.course_description or None,
        chapters=chapters,
        availability=_availability(row.availability or {}, kinds),
        previous_lesson_id=await _neighbour(db, row, before=True),
        next_lesson_id=await _neighbour(db, row, before=False),
        video=video,
        removed=removed,
    )


def _item(row: Any) -> NhprepItemOut:
    data = row.data or {}
    title, body = row.title or None, row.body_md or None
    if row.kind == "flashcard":
        title, body = data.get("front") or None, data.get("back") or None
    elif row.kind == "interview_question":
        scenario = f"Scenario: {data['scenario']}\n\n" if data.get("scenario") else ""
        title, body = data.get("question") or None, (scenario + (data.get("modelAnswer") or "")) or None
    return NhprepItemOut(id=row.id, kind=row.kind, title=title, body_md=body)


async def lesson_items(db: AsyncSession, lesson_id: uuid.UUID, kind: str) -> list[NhprepItemOut]:
    """One kind of a lesson's study aids, in NHprep's order. A missing or removed lesson is 404."""
    if _removed(await _row(db, lesson_id)):
        raise NotFound("Lesson not found.")
    items = NhprepLessonItem
    rows = await db.execute(
        select(items.id, items.kind, items.title, items.body_md, items.data)
        .where(items.lesson_id == lesson_id, items.kind == kind)
        .order_by(items.position, items.id)
        .limit(MAX_ITEMS)
    )
    return [_item(row) for row in rows]


async def lesson_video(db: AsyncSession, lesson_id: uuid.UUID) -> tuple[str | None, uuid.UUID | None]:
    """The video reference a playback signs (provider, NHprep's video id). A missing or removed lesson is 404."""
    row = await _row(db, lesson_id)
    if _removed(row):
        raise NotFound("Lesson not found.")
    return row.video_provider, row.video_id
