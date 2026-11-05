"""Knowledge Library lifecycle: sandbox runtime, embedding retries, re-queue, publication CLI, capacity, PDF access."""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from langchain_core.documents import Document as LCDocument
from sqlalchemy import func, select

from app.db.models import (
    DocumentRevision,
    KnowledgeAdminGrant,
    KnowledgePassageSource,
    KnowledgeSource,
    User,
)
from app.files.processing import SANDBOX_PROPERTIES
from app.knowledge import passages, processing
from app.knowledge.processing import _embed_batch, _extractor_command, processing_signature, queue_ready_files
from app.knowledge.publication import MAX_CHUNK_CHARS, StagedChunk
from app.knowledge.worker import RevisionClaim
from app.rag.context import _block
from app.rag.embeddings import KeywordHashEmbeddings
from app.rag.errors import EmbeddingError
from scripts import knowledge_admin
from tests.test_knowledge_import import _create_batch, _create_file, _grant_admin, _source, _upload, _user
from tests.test_knowledge_worker import _revision, _stored

from .conftest import register


def test_the_extractor_gets_a_book_sized_runtime_after_the_shared_sandbox():
    argv, _ = _extractor_command("systemd", "netsecai-knowledge-extract-x-1.service")
    properties = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-p"]
    assert properties[: len(SANDBOX_PROPERTIES)] == list(SANDBOX_PROPERTIES)
    # systemd applies the last value: the 20-minute worker timeout, not the File Library's 5 minutes.
    runtime = [prop for prop in properties if prop.startswith("RuntimeMaxSec=")]
    assert runtime[-1] == f"RuntimeMaxSec={processing.EXTRACT_TIMEOUT_S}"


def test_five_full_passages_fit_the_context_budget(settings):
    meta = {"title": "CCNA 200-301 Official Cert Guide, Volume 1", "source": "kb", "attributes": {"vendor": "cisco"}}
    blocks = [_block(n, [LCDocument(page_content="x" * MAX_CHUNK_CHARS, metadata=meta)]) for n in range(1, 6)]
    assert sum(len(block) for block in blocks) <= settings.rag_max_context_chars


def test_the_signature_records_the_chunker_in_use_and_the_model(settings):
    one = processing_signature("a" * 64, settings, "model-a")
    assert one != processing_signature("a" * 64, settings, "model-b")
    assert one == processing_signature("a" * 64, settings.model_copy(update={"rag_chunk_size": 123}), "model-a")


async def _revisions_of(app, file_id) -> list[DocumentRevision]:
    async with app.state.db.sessionmaker() as db:
        return list(await db.scalars(select(DocumentRevision).where(DocumentRevision.file_id == file_id)))


async def test_a_new_embedding_model_queues_a_published_file_again_once(app, settings):
    published = await _revision(app, status="published")
    model_b = KeywordHashEmbeddings(settings.embedding_dimensions, model="model-b")

    assert await queue_ready_files(app.state.db, settings, model_b) == 1
    assert await queue_ready_files(app.state.db, settings, model_b) == 0

    revisions = await _revisions_of(app, published.file_id)
    assert sorted(r.status for r in revisions) == ["published", "queued"]
    assert {r.embedding_model for r in revisions if r.status == "queued"} == {"model-b"}


async def test_a_revision_in_progress_is_not_queued_twice(app, settings):
    active = await _revision(app, status="extracting", attempts=1)
    model_b = KeywordHashEmbeddings(settings.embedding_dimensions, model="model-b")

    assert await queue_ready_files(app.state.db, settings, model_b) == 0
    assert len(await _revisions_of(app, active.file_id)) == 1


class _Flaky:
    model = "test"

    def __init__(self, failures: list[str]):
        self.failures = failures
        self.calls = 0

    async def aembed_documents(self, texts):
        self.calls += 1
        if self.failures:
            raise EmbeddingError(self.failures.pop(0))
        return [[0.0] * 3 for _ in texts]


@pytest.fixture
def no_retry_wait(monkeypatch):
    monkeypatch.setattr(processing, "EMBED_RETRY_DELAYS_S", (0, 0, 0))
    monkeypatch.setattr(processing.random, "random", lambda: 0.0)


async def test_one_provider_hiccup_is_retried_without_extracting_again(app, no_retry_wait):
    revision = await _revision(app, status="embedding", attempts=1)
    embeddings = _Flaky(["embedding_failed", "embedding_timeout"])

    vectors = await _embed_batch(app.state.db, RevisionClaim(revision.id, 1), embeddings, ["a", "b"])

    assert len(vectors) == 2
    assert embeddings.calls == 3


async def test_a_permanent_embedding_error_is_not_retried(app, no_retry_wait):
    revision = await _revision(app, status="embedding", attempts=1)
    embeddings = _Flaky(["embedding_unauthorized"])

    with pytest.raises(EmbeddingError):
        await _embed_batch(app.state.db, RevisionClaim(revision.id, 1), embeddings, ["a"])
    assert embeddings.calls == 1


async def test_no_text_leaves_for_a_revision_whose_claim_was_lost(app, no_retry_wait):
    revision = await _revision(app, status="retryable", attempts=1)
    embeddings = _Flaky([])

    with pytest.raises(RuntimeError, match="claim_lost"):
        await _embed_batch(app.state.db, RevisionClaim(revision.id, 1), embeddings, ["a"])
    assert embeddings.calls == 0


# ------------------------------------------------------------------ operator CLI (scripts/knowledge_admin.py)
async def _admin(app, *, active=True) -> str:
    email = f"knowledge-admin-{uuid.uuid4().hex}@example.com"
    async with app.state.db.sessionmaker() as db, db.begin():
        user = User(email=email, email_verified=True)
        db.add(user)
        await db.flush()
        if active:
            db.add(KnowledgeAdminGrant(user_id=user.id, granted_by_operator="test", reason="focused regression test"))
    return email


async def _owner_admin(app, revision) -> str:
    """The administrator who owns the revision's source (an administrator acts only on their own sources)."""
    async with app.state.db.sessionmaker() as db, db.begin():
        source = await db.get(KnowledgeSource, revision.source_id)
        user = await db.get(User, source.owner_user_id)
        db.add(KnowledgeAdminGrant(user_id=user.id, granted_by_operator="test", reason="focused regression test"))
        return user.email


async def test_an_unverified_account_cannot_be_made_a_knowledge_administrator(app):
    email = f"unverified-{uuid.uuid4().hex}@example.com"
    async with app.state.db.sessionmaker() as db, db.begin():
        db.add(User(email=email, email_verified=False))
    args = argparse.Namespace(action="grant", target=email, reason="focused regression test", actor=None)
    async with app.state.db.sessionmaker() as db:
        assert await knowledge_admin._grants(db, args) == 2
    async with app.state.db.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert await db.get(KnowledgeAdminGrant, user.id) is None


async def _cli(app, action, target, actor, reason=None) -> int:
    # Every option the real parser defines (--operator, --pdf, ...), then this call's own.
    defaults = vars(knowledge_admin._parser().parse_args([action]))
    args = argparse.Namespace(**{**defaults, "target": str(target), "actor": actor, "reason": reason})
    run = knowledge_admin._imports if action in knowledge_admin.IMPORT_ACTIONS else knowledge_admin._revision
    async with app.state.db.sessionmaker() as db:
        return await run(db, args)


async def _stage(app, revision, settings):
    chunk = StagedChunk(
        stable_key="b" * 64,
        ordinal=0,
        content="OSPF elects a designated router on broadcast segments.",
        content_sha256="c" * 64,
        section_path=(),
        physical_page_start=1,
        physical_page_end=1,
        printed_page_label=None,
        metadata={"element_type": "paragraph"},
    )
    async with app.state.db.sessionmaker() as db, db.begin():
        await passages.stage_passages(db, revision, [chunk])
        [(passage_id, _)] = await passages.missing_embeddings(db, revision.id, revision.embedding_model)
        await passages.store_embeddings(
            db, [(passage_id, [0.01] * settings.embedding_dimensions)], revision.embedding_model
        )


async def test_an_administrator_publishes_a_ready_revision_into_the_index(app, settings):
    revision = await _revision(app, status="ready_to_publish")
    await _stage(app, revision, settings)
    admin = await _owner_admin(app, revision)

    assert await _cli(app, "publish", revision.id, admin) == 0

    stored = await _stored(app, revision.id)
    assert stored.status == "published" and stored.published_by_user_id is not None
    async with app.state.db.sessionmaker() as db:
        live = await db.scalar(
            select(func.count())
            .select_from(KnowledgePassageSource)
            .where(KnowledgePassageSource.source_id == revision.source_id)
        )
    assert live == 1


async def test_only_an_active_administrator_can_publish(app, settings):
    revision = await _revision(app, status="ready_to_publish")
    await _stage(app, revision, settings)

    assert await _cli(app, "publish", revision.id, await _admin(app, active=False)) == 2
    assert await _cli(app, "publish", revision.id, "nobody@example.com") == 2
    other = await _admin(app)  # an active administrator, of other sources
    assert await _cli(app, "publish", revision.id, other) == 2
    assert await _cli(app, "approve", revision.id, other) == 2
    assert await _cli(app, "reject", revision.id, other, reason="not theirs") == 2
    assert (await _stored(app, revision.id)).status == "ready_to_publish"


async def test_a_revision_that_needs_review_is_approved_before_it_can_be_published(app, settings):
    revision = await _revision(app, status="needs_review")
    await _stage(app, revision, settings)
    admin = await _owner_admin(app, revision)

    assert await _cli(app, "publish", revision.id, admin) == 1
    assert await _cli(app, "approve", revision.id, admin) == 0
    assert (await _stored(app, revision.id)).status == "ready_to_publish"
    assert await _cli(app, "publish", revision.id, admin) == 0


async def test_rejecting_needs_a_reason_and_cancels(app):
    revision = await _revision(app, status="needs_review")
    admin = await _owner_admin(app, revision)

    assert await _cli(app, "reject", revision.id, admin) == 2
    assert await _cli(app, "reject", revision.id, admin, reason="scanned copy is unreadable") == 0
    stored = await _stored(app, revision.id)
    assert (stored.status, stored.error_code) == ("cancelled", "rejected_by_operator")


# ------------------------------------------------------------------ API: capacity and PDF access
async def test_an_import_stops_when_the_database_ceiling_is_reached(client, app, monkeypatch):
    token, _ = await _create_batch(client, app)
    monkeypatch.setattr("app.files.service.DB_CEILING_BYTES", 0)

    created = await _create_file(client, token, b"%PDF-1.7\nfull\n%%EOF\n")

    assert created.status_code == 503
    assert created.json()["error"]["code"] == "files_capacity_reached"


async def test_an_administrator_reads_only_the_books_of_sources_they_can_see(client, app):
    owner = await register(client, app)
    owner_id = (await _user(app, owner["email"])).id
    await _grant_admin(app, owner_id)
    source_id = await _source(app, owner_id)
    created = await client.post("/v1/knowledge/imports", json={"sourceId": str(source_id)}, headers=owner["headers"])
    token = created.json()["token"]
    content = b"%PDF-1.7\nowner only\n%%EOF\n"
    file_id = (await _create_file(client, token, content)).json()["id"]
    assert (await _upload(client, token, file_id, content)).status_code == 200
    finalized = await client.post(
        f"/v1/knowledge/imports/files/{file_id}/finalize", headers={"Authorization": f"Bearer {token}"}
    )
    assert finalized.json()["status"] == "ready"
    other = await register(client, app)
    await _grant_admin(app, (await _user(app, other["email"])).id)

    refused = await client.get(f"/v1/knowledge/files/{file_id}/content", headers=other["headers"])
    allowed = await client.get(f"/v1/knowledge/files/{file_id}/content", headers=owner["headers"])

    assert refused.status_code == 404
    assert allowed.status_code == 200 and allowed.content == content


async def test_the_cli_creates_a_source_and_a_batch_token_the_import_api_accepts(client, app, capsys):
    admin = await _admin(app)
    assert await _cli(app, "source", "CCNA lab books", admin) == 0
    assert await _cli(app, "source", "CCNA lab books", admin) == 1  # names are unique
    source_id = capsys.readouterr().out.split()[2]
    assert await _cli(app, "batch", source_id, await _admin(app)) == 2  # another admin's source

    assert await _cli(app, "batch", source_id, admin) == 0
    token = capsys.readouterr().out.strip().rsplit(" ", 1)[-1]
    created = await _create_file(client, token, b"%PDF-1.7\ncli batch\n%%EOF\n")

    assert created.status_code == 201


def test_the_cli_runs_as_a_script_from_the_backend_directory():
    # The operator runs `python scripts/knowledge_admin.py` (sys.path is scripts/, not backend/).
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "scripts/knowledge_admin.py", "--help"],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-500:]
    assert "publish" in result.stdout
