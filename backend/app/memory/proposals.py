from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryExtractionJob, MemoryProposal, Message, UserSettings
from app.errors import ValidationFailed, VersionConflict
from app.memory.policy import normalize_content, validate_memory_policy
from app.memory.repository import decode_cursor, encode_cursor


class ProposalRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, user_id: uuid.UUID, proposal_id: uuid.UUID, *, lock: bool = False) -> MemoryProposal | None:
        query = select(MemoryProposal).where(MemoryProposal.id == proposal_id, MemoryProposal.user_id == user_id)
        if lock:
            query = query.with_for_update()
        return await self.db.scalar(query)

    async def list_pending(
        self, user_id: uuid.UUID, limit: int, cursor: str | None
    ) -> tuple[list[MemoryProposal], str | None]:
        query = select(MemoryProposal).where(
            MemoryProposal.user_id == user_id,
            MemoryProposal.status == "pending",
        )
        if cursor:
            ts, row_id = decode_cursor(cursor)
            query = query.where(
                or_(MemoryProposal.created_at < ts, and_(MemoryProposal.created_at == ts, MemoryProposal.id < row_id))
            )
        rows = list(
            await self.db.scalars(
                query.order_by(MemoryProposal.created_at.desc(), MemoryProposal.id.desc()).limit(limit + 1)
            )
        )
        next_cursor = encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
        return rows[:limit], next_cursor

    async def source_is_eligible(self, proposal: MemoryProposal) -> bool:
        rows = (
            await self.db.execute(
                select(Message.id, Message.role, Message.status).where(
                    Message.conversation_id == proposal.conversation_id,
                    Message.id.in_([proposal.source_message_id, proposal.assistant_message_id]),
                )
            )
        ).all()
        states = {row.id: (row.role, row.status) for row in rows}
        source = states.get(proposal.source_message_id)
        assistant = states.get(proposal.assistant_message_id)
        return source == ("user", "complete") and assistant == ("assistant", "complete")

    async def preferences_allow(self, proposal: MemoryProposal) -> bool:
        # The same reading of the consent keys as the worker (jobs/memory.py): a malformed stored
        # value is no consent, never an error. Imported here: preferences.py imports this module.
        from app.memory.preferences import MemoryPreferences

        row = await self.db.get(UserSettings, proposal.user_id, with_for_update=True)
        current = MemoryPreferences.from_settings(row)
        cleared_before = current.cleared_before
        if cleared_before is not None and cleared_before.tzinfo is None:
            cleared_before = cleared_before.replace(tzinfo=UTC)
        return proposal.consent_epoch == current.consent_epoch and (
            cleared_before is None or proposal.observed_at > cleared_before
        )

    async def reject(self, proposal: MemoryProposal) -> None:
        await self.db.execute(
            update(MemoryProposal)
            .where(MemoryProposal.id == proposal.id, MemoryProposal.user_id == proposal.user_id)
            .values(
                status="rejected",
                version=proposal.version + 1,
                reviewed_at=func.clock_timestamp(),
                updated_at=func.clock_timestamp(),
            )
        )

    async def invalidate_pending(self, user_id: uuid.UUID) -> None:
        """Suggestions made under a consent that was withdrawn: closed, since they can never be approved."""
        await self.db.execute(
            update(MemoryProposal)
            .where(MemoryProposal.user_id == user_id, MemoryProposal.status == "pending")
            .values(
                status="invalidated",
                version=MemoryProposal.version + 1,
                updated_at=func.clock_timestamp(),
            )
        )

    async def delete_user_derived(self, user_id: uuid.UUID) -> None:
        await self.db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.user_id == user_id))
        await self.db.execute(delete(MemoryProposal).where(MemoryProposal.user_id == user_id))


def proposal_public(proposal: MemoryProposal) -> dict:
    return {
        "id": str(proposal.id),
        "operation": proposal.operation,
        "targetMemoryId": str(proposal.target_memory_id) if proposal.target_memory_id else None,
        "kind": proposal.kind,
        "content": proposal.content,
        "status": proposal.status,
        "version": proposal.version,
        "confidence": proposal.confidence,
        "evidenceReason": proposal.evidence_reason,
        "sourceMessageId": str(proposal.source_message_id),
        "assistantMessageId": str(proposal.assistant_message_id),
        "observedAt": proposal.observed_at.isoformat(),
        "expiresAt": proposal.expires_at.isoformat() if proposal.expires_at else None,
        "validFrom": proposal.valid_from.isoformat() if proposal.valid_from else None,
        "validTo": proposal.valid_to.isoformat() if proposal.valid_to else None,
        "appliedMemoryId": str(proposal.applied_memory_id) if proposal.applied_memory_id else None,
        "reviewedAt": proposal.reviewed_at.isoformat() if proposal.reviewed_at else None,
        "createdAt": proposal.created_at.isoformat(),
        "updatedAt": proposal.updated_at.isoformat(),
    }


def validate_approval_content(
    proposal: MemoryProposal,
    *,
    kind: str | None,
    content: str | None,
    expires_at: datetime | None,
    valid_from: datetime | None,
    valid_to: datetime | None,
    fields_set: set[str],
    max_chars: int,
) -> tuple[str, str, datetime | None, datetime | None, datetime | None]:
    approved_kind = kind if "kind" in fields_set else proposal.kind
    approved_content = content if "content" in fields_set else proposal.content
    approved_expiry = expires_at if "expires_at" in fields_set else proposal.expires_at
    approved_valid_from = valid_from if "valid_from" in fields_set else proposal.valid_from
    approved_valid_to = valid_to if "valid_to" in fields_set else proposal.valid_to
    normalized = normalize_content(approved_content, max_chars=max_chars)
    validate_memory_policy(normalized)
    if approved_valid_from and approved_valid_to and approved_valid_to <= approved_valid_from:
        raise ValidationFailed("validTo must be after validFrom.")
    return approved_kind, normalized, approved_expiry, approved_valid_from, approved_valid_to


def ensure_pending_version(proposal: MemoryProposal, expected_version: int) -> None:
    if proposal.status != "pending" or proposal.version != expected_version:
        raise VersionConflict(current=proposal_public(proposal))


ReviewOutcome = Literal["approved", "rejected"]
