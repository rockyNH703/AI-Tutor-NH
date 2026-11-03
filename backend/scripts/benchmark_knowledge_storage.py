"""Local PostgreSQL storage decision spike for Knowledge Library PDF blobs.

Requires an explicit loopback `*_test` URL. It creates its own uniquely named source/file rows,
measures sequential 1 MiB storage and byte-range first response, prints bounded JSON, then removes
every row. It never reads application settings or document content from disk.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
import uuid
from urllib.parse import urlsplit

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import DocumentFile, DocumentFileBlob, KnowledgeSource, User
from app.knowledge.content import stream_file_range

BLOB = 1024 * 1024


def _safe(url: str) -> None:
    parsed = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    name = parsed.path.rsplit("/", 1)[-1]
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not name.endswith("_test"):
        raise SystemExit("refusing: URL must be loopback and database name must end in _test")


async def _read(sessionmaker, file_id: uuid.UUID, size: int) -> float:
    started = time.perf_counter()
    seen = 0
    async for chunk in stream_file_range(sessionmaker, file_id, 0, min(size - 1, BLOB - 1)):
        seen += len(chunk)
        if seen:
            break
    if seen == 0:
        raise RuntimeError("no bytes returned")
    return (time.perf_counter() - started) * 1000


async def run(url: str, size_mib: int, repetitions: int) -> dict:
    _safe(url)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    nonce = uuid.uuid4().hex
    data = (b"netsec-storage-spike\0" * (BLOB // 21 + 1))[:BLOB]
    digest = hashlib.sha256()
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    started = time.perf_counter()
    try:
        async with sessions() as session, session.begin():
            session.add(User(id=user_id, email=f"storage-spike-{nonce}@example.com", email_verified=True))
            await session.flush()
            session.add(KnowledgeSource(id=source_id, name=f"storage-spike-{nonce}", owner_user_id=user_id))
            await session.flush()
            session.add(
                DocumentFile(
                    id=file_id,
                    source_id=source_id,
                    filename="synthetic.pdf",
                    size_bytes=size_mib * BLOB,
                    sha256="0" * 64,
                    status="uploading",
                    created_by_user_id=user_id,
                )
            )
            await session.flush()
            for ordinal in range(size_mib):
                digest.update(data)
                await session.execute(insert(DocumentFileBlob).values(file_id=file_id, ordinal=ordinal, data=data))
            await session.execute(
                delete(DocumentFile).where(
                    DocumentFile.filename == "synthetic.pdf", DocumentFile.sha256 == digest.hexdigest()
                )
            )
            await session.execute(
                DocumentFile.__table__.update()
                .where(DocumentFile.id == file_id)
                .values(sha256=digest.hexdigest(), received_bytes=size_mib * BLOB, status="ready")
            )
        write_seconds = time.perf_counter() - started
        sequential = [await _read(sessions, file_id, size_mib * BLOB) for _ in range(repetitions)]
        concurrent = []
        for _ in range(repetitions):
            concurrent.extend(await asyncio.gather(*(_read(sessions, file_id, size_mib * BLOB) for _ in range(3))))
        async with sessions() as session:
            stored = int(
                await session.scalar(select(DocumentFile.received_bytes).where(DocumentFile.id == file_id)) or 0
            )
        return {
            "sizeMiB": size_mib,
            "storedBytes": stored,
            "writeMiBPerSecond": round(size_mib / write_seconds, 2),
            "firstResponseMs": {
                "concurrency1P95": round(statistics.quantiles(sequential, n=20)[18], 2),
                "concurrency3P95": round(statistics.quantiles(concurrent, n=20)[18], 2),
            },
            "acceptance": max(sequential + concurrent) < 500,
        }
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(DocumentFile).where(DocumentFile.id == file_id))
            await session.execute(delete(KnowledgeSource).where(KnowledgeSource.id == source_id))
            await session.execute(delete(User).where(User.id == user_id))
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--size-mib", type=int, default=50, choices=range(1, 101))
    parser.add_argument("--repetitions", type=int, default=10, choices=range(5, 101))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.database_url, args.size_mib, args.repetitions)), sort_keys=True))


if __name__ == "__main__":
    main()
