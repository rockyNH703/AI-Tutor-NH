"""/v1/friend-zone/daily: the daily challenge (plan J8 and section 13; G1-G8). The engine is app/friendzone/daily.py.

Only members with the program open may see a challenge: anything else is the same 404. Every read brings my attempt up
to the database clock first. Before I finish, the response never carries a key, an explanation, a running score or a
later question; friends' results show only once I have finished or the day's grace is over. Logs: ids and outcomes."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_principal
from app.db.models import FzDailyAttempt, FzDailyChallenge, FzProfile, Program, QbQuestion
from app.db.session import get_session
from app.errors import Conflict, NotFound, RateLimited
from app.friendzone import access, daily
from app.friendzone.battles import clock, correct_key, mine_of, option_keys, option_texts
from app.friendzone.daily_schemas import (
    FriendZoneDailyAnswerRequest,
    FriendZoneDailyAttemptOut,
    FriendZoneDailyFriendOut,
    FriendZoneDailyOut,
    FriendZoneDailyQuestionOut,
    FriendZoneDailyReviewOut,
)
from app.friendzone.relations import initials
from app.friendzone.schemas import FriendZoneOptionOut, FriendZoneProgramOut
from app.progress.learning import day_end, learning_date

router = APIRouter(tags=["friend-zone"])


def _policy(request: Request) -> str:
    return request.app.state.settings.friend_zone_key_policy


def _seconds(request: Request) -> int | None:
    return request.app.state.settings.friend_zone_round_seconds  # end-to-end tests only; production refuses it


def _poll(request: Request, principal: Principal) -> None:
    """Daily reads and answers per member (in-process, like battles: about one a second, never security)."""
    if not request.app.state.friend_zone_poll_limiter.allow(str(principal.user.id)):
        raise RateLimited()


def _options(question: QbQuestion) -> list[FriendZoneOptionOut]:
    keys, texts = option_keys(question), option_texts(question)
    return [FriendZoneOptionOut(key=key, text=value) for key, value in zip(keys, texts, strict=True)]


# ------------------------------------------------------------------ the view
def _attempt_out(attempt: FzDailyAttempt | None) -> FriendZoneDailyAttemptOut:
    if attempt is None:
        return FriendZoneDailyAttemptOut(
            status="not_started", served=0, answered=0, correct=None, score=None, started_at=None, finished_at=None
        )
    done = attempt.finished_at is not None
    return FriendZoneDailyAttemptOut(
        status="finished" if done else "in_progress",
        served=attempt.served,
        answered=attempt.answered,
        correct=attempt.correct if done else None,
        score=attempt.score if done else None,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
    )


async def _review(
    db: AsyncSession, attempt: FzDailyAttempt, challenge: FzDailyChallenge, round_seconds: int | None
) -> list[FriendZoneDailyReviewOut]:
    """Every served question, with its key, my choice and the explanation."""
    served = await daily.items_of(db, attempt)
    ids = challenge.question_ids[: attempt.served]
    questions = {q.question_id: q for q in await db.scalars(select(QbQuestion).where(QbQuestion.question_id.in_(ids)))}
    out = []
    for position, question_id in enumerate(ids, 1):
        question, item = questions.get(question_id), served.get(position)
        if question is None:
            continue  # deleted from the bank since: nothing left to show
        voided = position in attempt.voided  # decided when it was served or answered, never re-judged now
        seconds = daily.seconds_at(challenge, position, round_seconds)
        out.append(
            FriendZoneDailyReviewOut(
                position=position,
                stem=question.stem,
                options=_options(question),
                correct_key=correct_key(question),
                explanation=question.explanation,
                my_choice=mine_of(item),
                correct=None if item is None or item.answered_at is None else bool(item.is_correct),
                points=0 if voided else daily.item_points(item, seconds),
                voided=voided,
            )
        )
    return out


def shown(profile: FzProfile, attempt: FzDailyAttempt) -> bool:
    """A friend's score: only when they show their stats, and only once they have finished (like my own)."""
    return profile.show_stats and attempt.finished_at is not None


async def _friends(db: AsyncSession, me: FzProfile, challenge: FzDailyChallenge) -> list[FriendZoneDailyFriendOut]:
    return [
        FriendZoneDailyFriendOut(
            player_id=profile.id,
            display_name=profile.display_name,
            initials=initials(profile.display_name),
            status="completed" if attempt.finished_at is not None else "in_progress",
            score=attempt.score if shown(profile, attempt) else None,
            correct=attempt.correct if shown(profile, attempt) else None,
        )
        for profile, attempt in await daily.friend_attempts(db, me, challenge.id)
    ]


async def daily_out(
    db: AsyncSession,
    me: FzProfile,
    program: Program,
    challenge: FzDailyChallenge | None,
    attempt: FzDailyAttempt | None,
    round_seconds: int | None = None,
) -> FriendZoneDailyOut:
    now = await clock(db)
    today = learning_date(now)
    shown_program = FriendZoneProgramOut(slug=program.slug, title=program.title)
    if challenge is None:
        return FriendZoneDailyOut(
            available=False,
            challenge_id=None,
            date=today,
            program=shown_program,
            starts_at=daily.starts_at(today),
            closes_at=daily.closes_at(today),
            server_time=now,
            attempt=None,
            question=None,
            review=None,
            friends=None,
            next_change_at=day_end(now),
        )
    day = challenge.challenge_date
    finished = attempt is not None and attempt.finished_at is not None
    question, open_until = None, None
    if attempt is not None and not finished and attempt.served:
        item = await daily.item_at(db, attempt, attempt.served)
        seconds = daily.seconds_at(challenge, attempt.served, round_seconds)
        if item is not None and now < daily.deadline(item, seconds, day):
            shown = await db.get(QbQuestion, item.question_id)
            open_until = daily.deadline(item, seconds, day)
            question = FriendZoneDailyQuestionOut(
                position=attempt.served,
                stem=shown.stem,
                options=_options(shown),
                seconds=seconds,
                served_at=item.served_at,
                closes_at=daily.shown_close(item, seconds, day),
                my_choice=mine_of(item),
            )
    if open_until is not None:
        next_change = open_until
    elif attempt is not None and not finished:
        next_change = daily.closes_at(day)
    else:
        next_change = now if day < today else day_end(now)  # a newer challenge is already waiting
    return FriendZoneDailyOut(
        available=True,
        challenge_id=challenge.id,
        date=day,
        program=shown_program,
        starts_at=daily.starts_at(day),
        closes_at=daily.closes_at(day),
        server_time=now,
        attempt=_attempt_out(attempt),
        question=question,
        review=await _review(db, attempt, challenge, round_seconds) if finished else None,
        friends=await _friends(db, me, challenge) if finished or now >= daily.closes_at(day) else None,
        next_change_at=next_change,
    )


async def _mine(db: AsyncSession, me: FzProfile, challenge_id: uuid.UUID) -> tuple[FzDailyChallenge, Program]:
    challenge = await daily.visible_challenge(db, me, challenge_id)
    if challenge is None:
        raise NotFound()
    return challenge, await db.get(Program, challenge.program_id)


# ------------------------------------------------------------------ routes
@router.get("/daily", response_model=FriendZoneDailyOut)
async def get_daily(
    request: Request,
    response: Response,
    program: str = Query(min_length=1, max_length=60),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Today's challenge in this program (made by the first read of the day), or yesterday's while my attempt at it
    is in its grace. `available: false` when the program has fewer than ten checked questions today (G5)."""
    response.headers.update(access.NO_STORE)
    _poll(request, principal)
    me = await access.member(request, principal, db)
    found = await daily.open_program(db, me, program)
    if found is None:
        raise NotFound()
    now: datetime = await clock(db)
    await daily.close_overdue(db, me, now)
    today = learning_date(now)
    challenge = await daily.carried_over(db, me, found.id, today) or await daily.challenge_for(
        db, found.id, today, _policy(request), _seconds(request)
    )
    attempt = None
    if challenge is not None:
        attempt = await daily.attempt_of(db, challenge.id, me.user_id, lock=True)
        if attempt is not None:
            await daily.advance(db, attempt, challenge, now, _seconds(request))
    await db.commit()
    return await daily_out(db, me, found, challenge, attempt, _seconds(request))


@router.post("/daily/{challenge_id}/next", response_model=FriendZoneDailyOut)
async def next_question(
    challenge_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Start (only on the challenge's own India day: 409 `challenge_closed` after it) or resume my attempt, then
    serve the next question unless the current one is still open and unanswered. Repeating it is safe."""
    response.headers.update(access.NO_STORE)
    _poll(request, principal)
    me = await access.member(request, principal, db)
    challenge, program = await _mine(db, me, challenge_id)
    now = await clock(db)
    attempt = await daily.start(db, me, challenge, now)
    await daily.advance(db, attempt, challenge, now, _seconds(request))
    await daily.serve_next(db, attempt, challenge, now, _policy(request), _seconds(request))
    await db.commit()
    return await daily_out(db, me, program, challenge, attempt, _seconds(request))


@router.post("/daily/{challenge_id}/answers", response_model=FriendZoneDailyOut)
async def answer_question(
    challenge_id: uuid.UUID,
    body: FriendZoneDailyAnswerRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Lock in one letter for the open question: final at once, graded by the database. Answering the tenth
    finishes the attempt."""
    response.headers.update(access.NO_STORE)
    _poll(request, principal)
    me = await access.member(request, principal, db)
    challenge, program = await _mine(db, me, challenge_id)
    attempt = await daily.attempt_of(db, challenge.id, me.user_id, lock=True)
    if attempt is None:
        raise Conflict("This question is not open.", code="round_closed")
    now = await clock(db)
    await daily.advance(db, attempt, challenge, now, _seconds(request))
    try:
        await daily.answer(db, attempt, challenge, body.position, body.choice, now, _policy(request), _seconds(request))
    except Conflict:
        await db.commit()  # keep what advancing did
        raise
    await db.commit()
    return await daily_out(db, me, program, challenge, attempt, _seconds(request))
