"""One bounded run of the durable Knowledge Library worker."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update

from app.db.models import DocumentRevision
from app.db.session import Database
from app.logging import get_logger

log = get_logger("app.knowledge.worker")
LOCK_KEY = 7_216_094_003
LEASE = timedelta(minutes=20)
MAX_ATTEMPTS = 3
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
CLAIMABLE = ("queued", "retryable")


@dataclass(frozen=True)
class RevisionClaim:
    id: uuid.UUID
    attempt: int


Processor = Callable[[RevisionClaim], Awaitable[str]]


async def recover_stale(db: Database, now: datetime) -> int:
    threshold = now - LEASE
    async with db.sessionmaker() as session, session.begin():
        failed = await session.execute(
            update(DocumentRevision)
            .where(
                DocumentRevision.status.notin_(("published", "retired", "failed", "cancelled")),
                DocumentRevision.claimed_at.is_not(None),
                DocumentRevision.claimed_at < threshold,
                DocumentRevision.attempts >= MAX_ATTEMPTS,
            )
            .values(status="failed", error_code="lease_expired", claimed_at=None, updated_at=now)
        )
        retried = await session.execute(
            update(DocumentRevision)
            .where(
                DocumentRevision.status.notin_(("published", "retired", "failed", "cancelled")),
                DocumentRevision.claimed_at.is_not(None),
                DocumentRevision.claimed_at < threshold,
                DocumentRevision.attempts < MAX_ATTEMPTS,
            )
            .values(
                status="retryable", error_code="lease_expired", claimed_at=None, next_attempt_at=now, updated_at=now
            )
        )
    return int(failed.rowcount or 0) + int(retried.rowcount or 0)


async def claim_one(db: Database, now: datetime) -> RevisionClaim | None:
    async with db.sessionmaker() as session, session.begin():
        revision = await session.scalar(
            select(DocumentRevision)
            .where(
                DocumentRevision.status.in_(CLAIMABLE),
                (DocumentRevision.next_attempt_at.is_(None) | (DocumentRevision.next_attempt_at <= now)),
                DocumentRevision.claimed_at.is_(None),
            )
            .order_by(DocumentRevision.created_at, DocumentRevision.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if revision is None:
            return None
        revision.attempts += 1
        revision.status = "extracting"
        revision.claimed_at = now
        revision.next_attempt_at = None
        revision.error_code = None
        revision.updated_at = now
        return RevisionClaim(revision.id, revision.attempts)


async def finish(db: Database, claim: RevisionClaim, status: str, now: datetime) -> bool:
    if status not in {"needs_review", "ready_to_publish"}:
        raise ValueError("invalid success status")
    async with db.sessionmaker() as session, session.begin():
        changed = await session.execute(
            update(DocumentRevision)
            .where(
                DocumentRevision.id == claim.id,
                DocumentRevision.attempts == claim.attempt,
                DocumentRevision.status.in_(("extracting", "embedding")),
            )
            .values(status=status, claimed_at=None, next_attempt_at=None, updated_at=now)
        )
    return bool(changed.rowcount)


async def fail(db: Database, claim: RevisionClaim, code: str, now: datetime, *, retryable: bool) -> bool:
    terminal = not retryable or claim.attempt >= MAX_ATTEMPTS
    delay = RETRY_DELAYS[min(claim.attempt - 1, len(RETRY_DELAYS) - 1)]
    async with db.sessionmaker() as session, session.begin():
        changed = await session.execute(
            update(DocumentRevision)
            .where(
                DocumentRevision.id == claim.id,
                DocumentRevision.attempts == claim.attempt,
                DocumentRevision.status.in_(("extracting", "embedding")),
            )
            .values(
                status="failed" if terminal else "retryable",
                claimed_at=None,
                next_attempt_at=None if terminal else now + delay,
                error_code=code[:80],
                updated_at=now,
            )
        )
    return bool(changed.rowcount)


async def run_once(db: Database, processor: Processor, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    report: dict[str, object] = {}
    async with db.engine.connect() as lock:
        if not await lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}):
            return {"skipped": "another knowledge worker holds the lock"}
        try:
            report["recovered"] = await recover_stale(db, now)
            claim = await claim_one(db, now)
            if claim is None:
                report["claimed"] = 0
                return report
            report["claimed"] = 1
            try:
                status = await processor(claim)
                report["completed"] = await finish(db, claim, status, datetime.now(UTC))
            except asyncio.CancelledError:
                await fail(db, claim, "worker_cancelled", datetime.now(UTC), retryable=True)
                raise
            except Exception as error:  # noqa: BLE001 - durable state records the bounded failure
                log.exception("knowledge revision failed", extra={"error_type": type(error).__name__})
                report["failed"] = await fail(
                    db,
                    claim,
                    type(error).__name__,
                    datetime.now(UTC),
                    retryable=True,
                )
            return report
        finally:
            await lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            await lock.commit()


async def pending_count(db: Database) -> int:
    async with db.sessionmaker() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(DocumentRevision).where(DocumentRevision.status.in_(CLAIMABLE))
            )
            or 0
        )
