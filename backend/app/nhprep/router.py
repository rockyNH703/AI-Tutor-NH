"""/v1/nhprep: NHprep's lessons, study aids and in-chat videos (docs/api-reference.md, "NHprep").

For NetworkersHome students only (app/nhprep/access.py). For anyone else, and for everyone while NHPREP_MODE=off,
every route is the 404 of a missing resource, before any id, query or body is validated: the router's dependency
runs first. Logs: ids, counts and reason codes, never a video link."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_principal
from app.db.models import NhprepLesson
from app.db.session import get_session
from app.errors import NotFound, RateLimited
from app.nhprep import playback, service
from app.nhprep.access import nhprep_allows
from app.nhprep.schemas import NhprepItemKind, NhprepItemList, NhprepLessonList, NhprepLessonOut, NhprepPlaybackOut
from app.programs.access import open_course_ids, programs_allow


async def require_nhprep(
    request: Request, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
) -> None:
    """Every route: the same 404 as a missing resource for anyone NHprep isn't for, then the per-user limit, so an
    outsider never sees a 429 or takes a limiter slot. A NetworkersHome student sees every course; an account an NH
    invoice opens a program to (app/programs) sees only the courses mapped to its programs' months (owner decision
    D3), kept in request.state.nhprep_courses and checked by every lesson route."""
    settings = request.app.state.settings
    request.state.nhprep_courses = None
    if not nhprep_allows(principal.user, settings):
        courses: frozenset = frozenset()
        if settings.nhprep_mode != "off" and programs_allow(principal.user, settings):
            courses = frozenset(await db.scalars(open_course_ids(principal.user.id)))
        if not courses:
            raise NotFound()
        request.state.nhprep_courses = courses
    if not request.app.state.nhprep_limiter.allow(str(principal.user.id)):
        raise RateLimited()


async def _readable(request: Request, db: AsyncSession, lesson_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """The lessons of these the reader may open: every one for a NetworkersHome student, else its programs' only."""
    courses = getattr(request.state, "nhprep_courses", None)
    if courses is None or not lesson_ids:
        return lesson_ids
    allowed = set(
        await db.scalars(
            select(NhprepLesson.id).where(NhprepLesson.id.in_(lesson_ids), NhprepLesson.course_id.in_(list(courses)))
        )
    )
    return [lesson for lesson in lesson_ids if lesson in allowed]


router = APIRouter(prefix="/v1/nhprep", tags=["nhprep"], dependencies=[Depends(require_nhprep)])

_UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# ONE query value: the web app's proxy forwards only the first of a repeated parameter (proxy-core.ts).
LESSON_IDS = rf"^{_UUID}(?:,{_UUID}){{0,9}}$"


@router.get("/lessons", response_model=NhprepLessonList)
async def list_lessons(
    request: Request,
    ids: str = Query(..., pattern=LESSON_IDS, description="1-10 lesson ids, comma-separated"),
    db: AsyncSession = Depends(get_session),
):
    """Summaries in the order asked; a missing or removed lesson, or one the reader may not open, is left out."""
    wanted = await _readable(request, db, list(dict.fromkeys(uuid.UUID(part) for part in ids.split(","))))
    return NhprepLessonList(items=await service.lesson_summaries(db, wanted))


@router.get("/lessons/{lesson_id}", response_model=NhprepLessonOut)
async def get_lesson(lesson_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_session)):
    if not await _readable(request, db, [lesson_id]):
        raise NotFound()
    playable = request.app.state.nhprep_signer is not None
    return await service.lesson_detail(db, lesson_id, playable=playable)


@router.get("/lessons/{lesson_id}/items", response_model=NhprepItemList)
async def list_lesson_items(
    lesson_id: uuid.UUID,
    request: Request,
    kind: NhprepItemKind = Query(...),
    db: AsyncSession = Depends(get_session),
):
    if not await _readable(request, db, [lesson_id]):
        raise NotFound()
    return NhprepItemList(items=await service.lesson_items(db, lesson_id, kind))


@router.post("/lessons/{lesson_id}/playback", response_model=NhprepPlaybackOut, response_model_exclude_none=True)
async def lesson_playback(
    lesson_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """A fresh, short-lived link to the lesson's video, or why it can't play here. Never cached, stored or logged."""
    response.headers["Cache-Control"] = "no-store"
    if not request.app.state.nhprep_playback_limiter.allow(str(principal.user.id)):
        raise RateLimited()
    if not await _readable(request, db, [lesson_id]):
        raise NotFound()
    provider, video_id = await service.lesson_video(db, lesson_id)
    await db.commit()  # the read is done: no transaction stays open while a signer is asked
    return await playback.play(request.app.state.nhprep_signer, lesson_id, provider, video_id)
