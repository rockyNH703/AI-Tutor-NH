from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryCommandReceipt, UserSettings
from app.errors import VersionConflict
from app.memory.foundation import lock_memory_user
from app.memory.proposals import ProposalRepository
from app.memory.repository import MemoryRepository

AUTOMATIC_PROPOSALS = "memoryAutomaticProposals"
CONSENT_EPOCH = "memoryConsentEpoch"
CLEARED_BEFORE = "memoryClearedBefore"
VERSION = "memoryVersion"
UPDATED_AT = "memoryUpdatedAt"


class MemoryPreferences:
    def __init__(
        self,
        *,
        automatic_proposals: bool = False,
        consent_epoch: int = 0,
        cleared_before: datetime | None = None,
        version: int = 0,
        updated_at: datetime | None = None,
    ) -> None:
        self.automatic_proposals = automatic_proposals
        self.consent_epoch = consent_epoch
        self.cleared_before = cleared_before
        self.version = version
        self.updated_at = updated_at

    @classmethod
    def from_settings(cls, row: UserSettings | None) -> MemoryPreferences:
        values = dict(row.preferences) if row else {}
        cleared = values.get(CLEARED_BEFORE)
        updated = values.get(UPDATED_AT)
        return cls(
            automatic_proposals=bool(values.get(AUTOMATIC_PROPOSALS, False)),
            consent_epoch=max(0, int(values.get(CONSENT_EPOCH, 0))),
            cleared_before=datetime.fromisoformat(cleared) if cleared else None,
            version=max(0, int(values.get(VERSION, 0))),
            updated_at=datetime.fromisoformat(updated) if updated else (row.updated_at if row else None),
        )

    def public(self) -> dict:
        return {
            "automaticProposals": self.automatic_proposals,
            "consentEpoch": self.consent_epoch,
            "clearedBefore": self.cleared_before.isoformat() if self.cleared_before else None,
            "version": self.version,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class MemoryPreferencesService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, user_id: uuid.UUID, *, lock: bool = False) -> tuple[UserSettings | None, MemoryPreferences]:
        row = await self.db.get(UserSettings, user_id, with_for_update=lock)
        return row, MemoryPreferences.from_settings(row)

    async def update(
        self, user_id: uuid.UUID, *, automatic_proposals: bool, expected_version: int
    ) -> MemoryPreferences:
        await lock_memory_user(self.db, user_id)
        row, current = await self.get(user_id, lock=True)
        if current.version != expected_version:
            await self.db.rollback()
            raise VersionConflict(current=current.public())
        now = datetime.now(UTC)
        values = dict(row.preferences) if row else {}
        values.update(
            {
                AUTOMATIC_PROPOSALS: automatic_proposals,
                CONSENT_EPOCH: current.consent_epoch
                + (1 if current.automatic_proposals and not automatic_proposals else 0),
                VERSION: current.version + 1,
                UPDATED_AT: now.isoformat(),
            }
        )
        if row is None:
            row = UserSettings(user_id=user_id, preferences=values, updated_at=now)
            self.db.add(row)
        else:
            row.preferences = values
            row.updated_at = now
        await self.db.commit()
        return MemoryPreferences.from_settings(row)

    async def clear(self, user_id: uuid.UUID, memory: MemoryRepository) -> tuple[int, list[str]]:
        await lock_memory_user(self.db, user_id)
        row, current = await self.get(user_id, lock=True)
        now = datetime.now(UTC)
        values = dict(row.preferences) if row else {}
        values.update(
            {
                AUTOMATIC_PROPOSALS: False,
                CONSENT_EPOCH: current.consent_epoch + 1,
                CLEARED_BEFORE: now.isoformat(),
                VERSION: current.version + 1,
                UPDATED_AT: now.isoformat(),
            }
        )
        hashes = await memory.all_content_hashes(user_id)
        await ProposalRepository(self.db).delete_user_derived(user_id)
        count = await memory.delete_all(user_id)
        await self.db.execute(delete(MemoryCommandReceipt).where(MemoryCommandReceipt.user_id == user_id))
        if row is None:
            self.db.add(UserSettings(user_id=user_id, preferences=values, updated_at=now))
        else:
            row.preferences = values
            row.updated_at = now
        return count, hashes
