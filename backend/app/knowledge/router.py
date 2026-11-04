"""Resumable, capability-scoped Knowledge Library import endpoints."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal
from app.db.models import DocumentFile, DocumentFileBlob, KnowledgeImportBatch, KnowledgeSource
from app.db.session import get_session
from app.errors import Conflict, NotFound, PayloadTooLarge, Unauthorized, ValidationFailed
from app.knowledge.access import require_knowledge_admin
from app.knowledge.content import parse_single_range, stream_file_range
from app.schemas import ApiModel, RequestModel

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])

MAX_IMPORT_FILE_BYTES = 50 * 1024 * 1024
MAX_BATCH_FILES = 500
MAX_BATCH_BYTES = 5 * 1024 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
BATCH_TTL = timedelta(hours=12)
SHA256 = r"^[0-9a-f]{64}$"


class KnowledgeAdminStatus(ApiModel):
    enabled: bool


class CreateImportBatch(RequestModel):
    source_id: uuid.UUID
    expected_file_count: int | None = None
    expected_total_bytes: int | None = None


class ImportBatchCreated(ApiModel):
    id: uuid.UUID
    token: str
    expires_at: datetime
    next_offset: int = 0


class CreateImportFile(RequestModel):
    filename: str
    size_bytes: int
    sha256: str


class ImportFileOut(ApiModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    sha256: str
    status: str
    next_offset: int
    duplicate_of_file_id: uuid.UUID | None = None


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def _filename(value: str) -> str:
    name = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 255 or name in {".", ".."} or any(ord(char) < 32 for char in name):
        raise ValidationFailed("The file needs a safe name.", fields=[{"field": "filename", "reason": "invalid"}])
    if not name.lower().endswith(".pdf"):
        raise ValidationFailed("Knowledge imports currently accept PDF files only.", code="unsupported_file_type")
    return name


async def _batch_from_token(db: AsyncSession, authorization: str | None, *, lock: bool = False) -> KnowledgeImportBatch:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized()
    statement = select(KnowledgeImportBatch).where(KnowledgeImportBatch.secret_hash == _hash_secret(token))
    if lock:
        statement = statement.with_for_update()
    batch = await db.scalar(statement)
    now = datetime.now(UTC)
    if batch is None or batch.status != "open" or batch.revoked_at is not None or batch.expires_at <= now:
        raise Unauthorized()
    return batch


async def import_batch(
    authorization: str | None = Header(None), db: AsyncSession = Depends(get_session)
) -> KnowledgeImportBatch:
    """The caller's open batch, locked for this request. A dependency, so a request without a valid token is a
    401 before its body is parsed or validated."""
    return await _batch_from_token(db, authorization, lock=True)


async def import_batch_read(
    authorization: str | None = Header(None), db: AsyncSession = Depends(get_session)
) -> KnowledgeImportBatch:
    return await _batch_from_token(db, authorization)


def _file_out(file: DocumentFile, *, duplicate: uuid.UUID | None = None) -> ImportFileOut:
    return ImportFileOut(
        id=file.id,
        filename=file.filename,
        size_bytes=file.size_bytes,
        sha256=file.sha256,
        status=file.status,
        next_offset=file.received_bytes,
        duplicate_of_file_id=duplicate,
    )


@router.get(
    "/files/{file_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def knowledge_file_content(
    file_id: uuid.UUID,
    request: Request,
    _principal: Principal = Depends(require_knowledge_admin),
    db: AsyncSession = Depends(get_session),
):
    file = await db.get(DocumentFile, file_id)
    if file is None or file.status != "ready":
        raise NotFound()
    source = await db.get(KnowledgeSource, file.source_id)
    if source is None:
        raise NotFound()
    etag = f'"{file.sha256}"'
    byte_range = None
    if_range = request.headers.get("if-range")
    if not if_range or if_range == etag:
        byte_range = parse_single_range(request.headers.get("range"), file.size_bytes)
    start, end = byte_range or (0, file.size_bytes - 1)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, max-age=3600",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(file.filename, safe='')}",
    }
    status = 200
    if byte_range:
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{file.size_bytes}"
    return StreamingResponse(
        stream_file_range(request.app.state.db.sessionmaker, file.id, start, end),
        status_code=status,
        media_type="application/pdf",
        headers=headers,
    )


@router.get("/admin", response_model=KnowledgeAdminStatus)
async def knowledge_admin_status(
    _principal: Principal = Depends(require_knowledge_admin),
) -> KnowledgeAdminStatus:
    return KnowledgeAdminStatus(enabled=True)


@router.post("/imports", response_model=ImportBatchCreated, status_code=201)
async def create_import_batch(
    body: CreateImportBatch,
    principal: Principal = Depends(require_knowledge_admin),
    db: AsyncSession = Depends(get_session),
) -> ImportBatchCreated:
    if body.expected_file_count is not None and not 1 <= body.expected_file_count <= MAX_BATCH_FILES:
        raise ValidationFailed(fields=[{"field": "expectedFileCount", "reason": "out_of_range"}])
    if body.expected_total_bytes is not None and not 1 <= body.expected_total_bytes <= MAX_BATCH_BYTES:
        raise ValidationFailed(fields=[{"field": "expectedTotalBytes", "reason": "out_of_range"}])
    source = await db.get(KnowledgeSource, body.source_id)
    if source is None or source.owner_user_id != principal.user.id:
        raise NotFound()
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + BATCH_TTL
    batch = KnowledgeImportBatch(
        owner_user_id=principal.user.id,
        issued_by_user_id=principal.user.id,
        source_id=source.id,
        secret_hash=_hash_secret(token),
        expected_file_count=body.expected_file_count,
        expected_total_bytes=body.expected_total_bytes,
        expires_at=expires_at,
    )
    db.add(batch)
    await db.commit()
    return ImportBatchCreated(id=batch.id, token=token, expires_at=expires_at)


@router.post("/imports/files", response_model=ImportFileOut, status_code=201)
async def create_import_file(
    body: CreateImportFile,
    response: Response,
    batch: KnowledgeImportBatch = Depends(import_batch),
    db: AsyncSession = Depends(get_session),
) -> ImportFileOut:
    if not 1 <= body.size_bytes <= MAX_IMPORT_FILE_BYTES:
        raise PayloadTooLarge(f"A Knowledge Library PDF can be at most {MAX_IMPORT_FILE_BYTES // 1024 // 1024} MB.")
    if len(body.sha256) != 64 or any(char not in "0123456789abcdef" for char in body.sha256):
        raise ValidationFailed(fields=[{"field": "sha256", "reason": "pattern"}])
    name = _filename(body.filename)
    now = datetime.now(UTC)
    existing = await db.scalar(
        select(DocumentFile)
        .where(
            DocumentFile.source_id == batch.source_id,
            DocumentFile.sha256 == body.sha256,
        )
        .with_for_update()
    )
    if existing is not None and existing.status == "ready":
        response.status_code = 200
        return _file_out(existing, duplicate=existing.id)
    if existing is not None and existing.status == "uploading" and existing.import_batch_id == batch.id:
        if existing.size_bytes != body.size_bytes or existing.filename != name:
            raise Conflict("This upload already exists with different metadata.", code="upload_metadata_conflict")
        response.status_code = 200
        return _file_out(existing)
    if existing is not None and existing.status == "uploading":
        existing_batch = (
            await db.get(KnowledgeImportBatch, existing.import_batch_id)
            if existing.import_batch_id is not None
            else None
        )
        if (
            existing_batch is not None
            and existing_batch.status == "open"
            and existing_batch.revoked_at is None
            and existing_batch.expires_at > now
        ):
            raise Conflict("An active import is already uploading this file.", code="upload_in_progress")
    if existing is not None:
        await db.delete(existing)
        await db.flush()
    active_file_count = int(
        await db.scalar(
            select(func.count())
            .select_from(DocumentFile)
            .where(
                DocumentFile.import_batch_id == batch.id,
                DocumentFile.status.in_(("uploading", "ready")),
            )
        )
        or 0
    )
    file_limit = min(batch.expected_file_count or MAX_BATCH_FILES, MAX_BATCH_FILES)
    if active_file_count >= file_limit:
        raise Conflict("This import already has its maximum number of files.", code="batch_file_limit")
    byte_limit = min(batch.expected_total_bytes or MAX_BATCH_BYTES, MAX_BATCH_BYTES)
    pending_bytes = int(
        await db.scalar(
            select(func.coalesce(func.sum(DocumentFile.size_bytes), 0)).where(
                DocumentFile.import_batch_id == batch.id,
                DocumentFile.status.in_(("uploading", "ready")),
            )
        )
        or 0
    )
    if pending_bytes + body.size_bytes > byte_limit:
        raise PayloadTooLarge("This import would exceed its total byte limit.", code="batch_byte_limit")
    file = DocumentFile(
        source_id=batch.source_id,
        import_batch_id=batch.id,
        filename=name,
        size_bytes=body.size_bytes,
        sha256=body.sha256,
        status="uploading",
        created_by_user_id=batch.owner_user_id,
    )
    db.add(file)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        duplicate = await db.scalar(
            select(DocumentFile).where(
                DocumentFile.source_id == batch.source_id,
                DocumentFile.sha256 == body.sha256,
                DocumentFile.status == "ready",
            )
        )
        if duplicate is None:
            raise
        response.status_code = 200
        return _file_out(duplicate, duplicate=duplicate.id)
    return _file_out(file)


@router.get("/imports/files/{file_id}", response_model=ImportFileOut)
async def get_import_file(
    file_id: uuid.UUID,
    batch: KnowledgeImportBatch = Depends(import_batch_read),
    db: AsyncSession = Depends(get_session),
) -> ImportFileOut:
    file = await db.scalar(
        select(DocumentFile).where(DocumentFile.id == file_id, DocumentFile.import_batch_id == batch.id)
    )
    if file is None:
        raise NotFound()
    return _file_out(file)


@router.put("/imports/files/{file_id}/content", response_model=ImportFileOut)
async def upload_import_file_chunk(
    file_id: uuid.UUID,
    request: Request,
    offset: int = Query(..., ge=0),
    batch: KnowledgeImportBatch = Depends(import_batch),
    db: AsyncSession = Depends(get_session),
) -> ImportFileOut:
    file = await db.scalar(
        select(DocumentFile)
        .where(DocumentFile.id == file_id, DocumentFile.import_batch_id == batch.id)
        .with_for_update()
    )
    if file is None:
        raise NotFound()
    if file.status != "uploading" or offset != file.received_bytes:
        raise Conflict(
            "The upload offset is no longer current.",
            code="upload_offset_conflict",
            fields=[{"field": "offset", "reason": str(file.received_bytes)}],
        )
    remaining = file.size_bytes - file.received_bytes
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > UPLOAD_CHUNK_BYTES or len(body) + len(chunk) > remaining:
            raise PayloadTooLarge("Each import request must contain at most 1 MB and cannot exceed the declared size.")
        body.extend(chunk)
    if not body:
        raise ValidationFailed("The upload chunk is empty.")
    if len(body) != remaining and len(body) != UPLOAD_CHUNK_BYTES:
        raise ValidationFailed(
            "Every non-final import chunk must be exactly 1 MB.",
            code="invalid_upload_chunk_size",
        )
    if file.received_bytes % UPLOAD_CHUNK_BYTES:
        raise Conflict("The stored upload offset is invalid.", code="upload_offset_invalid")
    db.add(DocumentFileBlob(file_id=file.id, ordinal=file.received_bytes // UPLOAD_CHUNK_BYTES, data=bytes(body)))
    file.received_bytes += len(body)
    await db.commit()
    return _file_out(file)


@router.post("/imports/files/{file_id}/finalize", response_model=ImportFileOut)
async def finalize_import_file(
    file_id: uuid.UUID,
    batch: KnowledgeImportBatch = Depends(import_batch),
    db: AsyncSession = Depends(get_session),
) -> ImportFileOut:
    file = await db.scalar(
        select(DocumentFile)
        .where(DocumentFile.id == file_id, DocumentFile.import_batch_id == batch.id)
        .with_for_update()
    )
    if file is None:
        raise NotFound()
    if file.status == "ready":
        return _file_out(file)
    if file.status != "uploading":
        raise Conflict("The upload cannot be finalized in its current state.", code="upload_not_finalizable")
    if file.received_bytes != file.size_bytes:
        raise Conflict("The file has not been completely uploaded.", code="upload_incomplete")
    digest = hashlib.sha256()
    blobs = await db.stream_scalars(
        select(DocumentFileBlob).where(DocumentFileBlob.file_id == file.id).order_by(DocumentFileBlob.ordinal)
    )
    async for blob in blobs:
        digest.update(blob.data)
    if digest.hexdigest() != file.sha256:
        file.status = "rejected"
        await db.commit()
        raise ValidationFailed("The uploaded bytes do not match the declared SHA-256.", code="sha256_mismatch")
    file.status = "ready"
    file.completed_at = datetime.now(UTC)
    await db.execute(
        update(KnowledgeImportBatch)
        .where(KnowledgeImportBatch.id == batch.id)
        .values(
            received_file_count=KnowledgeImportBatch.received_file_count + 1,
            received_total_bytes=KnowledgeImportBatch.received_total_bytes + file.size_bytes,
        )
    )
    await db.commit()
    return _file_out(file)
