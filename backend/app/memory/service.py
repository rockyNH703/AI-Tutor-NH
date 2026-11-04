from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from langchain_core.embeddings import Embeddings
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryItem
from app.errors import Conflict, NotFound, ServiceUnavailable, ValidationFailed, VersionConflict
from app.memory.extraction import recallable_chars
from app.memory.foundation import lock_memory_user
from app.memory.policy import normalize_content, validate_memory_policy
from app.memory.preferences import MemoryPreferencesService
from app.memory.proposals import ProposalRepository, ensure_pending_version, validate_approval_content
from app.memory.repository import MemoryRepository
from app.rag.embeddings import embedding_model_id, is_configured
from app.settings import Settings

MAX_ACTIVE_MEMORIES = 200  # per account (K-48b): far above real use, a bound on embeddings and recall


@dataclass(frozen=True)
class RecalledMemory:
    id: uuid.UUID
    kind: str
    content: str
    score: float


def content_hmac(settings: Settings, user_id: uuid.UUID, content_hash: str) -> str:
    """The deletion marker's key for one text: deleted memories leave this, never the text."""
    key = settings.secret_key.get_secret_value().encode()
    payload = f"memory:{user_id}:{settings.memory_deletion_policy_version}:{content_hash}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


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
            "origin": item.origin,
            "proposalId": str(item.proposal_id) if item.proposal_id else None,
            "lastConfirmedAt": item.last_confirmed_at.isoformat(),
            "validFrom": item.valid_from.isoformat() if item.valid_from else None,
            "validTo": item.valid_to.isoformat() if item.valid_to else None,
            "processorVersion": item.processor_version,
            "embeddingModel": item.embedding_model,
            "embeddingDimensions": item.embedding_dimensions,
            "embeddingVersion": item.embedding_version,
            "expiresAt": item.expires_at.isoformat() if item.expires_at else None,
            "createdAt": item.created_at.isoformat(),
            "updatedAt": item.updated_at.isoformat(),
        }

    def _content_hmac(self, user_id: uuid.UUID, content_hash: str) -> str:
        return content_hmac(self.settings, user_id, content_hash)

    async def check_capacity(self, user_id: uuid.UUID) -> None:
        """Every new memory is an embedding call and recall candidate: an account keeps a bounded number."""
        if await self.repo.count_active(user_id) >= MAX_ACTIVE_MEMORIES:
            raise Conflict(
                f"You have {MAX_ACTIVE_MEMORIES} memories, the most you can keep. Delete some in Settings → Memory.",
                code="memory_limit_reached",
            )

    async def suppressed(self, user_id: uuid.UUID, content_hash: str) -> bool:
        return await self.repo.deletion_marker_exists(
            user_id,
            self._content_hmac(user_id, content_hash),
            self.settings.memory_deletion_policy_version,
        )

    def _normalize(self, content: str) -> tuple[str, str]:
        normalized = normalize_content(content, max_chars=recallable_chars(self.settings))
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
        await self.check_capacity(user_id)
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
                embedding_dimensions=self.settings.embedding_dimensions,
                embedding_version=self.settings.memory_embedding_version,
            )
            await self.db.commit()
            return item
        except (NotFound, ValidationFailed):
            await self.db.rollback()
            raise
        except IntegrityError as error:
            await self.db.rollback()
            raise Conflict("That active memory already exists.") from error

    async def find_exact_active(self, user_id: uuid.UUID, content: str) -> list[MemoryItem]:
        normalized, content_hash = self._normalize(content)
        return [
            item for item in await self.repo.get_by_content_hash(user_id, content_hash) if item.content == normalized
        ]

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
                embedding_dimensions=self.settings.embedding_dimensions,
                embedding_version=self.settings.memory_embedding_version,
                version=item.version + 1,
                supersedes_id=item.id,
            )
            await self.db.commit()
            return successor
        except IntegrityError as error:
            await self.db.rollback()
            raise Conflict("The correction conflicts with an existing memory.") from error

    async def delete(self, user_id: uuid.UUID, memory_id: uuid.UUID, expected_version: int) -> None:
        await lock_memory_user(self.db, user_id)
        item = await self.repo.get(user_id, memory_id, lock=True)
        if item is None:
            raise NotFound("Memory not found.")
        if item.version != expected_version:
            await self.db.rollback()
            raise VersionConflict(current=self._public_current(item))
        hashes = await self.repo.chain_content_hashes(user_id, memory_id)
        await self.repo.add_deletion_markers(
            user_id,
            [
                (self._content_hmac(user_id, content_hash), self.settings.memory_deletion_policy_version, "delete")
                for content_hash in hashes
            ],
        )
        deleted = await self.repo.delete_chain(user_id, memory_id)
        if deleted == 0:
            await self.db.rollback()
            raise NotFound("Memory not found.")
        await self.db.commit()

    async def delete_all(self, user_id: uuid.UUID) -> int:
        preferences = MemoryPreferencesService(self.db)
        count, hashes = await preferences.clear(user_id, self.repo)
        await self.repo.add_deletion_markers(
            user_id,
            [
                (self._content_hmac(user_id, content_hash), self.settings.memory_deletion_policy_version, "clear")
                for content_hash in hashes
            ],
        )
        await self.db.commit()
        return count

    async def list_proposals(self, user_id: uuid.UUID, limit: int, cursor: str | None):
        return await ProposalRepository(self.db).list_pending(user_id, limit, cursor)

    async def approve_proposal(
        self,
        user_id: uuid.UUID,
        proposal_id: uuid.UUID,
        *,
        expected_version: int,
        kind: str | None,
        content: str | None,
        expires_at: datetime | None,
        valid_from: datetime | None,
        valid_to: datetime | None,
        fields_set: set[str],
    ) -> MemoryItem:
        proposals = ProposalRepository(self.db)
        proposal = await proposals.get(user_id, proposal_id)
        if proposal is None:
            raise NotFound("Memory proposal not found.")
        if proposal.status == "applied" and proposal.applied_memory_id:
            applied = await self.repo.get(user_id, proposal.applied_memory_id)
            if applied is not None:
                return applied
            raise Conflict("The applied memory no longer exists.")
        ensure_pending_version(proposal, expected_version)
        approved_kind, normalized, approved_expiry, approved_valid_from, approved_valid_to = validate_approval_content(
            proposal,
            kind=kind,
            content=content,
            expires_at=expires_at,
            valid_from=valid_from,
            valid_to=valid_to,
            fields_set=fields_set,
            max_chars=recallable_chars(self.settings),
        )
        self._validate_expiry(approved_expiry)
        content_hash = hashlib.sha256(normalized.encode()).hexdigest()
        if await self.suppressed(user_id, content_hash):
            raise Conflict("That memory was previously deleted or rejected.")
        if proposal.operation == "add":
            await self.check_capacity(user_id)
        await self.db.rollback()
        embedding = await self._embed(normalized)

        proposal = await proposals.get(user_id, proposal_id, lock=True)
        if proposal is None:
            await self.db.rollback()
            raise NotFound("Memory proposal not found.")
        if proposal.status == "applied" and proposal.applied_memory_id:
            applied = await self.repo.get(user_id, proposal.applied_memory_id)
            if applied is not None:
                await self.db.rollback()
                return applied
            await self.db.rollback()
            raise Conflict("The applied memory no longer exists.")
        ensure_pending_version(proposal, expected_version)
        await lock_memory_user(self.db, user_id)
        if await self.suppressed(user_id, content_hash):
            await self.db.rollback()
            raise Conflict("That memory was previously deleted or rejected.")
        if not await proposals.source_is_eligible(proposal) or not await proposals.preferences_allow(proposal):
            await self.db.rollback()
            raise Conflict("This proposal is no longer eligible for approval.")
        try:
            target = None
            version = 1
            supersedes_id = None
            source_message_id = proposal.source_message_id
            if proposal.operation == "supersede":
                target = await self.repo.get(user_id, proposal.target_memory_id, lock=True)
                if target is None or target.status != "active" or target.version != proposal.target_memory_version:
                    raise Conflict("The target memory changed before approval.")
                version = target.version + 1
                supersedes_id = target.id
                source_message_id = target.source_message_id or proposal.source_message_id
                await self.repo.supersede(target)
                await self.db.flush()
            item = await self.repo.create(
                user_id=user_id,
                kind=approved_kind,
                content=normalized,
                content_hash=content_hash,
                source_message_id=source_message_id,
                expires_at=approved_expiry,
                embedding=embedding,
                embedding_model=self.model,
                embedding_dimensions=self.settings.embedding_dimensions,
                embedding_version=self.settings.memory_embedding_version,
                origin="approved_extraction",
                proposal_id=proposal.id,
                processor_version=proposal.processor_version,
                valid_from=approved_valid_from,
                valid_to=approved_valid_to,
                version=version,
                supersedes_id=supersedes_id,
            )
            proposal.status = "applied"
            proposal.version += 1
            proposal.applied_memory_id = item.id
            proposal.reviewed_at = datetime.now(UTC)
            proposal.applied_at = proposal.reviewed_at
            proposal.updated_at = proposal.reviewed_at
            await self.db.commit()
            return item
        except IntegrityError as error:
            await self.db.rollback()
            raise Conflict("The proposal conflicts with an existing memory.") from error

    async def reject_proposal(self, user_id: uuid.UUID, proposal_id: uuid.UUID, expected_version: int) -> None:
        proposals = ProposalRepository(self.db)
        proposal = await proposals.get(user_id, proposal_id, lock=True)
        if proposal is None:
            raise NotFound("Memory proposal not found.")
        if proposal.status == "rejected":
            await self.db.rollback()
            return
        ensure_pending_version(proposal, expected_version)
        await self.repo.add_deletion_markers(
            user_id,
            [
                (
                    self._content_hmac(user_id, proposal.content_hash),
                    self.settings.memory_deletion_policy_version,
                    "reject",
                )
            ],
        )
        await proposals.reject(proposal)
        await self.db.commit()

    async def get_preferences(self, user_id: uuid.UUID):
        _, preferences = await MemoryPreferencesService(self.db).get(user_id)
        return preferences

    async def update_preferences(self, user_id: uuid.UUID, *, automatic_proposals: bool, expected_version: int):
        return await MemoryPreferencesService(self.db).update(
            user_id,
            automatic_proposals=automatic_proposals,
            expected_version=expected_version,
        )

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
            self.settings.memory_embedding_version,
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
