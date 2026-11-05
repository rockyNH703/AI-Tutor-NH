"""PostgreSQL state-transition tests for the Knowledge Library worker."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Document, DocumentFile, DocumentRevision, KnowledgeSource, User
from app.knowledge.worker import (
    LEASE,
    MAX_ATTEMPTS,
    RETRY_DELAYS,
    RevisionClaim,
    claim_one,
    fail,
    finish,
    recover_stale,
)


async def _revision(
    app, *, status: str = "queued", attempts: int = 0, claimed_at: datetime | None = None
) -> DocumentRevision:
    digest = "a" * 64
    async with app.state.db.sessionmaker() as db, db.begin():
        user = User(email=f"knowledge-worker-{uuid.uuid4().hex}@example.com", email_verified=True)
        db.add(user)
        await db.flush()
        source = KnowledgeSource(
            name=f"worker-{uuid.uuid4().hex}",
            owner_user_id=user.id,
            embed_egress_approved_at=datetime.now(UTC),
            embed_egress_basis="focused regression test",
            embed_egress_approved_by_user_id=user.id,
        )
        db.add(source)
        await db.flush()
        document = Document(
            source_id=source.id,
            uri=f"knowledge://worker/{uuid.uuid4().hex}",
            title="Worker test document",
            content_hash=digest,
            ingest_signature=digest,
        )
        db.add(document)
        await db.flush()
        file = DocumentFile(
            source_id=source.id,
            filename="worker.pdf",
            size_bytes=1,
            received_bytes=1,
            sha256=digest,
            status="ready",
            created_by_user_id=user.id,
        )
        db.add(file)
        await db.flush()
        revision = DocumentRevision(
            document_id=document.id,
            source_id=source.id,
            file_id=file.id,
            revision_number=1,
            status=status,
            processing_signature=digest,
            parser_name="test",
            parser_version="1",
            parser_config_hash=digest,
            chunking_config_hash=digest,
            embedding_model="test",
            embedding_dimensions=1536,
            attempts=attempts,
            claimed_at=claimed_at,
            created_by_user_id=user.id,
        )
        db.add(revision)
        await db.flush()
        return revision


async def _stored(app, revision_id):
    async with app.state.db.sessionmaker() as db:
        return await db.scalar(select(DocumentRevision).where(DocumentRevision.id == revision_id))


async def test_claim_one_locks_and_marks_a_queued_revision_extracting(app):
    revision = await _revision(app)
    now = datetime.now(UTC)

    claim = await claim_one(app.state.db, now)

    assert claim == RevisionClaim(revision.id, 1)
    stored = await _stored(app, revision.id)
    assert stored is not None
    assert (stored.status, stored.attempts, stored.claimed_at, stored.next_attempt_at) == ("extracting", 1, now, None)


async def test_recover_stale_retries_or_fails_by_attempt_limit(app):
    now = datetime.now(UTC)
    stale_at = now - LEASE - timedelta(seconds=1)
    retryable = await _revision(
        app,
        status="extracting",
        attempts=MAX_ATTEMPTS - 1,
        claimed_at=stale_at,
    )
    exhausted = await _revision(
        app,
        status="extracting",
        attempts=MAX_ATTEMPTS,
        claimed_at=stale_at,
    )

    assert await recover_stale(app.state.db, now) == 2

    recovered, failed = await _stored(app, retryable.id), await _stored(app, exhausted.id)
    assert recovered is not None and failed is not None
    assert (recovered.status, recovered.error_code, recovered.claimed_at, recovered.next_attempt_at) == (
        "retryable",
        "lease_expired",
        None,
        now,
    )
    assert (failed.status, failed.error_code, failed.claimed_at, failed.next_attempt_at) == (
        "failed",
        "lease_expired",
        None,
        None,
    )


async def test_fail_schedules_retry_then_records_terminal_failure(app):
    now = datetime.now(UTC)
    revision = await _revision(app, status="extracting", attempts=1, claimed_at=now)

    assert await fail(app.state.db, RevisionClaim(revision.id, 1), "temporary", now, retryable=True)
    retrying = await _stored(app, revision.id)
    assert retrying is not None
    assert (retrying.status, retrying.error_code, retrying.claimed_at, retrying.next_attempt_at) == (
        "retryable",
        "temporary",
        None,
        now + RETRY_DELAYS[0],
    )

    async with app.state.db.sessionmaker() as db, db.begin():
        stored = await db.scalar(select(DocumentRevision).where(DocumentRevision.id == revision.id).with_for_update())
        assert stored is not None
        stored.status = "extracting"
        stored.attempts = MAX_ATTEMPTS
        stored.claimed_at = now

    assert await fail(app.state.db, RevisionClaim(revision.id, MAX_ATTEMPTS), "exhausted", now, retryable=True)
    terminal = await _stored(app, revision.id)
    assert terminal is not None
    assert (terminal.status, terminal.error_code, terminal.claimed_at, terminal.next_attempt_at) == (
        "failed",
        "exhausted",
        None,
        None,
    )


async def test_finish_accepts_embedding_state_and_rejects_publication(app):
    now = datetime.now(UTC)
    revision = await _revision(app, status="embedding", attempts=2, claimed_at=now)

    assert not await finish(app.state.db, RevisionClaim(revision.id, 1), "ready_to_publish", now)
    fenced = await _stored(app, revision.id)
    assert fenced is not None
    assert (fenced.status, fenced.attempts, fenced.claimed_at) == ("embedding", 2, now)

    assert await finish(app.state.db, RevisionClaim(revision.id, 2), "ready_to_publish", now)
    completed = await _stored(app, revision.id)
    assert completed is not None
    assert (completed.status, completed.claimed_at, completed.next_attempt_at) == ("ready_to_publish", None, None)

    with pytest.raises(ValueError, match="invalid success status"):
        await finish(app.state.db, RevisionClaim(revision.id, 2), "published", now)
