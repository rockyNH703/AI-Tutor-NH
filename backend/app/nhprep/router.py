"""/v1/nhprep: NHprep's lessons, study aids and in-chat videos (docs/api-reference.md, "NHprep").

For NetworkersHome students only (app/nhprep/access.py). For anyone else, and for everyone while NHPREP_MODE=off,
every route is the 404 of a missing resource, before any id, query or body is validated: the router's dependency
runs first. Logs: ids, counts and reason codes, never a video link."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_principal
from app.db.session import get_session
from app.errors import NotFound, RateLimited
from app.nhprep import playback, service
from app.nhprep.access import nhprep_allows
from app.nhprep.schemas import NhprepItemKind, NhprepItemList, NhprepLessonList, NhprepLessonOut, NhprepPlaybackOut


async def require_nhprep(request: Request, principal: Principal = Depends(get_principal)) -> None:
    """Every route: the same 404 as a missing resource for anyone NHprep isn't for, then the per-user limit, so an
    outsider never sees a 429 or takes a limiter slot."""
    if not nhprep_allows(principal.user, request.app.state.settings):
        raise NotFound()
    if not request.app.state.nhprep_limiter.allow(str(principal.user.id)):
        raise RateLimited()


router = APIRouter(prefix="/v1/nhprep", tags=["nhprep"], dependencies=[Depends(require_nhprep)])

_UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# ONE query value: the web app's proxy forwards only the first of a repeated parameter (proxy-core.ts).
LESSON_IDS = rf"^{_UUID}(?:,{_UUID}){{0,9}}$"


@router.get("/lessons", response_model=NhprepLessonList)
async def list_lessons(
    ids: str = Query(..., pattern=LESSON_IDS, description="1-10 lesson ids, comma-separated"),
    db: AsyncSession = Depends(get_session),
):
    """Summaries in the order asked; a missing or removed lesson is left out."""
    wanted = list(dict.fromkeys(uuid.UUID(part) for part in ids.split(",")))
    return NhprepLessonList(items=await service.lesson_summaries(db, wanted))


@router.get("/lessons/{lesson_id}", response_model=NhprepLessonOut)
async def get_lesson(lesson_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_session)):
    playable = request.app.state.nhprep_signer is not None
    return await service.lesson_detail(db, lesson_id, playable=playable)


@router.get("/lessons/{lesson_id}/items", response_model=NhprepItemList)
async def list_lesson_items(
    lesson_id: uuid.UUID, kind: NhprepItemKind = Query(...), db: AsyncSession = Depends(get_session)
):
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
    provider, video_id = await service.lesson_video(db, lesson_id)
    await db.commit()  # the read is done: no transaction stays open while a signer is asked
    return await playback.play(request.app.state.nhprep_signer, lesson_id, provider, video_id)
