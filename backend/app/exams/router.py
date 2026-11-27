"""/v1/practice-exams and a month's exam options (netsec-handoffs/exams-contract.md; app/exams/service.py has the
rules). Every route needs PROGRAMS_MODE for the account and answers another account's exam with the 404 of a missing
one. Writes are limited per user (the practice-test limiter). Logs: ids, counts and flags, never an answer or a key."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_principal
from app.db.session import get_session
from app.errors import Conflict, NotFound, RateLimited
from app.exams import service
from app.exams.schemas import (
    ExamCreatedOut,
    ExamOptionsOut,
    ExamOut,
    ExamStartRequest,
    PracticeExamSaveRequest,
    PracticeExamSubmitRequest,
    SavedOut,
    SubmittedOut,
)
from app.logging import get_logger
from app.programs.access import programs_allow
from app.programs.schemas import SLUG

router = APIRouter(tags=["practice-exams"])
log = get_logger("app.exams")
NO_STORE = {"Cache-Control": "no-store"}
BASE = "/v1/practice-exams"


def _allowed(request: Request, principal: Principal) -> None:
    if not programs_allow(principal.user, request.app.state.settings):
        raise NotFound()


def _limit(request: Request, principal: Principal) -> None:
    if not request.app.state.assessment_limiter.allow(str(principal.user.id)):
        raise RateLimited()


@router.get("/v1/programs/{slug}/months/{month}/exam-options", response_model=ExamOptionsOut)
async def exam_options(
    request: Request,
    response: Response,
    slug: str = Path(pattern=SLUG, max_length=60),
    month: int = Path(ge=1, le=12),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """What a practice test of this month can be made of: its quizzes with the servable questions per difficulty,
    the lengths, durations and scoring rules. 404 unless the month is open to the caller."""
    response.headers.update(NO_STORE)
    _allowed(request, principal)
    found = await service.exam_options(db, principal.user.id, slug, month)
    if found is None:
        raise NotFound()
    return found


@router.post(
    BASE, response_model=ExamCreatedOut, status_code=status.HTTP_201_CREATED, responses={200: {"model": ExamCreatedOut}}
)
async def start_exam(
    body: ExamStartRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Start a practice test: 201 with its id, or 200 with the same id for a request id already used. 404 when the
    month is not open; 422 `one_track` / `unknown_quiz`; 409 `not_enough_questions` with {available, needed}."""
    response.headers.update(NO_STORE)
    _allowed(request, principal)
    _limit(request, principal)
    user_id = principal.user.id  # read now: a rollback in the service expires the session's objects
    exam_id, created = await service.start(db, user_id, body)
    await db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    log.info(
        "practice exam start",
        extra={"user_id": str(user_id), "exam_id": str(exam_id), "created": created, "length": body.length},
    )
    return ExamCreatedOut(exam_id=exam_id)


@router.get(BASE + "/{exam_id}", response_model=ExamOut)
async def get_exam(
    exam_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """The test as its owner sees it: no key, explanation or difficulty until it is submitted. Read after the cutoff,
    it is first submitted with the saved answers (at the deadline)."""
    response.headers.update(NO_STORE)
    _allowed(request, principal)
    exam = await service.load(db, principal.user.id, exam_id)
    out = await service.view(db, exam)
    await db.commit()
    return out


@router.put(BASE + "/{exam_id}/responses", response_model=SavedOut)
async def save_responses(
    exam_id: uuid.UUID,
    body: PracticeExamSaveRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Save answers as they change, when `baseRev` is still the test's revision (409 `stale` with {rev}). 409
    `closed` once submitted or past the cutoff (submitted then, with what was saved before)."""
    response.headers.update(NO_STORE)
    _allowed(request, principal)
    _limit(request, principal)
    exam = await service.load(db, principal.user.id, exam_id)
    try:
        await service.save(db, exam, body.base_rev, body.items)
    except Conflict:
        await db.commit()  # keep an auto-submit load just made
        raise
    out = SavedOut(rev=exam.rev, server_time=await service.clock(db))
    await db.commit()
    return out


@router.post(BASE + "/{exam_id}/submit", response_model=SubmittedOut)
async def submit_exam(
    exam_id: uuid.UUID,
    body: PracticeExamSubmitRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Submit with any unsent answers as final values. Idempotent: a test already submitted returns its stored result
    and writes nothing; `lateItems` lists the items sent that were not applied (after the cutoff or the submit). The
    submit that grades is never rate limited (time-up must get through); one that can apply nothing is."""
    response.headers.update(NO_STORE)
    _allowed(request, principal)
    exam = await service.load(db, principal.user.id, exam_id)
    applying = exam.submitted_at is None
    if not applying:
        try:
            _limit(request, principal)
        except RateLimited:
            await db.commit()  # keep an auto-submit load just made
            raise
    try:
        late = await service.submit(db, exam, body)
    except Conflict:
        await db.commit()
        raise
    out = SubmittedOut(**(await service.view(db, exam)).model_dump(), late_items=late)
    await db.commit()
    log.info(
        "practice exam submit",
        extra={"exam_id": str(exam_id), "applied": applying, "auto": body.auto, "late": len(late)},
    )
    return out
