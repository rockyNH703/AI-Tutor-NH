"""Precomputed vectors (`nhprep-vectors/1`): text embedded on the host by scripts/nhprep_embed_offline.py, imported
elsewhere with scripts/nhprep_import.py --vectors. Pure: no database, no provider.

JSON lines: a header {"format": FORMAT, "model": "<id>", "dimensions": 1536, "count": N}, then N lines
{"k": "<sha256 hex of the exact text>", "v": "<base64 of 1536 little-endian float32>"} (~8.2 KB a line; raw JSON
floats would be about twice that).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import struct
from collections.abc import Iterable, Sequence
from typing import TextIO

from app.db.models import EMBEDDING_DIMENSIONS
from app.rag.errors import EmbeddingError

FORMAT = "nhprep-vectors/1"
KEY = re.compile(r"[0-9a-f]{64}")


def text_key(text: str) -> str:
    """The key of a text: what the provider was given, before masking (the masked provider masks it the same way)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pack(vector: Sequence[float]) -> bytes:
    if len(vector) != EMBEDDING_DIMENSIONS or not all(map(math.isfinite, vector)):
        raise ValueError("vectors_file_invalid")
    return struct.pack(f"<{EMBEDDING_DIMENSIONS}f", *vector)


def write_vectors(stream: TextIO, model: str, items: Sequence[tuple[str, Sequence[float]]]) -> None:
    stream.write(
        json.dumps({"format": FORMAT, "model": model, "dimensions": EMBEDDING_DIMENSIONS, "count": len(items)})
    )
    stream.write("\n")
    for key, vector in items:
        if not KEY.fullmatch(key):
            raise ValueError("vectors_file_invalid")
        stream.write(json.dumps({"k": key, "v": base64.b64encode(_pack(vector)).decode("ascii")}) + "\n")


def read_vectors(lines: Iterable[str]) -> tuple[str, dict[str, bytes]]:
    """(model, {key: packed float32 bytes}); anything malformed raises ValueError("vectors_file_invalid")."""
    try:
        iterator = iter(lines)
        header = json.loads(next(iterator))
        model, count = header["model"], header["count"]
        if (
            header.get("format") != FORMAT
            or header.get("dimensions") != EMBEDDING_DIMENSIONS
            or not isinstance(model, str)
            or not 1 <= len(model) <= 200
            or not isinstance(count, int)
        ):
            raise ValueError
        raw: dict[str, bytes] = {}
        seen = 0
        for line in iterator:
            if not line.strip():
                continue
            item = json.loads(line)
            key, packed = item["k"], base64.b64decode(item["v"], validate=True)
            if not isinstance(key, str) or not KEY.fullmatch(key) or len(packed) != 4 * EMBEDDING_DIMENSIONS:
                raise ValueError
            if not all(map(math.isfinite, struct.unpack(f"<{EMBEDDING_DIMENSIONS}f", packed))):
                raise ValueError
            if raw.setdefault(key, packed) != packed:  # a duplicate key must carry the same vector
                raise ValueError
            seen += 1
        if seen != count:
            raise ValueError
    except (StopIteration, KeyError, TypeError, ValueError, binascii.Error) as error:
        raise ValueError("vectors_file_invalid") from error
    return model, raw


class PrecomputedEmbeddings:
    """The run's only embedder when --vectors is given (never mixed with a live provider: a missing key is an error).
    Vectors stay packed in memory (35k as Python floats would take ~1.7 GB). Never wrapped in RedactingEmbeddings:
    the offline script masked through build_embeddings, and masking again would change nothing it looks up by."""

    configured = True

    def __init__(self, model: str, raw: dict[str, bytes]) -> None:
        self.model = model
        self._raw = raw

    def has(self, text: str) -> bool:
        return text_key(text) in self._raw

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        missing = sum(1 for text in texts if not self.has(text))
        if missing:
            raise EmbeddingError("embedding_missing", f"{missing} texts have no precomputed vector")
        return [list(struct.unpack(f"<{EMBEDDING_DIMENSIONS}f", self._raw[text_key(text)])) for text in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return (await self.aembed_documents([text]))[0]
