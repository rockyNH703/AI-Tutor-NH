"""The daily challenge engine (plan J8 and section 13; owner-approved G1-G8, CD16).

One challenge per program per India day: ten competitive questions (section 5) from the program's whole pool, in an
order fixed by the date, made lazily by the first read of the day. Each member has one attempt (G7); its questions are
served one at a time into a question-bank set (mode `daily`, or `fz_qa` for the QA cohort), whose trigger grades
them and makes them final. A question's window is its `served_at` plus its seconds, plus a 2-second network allowance
(G6); one that passes unanswered stays unanswered. An attempt starts only on its own India day and may go on until
00:30 the next day (G2, G3); the next read after that closes it.

Every change to an attempt happens under its row lock (SELECT ... FOR UPDATE); finishing sets `finished_at` once, in
the transaction that calls progress.on_daily_finished (XP, badges, the completion notice). Nothing here writes XP.
Lock order: the attempt row, then the practice lock (record_practice), then the XP lock (on_daily_finished)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    FzDailyAttempt,
    FzDailyChallenge,
    FzFriendship,
    FzProfile,
    Program,
    ProgramModule,
    ProgramModuleBook,
    QbQuestion,
    QbSet,
    QbSetItem,
    UserNotification,
)
from app.errors import Conflict
from app.friendzone import progress, scoring
from app.friendzone.battles import clock, mine_of, option_keys
from app.friendzone.eligibility import WORDS, competitive
from app.friendzone.relations import LIST_LIMIT, hub_link, is_active, is_blocked
from app.logging import get_logger
from app.notifications.inbox import notify
from app.programs.access import open_program_ids
from app.progress.learning import LEARNING_ZONE, learning_date, record_practice

QUESTIONS = 10
GRACE = timedelta(minutes=30)  # G2: an attempt may go on until 00:30 the next India day
ALLOWANCE = timedelta(seconds=2)  # G6: network delay allowed after a question's time is up

log = get_logger("app.friendzone")


# ------------------------------------------------------------------ the India day
def starts_at(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), LEARNING_ZONE).astimezone(UTC)


def closes_at(day: date) -> datetime:
    """When the day's attempts close for good: the next India midnight plus the grace."""
    return starts_at(day + timedelta(days=1)) + GRACE


def seconds_at(challenge: FzDailyChallenge, position: int, override: int | None = None) -> int:
    """A question's seconds: as the challenge stored them, or FRIEND_ZONE_ROUND_SECONDS (end-to-end tests only)."""
    return override or challenge.seconds[position - 1]


def shown_close(item: QbSetItem, seconds: int, day: date) -> datetime:
    """When the question's time is up, as the student is shown it."""
    return min(item.served_at + timedelta(seconds=seconds), closes_at(day))


def deadline(item: QbSetItem, seconds: int, day: date) -> datetime:
    """The last instant an answer is taken: the time shown plus the network allowance, never past the grace."""
    return min(item.served_at + timedelta(seconds=seconds) + ALLOWANCE, closes_at(day))


# ------------------------------------------------------------------ challenges
def pool(program_id: uuid.UUID, policy: str = "human_only"):
    """The competitive questions of every month's books in this program (each once, however many months map it)."""
    books = (
        select(ProgramModuleBook.book_id)
        .join(ProgramModule, ProgramModule.id == ProgramModuleBook.module_id)
        .where(ProgramModule.program_id == program_id)
    )
    return select(QbQuestion.question_id, WORDS.label("words")).where(
        QbQuestion.book_id.in_(books), competitive(policy)
    )


async def challenge_on(db: AsyncSession, program_id: uuid.UUID, day: date) -> FzDailyChallenge | None:
    return await db.scalar(
        select(FzDailyChallenge).where(
            FzDailyChallenge.program_id == program_id, FzDailyChallenge.challenge_date == day
        )
    )


async def challenge_for(
    db: AsyncSession, program_id: uuid.UUID, day: date, policy: str = "human_only", round_seconds: int | None = None
) -> FzDailyChallenge | None:
    """The program's challenge for `day`, made now if this is the first read. None when fewer than ten competitive
    questions exist (G5): never a fallback to unchecked ones."""
    found = await challenge_on(db, program_id, day)
    if found is not None:
        return found
    picked = (
        await db.execute(
            pool(program_id, policy)
            .order_by(func.md5(QbQuestion.question_id + day.isoformat() + str(program_id)))
            .limit(QUESTIONS)
        )
    ).all()
    if len(picked) < QUESTIONS:
        return None
    await db.execute(
        insert(FzDailyChallenge)
        .values(
            program_id=program_id,
            challenge_date=day,
            question_ids=[row.question_id for row in picked],
            seconds=[round_seconds or scoring.round_seconds(row.words) for row in picked],  # override: e2e only
        )
        .on_conflict_do_nothing(index_elements=[FzDailyChallenge.program_id, FzDailyChallenge.challenge_date])
    )
    return await challenge_on(db, program_id, day)  # ours, or the parallel first read's


async def open_program(db: AsyncSession, me: FzProfile, slug: str) -> Program | None:
    """A program this member's invoice opens now; None for anything else."""
    return await db.scalar(select(Program).where(Program.slug == slug, Program.id.in_(open_program_ids(me.user_id))))


async def visible_challenge(db: AsyncSession, me: FzProfile, challenge_id: uuid.UUID) -> FzDailyChallenge | None:
    """A challenge of a program this member has open; None for anything else."""
    return await db.scalar(
        select(FzDailyChallenge).where(
            FzDailyChallenge.id == challenge_id, FzDailyChallenge.program_id.in_(open_program_ids(me.user_id))
        )
    )


async def carried_over(db: AsyncSession, me: FzProfile, program_id: uuid.UUID, today: date) -> FzDailyChallenge | None:
    """Yesterday's challenge in this program while my attempt at it is unfinished (still in its grace: close_overdue
    has closed it otherwise)."""
    return await db.scalar(
        select(FzDailyChallenge)
        .join(FzDailyAttempt, FzDailyAttempt.challenge_id == FzDailyChallenge.id)
        .where(
            FzDailyChallenge.program_id == program_id,
            FzDailyChallenge.challenge_date == today - timedelta(days=1),
            FzDailyAttempt.user_id == me.user_id,
            FzDailyAttempt.finished_at.is_(None),
        )
    )


# ------------------------------------------------------------------ attempts (the caller commits)
async def attempt_of(
    db: AsyncSession, challenge_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool
) -> FzDailyAttempt | None:
    query = (
        select(FzDailyAttempt)
        .where(FzDailyAttempt.challenge_id == challenge_id, FzDailyAttempt.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return await db.scalar(query.with_for_update() if lock else query)


async def items_of(db: AsyncSession, attempt: FzDailyAttempt, position: int | None = None) -> dict[int, QbSetItem]:
    """The attempt's served questions, by position."""
    if attempt.set_id is None:
        return {}
    query = select(QbSetItem).where(QbSetItem.set_id == attempt.set_id).execution_options(populate_existing=True)
    if position is not None:
        query = query.where(QbSetItem.position == position)
    return {item.position: item for item in (await db.scalars(query)).all()}


async def item_at(db: AsyncSession, attempt: FzDailyAttempt, position: int) -> QbSetItem | None:
    return (await items_of(db, attempt, position)).get(position)


async def start(db: AsyncSession, me: FzProfile, challenge: FzDailyChallenge, now: datetime) -> FzDailyAttempt:
    """My attempt, locked: made on the first call, only during the challenge's own India day (409
    `challenge_closed` otherwise); every later call answers the same attempt (G7)."""
    attempt = await attempt_of(db, challenge.id, me.user_id, lock=True)
    if attempt is None:
        if challenge.challenge_date != learning_date(now):
            raise Conflict("This daily challenge is over.", code="challenge_closed")
        await db.execute(
            insert(FzDailyAttempt)
            .values(challenge_id=challenge.id, user_id=me.user_id, cohort=me.cohort)
            .on_conflict_do_nothing(index_elements=[FzDailyAttempt.challenge_id, FzDailyAttempt.user_id])
        )
        attempt = await attempt_of(db, challenge.id, me.user_id, lock=True)
        log.info("friend zone daily started", extra={"user_id": str(me.user_id), "challenge_id": str(challenge.id)})
    if attempt.set_id is None:
        set_id = await db.scalar(
            insert(QbSet)
            .values(
                user_id=me.user_id,
                request_id=challenge.id,
                mode="fz_qa" if attempt.cohort == "qa" else "daily",  # QA never reaches a real record (section 6)
                filters={"friendZoneDaily": str(challenge.id)},
                requested=QUESTIONS,
            )
            .on_conflict_do_nothing(index_elements=[QbSet.user_id, QbSet.request_id])
            .returning(QbSet.id)
        )
        if set_id is None:
            set_id = await db.scalar(
                select(QbSet.id).where(QbSet.user_id == me.user_id, QbSet.request_id == challenge.id)
            )
        attempt.set_id = set_id
        await db.flush()
    return attempt


async def finish(db: AsyncSession, attempt: FzDailyAttempt, challenge: FzDailyChallenge) -> None:
    """Close the attempt once (the caller holds its row lock), with its rewards in the same transaction."""
    if attempt.finished_at is not None:
        return
    attempt.finished_at = await clock(db)
    if attempt.set_id is not None:
        await db.execute(
            update(QbSet).where(QbSet.id == attempt.set_id, QbSet.closed_at.is_(None)).values(closed_at=func.now())
        )
    await db.flush()
    await progress.on_daily_finished(db, attempt, challenge)
    log.info(
        "friend zone daily finished",
        extra={"user_id": str(attempt.user_id), "challenge_id": str(challenge.id), "served": attempt.served},
    )


async def advance(
    db: AsyncSession,
    attempt: FzDailyAttempt,
    challenge: FzDailyChallenge,
    now: datetime,
    round_seconds: int | None = None,
) -> None:
    """Bring a locked attempt up to the clock: closed after the grace (G2), or finished once the tenth question is
    answered or its window has passed."""
    if attempt.finished_at is not None:
        return
    day = challenge.challenge_date
    if now >= closes_at(day):
        await finish(db, attempt, challenge)
        return
    if attempt.served == QUESTIONS:
        last = await item_at(db, attempt, QUESTIONS)
        seconds = seconds_at(challenge, QUESTIONS, round_seconds)
        if last is None or last.answered_at is not None or now >= deadline(last, seconds, day):
            await finish(db, attempt, challenge)


async def close_overdue(db: AsyncSession, me: FzProfile, now: datetime) -> None:
    """Close my unfinished attempts whose grace has ended (the next read after it, G2)."""
    rows = (
        await db.execute(
            select(FzDailyAttempt, FzDailyChallenge)
            .join(FzDailyChallenge, FzDailyChallenge.id == FzDailyAttempt.challenge_id)
            .where(
                FzDailyAttempt.user_id == me.user_id,
                FzDailyAttempt.finished_at.is_(None),
                FzDailyChallenge.challenge_date < learning_date(now),
            )
            .order_by(FzDailyChallenge.challenge_date, FzDailyAttempt.challenge_id)
            .with_for_update(of=FzDailyAttempt)
            .execution_options(populate_existing=True)
        )
    ).all()
    for attempt, challenge in rows:
        if now >= closes_at(challenge.challenge_date):
            await finish(db, attempt, challenge)


async def serve_next(
    db: AsyncSession,
    attempt: FzDailyAttempt,
    challenge: FzDailyChallenge,
    now: datetime,
    policy: str = "human_only",
    round_seconds: int | None = None,
) -> None:
    """Resume (G1): the current question while it is open and unanswered; otherwise serve the next one. A question
    no longer competitive is withdrawn instead of served (section 5: checked at every serve); it scores nothing."""
    if attempt.finished_at is not None:
        return
    if attempt.served:
        current = await item_at(db, attempt, attempt.served)
        seconds = seconds_at(challenge, attempt.served, round_seconds)
        if (
            current is not None
            and current.answered_at is None
            and now < deadline(current, seconds, challenge.challenge_date)
        ):
            return
    while attempt.served < QUESTIONS:
        position = attempt.served + 1
        question_id = challenge.question_ids[position - 1]
        row = (
            await db.execute(
                select(QbQuestion.topic_id, competitive(policy).label("ok")).where(
                    QbQuestion.question_id == question_id
                )
            )
        ).one_or_none()
        attempt.served = position
        await db.execute(update(QbSet).where(QbSet.id == attempt.set_id).values(served=position))
        if row is not None and row.ok:
            await db.execute(
                insert(QbSetItem)
                .values(
                    set_id=attempt.set_id,
                    position=position,
                    user_id=attempt.user_id,
                    question_id=question_id,
                    topic_id=row.topic_id,
                )
                .on_conflict_do_nothing(index_elements=[QbSetItem.set_id, QbSetItem.position])
            )
            await db.flush()
            return
        attempt.voided = [*attempt.voided, position]  # withdrawn: recorded under the row lock, scores nothing
    await db.flush()
    await finish(db, attempt, challenge)  # the last positions were all withdrawn


async def still_counts(db: AsyncSession, item: QbSetItem, policy: str) -> bool:
    """The question is still competitive, with the content it was served with (section 5: checked at every close)."""
    return bool(
        await db.scalar(
            select(and_(competitive(policy), QbQuestion.content_hash == item.served_hash)).where(
                QbQuestion.question_id == item.question_id
            )
        )
    )


def item_points(item: QbSetItem | None, seconds: int) -> int:
    if item is None or item.answered_at is None:
        return 0
    left = int((item.served_at + timedelta(seconds=seconds) - item.answered_at).total_seconds() * 1000)
    return scoring.points(bool(item.is_correct), left, seconds)


async def answer(
    db: AsyncSession,
    attempt: FzDailyAttempt,
    challenge: FzDailyChallenge,
    position: int,
    choice: str,
    now: datetime,
    policy: str = "human_only",
    round_seconds: int | None = None,
) -> None:
    """Lock in one letter for the open question (the caller holds the attempt's row lock). Final once given: the same
    choice again answers the same; another is 409 `already_answered`; after the window 409 `round_closed`; a letter
    the question does not have 409 `invalid_choice`."""
    closed = Conflict("This question is not open.", code="round_closed")
    item = await item_at(db, attempt, position) if 1 <= position <= attempt.served else None
    if item is None:
        raise closed
    question = await db.get(QbQuestion, item.question_id)
    if question is None or choice not in option_keys(question):
        raise Conflict("That option is not in this question.", code="invalid_choice")
    if item.answered_at is not None:
        if mine_of(item) == choice:
            return
        raise Conflict("You have already answered this question.", code="already_answered")
    if attempt.finished_at is not None or position != attempt.served:
        raise closed
    seconds = seconds_at(challenge, position, round_seconds)
    values = {"chosen": choice} if question.correct_keys is None else {"chosen_keys": [choice]}
    graded = (
        await db.execute(
            update(QbSetItem)
            .where(
                QbSetItem.set_id == item.set_id,
                QbSetItem.position == position,
                QbSetItem.answered_at.is_(None),
                func.clock_timestamp() < deadline(item, seconds, challenge.challenge_date),
            )
            .values(**values)
            .returning(QbSetItem.answered_at, QbSetItem.is_correct)
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    if graded is None:
        raise Conflict("Time is up for this question.", code="round_closed")
    if attempt.cohort == "live":  # QA answers never reach the practice record (section 6)
        await record_practice(db, attempt.user_id, now, graded.answered_at)
    item = await item_at(db, attempt, position)
    if await still_counts(db, item, policy):
        attempt.answered += 1
        attempt.correct += 1 if graded.is_correct else 0
        attempt.score += item_points(item, seconds)
    else:
        attempt.voided = [*attempt.voided, position]  # changed or withdrawn since it was served: scores nothing
    await db.flush()
    if position == QUESTIONS:
        await finish(db, attempt, challenge)


# ------------------------------------------------------------------ friends (G8) and the bell
async def friend_attempts(db: AsyncSession, me: FzProfile, challenge_id: uuid.UUID):
    """(profile, attempt) for each active, unblocked friend of my cohort with an attempt at this challenge."""
    other_id = func.coalesce(
        func.nullif(FzFriendship.user_low, me.user_id), func.nullif(FzFriendship.user_high, me.user_id)
    )
    return (
        await db.execute(
            select(FzProfile, FzDailyAttempt)
            .join(FzFriendship, FzProfile.user_id == other_id)
            .join(
                FzDailyAttempt,
                and_(
                    FzDailyAttempt.user_id == FzProfile.user_id,
                    FzDailyAttempt.challenge_id == challenge_id,
                    FzDailyAttempt.cohort == me.cohort,
                ),
            )
            .where(
                or_(FzFriendship.user_low == me.user_id, FzFriendship.user_high == me.user_id),
                FzFriendship.cohort == me.cohort,
                is_active(FzProfile),
                ~is_blocked(me.user_id, FzProfile.user_id),
            )
            .order_by(func.lower(FzProfile.display_name), FzProfile.id)
            .limit(LIST_LIMIT)
        )
    ).all()


# Programs known to have no daily challenge today (too few competitive questions), per process, for a few minutes:
# GET /me asks on every page load, and the pool query is not cheap. A new day or another policy is a new key.
_EMPTY_FOR = 300.0
_empty: dict[tuple[uuid.UUID, date, str], float] = {}


def _known_empty(program_id: uuid.UUID, day: date, policy: str) -> bool:
    until = _empty.get((program_id, day, policy))
    return until is not None and until > monotonic()


def _remember_empty(program_id: uuid.UUID, day: date, policy: str) -> None:
    if len(_empty) > 1000:
        _empty.clear()
    _empty[(program_id, day, policy)] = monotonic() + _EMPTY_FOR


async def notify_available(
    db: AsyncSession, me: FzProfile, policy: str = "human_only", round_seconds: int | None = None
) -> None:
    """One `daily_available` bell notification per member per India day, when one of their open programs has (or can
    make) a challenge. Cheap after the first call of the day: today's notification is looked for first. Commits
    what it writes."""
    if me.suspended_at is not None:
        return
    today = learning_date(await clock(db))
    key = f"daily-available:{today.isoformat()}"
    if await db.scalar(
        select(exists().where(UserNotification.user_id == me.user_id, UserNotification.dedup_key == key))
    ):
        return
    programs = await db.scalars(
        select(Program.id).where(Program.id.in_(open_program_ids(me.user_id))).order_by(Program.slug)
    )
    for program_id in programs.all():
        if _known_empty(program_id, today, policy):
            continue
        if await challenge_for(db, program_id, today, policy, round_seconds) is None:
            _remember_empty(program_id, today, policy)
        else:
            await notify(
                db,
                me.user_id,
                kind="daily_available",
                title="Today's daily challenge is ready",
                body="Ten questions are waiting for you in Friend Zone.",
                dedup_key=key,
                link=await hub_link(db, me.user_id),
            )
            break
    await db.commit()
