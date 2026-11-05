"""Canonical identity of a passage of text (migration 0025): one normalized form, one key, one 64-bit simhash.

The key decides "the same text" (stored and indexed once, knowledge_passages.content_key); the simhash finds
"almost the same text" (a revised sentence in a new edition: knowledge_passage_links), never to merge it.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_SPACE = re.compile(r"\s+")
_LINE_HYPHEN = re.compile(r"(\w)-\n(\w)")  # "proto-\ncol": a word broken across lines by the typesetter
SOFT_HYPHEN = "­"
SHINGLE = 3
_MASK64 = (1 << 64) - 1


def normalize_text(text: str) -> str:
    """What two copies of a passage share however they were typeset: NFKC, no soft or line-break hyphens, single
    spaces, case folded."""
    text = unicodedata.normalize("NFKC", text).replace(SOFT_HYPHEN, "")
    text = _LINE_HYPHEN.sub(r"\1\2", text)
    return _SPACE.sub(" ", text).strip().casefold()


def content_key(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def simhash(text: str) -> int:
    """A 64-bit simhash over word 3-shingles, as a signed integer (PostgreSQL bigint)."""
    words = normalize_text(text).split()
    shingles = [" ".join(words[i : i + SHINGLE]) for i in range(max(1, len(words) - SHINGLE + 1))]
    weights = [0] * 64
    for shingle in shingles:
        value = int.from_bytes(hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += 1 if (value >> bit) & 1 else -1
    unsigned = sum(1 << bit for bit in range(64) if weights[bit] > 0)
    return unsigned - (1 << 64) if unsigned >= 1 << 63 else unsigned


def hamming(left: int, right: int) -> int:
    """Bits that differ between two simhashes (0 = the same shingles, small = near-duplicates)."""
    return ((left ^ right) & _MASK64).bit_count()
