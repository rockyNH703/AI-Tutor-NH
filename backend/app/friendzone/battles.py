"""The 1v1 battle engine: a JEE/NEET-style exam (owner-approved exam plan, revision 2, sections 2-4 and 9).

A battle is created `invited`; the opponent's accept is their Ready (`waiting`); the challenger's Ready picks the
paper and starts it (`active`) after a countdown. Both players get the same questions in the same order and one
deadline, `ends_at` = start + seconds per question x length; saves are taken until the cutoff, `ends_at` plus the
network allowance (D8). Answers are drafts (fz_battle_responses, with a server revision for compare-and-set) and are
graded only when the exam completes: when every present player has submitted, or at the first read or write at or
after the cutoff (no timer process). Completion writes each draft into the question-bank ledger one row at a time
(`qb_sets` mode `battle`, or `fz_qa` for the QA cohort; its trigger grades it), recording practice after each row.

Lock order everywhere: pair lock, then the member lock, then the live locks (user id order), then the battle row,
then the practice locks (user id order), then the XP locks (user id order). An overdue active exam may be completed
while at most one invited or waiting row is held, never the reverse. Account deletion touches no battle row: a
missing player is resolved here, at completion. `policy` (FRIEND_ZONE_KEY_POLICY) is required wherever a question's
validity is judged: a default would judge every question by the wrong rule."""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    FZ_BATTLE_OPEN,
    FzBattle,
    FzBattlePlayer,
    FzBattleResponse,
    FzBattleRound,
    FzProfile,
    Program,
    ProgramModule,
    ProgramModuleBook,
    QbQuestion,
    QbSet,
    QbSetItem,
)
from app.errors import Conflict
from app.friendzone import progress, scoring
from app.friendzone.eligibility import WORDS, competitive
from app.friendzone.relations import ordered
from app.friendzone.schemas import MAX_REV, FriendZoneResponseItem
from app.notifications.inbox import notify
from app.progress.learning import record_practice

INVITE_TTL = timedelta(minutes=10)  # R-B2
WAITING_TTL = timedelta(minutes=3)  # R-B2: the challenger has this long to press Ready after the accept
MAX_PENDING_INVITES = 3  # R-B2: invitations a member may have waiting at once
LIVE = ("waiting", "active")
UPGRADED = "upgraded"  # a legacy round battle still open after this release: ended, shown as "ended"


def pair_key(a: uuid.UUID, b: uuid.UUID) -> str:
    low, high = ordered(a, b)
    return f"{low}:{high}"


async def clock(db: AsyncSession) -> datetime:
    return await db.scalar(select(func.clock_timestamp()))


async def _advisory(db: AsyncSession, key: str) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key})


async def lock_live(db: AsyncSession, *user_ids: uuid.UUID) -> None:
    """The per-member live locks, in id order: at most one waiting or active battle per member."""
    for user_id in sorted(set(user_ids)):
        await _advisory(db, f"fz_live|{user_id}")


def cutoff(battle: FzBattle) -> datetime | None:
    """The last instant a save or a submit is taken: the deadline plus the network allowance (D8)."""
    return battle.ends_at + scoring.ALLOWANCE if battle.ends_at is not None else None


# ------------------------------------------------------------------ the question pool (section 5)
def pool(module_id: uuid.UUID, topic_id: str | None, difficulty: str, *, policy: str):
    """The competitive questions a battle in this month (topic, difficulty) may use."""
    query = (
        select(QbQuestion.question_id, QbQuestion.topic_id, WORDS.label("words"))
        .join(ProgramModuleBook, ProgramModuleBook.book_id == QbQuestion.book_id)
        .where(ProgramModuleBook.module_id == module_id, competitive(policy))
    )
    if topic_id is not None:
        query = query.where(QbQuestion.topic_id == topic_id)
    if difficulty != "mixed":
        query = query.where(QbQuestion.difficulty == difficulty)
    return query


async def pool_size(
    db: AsyncSession, module_id: uuid.UUID, topic_id: str | None, difficulty: str, *, policy: str
) -> int:
    query = pool(module_id, topic_id, difficulty, policy=policy)
    return await db.scalar(select(func.count()).select_from(query.subquery()))


# ------------------------------------------------------------------ reading
@dataclass
class Loaded:
    battle: FzBattle
    players: dict[str, FzBattlePlayer]
    mine: str  # my role

    @property
    def me(self) -> FzBattlePlayer:
        return self.players[self.mine]

    @property
    def other(self) -> FzBattlePlayer:
        return self.players["opponent" if self.mine == "challenger" else "challenger"]


async def _players(db: AsyncSession, battle_id: uuid.UUID) -> dict[str, FzBattlePlayer]:
    rows = await db.scalars(
        select(FzBattlePlayer).where(FzBattlePlayer.battle_id == battle_id).execution_options(populate_existing=True)
    )
    return {row.role: row for row in rows.all()}


async def load(db: AsyncSession, battle_id: uuid.UUID, me: FzProfile, *, lock: bool) -> Loaded | None:
    """A battle I play in (my cohort), its row locked when asked; None for anything else."""
    query = select(FzBattle).where(FzBattle.id == battle_id, FzBattle.cohort == me.cohort)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    battle = await db.scalar(query)
    if battle is None:
        return None
    players = await _players(db, battle.id)
    mine = next((role for role, row in players.items() if row.user_id == me.user_id), None)
    return Loaded(battle, players, mine) if mine is not None and len(players) == 2 else None


async def locked(db: AsyncSession, battle_id: uuid.UUID) -> Loaded:
    """Any battle by id, its row locked (operator paths, block, unfriend, overdue completion). The caller holds the
    locks that come before the row in the lock order."""
    battle = await db.scalar(
        select(FzBattle).where(FzBattle.id == battle_id).with_for_update().execution_options(populate_existing=True)
    )
    return Loaded(battle, await _players(db, battle_id), "challenger")


def taken_seconds(battle: FzBattle, player: FzBattlePlayer) -> int | None:
    """D2 (section 9): whole seconds from the start to the final submission, floored."""
    if player.submitted_at is None or battle.starts_at is None:
        return None
    return math.floor((player.submitted_at - battle.starts_at).total_seconds())


# ------------------------------------------------------------------ endings
async def _finish(db: AsyncSession, found: Loaded, status: str, reason: str, result: str | None) -> None:
    """Close a battle for good: no more pair key (so no user id stays in history), sets closed, and, for a battle
    decided by play, XP and each present player told (section 9, terminal outcomes)."""
    battle = found.battle
    battle.status, battle.end_reason, battle.result = status, reason, result
    battle.pair_key, battle.ended_at, battle.version = None, func.clock_timestamp(), battle.version + 1
    set_ids = [player.set_id for player in found.players.values() if player.set_id is not None]
    if set_ids:
        await db.execute(
            update(QbSet).where(QbSet.id.in_(set_ids), QbSet.closed_at.is_(None)).values(closed_at=func.now())
        )
    await db.flush()
    if status == "completed" and result != "void":
        await progress.on_battle_completed(db, battle, found.players)  # XP and badges, once (Phase 2)
        link = await battle_link(db, battle)
        for player in found.players.values():
            if player.user_id is not None:
                await notify(
                    db,
                    player.user_id,
                    kind="battle_finished",
                    title="Battle finished",
                    body="Your quiz battle is over. See how it went in Friend Zone.",
                    dedup_key=f"battle-finished:{battle.id}",
                    link=link,
                )


async def end_battle(
    db: AsyncSession, found: Loaded, status: str, reason: str, result: str | None = None, *, policy: str | None = None
) -> None:
    """End a battle that was not decided by play (a cancel, an expiry, a void). An active exam ended this way (block,
    unfriend, suspension, operator) first grades the drafts given so far as practice, on the questions still valid
    under `policy` (required on this path), keeps every question unclosed (no review, no key), and deletes the
    drafts."""
    battle = found.battle
    if battle.status == "active" and battle.format == "exam":
        if policy is None:
            raise ValueError("ending an active exam needs the key policy")
        rounds, voided, questions = await _validity(db, found, policy=policy)
        await _grade(db, found, rounds, voided, questions)
        await _drop_drafts(db, battle.id)
    await _finish(db, found, status, reason, result)


async def end_open(db: AsyncSession, a: uuid.UUID, b: uuid.UUID, reason: str, *, policy: str) -> None:
    """A block or an unfriend between a and b (the caller holds their pair lock) ends their open battle. It is
    brought up to the clock first, so an exam already past its cutoff keeps its real result; an exam still running
    is void (no winner, no stats), and before the start the battle is cancelled."""
    battle_id = await db.scalar(
        select(FzBattle.id).where(FzBattle.pair_key == pair_key(a, b), FzBattle.status.in_(FZ_BATTLE_OPEN))
    )
    if battle_id is None:
        return
    found = await locked(db, battle_id)
    await advance(db, found, policy=policy)
    if found.battle.status == "active":
        await end_battle(db, found, "completed", reason, "void", policy=policy)
    elif found.battle.status in FZ_BATTLE_OPEN:
        await end_battle(db, found, "cancelled", reason)


# ------------------------------------------------------------------ advancing (the caller holds the row lock)
async def advance(db: AsyncSession, found: Loaded, *, policy: str) -> None:
    """Bring a battle up to the database clock: expiries, a member who left before the start, a legacy round battle
    (ended as `upgraded`), and an exam's completion (every present player submitted, or past the cutoff)."""
    battle = found.battle
    if battle.status in ("invited", "waiting"):
        if battle.format != "exam":
            await end_battle(db, found, "cancelled", UPGRADED)
        elif any(player.user_id is None for player in found.players.values()):
            await end_battle(db, found, "cancelled", "deleted")
        elif await clock(db) >= battle.expires_at:
            await end_battle(db, found, "expired", "expired")
        return
    if battle.status != "active":
        return
    if battle.format != "exam":
        await end_battle(db, found, "completed", UPGRADED, "void")
        return
    present = [player for player in found.players.values() if player.user_id is not None]
    if all(player.submitted_at is not None for player in present) or await clock(db) >= cutoff(battle):
        await complete(db, found, policy=policy)


async def complete(db: AsyncSession, found: Loaded, *, policy: str) -> None:
    """Decide an exam, once (the status is checked and flipped under the row lock), in one transaction: validity
    before any grading, the drafts into the ledger one row at a time, every question closed, the tally, the decision,
    the drafts deleted, then XP and the notices."""
    battle = found.battle
    if battle.status != "active" or battle.format != "exam":
        return
    present = [player for player in found.players.values() if player.user_id is not None]
    for player in present:  # 1. whoever has not submitted is submitted at the deadline
        if player.submitted_at is None:
            player.submitted_at = battle.ends_at
    rounds, voided, questions = await _validity(db, found, policy=policy)  # 2.
    await _grade(db, found, rounds, voided, questions)  # 3.
    closed = min(await clock(db), battle.ends_at)
    for each in rounds:  # 4. what makes `counted`, the review and roundsClosed work
        each.closed_at, each.voided = closed, voided[each.round]
    served = await items(db, found)
    for role, player in found.players.items():  # 5.
        answered = [
            served[(role, each.round)]
            for each in rounds
            if not voided[each.round]
            and (role, each.round) in served
            and served[(role, each.round)].answered_at is not None
        ]
        player.answered = len(answered)
        player.correct = sum(1 for item in answered if item.is_correct)
        player.score = scoring.exam_marks(player.correct, player.answered - player.correct)
    battle.round = battle.length
    await db.flush()
    reason, result = _decide(battle, found.players, present)  # 6.
    await _drop_drafts(db, battle.id)  # 7.
    await _finish(db, found, "completed", reason, result)  # 8.


def _decide(battle: FzBattle, players: dict[str, FzBattlePlayer], present: list[FzBattlePlayer]) -> tuple[str, str]:
    if len(present) < 2:  # the other deleted their account: decided after the tally, never early
        if present and present[0].answered >= 1:
            present[0].winner = True
            return "deleted", "forfeit"
        return "deleted", "void"
    challenger, opponent = players["challenger"], players["opponent"]
    if challenger.answered == 0 and opponent.answered == 0:
        return "absent", "void"
    tallies = [
        scoring.ExamTally(p.score, p.answered - p.correct, taken_seconds(battle, p)) for p in (challenger, opponent)
    ]
    decided = scoring.exam_winner(*tallies)
    if decided is None:
        return "finished", "draw"
    (challenger, opponent)[decided].winner = True
    return "finished", "points"


async def _validity(
    db: AsyncSession, found: Loaded, *, policy: str
) -> tuple[list[FzBattleRound], dict[int, bool], dict[str, QbQuestion]]:
    """Each question's validity now, before any grading: void when it is no longer competitive under `policy`, or
    its content differs from what either player was served (R-E3)."""
    rounds = (
        await db.scalars(
            select(FzBattleRound)
            .where(FzBattleRound.battle_id == found.battle.id)
            .order_by(FzBattleRound.round)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    rows = (
        await db.execute(
            select(QbQuestion, competitive(policy).label("ok"))
            .where(QbQuestion.question_id.in_([each.question_id for each in rounds]))
            .execution_options(populate_existing=True)
        )
    ).all()
    questions = {question.question_id: question for question, _ in rows}
    ok = {question.question_id: bool(fine) for question, fine in rows}
    served = await items(db, found)
    voided = {}
    for each in rounds:
        question = questions[each.question_id]
        hashes = {item.served_hash for (_, position), item in served.items() if position == each.round}
        voided[each.round] = not ok[each.question_id] or any(found_ != question.content_hash for found_ in hashes)
    return list(rounds), voided, questions


async def _grade(
    db: AsyncSession,
    found: Loaded,
    rounds: list[FzBattleRound],
    voided: dict[int, bool],
    questions: dict[str, QbQuestion],
) -> None:
    """The drafts into the ledger, players in user id order, rounds in order, ONE row at a time, each followed at once
    by record_practice (it counts the day's answers, so it must see each one). A draft on a voided question is
    discarded (O1); one whose letter is no longer an option of the live question is skipped (the trigger would
    refuse it, and such a question is voided anyway)."""
    battle = found.battle
    by_round = {each.round: each for each in rounds}
    drafts = (
        await db.scalars(
            select(FzBattleResponse)
            .where(FzBattleResponse.battle_id == battle.id, FzBattleResponse.choice.is_not(None))
            .order_by(FzBattleResponse.round)
            .execution_options(populate_existing=True)
        )
    ).all()
    now = await clock(db)
    graded = [p for p in found.players.values() if p.user_id is not None and p.set_id is not None]
    for player in sorted(graded, key=lambda p: p.user_id):
        for draft in (each for each in drafts if each.role == player.role):
            each = by_round.get(draft.round)
            if each is None or voided[draft.round]:
                continue
            question = questions[each.question_id]
            if draft.choice not in option_keys(question):
                continue
            values = {"chosen": draft.choice} if question.correct_keys is None else {"chosen_keys": [draft.choice]}
            answered_at = await db.scalar(
                update(QbSetItem)
                .where(
                    QbSetItem.set_id == player.set_id,
                    QbSetItem.position == draft.round,
                    QbSetItem.answered_at.is_(None),
                )
                .values(**values)
                .returning(QbSetItem.answered_at)
                .execution_options(synchronize_session=False)
            )
            if answered_at is not None and battle.cohort == "live":  # QA answers never reach the practice record
                await record_practice(db, player.user_id, now, answered_at)


async def _drop_drafts(db: AsyncSession, battle_id: uuid.UUID) -> None:
    await db.execute(delete(FzBattleResponse).where(FzBattleResponse.battle_id == battle_id))


async def items(db: AsyncSession, found: Loaded, position: int | None = None) -> dict[tuple[str, int], QbSetItem]:
    """The players' served items, by (role, round)."""
    by_set = {player.set_id: role for role, player in found.players.items() if player.set_id is not None}
    if not by_set:
        return {}
    query = select(QbSetItem).where(QbSetItem.set_id.in_(by_set)).execution_options(populate_existing=True)
    if position is not None:
        query = query.where(QbSetItem.position == position)
    return {(by_set[item.set_id], item.position): item for item in (await db.scalars(query)).all()}


def round_points(item: QbSetItem | None, each: FzBattleRound, format_: str = "rounds") -> int:
    """A question's points: an exam's marks (+4 / -1 / 0, D1), or a legacy round's speed points (history only)."""
    if item is None or item.answered_at is None or each.voided:
        return 0
    if format_ == "exam":
        return scoring.CORRECT_MARKS if item.is_correct else scoring.INCORRECT_MARKS
    left = int((each.closes_at - item.answered_at).total_seconds() * 1000)
    return scoring.points(bool(item.is_correct), left, each.seconds)


# ------------------------------------------------------------------ changes
def _exam_due():
    """An active exam that is over though nobody has read it: past its cutoff, or every present player has submitted
    (an opponent who left after my submit cannot)."""
    player = aliased(FzBattlePlayer)
    unsubmitted = (
        select(player.battle_id)
        .where(player.battle_id == FzBattle.id, player.user_id.is_not(None), player.submitted_at.is_(None))
        .exists()
    )
    return and_(
        FzBattle.status == "active",
        FzBattle.format == "exam",
        or_(FzBattle.ends_at + scoring.ALLOWANCE <= func.clock_timestamp(), ~unsubmitted),
    )


async def busy(db: AsyncSession, user_id: uuid.UUID, other_than: uuid.UUID) -> bool:
    """This member is in another waiting or active battle (an exam that is over, though unread, no longer counts)."""
    running = ~_exam_due()
    return bool(
        await db.scalar(
            select(
                select(FzBattlePlayer.battle_id)
                .join(FzBattle, FzBattle.id == FzBattlePlayer.battle_id)
                .where(
                    FzBattlePlayer.user_id == user_id,
                    FzBattle.status.in_(LIVE),
                    FzBattle.id != other_than,
                    running,
                )
                .exists()
            )
        )
    )


async def complete_overdue(db: AsyncSession, user_id: uuid.UUID, *, policy: str) -> int:
    """Complete this member's active exams that are over (past their cutoff, or every present player submitted), and
    end any legacy round battle still open, each under its row lock (no timer process). Returns how many it touched:
    the caller commits."""
    due = or_(FzBattle.format != "exam", _exam_due())
    battle_ids = (
        await db.scalars(
            select(FzBattle.id)
            .join(FzBattlePlayer, FzBattlePlayer.battle_id == FzBattle.id)
            .where(FzBattlePlayer.user_id == user_id, FzBattle.status.in_(FZ_BATTLE_OPEN), due)
            .order_by(FzBattle.id)
        )
    ).all()
    for battle_id in battle_ids:
        await advance(db, await locked(db, battle_id), policy=policy)
    return len(battle_ids)


async def expire_due(db: AsyncSession, condition) -> None:
    """Mark invitations and waits past their time expired (a cheap bulk step before lists and creates)."""
    await db.execute(
        update(FzBattle)
        .where(condition, FzBattle.status.in_(("invited", "waiting")), FzBattle.expires_at <= func.clock_timestamp())
        .values(
            status="expired",
            end_reason="expired",
            pair_key=None,
            ended_at=func.clock_timestamp(),
            version=FzBattle.version + 1,
        )
        .execution_options(synchronize_session=False)
    )


async def start(db: AsyncSession, found: Loaded, *, policy: str, seconds_per_question: int | None = None) -> None:
    """The challenger's Ready: pick the paper now (only competitive questions; a rematch takes fresh ones first) and
    fix the one window. Too few competitive questions cancels the battle (`not_enough_questions`): nothing is
    recorded. Both players' whole sets are served now, from the same list, so the trigger takes every content hash
    at once; round 1 counts as visited."""
    battle = found.battle
    previous: list[str] = []
    if battle.rematch_of is not None:
        previous = await db.scalar(select(FzBattle.question_ids).where(FzBattle.id == battle.rematch_of)) or []
    fresh_first = [QbQuestion.question_id.in_(previous)] if previous else []  # false sorts first
    order = func.md5(QbQuestion.question_id + str(battle.seed))
    query = pool(battle.module_id, battle.topic_id, battle.difficulty, policy=policy)
    picked = (await db.execute(query.order_by(*fresh_first, order).limit(battle.length))).all()
    if len(picked) < battle.length:
        await end_battle(db, found, "cancelled", "not_enough_questions")
        return
    now = await clock(db)
    seconds = seconds_per_question or scoring.PER_QUESTION_SECONDS  # an override exists in e2e tests only
    starts = now + timedelta(seconds=scoring.COUNTDOWN_SECONDS)
    ends = starts + timedelta(seconds=seconds * battle.length)
    mode = "fz_qa" if battle.cohort == "qa" else "battle"
    for player in found.players.values():
        player.set_id = await db.scalar(
            insert(QbSet)
            .values(
                user_id=player.user_id,
                request_id=battle.id,
                mode=mode,
                filters={"friendZoneBattle": str(battle.id)},
                requested=battle.length,
                served=battle.length,
            )
            .returning(QbSet.id)
        )
        await db.execute(
            insert(QbSetItem).values(
                [
                    {
                        "set_id": player.set_id,
                        "position": position,
                        "user_id": player.user_id,
                        "question_id": row.question_id,
                        "topic_id": row.topic_id,
                    }
                    for position, row in enumerate(picked, 1)
                ]
            )
        )
    db.add_all(
        FzBattleRound(
            battle_id=battle.id,
            round=position,
            question_id=row.question_id,
            seconds=seconds,
            opened_at=starts,
            closes_at=ends,
        )
        for position, row in enumerate(picked, 1)
    )
    await db.flush()
    db.add_all(FzBattleResponse(battle_id=battle.id, role=role, round=1, rev=1) for role in found.players)
    found.me.ready_at = now
    battle.question_ids = [row.question_id for row in picked]
    battle.format, battle.status, battle.starts_at, battle.ends_at = "exam", "active", starts, ends
    battle.version += 1
    await db.flush()


# ------------------------------------------------------------------ saving and submitting (section 3 and 4)
@dataclass(frozen=True)
class Saved:
    round: int
    applied: bool
    rev: int  # the row's revision now (0: no row)


def _takes_changes(found: Loaded, now: datetime) -> bool:
    battle = found.battle
    return (
        battle.status == "active"
        and battle.format == "exam"
        and found.me.submitted_at is None
        and battle.starts_at <= now < cutoff(battle)
    )


async def _option_keys(db: AsyncSession, battle_id: uuid.UUID) -> dict[int, list[str]]:
    rows = await db.execute(
        select(FzBattleRound.round, QbQuestion)
        .join(QbQuestion, QbQuestion.question_id == FzBattleRound.question_id)
        .where(FzBattleRound.battle_id == battle_id)
    )
    return {position: option_keys(question) for position, question in rows.all()}


def _fits(item: FriendZoneResponseItem, keys: dict[int, list[str]]) -> bool:
    return item.round in keys and (item.op == "visit" or item.choice is None or item.choice in keys[item.round])


async def _write(db: AsyncSession, found: Loaded, item: FriendZoneResponseItem, *, absolute: bool) -> Saved:
    """One item. A visit inserts an empty row if there is none and never changes one. A set is compare-and-set on
    `baseRev` (0: only when no row exists), or, `absolute` (a submit), the final value whatever the revision."""
    pk = {"battle_id": found.battle.id, "role": found.mine, "round": item.round}
    where = and_(*(getattr(FzBattleResponse, name) == value for name, value in pk.items()))
    row = insert(FzBattleResponse).values(**pk, choice=item.choice, marked=item.marked, rev=1)
    index = [FzBattleResponse.battle_id, FzBattleResponse.role, FzBattleResponse.round]
    if item.op == "visit":
        await db.execute(insert(FzBattleResponse).values(**pk, rev=1).on_conflict_do_nothing(index_elements=index))
        applied = True
    elif absolute:
        statement = row.on_conflict_do_update(
            index_elements=index,
            set_={
                "choice": row.excluded.choice,
                "marked": row.excluded.marked,
                "rev": FzBattleResponse.rev + 1,
                "saved_at": func.clock_timestamp(),
            },
            where=FzBattleResponse.rev < MAX_REV,
        )
        applied = await db.scalar(statement.returning(FzBattleResponse.rev)) is not None
    elif item.base_rev == 0:
        statement = row.on_conflict_do_nothing(index_elements=index)
        applied = await db.scalar(statement.returning(FzBattleResponse.rev)) is not None
    else:
        statement = (
            update(FzBattleResponse)
            .where(where, FzBattleResponse.rev == item.base_rev, FzBattleResponse.rev < MAX_REV)
            .values(
                choice=item.choice,
                marked=item.marked,
                rev=FzBattleResponse.rev + 1,
                saved_at=func.clock_timestamp(),
            )
            .returning(FzBattleResponse.rev)
            .execution_options(synchronize_session=False)
        )
        applied = await db.scalar(statement) is not None
    rev = await db.scalar(select(FzBattleResponse.rev).where(where))
    return Saved(item.round, applied, rev or 0)


async def save(db: AsyncSession, found: Loaded, changes: Iterable[FriendZoneResponseItem]) -> list[Saved]:
    """Save my drafts (no grading, no practice, no XP, no version bump). 409 `battle_not_open` unless the exam is
    running for me (started, before the cutoff at the moment the write runs, not submitted); 409 `invalid_choice`
    for a question not in this exam or a letter not among its options (nothing is saved then)."""
    changes = list(changes)
    if not _takes_changes(found, await clock(db)):
        raise Conflict("This battle can no longer be changed.", code="battle_not_open")
    keys = await _option_keys(db, found.battle.id)
    if not all(_fits(item, keys) for item in changes):
        raise Conflict("That option is not in this question.", code="invalid_choice")
    return [await _write(db, found, item, absolute=False) for item in changes]


async def submit(
    db: AsyncSession, found: Loaded, changes: Iterable[FriendZoneResponseItem], *, auto: bool, policy: str
) -> bool:
    """Submit my exam, with the items as final values (no compare-and-set: they are my final state). An explicit
    submit is recorded at the clock, never after the deadline; an auto-submit at the deadline itself, whatever the
    clock says, so nobody gains by it (D2). When the exam no longer takes my changes (ended, past the cutoff, or
    already submitted) nothing is applied and the answer is still the battle. An item that no longer fits its
    question is skipped rather than failing the submit. The exam completes once every present player has
    submitted."""
    battle, now = found.battle, await clock(db)
    if not _takes_changes(found, now):
        await advance(db, found, policy=policy)
        return False
    keys = await _option_keys(db, battle.id)
    for item in changes:
        if _fits(item, keys):
            await _write(db, found, item, absolute=True)
    found.me.submitted_at = battle.ends_at if auto else min(now, battle.ends_at)
    battle.version += 1
    await db.flush()
    await advance(db, found, policy=policy)
    return True


async def responses_of(db: AsyncSession, battle_id: uuid.UUID, role: str) -> list[FzBattleResponse]:
    return list(
        (
            await db.scalars(
                select(FzBattleResponse)
                .where(FzBattleResponse.battle_id == battle_id, FzBattleResponse.role == role)
                .order_by(FzBattleResponse.round)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


# ------------------------------------------------------------------ question content
def option_texts(question: QbQuestion) -> list[str]:
    if question.options is not None:
        return list(question.options)
    return [question.option_a, question.option_b, question.option_c, question.option_d]


def option_keys(question: QbQuestion) -> list[str]:
    return [chr(ord("A") + index) for index in range(len(option_texts(question)))]


def correct_key(question: QbQuestion) -> str:
    return question.correct_keys[0] if question.correct_keys is not None else question.correct_option


def mine_of(item: QbSetItem | None) -> str | None:
    if item is None or item.answered_at is None:
        return None
    return item.chosen or (item.chosen_keys[0] if item.chosen_keys else None)


async def battle_link(db: AsyncSession, battle: FzBattle) -> str:
    slug = await db.scalar(select(Program.slug).where(Program.id == battle.program_id))
    return f"/programs/{slug}/quizzes?zone=friends&battle={battle.id}"


async def module_for(db: AsyncSession, slug: str, month: int) -> ProgramModule | None:
    return await db.scalar(
        select(ProgramModule)
        .join(Program, Program.id == ProgramModule.program_id)
        .where(Program.slug == slug, ProgramModule.month == month)
    )
