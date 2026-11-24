"""The 1v1 battle engine (plan sections 4 J4-J6, 5, 9; R-B1, R-B2, R-B3, R-T1, R-E3).

A battle is created `invited`; the opponent's accept is their Ready (`waiting`); the challenger's Ready picks the
questions and starts it (`active`) after a countdown. Every round's window is fixed at the start from the database
clock, so the state at any moment follows from the rows and the clock: whoever reads first after a deadline closes
the round (under the battle row lock), and no timer process is needed. Answers go through the question-bank ledger
(`qb_sets` mode `battle`, or `fz_qa` for the QA cohort), whose trigger grades them and makes them final.

Lock order everywhere (section 9): pair lock, then the live locks (user id order), then the battle row, then the
practice lock. Account deletion touches no battle row: a missing player is resolved here, at the next advance."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    FZ_BATTLE_OPEN,
    FzBattle,
    FzBattlePlayer,
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
from app.notifications.inbox import notify
from app.progress.learning import record_practice

INVITE_TTL = timedelta(minutes=10)  # R-B2
WAITING_TTL = timedelta(minutes=3)  # R-B2: the challenger has this long to press Ready after the accept
MAX_PENDING_INVITES = 3  # R-B2: invitations a member may have waiting at once
LIVE = ("waiting", "active")


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


# ------------------------------------------------------------------ the question pool (section 5)
def pool(module_id: uuid.UUID, topic_id: str | None, difficulty: str, policy: str = "human_only"):
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
    db: AsyncSession, module_id: uuid.UUID, topic_id: str | None, difficulty: str, policy: str = "human_only"
) -> int:
    return await db.scalar(select(func.count()).select_from(pool(module_id, topic_id, difficulty, policy).subquery()))


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


async def load(db: AsyncSession, battle_id: uuid.UUID, me: FzProfile, *, lock: bool) -> Loaded | None:
    """A battle I play in (my cohort), its row locked when asked; None for anything else."""
    query = select(FzBattle).where(FzBattle.id == battle_id, FzBattle.cohort == me.cohort)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    battle = await db.scalar(query)
    if battle is None:
        return None
    rows = (
        await db.scalars(
            select(FzBattlePlayer)
            .where(FzBattlePlayer.battle_id == battle.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    players = {row.role: row for row in rows}
    mine = next((role for role, row in players.items() if row.user_id == me.user_id), None)
    return Loaded(battle, players, mine) if mine is not None and len(players) == 2 else None


# ------------------------------------------------------------------ endings
async def end_battle(db: AsyncSession, found: Loaded, status: str, reason: str, result: str | None = None) -> None:
    """Close a battle for good: no more pair key (so no user id stays in history), sets closed, and, for a battle
    decided by play, each present player told (section 9, terminal outcomes)."""
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


async def end_open(db: AsyncSession, a: uuid.UUID, b: uuid.UUID, reason: str) -> None:
    """A block or an unfriend between a and b (the caller holds their pair lock) ends their open battle: before the
    start it is cancelled, after it void (no winner, no stats). Answers already given stay real practice."""
    battle_id = await db.scalar(
        select(FzBattle.id).where(FzBattle.pair_key == pair_key(a, b), FzBattle.status.in_(FZ_BATTLE_OPEN))
    )
    if battle_id is None:
        return
    battle = await db.scalar(
        select(FzBattle).where(FzBattle.id == battle_id).with_for_update().execution_options(populate_existing=True)
    )
    players = (await db.scalars(select(FzBattlePlayer).where(FzBattlePlayer.battle_id == battle_id))).all()
    found = Loaded(battle, {row.role: row for row in players}, "challenger")
    if battle.status == "active":
        await end_battle(db, found, "completed", reason, "void")
    elif battle.status in FZ_BATTLE_OPEN:
        await end_battle(db, found, "cancelled", reason)


# ------------------------------------------------------------------ advancing (the caller holds the row lock)
async def advance(db: AsyncSession, found: Loaded, policy: str = "human_only") -> None:
    """Bring a battle up to the database clock: expiries, a member who left, closed rounds, the round to serve."""
    battle, now = found.battle, await clock(db)
    if battle.status in ("invited", "waiting"):
        if any(player.user_id is None for player in found.players.values()):
            await end_battle(db, found, "cancelled", "deleted")
        elif now >= battle.expires_at:
            await end_battle(db, found, "expired", "expired")
        return
    if battle.status != "active":
        return
    rounds = (
        await db.scalars(
            select(FzBattleRound)
            .where(FzBattleRound.battle_id == battle.id)
            .order_by(FzBattleRound.round)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    for each in rounds:
        if each.closed_at is None and each.closes_at <= now:
            await _close_round(db, found, each, policy)
            if battle.status != "active":
                return
    present = [player for player in found.players.values() if player.user_id is not None]
    if len(present) < 2:
        if present and present[0].answered > 0:
            present[0].winner = True
            await end_battle(db, found, "completed", "deleted", "forfeit")
        else:
            await end_battle(db, found, "completed", "deleted", "void")
        return
    current = next((each for each in rounds if each.opened_at <= now < each.closes_at), None)
    if current is not None:
        await _serve(db, found, current)


async def _serve(db: AsyncSession, found: Loaded, current: FzBattleRound) -> None:
    """Put the open round's question in each player's set (its content hash is taken now, by the trigger)."""
    topic_id = await db.scalar(select(QbQuestion.topic_id).where(QbQuestion.question_id == current.question_id))
    for player in found.players.values():
        if player.set_id is None or player.user_id is None:
            continue
        await db.execute(
            insert(QbSetItem)
            .values(
                set_id=player.set_id,
                position=current.round,
                user_id=player.user_id,
                question_id=current.question_id,
                topic_id=topic_id,
            )
            .on_conflict_do_nothing(index_elements=[QbSetItem.set_id, QbSetItem.position])
        )
        await db.execute(
            update(QbSet).where(QbSet.id == player.set_id).values(served=func.greatest(QbSet.served, current.round))
        )


async def items(db: AsyncSession, found: Loaded, position: int | None = None) -> dict[tuple[str, int], QbSetItem]:
    """The players' served items, by (role, round)."""
    by_set = {player.set_id: role for role, player in found.players.items() if player.set_id is not None}
    if not by_set:
        return {}
    query = select(QbSetItem).where(QbSetItem.set_id.in_(by_set)).execution_options(populate_existing=True)
    if position is not None:
        query = query.where(QbSetItem.position == position)
    return {(by_set[item.set_id], item.position): item for item in (await db.scalars(query)).all()}


def round_points(item: QbSetItem | None, each: FzBattleRound) -> int:
    if item is None or item.answered_at is None or each.voided:
        return 0
    left = int((each.closes_at - item.answered_at).total_seconds() * 1000)
    return scoring.points(bool(item.is_correct), left, each.seconds)


async def _close_round(db: AsyncSession, found: Loaded, each: FzBattleRound, policy: str) -> None:
    """Score a round once its window has passed. The question must still be competitive and unchanged since it was
    served (R-E3); otherwise the round is void for both."""
    battle = found.battle
    served = await items(db, found, each.round)
    current = (
        await db.execute(
            select(QbQuestion.content_hash, competitive(policy).label("ok")).where(
                QbQuestion.question_id == each.question_id
            )
        )
    ).one()
    hashes = {item.served_hash for item in served.values()}
    each.voided = not current.ok or any(found_hash != current.content_hash for found_hash in hashes)
    each.closed_at = each.closes_at
    for role, player in found.players.items():
        item = served.get((role, each.round))
        if each.voided:
            continue
        if item is None or item.answered_at is None:
            player.missed_in_row += 1
            continue
        player.missed_in_row = 0
        player.answered += 1
        player.correct += 1 if item.is_correct else 0
        player.score += round_points(item, each)
        player.answer_ms += int((item.answered_at - each.opened_at).total_seconds() * 1000)
    battle.round, battle.version = each.round, battle.version + 1
    await db.flush()
    challenger, opponent = found.players["challenger"], found.players["opponent"]
    out = [p for p in (challenger, opponent) if p.missed_in_row >= scoring.FORFEIT_AFTER_MISSED]
    if len(out) == 2:
        await end_battle(db, found, "completed", "absent", "void")
    elif out:
        (opponent if out[0] is challenger else challenger).winner = True
        await end_battle(db, found, "completed", "forfeit", "forfeit")
    elif each.round == battle.length:
        tally = [scoring.Tally(p.score, p.correct, p.answer_ms) for p in (challenger, opponent)]
        decided = scoring.winner(*tally)
        if decided is not None:
            (challenger, opponent)[decided].winner = True
        await end_battle(db, found, "completed", "finished", "points" if decided is not None else "draw")


# ------------------------------------------------------------------ changes
async def busy(db: AsyncSession, user_id: uuid.UUID, other_than: uuid.UUID) -> bool:
    """This member is in another waiting or active battle."""
    return bool(
        await db.scalar(
            select(
                select(FzBattlePlayer.battle_id)
                .join(FzBattle, FzBattle.id == FzBattlePlayer.battle_id)
                .where(FzBattlePlayer.user_id == user_id, FzBattle.status.in_(LIVE), FzBattle.id != other_than)
                .exists()
            )
        )
    )


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


async def start(db: AsyncSession, found: Loaded, policy: str = "human_only", round_seconds: int | None = None) -> None:
    """The challenger's Ready: pick the questions now (only competitive ones) and fix every round's window. Too few
    competitive questions cancels the battle (`not_enough_questions`): nothing is recorded."""
    battle = found.battle
    query = pool(battle.module_id, battle.topic_id, battle.difficulty, policy)
    if battle.rematch_of is not None:  # a rematch asks fresh questions, when the pool holds enough of them
        previous = await db.scalar(select(FzBattle.question_ids).where(FzBattle.id == battle.rematch_of))
        fresh = query.where(QbQuestion.question_id.not_in(previous or []))
        if await db.scalar(select(func.count()).select_from(fresh.subquery())) >= battle.length:
            query = fresh
    picked = (
        await db.execute(query.order_by(func.md5(QbQuestion.question_id + str(battle.seed))).limit(battle.length))
    ).all()
    if len(picked) < battle.length:
        await end_battle(db, found, "cancelled", "not_enough_questions")
        return
    now = await clock(db)
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
            )
            .returning(QbSet.id)
        )
    opens = now + timedelta(seconds=scoring.COUNTDOWN_SECONDS)
    for position, row in enumerate(picked, 1):
        seconds = round_seconds or scoring.round_seconds(row.words)  # an override exists in e2e tests only
        closes = opens + timedelta(seconds=seconds)
        db.add(
            FzBattleRound(
                battle_id=battle.id,
                round=position,
                question_id=row.question_id,
                seconds=seconds,
                opened_at=opens,
                closes_at=closes,
            )
        )
        opens = closes + timedelta(seconds=scoring.REVEAL_SECONDS)
    found.me.ready_at = now
    battle.question_ids = [row.question_id for row in picked]
    battle.status, battle.starts_at = "active", now + timedelta(seconds=scoring.COUNTDOWN_SECONDS)
    battle.ends_at, battle.version = opens, battle.version + 1
    await db.flush()


@dataclass(frozen=True)
class Answered:
    choice: str
    answered_at: datetime


async def answer(db: AsyncSession, found: Loaded, position: int, choice: str) -> Answered:
    """Lock in my answer to the open round. Final once given: the same choice again answers the same; another
    choice is 409 `already_answered`; after the deadline 409 `round_closed`."""
    each = await db.scalar(
        select(FzBattleRound).where(FzBattleRound.battle_id == found.battle.id, FzBattleRound.round == position)
    )
    now = await clock(db)
    if found.battle.status != "active" or each is None or now < each.opened_at:
        raise Conflict("This round is not open.", code="round_closed")
    question = await db.get(QbQuestion, each.question_id)
    if choice not in option_keys(question):
        raise Conflict("That option is not in this question.", code="invalid_choice")
    item = (await items(db, found, position)).get((found.mine, position))
    if item is not None and item.answered_at is not None:
        if mine_of(item) == choice:
            return Answered(choice, item.answered_at)
        raise Conflict("You have already answered this round.", code="already_answered")
    if item is None:
        raise Conflict("This round is not open.", code="round_closed")
    values = {"chosen": choice} if question.correct_keys is None else {"chosen_keys": [choice]}
    answered_at = await db.scalar(
        update(QbSetItem)
        .where(
            QbSetItem.set_id == item.set_id,
            QbSetItem.position == position,
            QbSetItem.answered_at.is_(None),
            func.clock_timestamp() < each.closes_at,
        )
        .values(**values)
        .returning(QbSetItem.answered_at)
        .execution_options(synchronize_session=False)
    )
    if answered_at is None:
        raise Conflict("Time is up for this round.", code="round_closed")
    if found.battle.cohort == "live":  # QA answers never reach the practice record (plan section 6)
        await record_practice(db, found.me.user_id, now, answered_at)
    return Answered(choice, answered_at)


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
