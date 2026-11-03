"""Processing one Knowledge Library revision with the existing sandbox and embeddings provider."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.embeddings import Embeddings
from sqlalchemy import func, select

from app.db.models import Document, DocumentFile, DocumentFileBlob, DocumentRevision, DocumentRevisionChunk
from app.db.session import Database
from app.files.processing import BACKEND_DIR, SANDBOX_PROPERTIES, _systemctl
from app.knowledge.protocol import MAX_STDOUT_BYTES, ExtractedDocument, parse_ndjson
from app.knowledge.publication import build_chunks, stage_chunks
from app.knowledge.worker import RevisionClaim
from app.rag.embeddings import embedding_model_id
from app.settings import Settings

EXTRACT_TIMEOUT_S = 1_200
MAX_STDERR = 4_096
UNIT_PREFIX = "netsecai-knowledge-extract"


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


def _extractor_command(runner: str, unit: str) -> tuple[list[str], dict[str, str] | None]:
    module = [sys.executable, "-m", "app.knowledge.extract"]
    if runner == "systemd":
        properties = [arg for prop in SANDBOX_PROPERTIES for arg in ("-p", prop)]
        return ["systemd-run", "--quiet", "--pipe", "--wait", "--collect", f"--unit={unit}", *properties, *module], None
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "OMP_THREAD_LIMIT": "1"}
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
) -> ExtractedDocument:
    unit = f"{UNIT_PREFIX}-{claim.id}-{claim.attempt}.service"
    argv, env = _extractor_command(runner, unit)
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


async def process_revision(
    db: Database,
    settings: Settings,
    embeddings: Embeddings,
    claim: RevisionClaim,
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

    extracted = await extract_revision(db, settings.files_runner, claim, file_id, file_sha256)
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
        revision.quality_report = {
            "pageCount": extracted.page_count,
            "elements": len(extracted.elements),
            "chunks": len(chunks),
            "warnings": list(extracted.warnings),
        }
        await stage_chunks(session, revision, chunks)

    vectors: list[list[float]] = []
    for start in range(0, len(chunks), settings.embedding_batch_size):
        batch = chunks[start : start + settings.embedding_batch_size]
        vectors.extend(await embeddings.aembed_documents([chunk.content for chunk in batch]))
    if len(vectors) != len(chunks):
        raise RuntimeError("knowledge_embedding_count_mismatch")

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
        staged = list(
            await session.scalars(
                select(DocumentRevisionChunk)
                .where(DocumentRevisionChunk.revision_id == revision.id)
                .order_by(DocumentRevisionChunk.ordinal)
            )
        )
        if len(staged) != len(vectors):
            raise RuntimeError("knowledge_staging_changed")
        for chunk, vector in zip(staged, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_model = model
        file = await session.get(DocumentFile, revision.file_id)
        if file is not None:
            file.page_count = extracted.page_count
        document = await session.get(Document, revision.document_id)
        if document is not None:
            document.content_hash = extracted.content_sha256
            document.ingest_signature = revision.processing_signature
            document.updated_at = datetime.now(UTC)
        revision.embedding_model = model
        revision.embedding_dimensions = settings.embedding_dimensions
        revision.updated_at = datetime.now(UTC)
    return "needs_review" if extracted.warnings else "ready_to_publish"


def processing_signature(file_sha256: str, settings: Settings, model: str) -> str:
    payload = {
        "file": file_sha256,
        "parser": "netsec-knowledge-pdfium-tesseract-v2",
        "chunk": [settings.rag_chunk_size, settings.rag_chunk_overlap],
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
                .where(DocumentFile.status == "ready")
                .order_by(DocumentFile.created_at)
                .with_for_update(skip_locked=True)
            )
        )
        for file in files:
            existing = await session.scalar(select(DocumentRevision.id).where(DocumentRevision.file_id == file.id))
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
            signature = processing_signature(file.sha256, settings, model)
            session.add(
                DocumentRevision(
                    document_id=document.id,
                    source_id=file.source_id,
                    file_id=file.id,
                    revision_number=revision_number,
                    status="queued",
                    processing_signature=signature,
                    parser_name="netsec-knowledge-pdfium-tesseract",
                    parser_version="v2",
                    parser_config_hash=hashlib.sha256(b"netsec-knowledge-pdfium-tesseract-v2").hexdigest(),
                    chunking_config_hash=hashlib.sha256(
                        f"{settings.rag_chunk_size}:{settings.rag_chunk_overlap}".encode()
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
