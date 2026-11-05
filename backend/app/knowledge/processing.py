"""Processing one Knowledge Library revision with the existing sandbox and embeddings provider."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import random
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.embeddings import Embeddings
from sqlalchemy import func, select, update

from app.db.models import Document, DocumentFile, DocumentFileBlob, DocumentRevision, KnowledgeSource
from app.db.session import Database
from app.files.processing import BACKEND_DIR, EMBED_RETRY_DELAYS_S, SANDBOX_PROPERTIES, _systemctl
from app.knowledge import concepts, passages
from app.knowledge.protocol import MAX_STDOUT_BYTES, ExtractedDocument, parse_ndjson
from app.knowledge.publication import CHUNK_OVERLAP_CHARS, CHUNKING, MAX_CHUNK_CHARS, build_chunks
from app.knowledge.worker import RevisionClaim
from app.logging import get_logger
from app.rag.embeddings import embedding_model_id
from app.rag.errors import EmbeddingError
from app.settings import Settings

EXTRACT_TIMEOUT_S = 3_600
MAX_STDERR = 4_096
UNIT_PREFIX = "netsecai-knowledge-extract"
KNOWLEDGE_SLICE = "netsecai-knowledge.slice"  # deploy-netsec/systemd: low weights, a hard memory ceiling
HEARTBEAT_S = 60  # well inside the worker's 20-minute lease: a long book is never taken for a dead claim
PARSER_NAME, PARSER_VERSION = "netsec-knowledge-pdfium-tesseract", "v4"
# The File Library's sandbox, with what a book needs: a runtime for OCR (about 1-5 s per scanned page per CPU),
# and CPU and memory for its OCR processes (each ~150 MB). The later -p wins.
KNOWLEDGE_SANDBOX_OVERRIDES = (f"RuntimeMaxSec={EXTRACT_TIMEOUT_S}",)


def sandbox_overrides(ocr_workers: int) -> tuple[str, ...]:
    workers = max(1, ocr_workers)
    return (*KNOWLEDGE_SANDBOX_OVERRIDES, f"CPUQuota={100 * workers}%", f"MemoryMax={384 + 256 * workers}M")


# A revision still being worked on: a new one for the same file waits until it ends.
ACTIVE_STATUSES = ("queued", "extracting", "chunking", "embedding", "verifying", "retryable")

log = get_logger("app.knowledge")


async def _blobs(db: Database, file_id: uuid.UUID) -> AsyncIterator[bytes]:
    async with db.sessionmaker() as session:
        ordinal = 0
        while True:
            data = await session.scalar(
                select(DocumentFileBlob.data).where(
                    DocumentFileBlob.file_id == file_id,
                    DocumentFileBlob.ordinal == ordinal,
                )
            )
            if data is None:
                break
            yield bytes(data)
            ordinal += 1


def _extractor_command(
    runner: str, unit: str, ocr_workers: int = 1, slice_name: str | None = None
) -> tuple[list[str], dict[str, str] | None]:
    module = [sys.executable, "-m", "app.knowledge.extract"]
    if runner == "systemd":
        properties = [arg for prop in (*SANDBOX_PROPERTIES, *sandbox_overrides(ocr_workers)) for arg in ("-p", prop)]
        placement = [f"--slice={slice_name}"] if slice_name else []
        return [
            "systemd-run",
            "--quiet",
            "--pipe",
            "--wait",
            "--collect",
            f"--unit={unit}",
            *placement,
            f"--setenv=KNOWLEDGE_OCR_WORKERS={max(1, ocr_workers)}",
            *properties,
            *module,
        ], None
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "OMP_THREAD_LIMIT": "1",
        "KNOWLEDGE_OCR_WORKERS": str(max(1, ocr_workers)),
    }
    if os.name == "nt":
        env.update({key: os.environ[key] for key in ("SYSTEMROOT", "TEMP", "TMP") if key in os.environ})
    return module, env


async def _read_limited(stream: asyncio.StreamReader, limit: int, code: str) -> bytes:
    output = bytearray()
    while True:
        chunk = await stream.read(min(64 * 1024, limit + 1 - len(output)))
        if not chunk:
            return bytes(output)
        output.extend(chunk)
        if len(output) > limit:
            raise RuntimeError(code)


async def extract_revision(
    db: Database,
    runner: str,
    claim: RevisionClaim,
    file_id: uuid.UUID,
    expected_sha256: str,
    ocr_workers: int = 1,
) -> ExtractedDocument:
    unit = f"{UNIT_PREFIX}-{claim.id}-{claim.attempt}.service"
    argv, env = _extractor_command(runner, unit, ocr_workers, KNOWLEDGE_SLICE)
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BACKEND_DIR),
        env=env,
        start_new_session=runner != "systemd",
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None

    async def feed() -> None:
        async for chunk in _blobs(db, file_id):
            process.stdin.write(chunk)
            await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()

    feeder = asyncio.create_task(feed())
    try:
        async with asyncio.timeout(EXTRACT_TIMEOUT_S):
            stdout, _, _, code = await asyncio.gather(
                _read_limited(process.stdout, MAX_STDOUT_BYTES, "knowledge_extractor_stdout_too_large"),
                _read_limited(process.stderr, MAX_STDERR, "knowledge_extractor_stderr_too_large"),
                feeder,
                process.wait(),
            )
    except BaseException:
        feeder.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await feeder
        if runner == "systemd":
            await _systemctl("kill", "-s", "KILL", unit)
        else:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        raise
    if code != 0:
        raise RuntimeError("knowledge_extractor_failed")
    extracted = parse_ndjson(io.BytesIO(stdout))
    if extracted.document_sha256 != expected_sha256:
        raise RuntimeError("knowledge_extractor_digest_mismatch")
    return extracted


async def _embed_batch(
    db: Database, claim: RevisionClaim, embeddings: Embeddings, texts: list[str]
) -> list[list[float]]:
    """The File Library's retry contract: a provider hiccup is retried with jitter, without extracting again.

    The claim is checked before every attempt, so text never leaves for a revision this worker no longer holds.
    """
    for attempt, delay in enumerate((*EMBED_RETRY_DELAYS_S, None)):
        async with db.sessionmaker() as session:
            held = (
                await session.execute(
                    select(DocumentRevision.id, KnowledgeSource.embed_egress_approved_at)
                    .join(KnowledgeSource, KnowledgeSource.id == DocumentRevision.source_id)
                    .where(
                        DocumentRevision.id == claim.id,
                        DocumentRevision.attempts == claim.attempt,
                        DocumentRevision.status == "embedding",
                    )
                )
            ).first()
        if held is None:
            raise RuntimeError("knowledge_revision_claim_lost")
        if held.embed_egress_approved_at is None:  # withdrawn meanwhile: no more of this book leaves
            raise RuntimeError("knowledge_embed_egress_not_approved")
        try:
            return await embeddings.aembed_documents(texts)
        except EmbeddingError as error:
            if delay is None or error.code not in ("embedding_failed", "embedding_timeout"):
                raise
            log.warning("knowledge embedding retry", extra={"attempt": attempt + 1, "reason": error.code})
            await asyncio.sleep(delay + random.random())  # noqa: S311 - jitter, not security
    raise EmbeddingError("embedding_failed", "retries exhausted")


async def _heartbeat(db: Database, claim: RevisionClaim) -> None:
    """Keep this worker's lease fresh while it holds the claim (fenced by the attempt): recover_stale only ever
    takes a revision whose worker really stopped, however long its book takes."""
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        async with db.sessionmaker() as session, session.begin():
            await session.execute(
                update(DocumentRevision)
                .where(
                    DocumentRevision.id == claim.id,
                    DocumentRevision.attempts == claim.attempt,
                    DocumentRevision.status.in_(("extracting", "embedding")),
                )
                .values(claimed_at=datetime.now(UTC))
            )


async def process_revision(
    db: Database, settings: Settings, embeddings: Embeddings, claim: RevisionClaim, ocr_workers: int = 1
) -> str:
    beat = asyncio.create_task(_heartbeat(db, claim))
    try:
        return await _process_revision(db, settings, embeddings, claim, ocr_workers)
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await beat


async def _process_revision(
    db: Database,
    settings: Settings,
    embeddings: Embeddings,
    claim: RevisionClaim,
    ocr_workers: int = 1,
) -> str:
    model = embedding_model_id(embeddings)
    async with db.sessionmaker() as session:
        revision = await session.get(DocumentRevision, claim.id)
        if revision is None or revision.attempts != claim.attempt or revision.status != "extracting":
            raise RuntimeError("knowledge_revision_claim_lost")
        file = await session.get(DocumentFile, revision.file_id)
        if file is None or file.status != "ready":
            raise RuntimeError("knowledge_file_not_ready")
        revision_id, file_id, file_sha256 = revision.id, file.id, file.sha256

    extracted = await extract_revision(db, settings.files_runner, claim, file_id, file_sha256, ocr_workers)
    chunks = build_chunks(extracted)
    if not chunks:
        raise RuntimeError("knowledge_no_chunks")

    async with db.sessionmaker() as session, session.begin():
        revision = await session.scalar(
            select(DocumentRevision)
            .where(
                DocumentRevision.id == revision_id,
                DocumentRevision.attempts == claim.attempt,
                DocumentRevision.status == "extracting",
            )
            .with_for_update()
        )
        if revision is None:
            raise RuntimeError("knowledge_revision_claim_lost")
        revision.status = "embedding"
        revision.output_digest = extracted.content_sha256
        distinct = await passages.stage_passages(session, revision, chunks)
        await concepts.index_revision(session, revision.id)  # local patterns: no text leaves for this
        await passages.fingerprint_file(session, file_id, passages.pages_of(extracted.elements))
        document = await session.get(Document, revision.document_id)
        await passages.record_edition(
            session, revision, document.title if document else "", extracted.document_metadata
        )
        missing = await passages.missing_embeddings(session, revision.id, model)
        revision.quality_report = {
            "pageCount": extracted.page_count,
            "elements": len(extracted.elements),
            "chunks": len(chunks),
            "passages": distinct,
            "reusedVectors": distinct - len(missing),
            "warnings": list(extracted.warnings),
        }

    # Only text the library has no vector for (this model) leaves; each batch is stored as it comes back, so a
    # retry after a provider failure resumes where it stopped instead of embedding the book again.
    for start in range(0, len(missing), settings.embedding_batch_size):
        batch = missing[start : start + settings.embedding_batch_size]
        vectors = await _embed_batch(db, claim, embeddings, [content for _, content in batch])
        if len(vectors) != len(batch):
            raise RuntimeError("knowledge_embedding_count_mismatch")
        async with db.sessionmaker() as session, session.begin():
            await passages.store_embeddings(
                session, [(pid, vec) for (pid, _), vec in zip(batch, vectors, strict=True)], model
            )

    async with db.sessionmaker() as session, session.begin():
        revision = await session.scalar(
            select(DocumentRevision)
            .where(
                DocumentRevision.id == revision_id,
                DocumentRevision.attempts == claim.attempt,
                DocumentRevision.status == "embedding",
                DocumentRevision.file_id == file_id,
            )
            .with_for_update()
        )
        if revision is None:
            raise RuntimeError("knowledge_revision_claim_lost")
        if await passages.missing_embeddings(session, revision.id, model):
            raise RuntimeError("knowledge_staging_changed")
        warnings = list(extracted.warnings)
        file = await session.get(DocumentFile, revision.file_id)
        if file is not None:
            file.page_count = extracted.page_count
            # Every other copy, edition or volume in the library, whatever its source, course or batch.
            await passages.classify_file(session, file)
            if await passages.blocking_reviews(session, file.id):
                warnings.append("duplicate_review_pending")
        revision.quality_report = {**revision.quality_report, "warnings": warnings}
        document = await session.get(Document, revision.document_id)
        if document is not None:
            document.content_hash = extracted.content_sha256
            document.ingest_signature = revision.processing_signature
            document.updated_at = datetime.now(UTC)
        revision.embedding_model = model
        revision.embedding_dimensions = settings.embedding_dimensions
        revision.updated_at = datetime.now(UTC)
    return "needs_review" if warnings else "ready_to_publish"


def processing_signature(file_sha256: str, settings: Settings, model: str) -> str:
    payload = {
        "file": file_sha256,
        "parser": f"{PARSER_NAME}-{PARSER_VERSION}",
        "chunk": [MAX_CHUNK_CHARS, CHUNK_OVERLAP_CHARS, CHUNKING],
        "embedding": [model, settings.embedding_dimensions],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def queue_ready_files(db: Database, settings: Settings, embeddings: Embeddings) -> int:
    model = embedding_model_id(embeddings)
    queued = 0
    async with db.sessionmaker() as session, session.begin():
        files = list(
            await session.scalars(
                select(DocumentFile)
                .join(KnowledgeSource, KnowledgeSource.id == DocumentFile.source_id)
                .where(
                    DocumentFile.status == "ready",
                    # Book text goes to the embedding provider only with the owner's recorded approval (CLI egress).
                    KnowledgeSource.embed_egress_approved_at.is_not(None),
                    KnowledgeSource.rights_revoked_at.is_(None),
                )
                .order_by(DocumentFile.created_at)
                .with_for_update(of=DocumentFile, skip_locked=True)
            )
        )
        for file in files:
            # One revision per file and processing signature: a new embedding model (or chunking) queues the
            # file again, so its published chunks are not stranded under a model the retriever no longer reads.
            signature = processing_signature(file.sha256, settings, model)
            existing = await session.scalar(
                select(DocumentRevision.id).where(
                    DocumentRevision.file_id == file.id,
                    (DocumentRevision.processing_signature == signature) | DocumentRevision.status.in_(ACTIVE_STATUSES),
                )
            )
            if existing is not None:
                continue
            document = await session.scalar(
                select(Document).where(
                    Document.source_id == file.source_id, Document.uri == f"knowledge-file:{file.id}"
                )
            )
            now = datetime.now(UTC)
            if document is None:
                document = Document(
                    source_id=file.source_id,
                    uri=f"knowledge-file:{file.id}",
                    title=Path(file.filename).stem[:300],
                    content_hash=file.sha256,
                    ingest_signature=file.sha256,
                    metadata_={"filename": file.filename},
                    created_at=now,
                    updated_at=now,
                )
                session.add(document)
                await session.flush()
            revision_number = (
                int(
                    await session.scalar(
                        select(func.coalesce(func.max(DocumentRevision.revision_number), 0)).where(
                            DocumentRevision.document_id == document.id
                        )
                    )
                    or 0
                )
                + 1
            )
            session.add(
                DocumentRevision(
                    document_id=document.id,
                    source_id=file.source_id,
                    file_id=file.id,
                    revision_number=revision_number,
                    status="queued",
                    processing_signature=signature,
                    parser_name=PARSER_NAME,
                    parser_version=PARSER_VERSION,
                    parser_config_hash=hashlib.sha256(f"{PARSER_NAME}-{PARSER_VERSION}".encode()).hexdigest(),
                    chunking_config_hash=hashlib.sha256(
                        f"{MAX_CHUNK_CHARS}:{CHUNK_OVERLAP_CHARS}:{CHUNKING}".encode()
                    ).hexdigest(),
                    embedding_model=model,
                    embedding_dimensions=settings.embedding_dimensions,
                    created_by_user_id=file.created_by_user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            queued += 1
    return queued
