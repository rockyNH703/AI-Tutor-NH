"""/v1/friend-zone: NH Friend Zone (owner-approved plan, revision 3; docs/api-contract.md "NH Friend Zone").

Every answer is decided here on every request (app/friendzone/access.py). Anything this account may not use is the
same 404, except GET /me, which says enabled:false so the web app can hide Friend Zone. Logs: ids and outcomes only,
never a name or an invoice number."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_auth_service, get_principal
from app.auth.service import AuthService
from app.db.models import FzProfile
from app.db.session import get_session
from app.errors import Conflict, NotFound, RateLimited, VersionConflict
from app.friendzone import access, battle_routes, battles, daily, daily_routes, friends
from app.friendzone.names import clean_name
from app.friendzone.schemas import (
    FriendZoneJoinRequest,
    FriendZoneMeOut,
    FriendZoneProfileOut,
    UpdateFriendZoneProfileRequest,
)
from app.logging import get_logger

router = APIRouter(prefix="/v1/friend-zone", tags=["friend-zone"])
log = get_logger("app.friendzone")
router.include_router(friends.router)
router.include_router(battle_routes.router)
router.include_router(daily_routes.router)


def _profile_out(found: FzProfile) -> FriendZoneProfileOut:
    return FriendZoneProfileOut(
        player_id=found.id,
        display_name=found.display_name,
        discoverable=found.discoverable,
        show_program=found.show_program,
        show_stats=found.show_stats,
        version=found.version,
        qa=found.cohort == "qa",
    )


@router.get("/me", response_model=FriendZoneMeOut)
async def get_me(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """Whether Friend Zone is open to this account, and its profile once it has joined."""
    response.headers.update(access.NO_STORE)
    if not request.app.state.friend_zone_poll_limiter.allow(str(principal.user.id)):
        raise RateLimited()
    settings = request.app.state.settings
    now = await db.scalar(select(func.clock_timestamp()))
    enabled = access.enabled(principal.user, settings)
    eligible = enabled and await access.eligible(db, principal.user, settings)
    found = await access.profile(db, principal.user.id) if eligible else None
    if found is not None:
        # an exam of mine past its cutoff completes here too (no timer process), never for a suspended profile (O5);
        # notify_available may return early without committing, so the completion commits on its own
        policy = settings.friend_zone_key_policy
        if found.suspended_at is None and await battles.complete_overdue(db, found.user_id, policy=policy):
            await db.commit()
        # once a day; commits what it writes
        await daily.notify_available(db, found, settings.friend_zone_key_policy, settings.friend_zone_round_seconds)
    return FriendZoneMeOut(
        enabled=enabled,
        eligible=eligible,
        member=found is not None,
        suspended=found is not None and found.suspended_at is not None,
        profile=_profile_out(found) if found is not None else None,
        server_time=now,
    )


@router.post("/me", response_model=FriendZoneProfileOut, status_code=status.HTTP_201_CREATED)
async def join(
    body: FriendZoneJoinRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
):
    """Join Friend Zone. Findable by invoice and showing program or stats are off unless chosen here."""
    response.headers.update(access.NO_STORE)
    settings = request.app.state.settings
    user_id = principal.user.id
    if not await access.eligible(db, principal.user, settings):
        raise NotFound()
    name = clean_name(body.display_name)
    cohort = access.cohort_for(principal.user, settings)
    await auth.throttle_friend_zone_profile(principal.user)  # commits the count
    created = await db.scalar(
        insert(FzProfile)
        .values(
            user_id=user_id,
            cohort=cohort,
            display_name=name,
            discoverable=body.discoverable,
            show_program=body.show_program,
            show_stats=body.show_stats,
        )
        .on_conflict_do_nothing(index_elements=[FzProfile.user_id])
        .returning(FzProfile.id)
    )
    if created is None:
        await db.rollback()
        raise Conflict("You have already joined Friend Zone.", code="already_member")
    await db.commit()
    log.info("friend zone joined", extra={"user_id": str(user_id), "cohort": cohort})
    return _profile_out(await access.profile(db, user_id))


@router.patch("/me", response_model=FriendZoneProfileOut)
async def update_profile(
    body: UpdateFriendZoneProfileRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
):
    """A versioned edit: of two edits made from the same version, the second is a 409 version_conflict."""
    response.headers.update(access.NO_STORE)
    found = await access.member(request, principal, db)
    user_id, version = principal.user.id, body.version
    changes = body.set.model_dump(include=body.set.model_fields_set)
    if "display_name" in changes:
        changes["display_name"] = clean_name(changes["display_name"])
    if not changes:
        return _profile_out(found)
    await auth.throttle_friend_zone_profile(principal.user)
    won = await db.scalar(
        update(FzProfile)
        .where(FzProfile.user_id == user_id, FzProfile.version == version)
        .values(**changes, version=FzProfile.version + 1, updated_at=func.clock_timestamp())
        .returning(FzProfile.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if won is None:
        raise VersionConflict()
    db.expire_all()
    return _profile_out(await access.profile(db, user_id))
