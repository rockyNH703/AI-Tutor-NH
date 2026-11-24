"""Friend Zone progression (Phase 2, owner-approved R-P2 as written, CD16): XP, levels, badges and statistics.

XP (live members only; the database refuses a QA row):
- battle completed, having answered at least half its counted rounds: +20; a win (both answered at least half, by
  points): +30; a draw: +10 each. At most 5 rewarded battles a day, and 2 a day against the same friend. A forfeit
  gives the remaining player completion XP only, and the player who forfeited nothing (plan section 9); a void
  battle gives nothing.
- daily challenge completed (all ten served, at least half of the counted ones answered): +25; perfect (10 of 10):
  +15; daily streaks of 3, 7, 14 and 30 days: +20, +50, +100, +200, once per streak.
Levels: level n is reached at 50 * n * (n - 1) XP. Badges: First Battle (a first rewarded battle), Quiz Champion
(10 rewarded wins against at least 3 different friends), Daily Challenger (10 dailies completed), Consistency Master
(7 dailies in a row), Perfect Score (100% in a daily, or in a 10-question battle).

Every award is one row keyed (user, reason, ref), written in the transaction that earned it, under the member's XP
lock (after the battle row lock: plan section 9's order), so replays and races award once."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    FzBadgeUnlock,
    FzBattle,
    FzBattlePlayer,
    FzBattleRound,
    FzDailyAttempt,
    FzDailyChallenge,
    FzXpEvent,
)
from app.friendzone.relations import hub_link
from app.notifications.inbox import notify
from app.progress.learning import LEARNING_ZONE, learning_date

XP = {"battle": 20, "win": 30, "draw": 10, "daily": 25, "perfect": 15}
STREAK_XP = {3: 20, 7: 50, 14: 100, 30: 200}
REWARDED_PER_DAY = 5
REWARDED_PER_FRIEND_PER_DAY = 2
BADGES = {
    "first_battle": ("First Battle", "Finish your first rewarded battle."),
    "quiz_champion": ("Quiz Champion", "Win 10 rewarded battles against at least 3 different friends."),
    "daily_challenger": ("Daily Challenger", "Complete 10 daily challenges."),
    "consistency_master": ("Consistency Master", "Complete daily challenges 7 days in a row."),
    "perfect_score": ("Perfect Score", "Get everything right in a daily challenge or a 10-question battle."),
}


def level_for(xp: int) -> int:
    level = 1
    while 50 * (level + 1) * level <= xp:
        level += 1
    return level


def level_floor(level: int) -> int:
    return 50 * level * (level - 1)


async def lock_xp(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"fz_xp|{user_id}"})


async def total_xp(db: AsyncSession, user_id: uuid.UUID) -> int:
    return await db.scalar(select(func.coalesce(func.sum(FzXpEvent.amount), 0)).where(FzXpEvent.user_id == user_id))


def _day_bounds(moment: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(learning_date(moment), time(0, 0), LEARNING_ZONE).astimezone(UTC)
    return start, start + timedelta(days=1)


async def _award(db: AsyncSession, user_id: uuid.UUID, reason: str, ref_id: str, amount: int) -> bool:
    inserted = await db.scalar(
        insert(FzXpEvent)
        .values(user_id=user_id, cohort="live", reason=reason, ref_id=ref_id, amount=amount)
        .on_conflict_do_nothing(index_elements=[FzXpEvent.user_id, FzXpEvent.reason, FzXpEvent.ref_id])
        .returning(FzXpEvent.id)
    )
    return inserted is not None


async def _unlock(db: AsyncSession, user_id: uuid.UUID, badge: str) -> None:
    inserted = await db.scalar(
        insert(FzBadgeUnlock)
        .values(user_id=user_id, badge=badge, cohort="live")
        .on_conflict_do_nothing(index_elements=[FzBadgeUnlock.user_id, FzBadgeUnlock.badge])
        .returning(FzBadgeUnlock.badge)
    )
    if inserted is not None:
        title, _ = BADGES[badge]
        await notify(
            db,
            user_id,
            kind="badge_unlocked",
            title=f"Badge earned: {title}",
            body=f"You earned the {title} badge in Friend Zone.",
            dedup_key=f"badge:{badge}",
            link=await hub_link(db, user_id),
        )


async def _level_check(db: AsyncSession, user_id: uuid.UUID, before: int) -> None:
    after = level_for(await total_xp(db, user_id))
    if after > level_for(before):
        await notify(
            db,
            user_id,
            kind="level_up",
            title=f"Level {after}",
            body=f"You reached level {after} in Friend Zone.",
            dedup_key=f"level:{after}",
            link=await hub_link(db, user_id),
        )


# ------------------------------------------------------------------ battles
async def on_battle_completed(db: AsyncSession, battle: FzBattle, players: dict[str, FzBattlePlayer]) -> None:
    """XP and badges for a battle decided by play (points, draw or forfeit), live cohort only. Called once, in the
    transaction that completes the battle, under its row lock."""
    if battle.cohort != "live" or battle.result not in ("points", "draw", "forfeit"):
        return
    counted = await db.scalar(
        select(func.count())
        .select_from(FzBattleRound)
        .where(FzBattleRound.battle_id == battle.id, FzBattleRound.closed_at.is_not(None), ~FzBattleRound.voided)
    )
    half = (counted + 1) // 2
    present = [p for p in players.values() if p.user_id is not None]
    rewarded = {p.role: counted > 0 and p.answered >= half for p in present}
    if battle.result == "forfeit":  # plan section 9: completion XP for the remaining player only
        rewarded = {role: ok and players[role].winner for role, ok in rewarded.items()}
    for player in sorted(present, key=lambda p: p.user_id):
        await lock_xp(db, player.user_id)
    now = await db.scalar(select(func.clock_timestamp()))
    for player in present:
        other = next((p for p in present if p is not player), None)
        if not rewarded[player.role] or not await _under_caps(db, player.user_id, other, now):
            continue
        before = await total_xp(db, player.user_id)
        ref = str(battle.id)
        await _award(db, player.user_id, "battle", ref, XP["battle"])
        both = other is not None and rewarded.get(other.role, False)
        if battle.result == "points" and player.winner and both:
            await _award(db, player.user_id, "win", ref, XP["win"])
        if battle.result == "draw":
            await _award(db, player.user_id, "draw", ref, XP["draw"])
        await _unlock(db, player.user_id, "first_battle")
        if battle.length == 10 and counted == 10 and player.correct == 10:
            await _unlock(db, player.user_id, "perfect_score")
        if player.winner and await _champion(db, player.user_id):
            await _unlock(db, player.user_id, "quiz_champion")
        await _level_check(db, player.user_id, before)


async def _under_caps(db: AsyncSession, user_id: uuid.UUID, other: FzBattlePlayer | None, now: datetime) -> bool:
    start, end = _day_bounds(now)
    today = and_(
        FzXpEvent.user_id == user_id,
        FzXpEvent.reason == "battle",
        FzXpEvent.created_at >= start,
        FzXpEvent.created_at < end,
    )
    if await db.scalar(select(func.count()).select_from(FzXpEvent).where(today)) >= REWARDED_PER_DAY:
        return False
    if other is None:
        return True
    battle_ids = [uuid.UUID(ref) for ref in await db.scalars(select(FzXpEvent.ref_id).where(today))]
    with_friend = await db.scalar(
        select(func.count())
        .select_from(FzBattlePlayer)
        .where(FzBattlePlayer.battle_id.in_(battle_ids), FzBattlePlayer.user_id == other.user_id)
    )
    return with_friend < REWARDED_PER_FRIEND_PER_DAY


async def _champion(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """10 rewarded wins against at least 3 different friends (checked only until the badge is earned)."""
    if await db.scalar(
        select(exists().where(FzBadgeUnlock.user_id == user_id, FzBadgeUnlock.badge == "quiz_champion"))
    ):
        return False
    wins = [
        uuid.UUID(ref)
        for ref in await db.scalars(
            select(FzXpEvent.ref_id).where(FzXpEvent.user_id == user_id, FzXpEvent.reason == "win")
        )
    ]
    if len(wins) < 10:
        return False
    opponents = await db.scalar(
        select(func.count(FzBattlePlayer.user_id.distinct())).where(
            FzBattlePlayer.battle_id.in_(wins), FzBattlePlayer.user_id != user_id
        )
    )
    return opponents >= 3


# ------------------------------------------------------------------ daily challenges
def _completed(attempt_table=FzDailyAttempt):
    """SQL: a completed daily: finished, all ten served, and at least half of the counted (not voided) questions
    answered, so a run that answers nothing earns nothing (the same rule as a rewarded battle)."""
    counted = 10 - func.cardinality(attempt_table.voided)
    return and_(
        attempt_table.finished_at.is_not(None),
        attempt_table.served == 10,
        counted > 0,
        attempt_table.answered * 2 >= counted,
    )


def completed(attempt: FzDailyAttempt) -> bool:
    counted = 10 - len(attempt.voided or [])
    return attempt.served == 10 and counted > 0 and attempt.answered * 2 >= counted


async def daily_streak(db: AsyncSession, user_id: uuid.UUID, on: date) -> int:
    """Consecutive India days, ending on `on`, with a completed daily challenge (any program: G4)."""
    days = set(
        await db.scalars(
            select(FzDailyChallenge.challenge_date)
            .join(FzDailyAttempt, FzDailyAttempt.challenge_id == FzDailyChallenge.id)
            .where(
                FzDailyAttempt.user_id == user_id,
                _completed(),
                FzDailyChallenge.challenge_date <= on,
                FzDailyChallenge.challenge_date > on - timedelta(days=40),
            )
        )
    )
    streak, day = 0, on
    while day in days:
        streak, day = streak + 1, day - timedelta(days=1)
    return streak


async def on_daily_finished(db: AsyncSession, attempt: FzDailyAttempt, challenge: FzDailyChallenge) -> None:
    """XP, badges and the completion notice for a finished daily attempt. Called once, in the transaction that sets
    `finished_at`. Only a completed daily counts (`completed`): one closed early (G2), or with fewer than half of its
    questions answered, earns nothing and sends nothing."""
    if not completed(attempt):
        return
    await notify(
        db,
        attempt.user_id,
        kind="daily_completed",
        title="Daily challenge done",
        body="You finished today's daily challenge. See how your friends did in Friend Zone.",
        dedup_key=f"daily-completed:{challenge.id}",
        link=await hub_link(db, attempt.user_id),
    )
    if attempt.cohort != "live":
        return
    user_id, ref = attempt.user_id, str(challenge.id)
    await lock_xp(db, user_id)
    before = await total_xp(db, user_id)
    await _award(db, user_id, "daily", ref, XP["daily"])
    if attempt.correct == 10:
        await _award(db, user_id, "perfect", ref, XP["perfect"])
        await _unlock(db, user_id, "perfect_score")
    await db.flush()
    streak = await daily_streak(db, user_id, challenge.challenge_date)
    for days, amount in STREAK_XP.items():
        if streak != days:
            continue
        start = (challenge.challenge_date - timedelta(days=days - 1)).isoformat()
        if await _award(db, user_id, f"streak_{days}", start, amount):
            await notify(
                db,
                user_id,
                kind="xp_milestone",
                title=f"{days}-day daily streak",
                body=f"{days} daily challenges in a row: +{amount} XP.",
                dedup_key=f"daily-streak:{days}:{start}",
                link=await hub_link(db, user_id),
            )
    if streak >= 7:
        await _unlock(db, user_id, "consistency_master")
    dailies = await db.scalar(
        select(func.count()).select_from(FzDailyAttempt).where(FzDailyAttempt.user_id == user_id, _completed())
    )
    if dailies >= 10:
        await _unlock(db, user_id, "daily_challenger")
    await _level_check(db, user_id, before)


# ------------------------------------------------------------------ statistics (the profile)
@dataclass(frozen=True)
class Stats:
    battles: int
    wins: int
    losses: int
    draws: int
    correct: int
    answered: int
    xp: int
    level: int
    level_floor: int
    next_level_at: int
    badges: list[str]
    dailies_completed: int
    daily_streak: int


async def stats(db: AsyncSession, user_id: uuid.UUID) -> Stats:
    """Counted battles are those decided by play (points, draw or forfeit); void ones are left out."""
    record = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(FzBattlePlayer.winner),
                func.count().filter(FzBattle.result == "draw"),
                func.coalesce(func.sum(FzBattlePlayer.correct), 0),
                func.coalesce(func.sum(FzBattlePlayer.answered), 0),
            )
            .select_from(FzBattlePlayer)
            .join(FzBattle, FzBattlePlayer.battle_id == FzBattle.id)
            .where(
                FzBattlePlayer.user_id == user_id,
                FzBattle.status == "completed",
                FzBattle.result.in_(("points", "draw", "forfeit")),
            )
        )
    ).one()
    battles, wins, draws, correct, answered = (int(value) for value in record)
    xp = await total_xp(db, user_id)
    level = level_for(xp)
    badges = list(
        await db.scalars(
            select(FzBadgeUnlock.badge).where(FzBadgeUnlock.user_id == user_id).order_by(FzBadgeUnlock.unlocked_at)
        )
    )
    dailies = await db.scalar(
        select(func.count()).select_from(FzDailyAttempt).where(FzDailyAttempt.user_id == user_id, _completed())
    )
    today = learning_date(await db.scalar(select(func.clock_timestamp())))
    streak = await daily_streak(db, user_id, today) or await daily_streak(db, user_id, today - timedelta(days=1))
    return Stats(
        battles=battles,
        wins=wins,
        losses=battles - wins - draws,
        draws=draws,
        correct=correct,
        answered=answered,
        xp=xp,
        level=level,
        level_floor=level_floor(level),
        next_level_at=level_floor(level + 1),
        badges=badges,
        dailies_completed=dailies,
        daily_streak=streak,
    )
