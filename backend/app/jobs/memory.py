from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.models import MemoryExtractionJob, MemoryProposal, Message, UserSettings
from app.db.session import Database
from app.errors import ValidationFailed
from app.llm.base import LLMError, LLMProvider
from app.memory.extraction import ExtractedProposal, ExtractionOutputError, extract_user_memories
from app.memory.foundation import MEMORY_PROMPT_VERSION, MEMORY_SCHEMA_VERSION, lock_memory_user
from app.memory.policy import normalize_content
from app.memory.preferences import AUTOMATIC_PROPOSALS, CLEARED_BEFORE, CONSENT_EPOCH
from app.settings import Settings


@dataclass(frozen=True)
class MemoryWorkReport:
    claimed: int = 0
    completed: int = 0
    retryable: int = 0
    dead: int = 0
    invalidated: int = 0
    proposals: int = 0


def _digest(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def enqueue_memory_extraction(
    session,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    source_message: Message,
    assistant_message_id: uuid.UUID,
) -> bool:
    """Insert the outbox row in the caller's transaction; never commits."""

    if (
        not settings.memory_enabled
        or settings.memory_automatic_proposals != "on"
        or source_message.role != "user"
        or source_message.status != "complete"
    ):
        return False
    preferences = await session.get(UserSettings, user_id)
    values = dict(preferences.preferences) if preferences else {}
    if not bool(values.get(AUTOMATIC_PROPOSALS, False)):
        return False
    consent_epoch = max(0, int(values.get(CONSENT_EPOCH, 0)))
    source_hash = _digest(source_message.content)
    idempotency_key = _digest(
        user_id,
        source_message.id,
        assistant_message_id,
        source_hash,
        consent_epoch,
        settings.memory_processor_version,
    )
    statement = (
        insert(MemoryExtractionJob)
        .values(
            user_id=user_id,
            conversation_id=conversation_id,
            source_message_id=source_message.id,
            assistant_message_id=assistant_message_id,
            source_hash=source_hash,
            idempotency_key=idempotency_key,
            consent_epoch=consent_epoch,
            source_created_at=source_message.created_at,
            processor_version=settings.memory_processor_version,
            extractor_provider=settings.llm_primary_name,
            extractor_model=settings.memory_worker_model or settings.llm_primary_model,
            prompt_version=MEMORY_PROMPT_VERSION,
            schema_version=MEMORY_SCHEMA_VERSION,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
    )
    result = await session.execute(statement)
    return bool(result.rowcount)


async def process_memory_jobs(
    db: Database,
    settings: Settings,
    provider: LLMProvider,
    now: datetime | None = None,
) -> dict[str, int | str]:
    if settings.memory_automatic_proposals != "on":
        return {"skipped": "MEMORY_AUTOMATIC_PROPOSALS is not on"}
    now = now or datetime.now(UTC)
    await _recover_expired_leases(db, settings, now)
    owner = f"memory-worker-{uuid.uuid4()}"
    job_ids = await _claim_fair(db, settings, owner, now)
    totals = {"claimed": len(job_ids), "completed": 0, "retryable": 0, "dead": 0, "invalidated": 0, "proposals": 0}
    for job_id in job_ids:
        outcome, count = await _process_one(db, settings, provider, job_id, owner, now)
        totals[outcome] += 1
        totals["proposals"] += count
    return totals


async def _recover_expired_leases(db: Database, settings: Settings, now: datetime) -> None:
    async with db.sessionmaker() as session:
        await session.execute(
            update(MemoryExtractionJob)
            .where(
                MemoryExtractionJob.status == "running",
                MemoryExtractionJob.lease_expires_at < now,
            )
            .values(
                status=case(
                    (MemoryExtractionJob.attempt_count >= settings.memory_worker_max_attempts, "dead"),
                    else_="retryable",
                ),
                lease_owner=None,
                lease_expires_at=None,
                next_attempt_at=now,
                error_code="lease_expired",
                error_type="LeaseExpired",
                updated_at=func.clock_timestamp(),
            )
        )
        await session.commit()


async def _claim_fair(db: Database, settings: Settings, owner: str, now: datetime) -> list[uuid.UUID]:
    async with db.sessionmaker() as session:
        ranked = (
            select(
                MemoryExtractionJob.id.label("id"),
                func.row_number()
                .over(
                    partition_by=MemoryExtractionJob.user_id,
                    order_by=(MemoryExtractionJob.created_at, MemoryExtractionJob.id),
                )
                .label("user_rank"),
            )
            .where(
                MemoryExtractionJob.status.in_(("queued", "retryable")),
                or_(
                    MemoryExtractionJob.status == "queued",
                    MemoryExtractionJob.next_attempt_at <= now,
                ),
            )
            .subquery()
        )
        ids = list(
            await session.scalars(
                select(MemoryExtractionJob.id)
                .join(ranked, ranked.c.id == MemoryExtractionJob.id)
                .where(ranked.c.user_rank <= settings.memory_worker_max_per_user_per_run)
                .order_by(ranked.c.user_rank, MemoryExtractionJob.created_at, MemoryExtractionJob.id)
                .limit(settings.memory_worker_max_per_run)
                .with_for_update(skip_locked=True, of=MemoryExtractionJob)
            )
        )
        if ids:
            await session.execute(
                update(MemoryExtractionJob)
                .where(MemoryExtractionJob.id.in_(ids))
                .values(
                    status="running",
                    attempt_count=MemoryExtractionJob.attempt_count + 1,
                    lease_owner=owner,
                    lease_expires_at=now + timedelta(seconds=settings.memory_worker_lease_s),
                    error_code=None,
                    error_type=None,
                    updated_at=func.clock_timestamp(),
                )
            )
        await session.commit()
        return ids


async def _eligibility(session, job: MemoryExtractionJob) -> tuple[str | None, Message | None]:
    rows = list(
        await session.scalars(
            select(Message).where(
                Message.conversation_id == job.conversation_id,
                Message.id.in_((job.source_message_id, job.assistant_message_id)),
            )
        )
    )
    states = {row.id: row for row in rows}
    source = states.get(job.source_message_id)
    assistant = states.get(job.assistant_message_id)
    if (
        source is None
        or assistant is None
        or source.role != "user"
        or source.status != "complete"
        or assistant.role != "assistant"
        or assistant.status != "complete"
    ):
        return "source_ineligible", source
    if _digest(source.content) != job.source_hash:
        return "source_changed", source
    settings_row = await session.get(UserSettings, job.user_id)
    values = dict(settings_row.preferences) if settings_row else {}
    epoch = max(0, int(values.get(CONSENT_EPOCH, 0)))
    cleared = values.get(CLEARED_BEFORE)
    cleared_before = datetime.fromisoformat(cleared) if cleared else None
    if not bool(values.get(AUTOMATIC_PROPOSALS, False)) or epoch != job.consent_epoch:
        return "consent_changed", source
    if cleared_before is not None and _utc(job.source_created_at) <= _utc(cleared_before):
        return "memory_cleared", source
    return None, source


async def _process_one(
    db: Database,
    settings: Settings,
    provider: LLMProvider,
    job_id: uuid.UUID,
    owner: str,
    now: datetime,
) -> tuple[str, int]:
    async with db.sessionmaker() as session:
        job = await session.get(MemoryExtractionJob, job_id)
        if job is None or job.status != "running" or job.lease_owner != owner:
            return "invalidated", 0
        await lock_memory_user(session, job.user_id)
        reason, source = await _eligibility(session, job)
        if reason:
            await _finish(session, job, "invalidated", now, error_code=reason)
            await session.commit()
            return "invalidated", 0
        source_text = source.content
        try:
            result = await extract_user_memories(provider, settings, source_text)
        except Exception as error:  # noqa: BLE001 - every job failure is isolated and classified
            await session.rollback()
            permanent = isinstance(error, (ExtractionOutputError, ValidationFailed))
            retryable = not permanent and (not isinstance(error, LLMError) or getattr(error, "retryable", True))
            return await _fail(db, settings, job_id, owner, now, error, retryable), 0
        await session.rollback()

    async with db.sessionmaker() as session:
        job = await session.get(MemoryExtractionJob, job_id, with_for_update=True)
        if job is None or job.status != "running" or job.lease_owner != owner:
            return "invalidated", 0
        reason, _ = await _eligibility(session, job)
        if reason:
            await _finish(session, job, "invalidated", now, error_code=reason)
            await session.commit()
            return "invalidated", 0
        inserted = 0
        if settings.memory_automatic_proposals == "on":
            for candidate in result.proposals:
                inserted += await _insert_proposal(session, job, candidate, result.provider, result.model)
        await _finish(session, job, "completed", now, proposal_count=inserted)
        await session.commit()
        return "completed", inserted


async def _insert_proposal(
    session,
    job: MemoryExtractionJob,
    candidate: ExtractedProposal,
    provider: str,
    model: str,
) -> int:
    normalized = normalize_content(candidate.content)
    content_hash = _digest(normalized)
    key = _digest(job.idempotency_key, candidate.operation, candidate.kind, content_hash)
    statement = (
        insert(MemoryProposal)
        .values(
            user_id=job.user_id,
            conversation_id=job.conversation_id,
            source_message_id=job.source_message_id,
            assistant_message_id=job.assistant_message_id,
            operation=candidate.operation,
            kind=candidate.kind,
            content=normalized,
            content_hash=content_hash,
            confidence=candidate.confidence,
            evidence_reason=candidate.evidence_reason,
            source_hash=job.source_hash,
            observed_at=job.source_created_at,
            extractor_provider=provider,
            extractor_model=model,
            prompt_version=job.prompt_version,
            schema_version=job.schema_version,
            processor_version=job.processor_version,
            idempotency_key=key,
            consent_epoch=job.consent_epoch,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
    )
    result = await session.execute(statement)
    return int(bool(result.rowcount))


async def _fail(
    db: Database,
    settings: Settings,
    job_id: uuid.UUID,
    owner: str,
    now: datetime,
    error: Exception,
    retryable: bool,
) -> str:
    async with db.sessionmaker() as session:
        job = await session.get(MemoryExtractionJob, job_id, with_for_update=True)
        if job is None or job.status != "running" or job.lease_owner != owner:
            return "invalidated"
        dead = not retryable or job.attempt_count >= settings.memory_worker_max_attempts
        status = "dead" if dead else "retryable"
        delay = min(
            settings.memory_worker_backoff_max_s,
            settings.memory_worker_backoff_base_s * (2 ** max(0, job.attempt_count - 1)),
        )
        await _finish(
            session,
            job,
            status,
            now,
            error_code="invalid_output"
            if isinstance(error, (ExtractionOutputError, ValidationFailed))
            else "provider_error",
            error_type=type(error).__name__,
            next_attempt_at=now + timedelta(seconds=delay),
        )
        await session.commit()
        return status


async def _finish(
    session,
    job: MemoryExtractionJob,
    status: str,
    now: datetime,
    *,
    proposal_count: int = 0,
    error_code: str | None = None,
    error_type: str | None = None,
    next_attempt_at: datetime | None = None,
) -> None:
    await session.execute(
        update(MemoryExtractionJob)
        .where(MemoryExtractionJob.id == job.id)
        .values(
            status=status,
            proposal_count=proposal_count,
            lease_owner=None,
            lease_expires_at=None,
            error_code=error_code,
            error_type=error_type,
            next_attempt_at=next_attempt_at or job.next_attempt_at,
            completed_at=now if status in ("completed", "dead", "invalidated") else None,
            updated_at=func.clock_timestamp(),
        )
    )
