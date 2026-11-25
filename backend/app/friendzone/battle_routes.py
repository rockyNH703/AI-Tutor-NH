"""/v1/friend-zone/battles: 1v1 exam battles between friends in the same program (exam plan revision 2, section 5).

The engine is app/friendzone/battles.py. Every state read brings the battle up to the database clock first, so
polling is what moves a battle on (an exam completes at the first read or write at or after its cutoff). A battle
that is not the caller's, or not in their cohort, is one 404. Before completion the state never carries a key, an
explanation, a score or the opponent's responses; the paper is sent only once the exam has started. Every exam route
builds its answer before it commits, and commits before any 409, so a completion is never rolled back."""

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
    FzBattleResponse,
    FzBattleRound,
    FzProfile,
    FzXpEvent,
    NhInvoice,
    Program,
    ProgramModule,
    QbQuestion,
)
from app.db.session import get_session
from app.errors import Conflict, NotFound, RateLimited
from app.friendzone import access, battles, relations, scoring
from app.friendzone.schemas import (
    FriendZoneAnswerRequest,
    FriendZoneBattleOut,
    FriendZoneBattlePlayerOut,
    FriendZoneBattleResponsesOut,
    FriendZoneBattleResponsesRequest,
    FriendZoneBattlesOut,
    FriendZoneBattleSummaryOut,
    FriendZoneBattleXpOut,
    FriendZoneCreateBattleRequest,
    FriendZoneExamOut,
    FriendZoneExamQuestionOut,
    FriendZoneOptionOut,
    FriendZoneProgramOut,
    FriendZoneRematchRequest,
    FriendZoneResponseOut,
    FriendZoneResponseResultOut,
    FriendZoneReviewOut,
    FriendZoneRoundOut,
    FriendZoneSubmitRequest,
)
from app.logging import get_logger
from app.notifications.inbox import notify
from app.programs.access import open_program_ids

router = APIRouter(tags=["friend-zone"])
log = get_logger("app.friendzone")
PAGE = 20
XP_LINES = ("battle", "win", "draw")


def _not_open() -> Conflict:
    return Conflict("This battle can no longer be changed.", code="battle_not_open")


def _policy(request: Request) -> str:
    return request.app.state.settings.friend_zone_key_policy


def _poll(request: Request, principal: Principal) -> None:
    """Battle reads and saves per member (in-process; plan section 10: about one a second, never security)."""
    if not request.app.state.friend_zone_poll_limiter.allow(str(principal.user.id)):
        raise RateLimited()


async def _mine(
    db: AsyncSession, me: FzProfile, battle_id: uuid.UUID, policy: str, *, role: str | None = None
) -> battles.Loaded:
    """My battle, its row locked and brought up to the clock; 404 when it is not mine (or I am not `role`)."""
    found = await battles.load(db, battle_id, me, lock=True)
    if found is None or (role is not None and found.mine != role):
        raise NotFound()
    await battles.advance(db, found, policy=policy)
    return found


# ------------------------------------------------------------------ views
async def _profiles(db: AsyncSession, user_ids) -> dict[uuid.UUID, FzProfile]:
    present = [user_id for user_id in user_ids if user_id is not None]
    if not present:
        return {}
    return {row.user_id: row for row in await db.scalars(select(FzProfile).where(FzProfile.user_id.in_(present)))}


def _player(
    player: FzBattlePlayer, profiles: dict[uuid.UUID, FzProfile], battle: FzBattle
) -> FriendZoneBattlePlayerOut:
    """A participant as a former opponent may see them: name and initials, never a photo (privacy matrix). The score,
    counts and winner only once the battle is completed (secrecy while it runs)."""
    profile = profiles.get(player.user_id) if player.user_id is not None else None
    done = battle.status == "completed"
    timed = done and battle.format == "exam" and battle.result != "void"
    return FriendZoneBattlePlayerOut(
        player_id=profile.id if profile else None,
        display_name=profile.display_name if profile else None,
        initials=relations.initials(profile.display_name) if profile else None,
        role=player.role,
        ready=player.ready_at is not None,
        score=player.score if done else 0,
        correct=player.correct if done else 0,
        answered=player.answered if done else 0,
        winner=player.winner if done else False,
        time_taken_seconds=battles.taken_seconds(battle, player) if timed else None,
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


# Why a battle ended, as its players may see it. A block, an unfriend, a suspension, a deleted account, an upgrade or
# an operator all read "ended": none of them may be told apart (I21).
SHOWN_REASONS = ("finished", "forfeit", "absent", "expired", "declined", "cancelled", "not_enough_questions")


def public_reason(reason: str | None) -> str | None:
    return reason if reason is None or reason in SHOWN_REASONS else "ended"


def _next_change(battle: FzBattle, rounds, now: datetime) -> datetime | None:
    """When polling can next show something new: an exam's start, then its cutoff."""
    if battle.status in ("invited", "waiting"):
        return battle.expires_at
    if battle.status != "active":
        return None
    if battle.format == "exam":
        return battle.starts_at if now < battle.starts_at else battles.cutoff(battle)
    for each in rounds:
        if now < each.opened_at:
            return each.opened_at
        if now < each.closes_at:
            return each.closes_at
    return battle.ends_at


def _response(row: FzBattleResponse) -> FriendZoneResponseOut:
    return FriendZoneResponseOut(round=row.round, choice=row.choice, marked=row.marked, rev=row.rev)


async def _exam(
    db: AsyncSession, found: battles.Loaded, rounds, questions: dict[str, QbQuestion], now: datetime
) -> FriendZoneExamOut | None:
    """The running exam: the paper only once it has started (never during the countdown), and my responses only."""
    battle = found.battle
    if battle.format != "exam" or battle.status != "active" or now < battle.starts_at:
        return None
    return FriendZoneExamOut(
        seconds_per_question=rounds[0].seconds if rounds else scoring.PER_QUESTION_SECONDS,
        ends_at=battle.ends_at,
        cutoff_at=battles.cutoff(battle),
        questions=[
            FriendZoneExamQuestionOut(
                round=each.round,
                stem=questions[each.question_id].stem,
                options=_options(questions[each.question_id]),
            )
            for each in rounds
        ],
        responses=[_response(row) for row in await battles.responses_of(db, battle.id, found.mine)],
        submitted_at=found.me.submitted_at,
        opponent_submitted=found.other.submitted_at is not None,
    )


async def _xp(db: AsyncSession, found: battles.Loaded) -> list[FriendZoneBattleXpOut]:
    """The XP this battle gave me, line by line (battle, win, draw)."""
    if found.me.user_id is None:
        return []
    rows = await db.execute(
        select(FzXpEvent.reason, FzXpEvent.amount).where(
            FzXpEvent.user_id == found.me.user_id,
            FzXpEvent.ref_id == str(found.battle.id),
            FzXpEvent.reason.in_(XP_LINES),
        )
    )
    lines = sorted(rows.all(), key=lambda row: XP_LINES.index(row.reason))
    return [FriendZoneBattleXpOut(reason=row.reason, amount=row.amount) for row in lines]


async def _repeats(db: AsyncSession, battle: FzBattle) -> int:
    """A rematch: how many of its questions repeat from the battle it repeats."""
    if battle.rematch_of is None or battle.question_ids is None:
        return 0
    previous = await db.scalar(select(FzBattle.question_ids).where(FzBattle.id == battle.rematch_of))
    return len(set(battle.question_ids) & set(previous or []))


def _review(each: FzBattleRound, question: QbQuestion, served: dict, mine: str, theirs: str, exam: bool):
    hidden = exam and each.voided  # changed or withdrawn after the exam started: no stem, key or explanation
    return FriendZoneReviewOut(
        round=each.round,
        stem=None if hidden else question.stem,
        options=[] if hidden else _options(question),
        correct_key=None if hidden else battles.correct_key(question),
        explanation=None if hidden else question.explanation,
        voided=each.voided,
        my_choice=battles.mine_of(served.get((mine, each.round))),
        their_choice=battles.mine_of(served.get((theirs, each.round))),
    )


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
    exam = battle.format == "exam"
    profiles = await _profiles(db, [player.user_id for player in found.players.values()])
    program, month = await _program(db, battle)

    closed = [each for each in rounds if each.closed_at is not None]  # an exam's: only at completion
    shown = [
        FriendZoneRoundOut(
            round=each.round,
            voided=each.voided,
            correct_key=None if exam and each.voided else battles.correct_key(questions[each.question_id]),
            my_choice=battles.mine_of(served.get((mine, each.round))),
            their_choice=battles.mine_of(served.get((theirs, each.round))),
            my_points=battles.round_points(served.get((mine, each.round)), each, battle.format),
            their_points=battles.round_points(served.get((theirs, each.round)), each, battle.format),
        )
        for each in closed
    ]
    completed = battle.status == "completed"
    review = None
    if completed:
        review = [_review(each, questions[each.question_id], served, mine, theirs, exam) for each in closed]
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
        me=_player(found.me, profiles, battle),
        opponent=_player(found.other, profiles, battle),
        question=None,  # a round battle's open round: none is open any more
        rounds=shown,
        review=review,
        format=battle.format,
        repeats_from_previous=await _repeats(db, battle),
        exam=await _exam(db, found, rounds, questions, now),
        xp=await _xp(db, found) if completed else None,
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
    await battles.complete_overdue(db, me.user_id, policy=_policy(request))
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
                opponent=_player(other_of[battle.id], profiles, battle),
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
    # a legacy 5-round battle is rematched as a 10-question exam; fresh questions first, then repeats if needed
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
    length = 10 if previous.length == 5 else previous.length
    choice = Choice(other.id, module, previous.topic_id, previous.difficulty, length, body.request_id, previous.id)
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
    available = await battles.pool_size(
        db, choice.module.id, choice.topic_id, choice.difficulty, policy=_policy(request)
    )
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
    await battles.complete_overdue(db, me.user_id, policy=_policy(request))  # an exam past its cutoff is over
    existing = await db.scalar(select(FzBattle.id).where(FzBattle.pair_key == key))
    if existing is not None:
        await db.commit()  # keep the expiries and completions
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
        await db.commit()
        raise Conflict(f"You can have {battles.MAX_PENDING_INVITES} invitations waiting at once.", code="invite_limit")
    battle = FzBattle(
        cohort=me.cohort,
        program_id=program_id,
        module_id=choice.module.id,
        topic_id=choice.topic_id,
        difficulty=choice.difficulty,
        length=choice.length,
        format="exam",
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
    out = await battle_out(db, found)
    await db.commit()
    return out


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
        for user_id in (me.user_id, found.other.user_id):  # an exam past its cutoff no longer holds either of us
            await battles.complete_overdue(db, user_id, policy=_policy(request))
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
    out = await battle_out(db, found)
    await db.commit()
    return out


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
        else:
            await battles.complete_overdue(db, me.user_id, policy=_policy(request))
            if await battles.busy(db, me.user_id, battle.id):
                await db.commit()
                raise Conflict("You are already in another battle.", code="busy_in_battle")
            seconds = request.app.state.settings.friend_zone_round_seconds  # e2e only: seconds per question
            await battles.start(db, found, policy=_policy(request), seconds_per_question=seconds)
    elif battle.status != "active":
        await db.commit()
        raise _not_open()
    out = await battle_out(db, found)
    await db.commit()
    log.info("friend zone battle ready", extra={"battle_id": str(battle.id), "status": battle.status})
    return out


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
    out = await battle_out(db, found)
    await db.commit()
    return out


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
    """Retired with the round battle (kept for /v1 compatibility): always 409 `battle_not_open`, and nothing is
    graded. An exam is answered with `/responses` and `/submit`."""
    response.headers.update(access.NO_STORE)
    _poll(request, principal)
    me = await access.member(request, principal, db)
    await _mine(db, me, battle_id, _policy(request))
    await db.commit()  # keep what advancing did
    raise _not_open()


@router.post("/battles/{battle_id}/responses", response_model=FriendZoneBattleResponsesOut)
async def save_responses(
    battle_id: uuid.UUID,
    body: FriendZoneBattleResponsesRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Save exam answers as they change: each item is applied only when its `baseRev` still matches. Nothing is
    graded until the exam ends. 409 `battle_not_open` once I have submitted or past the cutoff."""
    # 409 invalid_choice (nothing saved) for a letter not among the options; `responses` is every row of mine after
    # the save, `items` one result per item sent, in order
    response.headers.update(access.NO_STORE)
    _poll(request, principal)
    me = await access.member(request, principal, db)
    found = await _mine(db, me, battle_id, _policy(request))
    try:
        saved = await battles.save(db, found, body.responses)
    except Conflict:
        await db.commit()  # keep what advancing did
        raise
    out = FriendZoneBattleResponsesOut(
        server_time=await battles.clock(db),
        status=found.battle.status,
        submitted_at=found.me.submitted_at,
        opponent_submitted=found.other.submitted_at is not None,
        responses=[_response(row) for row in await battles.responses_of(db, found.battle.id, found.mine)],
        items=[FriendZoneResponseResultOut(round=each.round, applied=each.applied, rev=each.rev) for each in saved],
    )
    await db.commit()
    return out


@router.post("/battles/{battle_id}/submit", response_model=FriendZoneBattleOut)
async def submit_exam(
    battle_id: uuid.UUID,
    body: FriendZoneSubmitRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Submit my exam, with any unsaved responses as final values. After the end it answers 200 with the battle and
    nothing applied (never 409). The applying submit is never rate limited (the deadline's auto-submit must always get
    through); one that applies nothing goes through the poll limit, so a loop cannot bypass the GET's."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    peek = await battles.load(db, battle_id, me, lock=False)
    polled = peek is not None and (peek.battle.status != "active" or peek.me.submitted_at is not None)
    if polled:  # one that can apply nothing is throttled before it takes the row lock the opponent's saves need
        _poll(request, principal)
    found = await _mine(db, me, battle_id, _policy(request))
    applied = await battles.submit(db, found, body.responses, auto=body.auto, policy=_policy(request))
    if not applied and not polled:
        try:
            _poll(request, principal)
        except RateLimited:
            await db.commit()  # keep what advancing did
            raise
    out = (await battle_out(db, found)).model_copy(update={"submit_applied": applied})
    await db.commit()
    log.info("friend zone exam submit", extra={"battle_id": str(battle_id), "applied": applied, "auto": body.auto})
    return out
