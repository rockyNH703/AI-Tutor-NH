"""/v1/friend-zone P1.2: find a classmate by invoice number, friend requests, friends, blocks, reports and the player
view (plan sections 4, 9, 10 and 12). Included by app/friendzone/router.py (same prefix).

Anything the caller may not see is one identical 404. A block looks exactly like an unfriend or a member who is not
findable. Search answers no faster than SEARCH_FLOOR_SECONDS, so a hit and a miss take the same time, and an invoice
number is only ever logged as its fingerprint."""

from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, client_info, get_auth_service, get_principal
from app.auth.service import AuthService
from app.db.models import FzFriendRequest, FzProfile, FzXpEvent, UserAvatar
from app.db.session import get_session
from app.errors import NotFound, error_body
from app.friendzone import access, battles, progress, relations
from app.friendzone.schemas import (
    FriendZoneBadgeOut,
    FriendZonePlayerOut,
    FriendZonePlayerRef,
    FriendZonePlayersOut,
    FriendZoneReportOut,
    FriendZoneReportRequest,
    FriendZoneRequestsOut,
    FriendZoneSearchRequest,
    FriendZoneStatsOut,
    FriendZoneXpEventOut,
    FriendZoneXpOut,
)
from app.logging import get_logger
from app.programs.service import invoice_fingerprint, normalize_invoice

router = APIRouter(tags=["friend-zone"])
log = get_logger("app.friendzone")
XP_PAGE = 30
SEARCH_FLOOR_SECONDS = 0.4  # R-S1: every search answer, hit, miss or refusal, takes at least this long
NO_MATCH = "No match. Your friend needs to join Friend Zone and turn on 'Let classmates find me'."
AVATAR_RESPONSE = {200: {"content": {"image/webp": {"schema": {"type": "string", "format": "binary"}}}}}


# ------------------------------------------------------------------ search
@router.post("/search", response_model=FriendZonePlayerOut)
async def search(
    body: FriendZoneSearchRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
):
    """Find a classmate by the exact invoice number. A hit gives their name and initials; every miss (unknown,
    not joined, not findable, blocked, another cohort) is the same 404 `no_match`."""
    started = time.monotonic()
    try:
        return await _search(body, request, response, principal, db, auth)
    finally:
        await db.rollback()  # the count is committed: give the connection back before waiting out the floor
        await asyncio.sleep(max(0.0, SEARCH_FLOOR_SECONDS - (time.monotonic() - started)))


async def _search(body, request, response, principal, db, auth) -> FriendZonePlayerOut:
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    user_id = principal.user.id
    await auth.throttle_friend_zone_search(principal.user, client_info(request).ip)  # counted before the lookup
    number = normalize_invoice(body.invoice_number)
    found = await relations.by_invoice(db, me, number) if number else None
    secret = request.app.state.settings.secret_key.get_secret_value()
    log.info(
        "friend zone search",
        extra={
            "user_id": str(user_id),
            "outcome": "hit" if found is not None else "miss",
            "invoice_fingerprint": invoice_fingerprint(number, secret) if number else None,
        },
    )
    if found is None:
        raise NotFound(NO_MATCH, code="no_match")
    return relations.card(found, await relations.relation(db, me, found), None, None)


# ------------------------------------------------------------------ players
@router.get("/players/{player_id}", response_model=FriendZonePlayerOut)
async def get_player(
    player_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """A member as the privacy matrix lets the caller see them: themself, a friend, or a pending-request party."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    if player_id == me.id:
        mine = await relations.player_out(db, me, me, "self")
        return mine.model_copy(update={"stats": await stats_out(db, me.user_id)})
    other = await relations.visible(db, me, player_id)
    relation = await relations.relation(db, me, other) if other is not None else "none"
    if other is None or relation == "none":
        raise NotFound()
    found = await relations.player_out(db, me, other, relation)
    if relation == "friend" and other.show_stats:
        found = found.model_copy(update={"stats": await stats_out(db, other.user_id)})
    return found


@router.get("/players/{player_id}/avatar", response_class=Response, responses=AVATAR_RESPONSE)
async def get_player_avatar(
    player_id: uuid.UUID,
    request: Request,
    v: str = Query(pattern=r"^[0-9a-f]{32}$", description="The hash in the player's `avatarUrl`."),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """A friend's photo (or your own). Never cached: whether the caller may see it is decided on every request."""
    me = await access.member(request, principal, db)
    other = me if player_id == me.id else await relations.visible(db, me, player_id)
    allowed = other is me or (
        other is not None and await db.scalar(select(relations.are_friends(me.user_id, other.user_id)))
    )
    row = await db.get(UserAvatar, other.user_id) if allowed else None
    if row is None or row.hash != v:
        return JSONResponse(status_code=404, content=error_body("not_found", "Not found."), headers=access.NO_STORE)
    return Response(
        content=row.image,
        media_type=row.content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


async def stats_out(db: AsyncSession, user_id: uuid.UUID) -> FriendZoneStatsOut:
    found = await progress.stats(db, user_id)
    badges = [
        FriendZoneBadgeOut(key=key, title=progress.BADGES[key][0], description=progress.BADGES[key][1])
        for key in found.badges
    ]
    return FriendZoneStatsOut(**{**found.__dict__, "badges": badges})


@router.get("/me/xp", response_model=FriendZoneXpOut)
async def my_xp(
    request: Request,
    response: Response,
    cursor: str | None = Query(None, max_length=200),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """My XP, level and XP history, newest first (Phase 2). Only ever my own."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    query = (
        select(FzXpEvent)
        .where(FzXpEvent.user_id == me.user_id)
        .order_by(FzXpEvent.created_at.desc(), FzXpEvent.id.desc())
        .limit(XP_PAGE + 1)
    )
    if cursor:
        query = query.where(tuple_(FzXpEvent.created_at, FzXpEvent.id) < tuple_(*access.decode_cursor(cursor)))
    rows = (await db.scalars(query)).all()
    page = rows[:XP_PAGE]
    xp = await progress.total_xp(db, me.user_id)
    level = progress.level_for(xp)
    return FriendZoneXpOut(
        xp=xp,
        level=level,
        level_floor=progress.level_floor(level),
        next_level_at=progress.level_floor(level + 1),
        items=[FriendZoneXpEventOut(reason=e.reason, amount=e.amount, created_at=e.created_at) for e in page],
        next_cursor=access.encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > XP_PAGE else None,
    )


# ------------------------------------------------------------------ friends
@router.get("/friends", response_model=FriendZonePlayersOut)
async def list_friends(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    return FriendZonePlayersOut(items=await relations.friends(db, me))


@router.delete("/friends/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(
    player_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Remove a friend, silently: to them it looks exactly like a block."""
    me = await access.member(request, principal, db)
    other = await relations.in_cohort(db, me, player_id)
    if other is None:
        raise NotFound()
    await relations.lock_pair(db, me.user_id, other.user_id)
    if not await relations.unfriend(db, me.user_id, other.user_id):
        raise NotFound()
    await battles.end_open(db, me.user_id, other.user_id, "unfriended")
    await db.commit()
    log.info("friend zone unfriend", extra={"user_id": str(principal.user.id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=access.NO_STORE)


# ------------------------------------------------------------------ requests
@router.get("/requests", response_model=FriendZoneRequestsOut)
async def list_requests(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    incoming, outgoing = await relations.requests(db, me)
    return FriendZoneRequestsOut(incoming=incoming, outgoing=outgoing)


@router.post("/requests", response_model=FriendZonePlayerOut, status_code=status.HTTP_201_CREATED)
async def send_request(
    body: FriendZonePlayerRef,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
):
    """Ask a findable classmate (or answer their request: theirs to you makes you friends at once). The answer is
    the player with their new relation, `outgoing` or `friend`."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    user_id = principal.user.id
    await auth.throttle_friend_zone_request(principal.user)  # commits the count
    other = await relations.visible(db, me, body.player_id)
    if other is None:
        raise NotFound()
    await relations.lock_pair(db, me.user_id, other.user_id)
    if not other.discoverable and await relations.relation(db, me, other) not in ("incoming", "friend"):
        raise NotFound()  # only a findable member can be asked; one who asked you can always be answered
    sent = await relations.send(db, me, other)
    await db.commit()
    log.info("friend zone request", extra={"user_id": str(user_id), "outcome": sent.relation})
    return await relations.player_out(db, me, other, sent.relation)


async def _my_request(db: AsyncSession, me: FzProfile, request_id: uuid.UUID, *, mine_as: str) -> FzFriendRequest:
    """A request where I am the recipient (or sender), in my cohort; else 404."""
    column = FzFriendRequest.recipient_id if mine_as == "recipient" else FzFriendRequest.sender_id
    found = await db.scalar(
        select(FzFriendRequest).where(
            FzFriendRequest.id == request_id, column == me.user_id, FzFriendRequest.cohort == me.cohort
        )
    )
    if found is None:
        raise NotFound()
    return found


async def _locked(db: AsyncSession, request_id: uuid.UUID) -> FzFriendRequest:
    return await db.scalar(
        select(FzFriendRequest)
        .where(FzFriendRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


@router.post("/requests/{request_id}/accept", response_model=FriendZonePlayerOut)
async def accept_request(
    request_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Accept a request sent to you (an expired one is gone). Accepting twice answers the same."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    found = await _my_request(db, me, request_id, mine_as="recipient")
    sender = await db.scalar(select(FzProfile).where(FzProfile.user_id == found.sender_id))
    other = await relations.visible(db, me, sender.id) if sender is not None else None
    if other is None:
        raise NotFound()
    await relations.lock_pair(db, me.user_id, other.user_id)
    pending = await _locked(db, request_id)
    fresh = await db.scalar(select(func.clock_timestamp())) < pending.created_at + relations.REQUEST_LIFETIME
    if pending.status == "pending" and fresh:
        await relations.accept(db, me, other, pending)
        await db.commit()
        log.info("friend zone accept", extra={"user_id": str(principal.user.id)})
    elif pending.status != "accepted" or await relations.relation(db, me, other) != "friend":
        raise NotFound()
    return await relations.player_out(db, me, other, "friend")


async def _close(db: AsyncSession, found: FzFriendRequest, status_: str) -> None:
    """Decline or cancel a request still open (repeating it is a no-op); a request in any other state is 404.
    A sender may cancel a request that was declined: to them it looked unanswered."""
    pending = await _locked(db, found.id)
    if pending.status == status_:
        return
    if pending.status != "pending" and not (status_ == "cancelled" and pending.status == "declined"):
        raise NotFound()
    pending.status, pending.responded_at = status_, func.clock_timestamp()
    await db.commit()


@router.post("/requests/{request_id}/decline", status_code=status.HTTP_204_NO_CONTENT)
async def decline_request(
    request_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Decline a request sent to you. The sender is not told; to them it stays unanswered until it expires."""
    me = await access.member(request, principal, db)
    await _close(db, await _my_request(db, me, request_id, mine_as="recipient"), "declined")
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=access.NO_STORE)


@router.post("/requests/{request_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_request(
    request_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    me = await access.member(request, principal, db)
    await _close(db, await _my_request(db, me, request_id, mine_as="sender"), "cancelled")
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=access.NO_STORE)


# ------------------------------------------------------------------ blocks
@router.get("/blocks", response_model=FriendZonePlayersOut)
async def list_blocks(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    return FriendZonePlayersOut(items=await relations.blocked_by_me(db, me))


@router.post("/blocks", status_code=status.HTTP_204_NO_CONTENT)
async def block_player(
    body: FriendZonePlayerRef,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Block a member: the friendship and pending requests end, and neither of you can see or find the other.
    They are not told. Blocking twice answers the same."""
    me = await access.member(request, principal, db)
    other = await relations.in_cohort(db, me, body.player_id)
    if other is None:
        raise NotFound()
    await relations.lock_pair(db, me.user_id, other.user_id)
    await relations.block(db, me, other)
    await battles.end_open(db, me.user_id, other.user_id, "blocked")
    await db.commit()
    log.info("friend zone block", extra={"user_id": str(principal.user.id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=access.NO_STORE)


@router.delete("/blocks/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_player(
    player_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Unblock. A friendship ended by the block does not come back."""
    me = await access.member(request, principal, db)
    other = await relations.in_cohort(db, me, player_id)
    if other is None:
        raise NotFound()
    await relations.lock_pair(db, me.user_id, other.user_id)
    if not await relations.unblock(db, me, other):
        raise NotFound()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=access.NO_STORE)


# ------------------------------------------------------------------ reports
@router.post("/reports", response_model=FriendZoneReportOut, status_code=status.HTTP_201_CREATED)
async def report_player(
    body: FriendZoneReportRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
):
    """Report a member to NetworkersHome (the owner reviews reports; plan section 7). The member is not told."""
    response.headers.update(access.NO_STORE)
    me = await access.member(request, principal, db)
    user_id = principal.user.id
    await auth.throttle_friend_zone_report(principal.user)  # commits the count
    other = await relations.in_cohort(db, me, body.player_id)
    if other is None:
        raise NotFound()
    if body.battle_id is not None and not await relations.shared_battle(db, body.battle_id, me.user_id, other.user_id):
        raise NotFound()
    details = (body.details or "").strip() or None
    report_id = await relations.report(db, me, other, body.reason, details, body.battle_id)
    await db.commit()
    log.info("friend zone report", extra={"user_id": str(user_id), "report_id": str(report_id), "reason": body.reason})
    return FriendZoneReportOut(report_id=report_id)
