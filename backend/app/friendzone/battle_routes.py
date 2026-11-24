"""/v1/friend-zone/battles: 1v1 quiz battles between friends in the same program (P1.3; plan sections 4, 9, 10).

The engine is app/friendzone/battles.py. Every state read brings the battle up to the database clock first, so
polling is what moves a battle on. A battle that is not the caller's, or not in their cohort, is one 404. The state
never carries a key, an explanation or the opponent's pick before the round has closed, nor a future question."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_auth_service, get_principal
from app.auth.service import AuthService
from app.db.models import (
    FZ_BATTLE_OPEN,
    FzBattle,
    FzBattlePlayer,
    FzBattleRound,
    FzProfile,
    NhInvoice,
    Program,
    ProgramModule,
    QbQuestion,
)
from app.db.session import get_session
from app.errors import Conflict, NotFound, RateLimited
from app.friendzone import access, battles, relations
from app.friendzone.schemas import (
    FriendZoneAnswerRequest,
    FriendZoneBattleOut,
    FriendZoneBattlePlayerOut,
    FriendZoneBattlesOut,
    FriendZoneBattleSummaryOut,
    FriendZoneCreateBattleRequest,
    FriendZoneOptionOut,
    FriendZoneProgramOut,
    FriendZoneQuestionOut,
    FriendZoneRematchRequest,
    FriendZoneReviewOut,
    FriendZoneRoundOut,
)
from app.logging import get_logger
from app.notifications.inbox import notify
from app.programs.access import open_program_ids

router = APIRouter(tags=["friend-zone"])
log = get_logger("app.friendzone")
PAGE = 20


def _not_open() -> Conflict:
    return Conflict("This battle can no longer be changed.", code="battle_not_open")


def _policy(request: Request) -> str:
    return request.app.state.settings.friend_zone_key_policy


def _poll(request: Request, principal: Principal) -> None:
    """Battle reads and answers per member (in-process; plan section 10: about one a second, never security)."""
    if not request.app.state.friend_zone_poll_limiter.allow(str(principal.user.id)):
        raise RateLimited()


async def _mine(
    db: AsyncSession, me: FzProfile, battle_id: uuid.UUID, policy: str, *, role: str | None = None
) -> battles.Loaded:
    """My battle, its row locked and brought up to the clock; 404 when it is not mine (or I am not `role`)."""
    found = await battles.load(db, battle_id, me, lock=True)
    if found is None or (role is not None and found.mine != role):
        raise NotFound()
    await battles.advance(db, found, policy)
    return found


# ------------------------------------------------------------------ views
async def _profiles(db: AsyncSession, user_ids) -> dict[uuid.UUID, FzProfile]:
    present = [user_id for user_id in user_ids if user_id is not None]
    if not present:
        return {}
    return {row.user_id: row for row in await db.scalars(select(FzProfile).where(FzProfile.user_id.in_(present)))}


def _player(player: FzBattlePlayer, profiles: dict[uuid.UUID, FzProfile]) -> FriendZoneBattlePlayerOut:
    """A participant as a former opponent may see them: name and initials, never a photo (privacy matrix)."""
    profile = profiles.get(player.user_id) if player.user_id is not None else None
    return FriendZoneBattlePlayerOut(
        player_id=profile.id if profile else None,
        display_name=profile.display_name if profile else None,
        initials=relations.initials(profile.display_name) if profile else None,
        role=player.role,
        ready=player.ready_at is not None,
        score=player.score,
        correct=player.correct,
        answered=player.answered,
        winner=player.winner,
    )


def _options(question: QbQuestion) -> list[FriendZoneOptionOut]:
    keys, texts = battles.option_keys(question), battles.option_texts(question)
    return [FriendZoneOptionOut(key=key, text=value) for key, value in zip(keys, texts, strict=True)]


async def _program(db: AsyncSession, battle: FzBattle) -> tuple[FriendZoneProgramOut, int]:
    row = (
        await db.execute(
            select(Program.slug, Program.title, ProgramModule.month)
            .join(ProgramModule, ProgramModule.program_id == Program.id)
            .where(ProgramModule.id == battle.module_id)
        )
    ).one()
    return FriendZoneProgramOut(slug=row.slug, title=row.title), row.month


# Why a battle ended, as its players may see it. A block, an unfriend, a suspension or a deleted account all read
# "ended": none of them may be told apart (I21).
SHOWN_REASONS = ("finished", "forfeit", "absent", "expired", "declined", "cancelled", "not_enough_questions")


def public_reason(reason: str | None) -> str | None:
    return reason if reason is None or reason in SHOWN_REASONS else "ended"


def _next_change(battle: FzBattle, rounds, now: datetime) -> datetime | None:
    """When polling can next show something new."""
    if battle.status in ("invited", "waiting"):
        return battle.expires_at
    if battle.status != "active":
        return None
    for each in rounds:
        if now < each.opened_at:
            return each.opened_at
        if now < each.closes_at:
            return each.closes_at
    return battle.ends_at


async def battle_out(db: AsyncSession, found: battles.Loaded) -> FriendZoneBattleOut:
    battle, now = found.battle, await battles.clock(db)
    rounds = (
        await db.scalars(
            select(FzBattleRound)
            .where(FzBattleRound.battle_id == battle.id)
            .order_by(FzBattleRound.round)
            .execution_options(populate_existing=True)
        )
    ).all()
    served = await battles.items(db, found)
    ids = [each.question_id for each in rounds]
    questions = {
        row.question_id: row for row in await db.scalars(select(QbQuestion).where(QbQuestion.question_id.in_(ids)))
    }
    mine, theirs = found.mine, found.other.role
    profiles = await _profiles(db, [player.user_id for player in found.players.values()])
    program, month = await _program(db, battle)

    current = None
    if battle.status == "active":
        each = next((r for r in rounds if r.closed_at is None and r.opened_at <= now < r.closes_at), None)
        if each is not None:
            question = questions[each.question_id]
            current = FriendZoneQuestionOut(
                round=each.round,
                stem=question.stem,
                options=_options(question),
                seconds=each.seconds,
                opens_at=each.opened_at,
                closes_at=each.closes_at,
                my_choice=battles.mine_of(served.get((mine, each.round))),
            )
    closed = [each for each in rounds if each.closed_at is not None]
    shown = [
        FriendZoneRoundOut(
            round=each.round,
            voided=each.voided,
            correct_key=battles.correct_key(questions[each.question_id]),
            my_choice=battles.mine_of(served.get((mine, each.round))),
            their_choice=battles.mine_of(served.get((theirs, each.round))),
            my_points=battles.round_points(served.get((mine, each.round)), each),
            their_points=battles.round_points(served.get((theirs, each.round)), each),
        )
        for each in closed
    ]
    review = None
    if battle.status == "completed":
        review = [
            FriendZoneReviewOut(
                round=each.round,
                stem=questions[each.question_id].stem,
                options=_options(questions[each.question_id]),
                correct_key=battles.correct_key(questions[each.question_id]),
                explanation=questions[each.question_id].explanation,
                voided=each.voided,
                my_choice=battles.mine_of(served.get((mine, each.round))),
                their_choice=battles.mine_of(served.get((theirs, each.round))),
            )
            for each in closed
        ]
    return FriendZoneBattleOut(
        battle_id=battle.id,
        status=battle.status,
        end_reason=public_reason(battle.end_reason),
        result=battle.result,
        program=program,
        month=month,
        topic_id=battle.topic_id,
        difficulty=battle.difficulty,
        length=battle.length,
        rounds_closed=len(closed),
        expires_at=battle.expires_at if battle.status in ("invited", "waiting") else None,
        starts_at=battle.starts_at,
        ends_at=battle.ends_at,
        next_change_at=_next_change(battle, rounds, now),
        server_time=now,
        me=_player(found.me, profiles),
        opponent=_player(found.other, profiles),
        question=current,
        rounds=shown,
        review=review,
    )


# ------------------------------------------------------------------ list
@router.get("/battles", response_model=FriendZoneBattlesOut)
async def list_battles(
    request: Request,
    response: Response,
    view: str = Query("open", pattern="^(open|history)$"),
    cursor: str | None = Query(None, max_length=200),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """My open battles (invitations either way, waiting, active), or my finished ones, newest first."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    mine = select(FzBattlePlayer.battle_id).where(FzBattlePlayer.user_id == me.user_id)
    await battles.expire_due(db, FzBattle.id.in_(mine))
    await db.commit()
    is_open = FzBattle.status.in_(FZ_BATTLE_OPEN)
    query = (
        select(FzBattle, FzBattlePlayer.role)
        .join(FzBattlePlayer, FzBattlePlayer.battle_id == FzBattle.id)
        .where(
            FzBattlePlayer.user_id == me.user_id, FzBattle.cohort == me.cohort, is_open if view == "open" else ~is_open
        )
        .order_by(FzBattle.created_at.desc(), FzBattle.id.desc())
        .limit(PAGE + 1)
    )
    if cursor:
        query = query.where(tuple_(FzBattle.created_at, FzBattle.id) < tuple_(*access.decode_cursor(cursor)))
    rows = (await db.execute(query)).all()
    page = rows[:PAGE]
    others = (
        await db.scalars(
            select(FzBattlePlayer).where(
                FzBattlePlayer.battle_id.in_([battle.id for battle, _ in page]),
                or_(FzBattlePlayer.user_id.is_(None), FzBattlePlayer.user_id != me.user_id),
            )
        )
    ).all()
    other_of = {row.battle_id: row for row in others}
    profiles = await _profiles(db, [row.user_id for row in others])
    items = []
    for battle, role in page:
        program, month = await _program(db, battle)
        items.append(
            FriendZoneBattleSummaryOut(
                battle_id=battle.id,
                status=battle.status,
                result=battle.result,
                role=role,
                opponent=_player(other_of[battle.id], profiles),
                program=program,
                month=month,
                length=battle.length,
                created_at=battle.created_at,
                ended_at=battle.ended_at,
                expires_at=battle.expires_at if battle.status in ("invited", "waiting") else None,
            )
        )
    last = page[-1][0] if page else None
    return FriendZoneBattlesOut(
        items=items, next_cursor=access.encode_cursor(last.created_at, last.id) if len(rows) > PAGE else None
    )


# ------------------------------------------------------------------ create
def _open_for(program_id: uuid.UUID, user_id: uuid.UUID):
    """SQL: this program is open for this member now (a claimed, active, unexpired invoice)."""
    return open_program_ids(user_id).where(NhInvoice.program_id == program_id).exists()


async def _replay(db: AsyncSession, me: FzProfile, request_id: uuid.UUID) -> battles.Loaded | None:
    battle_id = await db.scalar(
        select(FzBattlePlayer.battle_id).where(
            FzBattlePlayer.user_id == me.user_id, FzBattlePlayer.request_id == request_id
        )
    )
    return await battles.load(db, battle_id, me, lock=False) if battle_id is not None else None


@router.post("/battles", response_model=FriendZoneBattleOut, status_code=status.HTTP_201_CREATED)
async def create_battle(
    body: FriendZoneCreateBattleRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
):
    """Challenge a friend in a program you both have open, on a month (and a topic). The same `requestId` again
    answers the same battle. The pool must hold enough competitive questions (409 `not_enough_questions`)."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    replay = await _replay(db, me, body.request_id)
    if replay is not None:
        return await battle_out(db, replay)
    module = await battles.module_for(db, body.program, body.month)
    if module is None:
        raise NotFound()
    choice = Choice(body.opponent_id, module, body.topic_id, body.difficulty, body.length, body.request_id)
    return await _challenge(request, principal, db, auth, me, choice)


@router.post("/battles/{battle_id}/rematch", response_model=FriendZoneBattleOut, status_code=status.HTTP_201_CREATED)
async def rematch(
    battle_id: uuid.UUID,
    body: FriendZoneRematchRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
):
    """Phase 2 Rematch: the same friend and settings again, as a new invitation the friend must accept, with fresh
    questions where the pool allows. Only after a battle decided by play (not a void one). Either player may ask."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    replay = await _replay(db, me, body.request_id)
    if replay is not None:
        return await battle_out(db, replay)
    old = await battles.load(db, battle_id, me, lock=False)
    if old is None or old.battle.status != "completed" or old.battle.result == "void" or old.other.user_id is None:
        raise NotFound()
    other = await db.scalar(select(FzProfile).where(FzProfile.user_id == old.other.user_id))
    module = await db.get(ProgramModule, old.battle.module_id)
    if other is None or module is None:
        raise NotFound()
    previous = old.battle
    choice = Choice(
        other.id, module, previous.topic_id, previous.difficulty, previous.length, body.request_id, previous.id
    )
    return await _challenge(request, principal, db, auth, me, choice)


@dataclass(frozen=True)
class Choice:
    """What an invitation asks for."""

    opponent_id: uuid.UUID
    module: ProgramModule
    topic_id: str | None
    difficulty: str
    length: int
    request_id: uuid.UUID
    rematch_of: uuid.UUID | None = None


async def _challenge(
    request: Request, principal: Principal, db: AsyncSession, auth: AuthService, me: FzProfile, choice: Choice
) -> FriendZoneBattleOut:
    """Create an invitation (a new battle or a rematch) after every check: friends, a program both have open, enough
    competitive questions, one open battle per pair, three waiting invitations per member, the daily limit."""
    await auth.throttle_friend_zone_battle(principal.user)  # commits the count
    other = await relations.visible(db, me, choice.opponent_id)
    if other is None or await relations.relation(db, me, other) != "friend":
        raise NotFound()
    program_id = choice.module.program_id
    if not await db.scalar(select(and_(_open_for(program_id, me.user_id), _open_for(program_id, other.user_id)))):
        raise NotFound()  # battles only between friends in a program both have open (CD12)
    available = await battles.pool_size(db, choice.module.id, choice.topic_id, choice.difficulty, _policy(request))
    if available < choice.length:
        short = Conflict("There are not enough checked questions for this choice yet.", code="not_enough_questions")
        short.current = {"available": available, "needed": choice.length}
        raise short
    await relations.lock_pair(db, me.user_id, other.user_id)
    await relations.lock_members(db, me.user_id)  # the waiting-invitations limit, against parallel invitations
    replay = await _replay(db, me, choice.request_id)  # a parallel duplicate that won the lock
    if replay is not None:
        return await battle_out(db, replay)
    key = battles.pair_key(me.user_id, other.user_id)
    await battles.expire_due(db, FzBattle.pair_key == key)
    existing = await db.scalar(select(FzBattle.id).where(FzBattle.pair_key == key))
    if existing is not None:
        taken = Conflict("You already have a battle with this friend.", code="battle_exists")
        taken.current = {"battleId": str(existing)}
        raise taken
    waiting = await db.scalar(
        select(func.count())
        .select_from(FzBattle)
        .join(FzBattlePlayer, FzBattlePlayer.battle_id == FzBattle.id)
        .where(FzBattlePlayer.user_id == me.user_id, FzBattlePlayer.role == "challenger", FzBattle.status == "invited")
    )
    if waiting >= battles.MAX_PENDING_INVITES:
        raise Conflict(f"You can have {battles.MAX_PENDING_INVITES} invitations waiting at once.", code="invite_limit")
    battle = FzBattle(
        cohort=me.cohort,
        program_id=program_id,
        module_id=choice.module.id,
        topic_id=choice.topic_id,
        difficulty=choice.difficulty,
        length=choice.length,
        pair_key=key,
        status="invited",
        expires_at=await battles.clock(db) + battles.INVITE_TTL,
        rematch_of=choice.rematch_of,
    )
    db.add(battle)
    await db.flush()
    db.add_all(
        [
            FzBattlePlayer(
                battle_id=battle.id,
                role="challenger",
                cohort=me.cohort,
                user_id=me.user_id,
                request_id=choice.request_id,
            ),
            FzBattlePlayer(battle_id=battle.id, role="opponent", cohort=me.cohort, user_id=other.user_id),
        ]
    )
    await db.flush()
    again = choice.rematch_of is not None
    await notify(
        db,
        other.user_id,
        kind="battle_invite",
        title="Rematch invitation" if again else "Quiz battle invitation",
        body="A friend wants a rematch in Friend Zone." if again else "A friend challenged you to a quiz battle.",
        dedup_key=f"battle-invite:{battle.id}",
        link=await battles.battle_link(db, battle),
    )
    await db.commit()
    log.info(
        "friend zone battle created",
        extra={"user_id": str(principal.user.id), "battle_id": str(battle.id), "rematch": again},
    )
    return await battle_out(db, await battles.load(db, battle.id, me, lock=False))


# ------------------------------------------------------------------ one battle
@router.get("/battles/{battle_id}", response_model=FriendZoneBattleOut)
async def get_battle(
    battle_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """The battle as it is now (poll it: `nextChangeAt` says when something can change)."""
    response.headers.update(access.NO_STORE)
    _poll(request, principal)
    me = await access.member(request, principal, db)
    found = await _mine(db, me, battle_id, _policy(request))
    await db.commit()
    return await battle_out(db, found)


@router.post("/battles/{battle_id}/accept", response_model=FriendZoneBattleOut)
async def accept_battle(
    battle_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Accept an invitation: it counts as your Ready. Accepting twice answers the same; 409 `busy_in_battle` while
    either of you is in another battle."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    peek = await battles.load(db, battle_id, me, lock=False)
    if peek is None or peek.mine != "opponent":
        raise NotFound()
    await battles.lock_live(db, *[p.user_id for p in peek.players.values() if p.user_id is not None])
    found = await _mine(db, me, battle_id, _policy(request), role="opponent")
    battle = found.battle
    if battle.status == "invited":
        if await battles.busy(db, me.user_id, battle.id) or await battles.busy(db, found.other.user_id, battle.id):
            await db.commit()
            raise Conflict("One of you is already in a battle.", code="busy_in_battle")
        now = await battles.clock(db)
        battle.status, battle.expires_at, battle.version = "waiting", now + battles.WAITING_TTL, battle.version + 1
        found.me.ready_at = now
        await db.flush()
        await notify(
            db,
            found.other.user_id,
            kind="battle_accepted",
            title="Challenge accepted",
            body="Your friend accepted your quiz battle. Press Ready to start.",
            dedup_key=f"battle-accepted:{battle.id}",
            link=await battles.battle_link(db, battle),
        )
    elif battle.status not in ("waiting", "active"):
        await db.commit()
        raise _not_open()
    await db.commit()
    return await battle_out(db, found)


@router.post("/battles/{battle_id}/ready", response_model=FriendZoneBattleOut)
async def ready(
    battle_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """The challenger's Ready, once the friend has accepted: the questions are picked now and the countdown starts.
    Too few competitive questions by now ends it as `cancelled` (`not_enough_questions`)."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    peek = await battles.load(db, battle_id, me, lock=False)
    if peek is None or peek.mine != "challenger":
        raise NotFound()
    other_id = peek.other.user_id
    if other_id is not None:
        await relations.lock_pair(db, me.user_id, other_id)
        await battles.lock_live(db, me.user_id, other_id)
    found = await _mine(db, me, battle_id, _policy(request), role="challenger")
    battle = found.battle
    if battle.status == "waiting":
        if not await db.scalar(select(relations.are_friends(me.user_id, other_id))):
            await battles.end_battle(db, found, "cancelled", "unfriended")
        elif await battles.busy(db, me.user_id, battle.id):
            await db.commit()
            raise Conflict("You are already in another battle.", code="busy_in_battle")
        else:
            await battles.start(db, found, _policy(request), request.app.state.settings.friend_zone_round_seconds)
    elif battle.status != "active":
        await db.commit()
        raise _not_open()
    await db.commit()
    log.info("friend zone battle ready", extra={"battle_id": str(battle.id), "status": battle.status})
    return await battle_out(db, found)


async def _close(request: Request, principal: Principal, db: AsyncSession, battle_id: uuid.UUID, role: str):
    """Decline (the invited friend) or cancel (the challenger, before the start). Repeating it answers the same."""
    me = await access.member(request, principal, db)
    found = await _mine(db, me, battle_id, _policy(request), role=role)
    battle = found.battle
    target = "declined" if role == "opponent" else "cancelled"
    allowed = ("invited",) if role == "opponent" else ("invited", "waiting")
    if battle.status in allowed:
        await battles.end_battle(db, found, target, target)
    elif battle.status != target:
        await db.commit()
        raise _not_open()
    await db.commit()
    return await battle_out(db, found)


@router.post("/battles/{battle_id}/decline", response_model=FriendZoneBattleOut)
async def decline_battle(
    battle_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    response.headers.update(access.NO_STORE)
    return await _close(request, principal, db, battle_id, "opponent")


@router.post("/battles/{battle_id}/cancel", response_model=FriendZoneBattleOut)
async def cancel_battle(
    battle_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    response.headers.update(access.NO_STORE)
    return await _close(request, principal, db, battle_id, "challenger")


@router.post("/battles/{battle_id}/answers", response_model=FriendZoneBattleOut)
async def answer(
    battle_id: uuid.UUID,
    body: FriendZoneAnswerRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Lock in one letter for the open round: final at once, graded by the database, scored when the round closes.
    The answer shows only your own choice until then."""
    response.headers.update(access.NO_STORE)
    _poll(request, principal)
    me = await access.member(request, principal, db)
    found = await _mine(db, me, battle_id, _policy(request))
    try:
        await battles.answer(db, found, body.round, body.choice)
    except Conflict:
        await db.commit()  # keep what advancing did
        raise
    await db.commit()
    return await battle_out(db, found)
