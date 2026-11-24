"""Who a Friend Zone member can see and how two members are related (plan sections 4, 9 and 12).

Every lookup stays inside the caller's cohort, and another member is visible only while they are active (not
suspended, and an NH invoice still opens a program for them) and neither has blocked the other. Every change between
two members takes the pair lock first (plan section 9, lock order); the database's unique keys are the backstop."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    FzBattlePlayer,
    FzBlock,
    FzFriendRequest,
    FzFriendship,
    FzProfile,
    FzReport,
    InvoiceClaim,
    NhInvoice,
    Program,
    UserAvatar,
)
from app.errors import Conflict, NotFound
from app.friendzone.schemas import FriendZonePlayerOut, FriendZoneProgramOut, FriendZoneRequestOut
from app.notifications.inbox import notify
from app.programs.access import open_program_ids

REQUEST_LIFETIME = timedelta(days=30)  # R-F1: a pending request expires after 30 days
MAX_PENDING_SENT = 50
MAX_FRIENDS = 200
LIST_LIMIT = 200


# ------------------------------------------------------------------ predicates
def is_active(member):
    """SQL: this member can be seen by others now (not suspended, and an invoice still opens a program for them)."""
    return and_(member.suspended_at.is_(None), open_program_ids(member.user_id).exists())


def is_blocked(a, b):
    """SQL: either member has blocked the other (account ids or columns)."""
    return exists().where(
        or_(
            and_(FzBlock.blocker_id == a, FzBlock.blocked_id == b),
            and_(FzBlock.blocker_id == b, FzBlock.blocked_id == a),
        )
    )


def are_friends(a, b):
    return exists().where(
        or_(
            and_(FzFriendship.user_low == a, FzFriendship.user_high == b),
            and_(FzFriendship.user_low == b, FzFriendship.user_high == a),
        )
    )


def _fresh():
    return FzFriendRequest.created_at > func.clock_timestamp() - REQUEST_LIFETIME


def _between(a: uuid.UUID, b: uuid.UUID):
    return or_(
        and_(FzFriendRequest.sender_id == a, FzFriendRequest.recipient_id == b),
        and_(FzFriendRequest.sender_id == b, FzFriendRequest.recipient_id == a),
    )


def ordered(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A pair in friendship order (UUID order is the same in Python and PostgreSQL: byte order)."""
    return (a, b) if a < b else (b, a)


async def lock_pair(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> None:
    """The pair lock (held until the transaction ends): first in every path that changes what two members are."""
    low, high = ordered(a, b)
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"fz_pair|{low}|{high}"})


async def lock_members(db: AsyncSession, *user_ids: uuid.UUID) -> None:
    """Per-member locks, in id order, taken after the pair lock: a member's counted limits (friends, waiting
    requests, waiting invitations) hold under parallel requests to different members."""
    for user_id in sorted(set(user_ids)):
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"fz_member|{user_id}"})


# ------------------------------------------------------------------ lookups
async def visible(db: AsyncSession, me: FzProfile, player_id: uuid.UUID) -> FzProfile | None:
    """Another active member of my cohort that neither of us has blocked; None for anything else."""
    if player_id == me.id:
        return None
    return await db.scalar(
        select(FzProfile).where(
            FzProfile.id == player_id,
            FzProfile.cohort == me.cohort,
            is_active(FzProfile),
            ~is_blocked(me.user_id, FzProfile.user_id),
        )
    )


async def in_cohort(db: AsyncSession, me: FzProfile, player_id: uuid.UUID) -> FzProfile | None:
    """Any other member of my cohort, whatever their state: what block, unblock, unfriend and report act on."""
    if player_id == me.id:
        return None
    return await db.scalar(select(FzProfile).where(FzProfile.id == player_id, FzProfile.cohort == me.cohort))


async def by_invoice(db: AsyncSession, me: FzProfile, number: str) -> FzProfile | None:
    """The member whose open invoice this is: myself, or a discoverable active member of my cohort that neither of us
    has blocked. Exact match only; every other case is None."""
    holder = (
        select(InvoiceClaim.user_id)
        .join(NhInvoice, NhInvoice.id == InvoiceClaim.invoice_id)
        .where(
            NhInvoice.invoice_no == number,
            NhInvoice.status == "active",
            or_(NhInvoice.valid_until.is_(None), NhInvoice.valid_until > func.clock_timestamp()),
        )
        .scalar_subquery()
    )
    return await db.scalar(
        select(FzProfile).where(
            FzProfile.user_id == holder,
            FzProfile.cohort == me.cohort,
            is_active(FzProfile),
            or_(
                FzProfile.user_id == me.user_id,
                and_(FzProfile.discoverable, ~is_blocked(me.user_id, FzProfile.user_id)),
            ),
        )
    )


async def relation(db: AsyncSession, me: FzProfile, other: FzProfile) -> str:
    if other.id == me.id:
        return "self"
    if await db.scalar(select(are_friends(me.user_id, other.user_id))):
        return "friend"
    sender = await db.scalar(
        select(FzFriendRequest.sender_id)
        .where(_waiting_for(me.user_id), _fresh(), _between(me.user_id, other.user_id))
        .order_by((FzFriendRequest.status == "pending").desc())  # their live request beats my declined one
        .limit(1)
    )
    if sender is None:
        return "none"
    return "outgoing" if sender == me.user_id else "incoming"


def _waiting_for(viewer: uuid.UUID):
    """SQL: a request still open as `viewer` sees it. A decline is silent, so to its sender a declined request
    stays waiting until it expires."""
    return or_(
        FzFriendRequest.status == "pending",
        and_(FzFriendRequest.status == "declined", FzFriendRequest.sender_id == viewer),
    )


async def player_out(db: AsyncSession, me: FzProfile, other: FzProfile, relation_: str) -> FriendZonePlayerOut:
    """The privacy matrix: friends (and I) see the photo; programs only when shown, or for myself."""
    close = relation_ in ("self", "friend")
    avatar = await db.scalar(select(UserAvatar.hash).where(UserAvatar.user_id == other.user_id)) if close else None
    programs = None
    if relation_ == "self" or (relation_ == "friend" and other.show_program):
        programs = (await programs_of(db, [other.user_id])).get(other.user_id, [])
    return card(other, relation_, avatar, programs)


def card(other: FzProfile, relation_: str, avatar: str | None, programs) -> FriendZonePlayerOut:
    return FriendZonePlayerOut(
        player_id=other.id,
        display_name=other.display_name,
        initials=initials(other.display_name),
        relation=relation_,
        avatar_url=f"/v1/friend-zone/players/{other.id}/avatar?v={avatar}" if avatar else None,
        programs=programs,
    )


def initials(name: str) -> str:
    words = [word for word in name.replace(".", " ").replace("_", " ").split() if word[0].isalnum()]
    return "".join(word[0] for word in words[:2]).upper() or name[:1].upper()


async def programs_of(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[FriendZoneProgramOut]]:
    """Each member's open programs, in one query."""
    if not user_ids:
        return {}
    rows = await db.execute(
        select(InvoiceClaim.user_id, Program.slug, Program.title)
        .join(NhInvoice, NhInvoice.id == InvoiceClaim.invoice_id)
        .join(Program, Program.id == NhInvoice.program_id)
        .where(
            InvoiceClaim.user_id.in_(user_ids),
            NhInvoice.status == "active",
            or_(NhInvoice.valid_until.is_(None), NhInvoice.valid_until > func.clock_timestamp()),
        )
        .distinct()
        .order_by(InvoiceClaim.user_id, Program.slug)
    )
    found: dict[uuid.UUID, list[FriendZoneProgramOut]] = {}
    for user_id, slug, title in rows.all():
        found.setdefault(user_id, []).append(FriendZoneProgramOut(slug=slug, title=title))
    return found


async def friends(db: AsyncSession, me: FzProfile) -> list[FriendZonePlayerOut]:
    other_id = func.coalesce(
        func.nullif(FzFriendship.user_low, me.user_id), func.nullif(FzFriendship.user_high, me.user_id)
    )
    rows = (
        await db.execute(
            select(FzProfile, UserAvatar.hash)
            .join(FzFriendship, FzProfile.user_id == other_id)
            .outerjoin(UserAvatar, UserAvatar.user_id == FzProfile.user_id)
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
    shown = await programs_of(db, [found.user_id for found, _ in rows if found.show_program])
    return [
        card(found, "friend", avatar, shown.get(found.user_id, []) if found.show_program else None)
        for found, avatar in rows
    ]


async def requests(db: AsyncSession, me: FzProfile) -> tuple[list[FriendZoneRequestOut], list[FriendZoneRequestOut]]:
    """Pending, unexpired requests to and from active, unblocked members: (incoming, outgoing)."""
    out: list[list[FriendZoneRequestOut]] = []
    for mine, theirs, relation_ in (
        (FzFriendRequest.recipient_id, FzFriendRequest.sender_id, "incoming"),
        (FzFriendRequest.sender_id, FzFriendRequest.recipient_id, "outgoing"),
    ):
        rows = (
            await db.execute(
                select(FzFriendRequest, FzProfile)
                .join(FzProfile, and_(FzProfile.user_id == theirs, FzProfile.cohort == FzFriendRequest.cohort))
                .where(
                    mine == me.user_id,
                    _waiting_for(me.user_id),
                    _fresh(),
                    is_active(FzProfile),
                    ~is_blocked(me.user_id, FzProfile.user_id),
                )
                .order_by(FzFriendRequest.created_at.desc(), FzFriendRequest.id)
                .limit(LIST_LIMIT)
            )
        ).all()
        out.append([_request_out(row, other, relation_) for row, other in rows])
    return out[0], out[1]


def _request_out(row: FzFriendRequest, other: FzProfile, relation_: str) -> FriendZoneRequestOut:
    return FriendZoneRequestOut(
        request_id=row.id,
        player=card(other, relation_, None, None),
        created_at=row.created_at,
        expires_at=row.created_at + REQUEST_LIFETIME,
    )


async def blocked_by_me(db: AsyncSession, me: FzProfile) -> list[FriendZonePlayerOut]:
    rows = await db.scalars(
        select(FzProfile)
        .join(FzBlock, and_(FzBlock.blocked_id == FzProfile.user_id, FzBlock.cohort == FzProfile.cohort))
        .where(FzBlock.blocker_id == me.user_id, FzBlock.cohort == me.cohort)
        .order_by(FzBlock.created_at.desc())
        .limit(LIST_LIMIT)
    )
    return [card(found, "blocked", None, None) for found in rows]


# ------------------------------------------------------------------ changes (the caller holds the pair lock)
async def friend_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(FzFriendship)
        .where(or_(FzFriendship.user_low == user_id, FzFriendship.user_high == user_id))
    )


async def _expire_stale(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> None:
    """Mark the pair's pending requests older than REQUEST_LIFETIME expired, freeing the pending-pair key."""
    await db.execute(
        update(FzFriendRequest)
        .where(FzFriendRequest.status == "pending", ~_fresh(), _between(a, b))
        .values(status="expired", responded_at=func.clock_timestamp())
        .execution_options(synchronize_session=False)
    )


async def _befriend(db: AsyncSession, me: FzProfile, other: FzProfile) -> None:
    await lock_members(db, me.user_id, other.user_id)
    if await friend_count(db, me.user_id) >= MAX_FRIENDS:
        raise Conflict(f"A member can have at most {MAX_FRIENDS} friends.", code="friend_limit")
    if await friend_count(db, other.user_id) >= MAX_FRIENDS:
        raise NotFound()  # their limit is theirs to know: to me it looks like any member I cannot reach
    low, high = ordered(me.user_id, other.user_id)
    await db.execute(
        insert(FzFriendship).values(user_low=low, user_high=high, cohort=me.cohort).on_conflict_do_nothing()
    )


@dataclass(frozen=True)
class Sent:
    relation: str  # "outgoing" (a new request) or "friend" (their request to me, accepted)
    request_id: uuid.UUID


async def send(db: AsyncSession, me: FzProfile, other: FzProfile) -> Sent:
    """A friend request to `other`, who is visible. Their pending request to me makes us friends at once."""
    await lock_members(db, me.user_id, other.user_id)
    await _expire_stale(db, me.user_id, other.user_id)
    if await db.scalar(select(are_friends(me.user_id, other.user_id))):
        raise Conflict("You are already friends.", code="already_friends")
    pending = await db.scalar(
        select(FzFriendRequest)
        .where(FzFriendRequest.status == "pending", _between(me.user_id, other.user_id))
        .with_for_update()
    )
    if pending is not None and pending.sender_id == other.user_id:
        await accept(db, me, other, pending)
        return Sent("friend", pending.id)
    declined = await db.scalar(
        select(
            exists().where(
                FzFriendRequest.sender_id == me.user_id,
                FzFriendRequest.recipient_id == other.user_id,
                FzFriendRequest.status == "declined",
                _fresh(),
            )
        )
    )
    if pending is not None or declined:  # a decline looks like no answer yet: asking again changes nothing
        raise Conflict("Your request is already waiting for an answer.", code="request_pending")
    sent = await db.scalar(
        select(func.count())
        .select_from(FzFriendRequest)
        .where(FzFriendRequest.sender_id == me.user_id, _waiting_for(me.user_id), _fresh())
    )
    if sent >= MAX_PENDING_SENT:
        raise Conflict(f"You can have at most {MAX_PENDING_SENT} requests waiting.", code="request_limit")
    request_id = await db.scalar(
        insert(FzFriendRequest)
        .values(sender_id=me.user_id, recipient_id=other.user_id, cohort=me.cohort)
        .returning(FzFriendRequest.id)
    )
    await notify(
        db,
        other.user_id,
        kind="friend_request",
        title="New friend request",
        body="A classmate wants to be your friend in Friend Zone.",
        dedup_key=f"friend-request:{request_id}",
        link=await hub_link(db, other.user_id),
    )
    return Sent("outgoing", request_id)


async def accept(db: AsyncSession, me: FzProfile, sender: FzProfile, pending: FzFriendRequest) -> None:
    """Accept `sender`'s pending request to me (locked by the caller): a friendship, and the sender hears of it."""
    await _befriend(db, me, sender)
    pending.status, pending.responded_at = "accepted", func.clock_timestamp()
    await db.flush()
    await notify(
        db,
        sender.user_id,
        kind="friend_accepted",
        title="Friend request accepted",
        body="You have a new friend in Friend Zone.",
        dedup_key=f"friend-accepted:{pending.id}",  # dedup_key is at most 120 characters: the request id is unique
        link=await hub_link(db, sender.user_id),
    )


async def unfriend(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    low, high = ordered(a, b)
    removed = await db.execute(delete(FzFriendship).where(FzFriendship.user_low == low, FzFriendship.user_high == high))
    return removed.rowcount > 0


async def block(db: AsyncSession, me: FzProfile, other: FzProfile) -> None:
    """Block: ends the friendship and every pending request between us, silently (plan I21)."""
    await unfriend(db, me.user_id, other.user_id)
    await db.execute(
        update(FzFriendRequest)
        .where(FzFriendRequest.status == "pending", _between(me.user_id, other.user_id))
        .values(status="cancelled", responded_at=func.clock_timestamp())
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        insert(FzBlock)
        .values(blocker_id=me.user_id, blocked_id=other.user_id, cohort=me.cohort)
        .on_conflict_do_nothing()
    )


async def unblock(db: AsyncSession, me: FzProfile, other: FzProfile) -> bool:
    removed = await db.execute(
        delete(FzBlock).where(FzBlock.blocker_id == me.user_id, FzBlock.blocked_id == other.user_id)
    )
    return removed.rowcount > 0


async def shared_battle(db: AsyncSession, battle_id: uuid.UUID, a: uuid.UUID, b: uuid.UUID) -> bool:
    """Both members played in this battle (what a report may point at)."""
    players = await db.scalars(select(FzBattlePlayer.user_id).where(FzBattlePlayer.battle_id == battle_id))
    return {a, b} <= set(players)


async def report(db: AsyncSession, me: FzProfile, other: FzProfile, reason: str, details, battle_id) -> uuid.UUID:
    return await db.scalar(
        insert(FzReport)
        .values(
            reporter_id=me.user_id,
            reported_id=other.user_id,
            cohort=me.cohort,
            battle_id=battle_id,
            reason=reason,
            details=details,
        )
        .returning(FzReport.id)
    )


async def hub_link(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    """Where a Friend Zone notification opens: the member's first open program's Quizzes & Games, Friend Zone."""
    slug = await db.scalar(
        select(Program.slug).where(Program.id.in_(open_program_ids(user_id))).order_by(Program.slug).limit(1)
    )
    return f"/programs/{slug}/quizzes?zone=friends" if slug else None
