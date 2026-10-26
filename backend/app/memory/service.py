from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from langchain_core.embeddings import Embeddings
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryItem
from app.errors import Conflict, NotFound, ServiceUnavailable, ValidationFailed, VersionConflict
from app.memory.policy import normalize_content, validate_memory_policy
from app.memory.repository import MemoryRepository
from app.rag.embeddings import embedding_model_id, is_configured
from app.settings import Settings


@dataclass(frozen=True)
class RecalledMemory:
    id: uuid.UUID
    kind: str
    content: str
    score: float


def rank_candidates(candidates: list[tuple[MemoryItem, float]], settings: Settings) -> list[RecalledMemory]:
    result: list[RecalledMemory] = []
    per_kind: dict[str, int] = {}
    chars = 0
    for item, distance in candidates:
        score = max(0.0, 1.0 - distance)
        if score < settings.memory_min_score:
            continue
        if per_kind.get(item.kind, 0) >= settings.memory_recall_per_kind:
            continue
        extra = len(item.content) + (1 if result else 0)
        if chars + extra > settings.memory_max_context_chars:
            continue
        result.append(RecalledMemory(item.id, item.kind, item.content, score))
        per_kind[item.kind] = per_kind.get(item.kind, 0) + 1
        chars += extra
        if len(result) >= settings.memory_recall_limit:
            break
    return result


class MemoryService:
    def __init__(self, db: AsyncSession, settings: Settings, embeddings: Embeddings) -> None:
        self.db = db
        self.settings = settings
        self.embeddings = embeddings
        self.repo = MemoryRepository(db)
        self.model = embedding_model_id(embeddings)

    def _public_current(self, item: MemoryItem) -> dict:
        return {
            "id": str(item.id),
            "kind": item.kind,
            "content": item.content,
            "status": item.status,
            "version": item.version,
            "supersedesId": str(item.supersedes_id) if item.supersedes_id else None,
            "sourceMessageId": str(item.source_message_id) if item.source_message_id else None,
            "expiresAt": item.expires_at.isoformat() if item.expires_at else None,
            "createdAt": item.created_at.isoformat(),
            "updatedAt": item.updated_at.isoformat(),
        }

    def _normalize(self, content: str) -> tuple[str, str]:
        normalized = normalize_content(content, max_chars=self.settings.memory_max_item_chars)
        validate_memory_policy(normalized)
        return normalized, hashlib.sha256(normalized.encode()).hexdigest()

    async def _embed(self, content: str) -> list[float]:
        if not is_configured(self.embeddings):
            raise ServiceUnavailable("Memory embeddings are not configured.")
        try:
            return (await self.embeddings.aembed_documents([content]))[0]
        except Exception as error:  # noqa: BLE001 - provider details never reach clients
            raise ServiceUnavailable("Memory embedding is temporarily unavailable.") from error

    @staticmethod
    def _validate_expiry(expires_at: datetime | None) -> None:
        if expires_at is not None and expires_at <= datetime.now(UTC):
            raise ValidationFailed("expiresAt must be in the future.")

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        kind: str,
        content: str,
        source_message_id: uuid.UUID | None,
        expires_at: datetime | None,
    ) -> MemoryItem:
        normalized, content_hash = self._normalize(content)
        self._validate_expiry(expires_at)
        if source_message_id is not None and not await self.repo.source_message_owned(user_id, source_message_id):
            raise NotFound("Source message not found.")
        try:
            await self.db.rollback()  # release the read transaction before the provider call
            embedding = await self._embed(normalized)
            if source_message_id is not None and not await self.repo.source_message_owned(user_id, source_message_id):
                raise NotFound("Source message not found.")
            item = await self.repo.create(
                user_id=user_id,
                kind=kind,
                content=normalized,
                content_hash=content_hash,
                source_message_id=source_message_id,
                expires_at=expires_at,
                embedding=embedding,
                embedding_model=self.model,
            )
            await self.db.commit()
            return item
        except (NotFound, ValidationFailed):
            await self.db.rollback()
            raise
        except IntegrityError as error:
            await self.db.rollback()
            raise Conflict("That active memory already exists.") from error

    async def get(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> MemoryItem:
        item = await self.repo.get(user_id, memory_id)
        if item is None:
            raise NotFound("Memory not found.")
        return item

    async def list(self, user_id: uuid.UUID, limit: int, cursor: str | None, kind: str | None):
        return await self.repo.list_active(user_id, limit, cursor, kind)

    async def export(self, user_id: uuid.UUID) -> list[MemoryItem]:
        return await self.repo.export_active(user_id)

    async def correct(
        self,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        *,
        expected_version: int,
        kind: str | None,
        content: str | None,
        expires_at: datetime | None,
        fields_set: set[str],
    ) -> MemoryItem:
        current = await self.repo.get(user_id, memory_id)
        if current is None:
            raise NotFound("Memory not found.")
        if current.status != "active" or current.version != expected_version:
            raise VersionConflict(current=self._public_current(current))

        new_kind = kind if "kind" in fields_set else current.kind
        new_content = content if "content" in fields_set else current.content
        new_expiry = expires_at if "expires_at" in fields_set else current.expires_at
        normalized, content_hash = self._normalize(new_content)
        self._validate_expiry(new_expiry)
        if new_kind == current.kind and normalized == current.content and new_expiry == current.expires_at:
            raise ValidationFailed("The correction does not change the memory.")
        await self.db.rollback()  # never hold a database transaction across the provider call
        embedding = await self._embed(normalized)

        # Do not hold a row lock while waiting on the embedding provider. Re-lock and revalidate
        # immediately before the correction transaction so concurrent updates have one winner.
        item = await self.repo.get(user_id, memory_id, lock=True)
        if item is None:
            await self.db.rollback()
            raise NotFound("Memory not found.")
        if item.status != "active" or item.version != expected_version:
            conflict = self._public_current(item)
            await self.db.rollback()
            raise VersionConflict(current=conflict)
        try:
            new_kind = kind if "kind" in fields_set else item.kind
            new_content = content if "content" in fields_set else item.content
            new_expiry = expires_at if "expires_at" in fields_set else item.expires_at
            normalized, content_hash = self._normalize(new_content)
            self._validate_expiry(new_expiry)
            if new_kind == item.kind and normalized == item.content and new_expiry == item.expires_at:
                await self.db.rollback()
                raise ValidationFailed("The correction does not change the memory.")
            await self.repo.supersede(item)
            await self.db.flush()
            successor = await self.repo.create(
                user_id=user_id,
                kind=new_kind,
                content=normalized,
                content_hash=content_hash,
                source_message_id=item.source_message_id,
                expires_at=new_expiry,
                embedding=embedding,
                embedding_model=self.model,
                version=item.version + 1,
                supersedes_id=item.id,
            )
            await self.db.commit()
            return successor
        except IntegrityError as error:
            await self.db.rollback()
            raise Conflict("The correction conflicts with an existing memory.") from error

    async def delete(self, user_id: uuid.UUID, memory_id: uuid.UUID, expected_version: int) -> None:
        item = await self.repo.get(user_id, memory_id, lock=True)
        if item is None:
            raise NotFound("Memory not found.")
        if item.version != expected_version:
            await self.db.rollback()
            raise VersionConflict(current=self._public_current(item))
        await self.repo.delete_chain(user_id, memory_id)
        await self.db.commit()

    async def delete_all(self, user_id: uuid.UUID) -> int:
        count = await self.repo.delete_all(user_id)
        await self.db.commit()
        return count

    async def recall_with_embedding(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        embedding: list[float],
        *,
        kinds: tuple[str, ...] | None = None,
    ) -> list[RecalledMemory]:
        if not self.settings.memory_recall_enabled:
            return []
        candidates = await MemoryRepository(db).recall_candidates(
            user_id,
            embedding,
            self.model,
            limit=self.settings.memory_recall_candidates,
            kinds=kinds,
        )
        return rank_candidates(candidates, self.settings)

    async def recall(
        self, user_id: uuid.UUID, query: str, *, kinds: tuple[str, ...] | None = None
    ) -> list[RecalledMemory]:
        """Convenience for non-chat callers using this service's request session."""
        if not self.settings.memory_recall_enabled or not query.strip() or not is_configured(self.embeddings):
            return []
        try:
            embedding = await self.embeddings.aembed_query(query)
        except Exception as error:  # noqa: BLE001 - provider details never reach clients
            raise ServiceUnavailable("Memory retrieval is temporarily unavailable.") from error
        return await self.recall_with_embedding(self.db, user_id, embedding, kinds=kinds)
