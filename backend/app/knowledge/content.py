"""Immutable Knowledge Library PDF byte streaming, including one HTTP byte range."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models import DocumentFileBlob
from app.errors import AppError

BLOB_BYTES = 1024 * 1024


class RangeNotSatisfiable(AppError):
    status_code, code, message = 416, "range_not_satisfiable", "The requested byte range is not available."

    def __init__(self, size: int) -> None:
        super().__init__()
        self.headers = {"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"}


def parse_single_range(value: str | None, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise RangeNotSatisfiable(size)
    first, separator, last = value[6:].partition("-")
    if not separator:
        raise RangeNotSatisfiable(size)
    try:
        if not first:
            suffix = int(last)
            if suffix <= 0:
                raise ValueError
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(first)
            end = size - 1 if not last else int(last)
            if start < 0 or start >= size or end < start:
                raise ValueError
            end = min(end, size - 1)
    except ValueError as error:
        raise RangeNotSatisfiable(size) from error
    return start, end


async def stream_file_range(sessionmaker, file_id: uuid.UUID, start: int, end: int):
    first_blob = start // BLOB_BYTES
    last_blob = end // BLOB_BYTES
    for ordinal in range(first_blob, last_blob + 1):
        async with sessionmaker() as session:
            data = await session.scalar(
                select(DocumentFileBlob.data).where(
                    DocumentFileBlob.file_id == file_id, DocumentFileBlob.ordinal == ordinal
                )
            )
        if data is None:
            return
        lower = start % BLOB_BYTES if ordinal == first_blob else 0
        upper = end % BLOB_BYTES + 1 if ordinal == last_blob else len(data)
        yield bytes(data[lower:upper])
