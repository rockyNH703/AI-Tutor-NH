from __future__ import annotations

import base64
import uuid
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, MemoryDeletionMarker, MemoryItem, Message
from app.errors import ValidationFailed


def encode_cursor(ts: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{row_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(ts), uuid.UUID(row_id)
    except Exception as error:  # noqa: BLE001
        raise ValidationFailed("Invalid cursor.") from error


class MemoryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _currently_valid():
        return (
            or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > func.clock_timestamp()),
            or_(MemoryItem.valid_from.is_(None), MemoryItem.valid_from <= func.clock_timestamp()),
            or_(MemoryItem.valid_to.is_(None), MemoryItem.valid_to > func.clock_timestamp()),
        )

    async def get(self, user_id: uuid.UUID, memory_id: uuid.UUID, *, lock: bool = False) -> MemoryItem | None:
        query = select(MemoryItem).where(MemoryItem.id == memory_id, MemoryItem.user_id == user_id)
        if lock:
            query = query.with_for_update()
        return await self.db.scalar(query)

    async def get_by_content_hash(
        self, user_id: uuid.UUID, content_hash: str, *, lock: bool = False
    ) -> list[MemoryItem]:
        query = (
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                MemoryItem.content_hash == content_hash,
                MemoryItem.status == "active",
                *self._currently_valid(),
            )
            .order_by(MemoryItem.updated_at.desc(), MemoryItem.id)
        )
        if lock:
            query = query.with_for_update()
        return list(await self.db.scalars(query))

    async def list_active(
        self, user_id: uuid.UUID, limit: int, cursor: str | None, kind: str | None
    ) -> tuple[list[MemoryItem], str | None]:
        query = select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.status == "active",
            *self._currently_valid(),
        )
        if kind:
            query = query.where(MemoryItem.kind == kind)
        if cursor:
            ts, row_id = decode_cursor(cursor)
            query = query.where(
                or_(MemoryItem.updated_at < ts, and_(MemoryItem.updated_at == ts, MemoryItem.id < row_id))
            )
        rows = list(
            await self.db.scalars(query.order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc()).limit(limit + 1))
        )
        next_cursor = encode_cursor(rows[limit - 1].updated_at, rows[limit - 1].id) if len(rows) > limit else None
        return rows[:limit], next_cursor

    async def export_active(self, user_id: uuid.UUID) -> list[MemoryItem]:
        return list(
            await self.db.scalars(
                select(MemoryItem)
                .where(
                    MemoryItem.user_id == user_id,
                    MemoryItem.status == "active",
                    *self._currently_valid(),
                )
                .order_by(MemoryItem.created_at, MemoryItem.id)
            )
        )

    async def source_message_owned(self, user_id: uuid.UUID, message_id: uuid.UUID) -> bool:
        return (
            await self.db.scalar(
                select(Message.id)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Message.id == message_id, Conversation.user_id == user_id)
            )
        ) is not None

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        kind: str,
        content: str,
        content_hash: str,
        source_message_id: uuid.UUID | None,
        expires_at: datetime | None,
        embedding: list[float],
        embedding_model: str,
        embedding_dimensions: int,
        embedding_version: str,
        origin: str = "explicit",
        proposal_id: uuid.UUID | None = None,
        processor_version: str | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        version: int = 1,
        supersedes_id: uuid.UUID | None = None,
    ) -> MemoryItem:
        item = MemoryItem(
            user_id=user_id,
            kind=kind,
            content=content,
            content_hash=content_hash,
            status="active",
            version=version,
            supersedes_id=supersedes_id,
            source_message_id=source_message_id,
            origin=origin,
            proposal_id=proposal_id,
            processor_version=processor_version,
            valid_from=valid_from,
            valid_to=valid_to,
            expires_at=expires_at,
            embedding=embedding,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            embedding_version=embedding_version,
        )
        self.db.add(item)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def supersede(self, item: MemoryItem) -> None:
        await self.db.execute(
            update(MemoryItem)
            .where(MemoryItem.id == item.id, MemoryItem.user_id == item.user_id)
            .values(status="superseded", superseded_at=func.clock_timestamp(), updated_at=func.clock_timestamp())
        )
        item.status = "superseded"

    async def add_deletion_markers(self, user_id: uuid.UUID, markers: list[tuple[str, str, str]]) -> None:
        for content_hmac, policy_version, reason in markers:
            exists = await self.db.scalar(
                select(MemoryDeletionMarker.id).where(
                    MemoryDeletionMarker.user_id == user_id,
                    MemoryDeletionMarker.content_hmac == content_hmac,
                    MemoryDeletionMarker.policy_version == policy_version,
                )
            )
            if exists is None:
                self.db.add(
                    MemoryDeletionMarker(
                        user_id=user_id,
                        content_hmac=content_hmac,
                        policy_version=policy_version,
                        reason=reason,
                    )
                )

    async def deletion_marker_exists(self, user_id: uuid.UUID, content_hmac: str, policy_version: str) -> bool:
        return (
            await self.db.scalar(
                select(MemoryDeletionMarker.id).where(
                    MemoryDeletionMarker.user_id == user_id,
                    MemoryDeletionMarker.content_hmac == content_hmac,
                    MemoryDeletionMarker.policy_version == policy_version,
                )
            )
        ) is not None

    async def chain_content_hashes(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> list[str]:
        rows = await self.db.execute(
            text(
                """
                WITH RECURSIVE ancestors AS (
                    SELECT id, supersedes_id, content_hash
                    FROM memory_items
                    WHERE id = :memory_id AND user_id = :user_id
                    UNION ALL
                    SELECT parent.id, parent.supersedes_id, parent.content_hash
                    FROM memory_items parent JOIN ancestors child ON child.supersedes_id = parent.id
                    WHERE parent.user_id = :user_id
                ), descendants AS (
                    SELECT id, content_hash FROM memory_items WHERE id = :memory_id AND user_id = :user_id
                    UNION ALL
                    SELECT child.id, child.content_hash
                    FROM memory_items child JOIN descendants parent ON child.supersedes_id = parent.id
                    WHERE child.user_id = :user_id
                )
                SELECT content_hash FROM ancestors UNION SELECT content_hash FROM descendants
                """
            ),
            {"user_id": user_id, "memory_id": memory_id},
        )
        return [row.content_hash for row in rows]

    async def all_content_hashes(self, user_id: uuid.UUID) -> list[str]:
        return list(
            await self.db.scalars(select(MemoryItem.content_hash).where(MemoryItem.user_id == user_id).distinct())
        )

    async def delete_chain(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> int:
        chain_rows = await self.db.execute(
            text(
                """
                WITH RECURSIVE ancestors AS (
                    SELECT id, supersedes_id FROM memory_items WHERE id = :memory_id AND user_id = :user_id
                    UNION ALL
                    SELECT parent.id, parent.supersedes_id
                    FROM memory_items parent JOIN ancestors child ON child.supersedes_id = parent.id
                    WHERE parent.user_id = :user_id
                ), descendants AS (
                    SELECT id FROM memory_items WHERE id = :memory_id AND user_id = :user_id
                    UNION ALL
                    SELECT child.id
                    FROM memory_items child JOIN descendants parent ON child.supersedes_id = parent.id
                    WHERE child.user_id = :user_id
                )
                SELECT id FROM ancestors UNION SELECT id FROM descendants
                """
            ),
            {"user_id": user_id, "memory_id": memory_id},
        )
        chain = [row.id for row in chain_rows]
        if not chain:
            return 0
        await self.db.execute(
            update(MemoryItem).where(MemoryItem.user_id == user_id, MemoryItem.id.in_(chain)).values(supersedes_id=None)
        )
        result = await self.db.execute(
            delete(MemoryItem).where(MemoryItem.user_id == user_id, MemoryItem.id.in_(chain))
        )
        return result.rowcount

    async def delete_all(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(delete(MemoryItem).where(MemoryItem.user_id == user_id))
        return result.rowcount

    async def recall_candidates(
        self,
        user_id: uuid.UUID,
        embedding: list[float],
        embedding_model: str,
        embedding_version: str,
        *,
        limit: int,
        kinds: tuple[str, ...] | None = None,
    ) -> list[tuple[MemoryItem, float]]:
        distance = MemoryItem.embedding.cosine_distance(embedding)
        query = select(MemoryItem, distance.label("distance")).where(
            MemoryItem.user_id == user_id,
            MemoryItem.status == "active",
            *self._currently_valid(),
            MemoryItem.embedding_model == embedding_model,
            MemoryItem.embedding_version == embedding_version,
        )
        if kinds:
            query = query.where(MemoryItem.kind.in_(kinds))
        rows = await self.db.execute(query.order_by(distance, MemoryItem.updated_at.desc(), MemoryItem.id).limit(limit))
        return [(row.MemoryItem, float(row.distance)) for row in rows]
