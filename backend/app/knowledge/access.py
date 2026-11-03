"""Knowledge Library access invariants.

Capabilities and source visibility are always decided in PostgreSQL. Callers never supply a
trusted user id or broaden a source query after this layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_principal
from app.db.models import KnowledgeAdminGrant, KnowledgeSource, KnowledgeSourceUserGrant
from app.db.session import get_session
from app.errors import NotFound


def utcnow() -> datetime:
    return datetime.now(UTC)


async def is_knowledge_admin(db: AsyncSession, user_id: uuid.UUID) -> bool:
    now = utcnow()
    return bool(
        await db.scalar(
            select(
                exists().where(
                    KnowledgeAdminGrant.user_id == user_id,
                    KnowledgeAdminGrant.revoked_at.is_(None),
                    or_(KnowledgeAdminGrant.expires_at.is_(None), KnowledgeAdminGrant.expires_at > now),
                )
            )
        )
    )


async def require_knowledge_admin(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
) -> Principal:
    if not await is_knowledge_admin(db, principal.user.id):
        raise NotFound()
    return principal


async def accessible_source_ids(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    now = utcnow()
    rows = await db.scalars(
        select(KnowledgeSource.id).where(
            KnowledgeSource.rights_revoked_at.is_(None),
            or_(
                KnowledgeSource.owner_user_id == user_id,
                (KnowledgeSource.visibility == "global") & (KnowledgeSource.rights_status == "approved"),
                (KnowledgeSource.visibility == "selected")
                & (KnowledgeSource.rights_status == "approved")
                & exists().where(
                    KnowledgeSourceUserGrant.source_id == KnowledgeSource.id,
                    KnowledgeSourceUserGrant.user_id == user_id,
                    KnowledgeSourceUserGrant.revoked_at.is_(None),
                    or_(
                        KnowledgeSourceUserGrant.expires_at.is_(None),
                        KnowledgeSourceUserGrant.expires_at > now,
                    ),
                ),
            ),
        )
    )
    return list(rows)
