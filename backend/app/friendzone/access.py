"""Who may use NH Friend Zone and which cohort they join (plan sections 4 and 6). Every route asks here on every
request: an invoice revoked or expired ends access at once, and FRIEND_ZONE_MODE=off hides everything."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal
from app.db.models import FzProfile
from app.errors import NotFound, ValidationFailed
from app.programs.access import open_program_ids, programs_allow
from app.settings import Settings

NO_STORE = {"Cache-Control": "no-store"}


def encode_cursor(moment: datetime, row_id: uuid.UUID) -> str:
    """A keyset page cursor: (created_at, id) of the last row shown."""
    return base64.urlsafe_b64encode(f"{moment.isoformat()}|{row_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        moment, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(moment), uuid.UUID(row_id)
    except Exception as error:  # noqa: BLE001
        raise ValidationFailed("Invalid cursor.") from error


def _email(user: Any) -> str:
    return getattr(user, "email", "") or ""


def enabled(user: Any, settings: Settings) -> bool:
    """The rollout lets this account in: FRIEND_ZONE_MODE (and PROGRAMS_MODE, which also needs a verified address)."""
    return programs_allow(user, settings) and settings.friend_zone_allow(_email(user))


async def eligible(session: AsyncSession, user: Any, settings: Settings) -> bool:
    """Enabled, and at least one claimed NH invoice opens a program right now."""
    if not enabled(user, settings):
        return False
    return bool(await session.scalar(select(open_program_ids(user.id).exists())))


def cohort_for(user: Any, settings: Settings) -> str:
    """The cohort a joining account gets, for good: 'qa' for the QA list, 'live' for everyone else."""
    return "qa" if settings.friend_zone_qa(_email(user)) else "live"


async def profile(session: AsyncSession, user_id: uuid.UUID) -> FzProfile | None:
    return await session.scalar(select(FzProfile).where(FzProfile.user_id == user_id))


async def member(request: Request, principal: Principal, session: AsyncSession) -> FzProfile:
    """This account's profile, while it is eligible and not suspended; else the 404 of a missing resource."""
    if not await eligible(session, principal.user, request.app.state.settings):
        raise NotFound()
    found = await profile(session, principal.user.id)
    if found is None or found.suspended_at is not None:
        raise NotFound()
    return found
