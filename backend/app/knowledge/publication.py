"""Deterministic structure-aware chunks (publication itself is app.knowledge.passages.publish)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.knowledge.protocol import ExtractedDocument

# Five passages (RAG_TOP_K) with their headings fit RAG_MAX_CONTEXT_CHARS (8,000): about 1,550 characters each.
MAX_CHUNK_CHARS = 1_400
CHUNK_OVERLAP_CHARS = 200


@dataclass(frozen=True)
class StagedChunk:
    stable_key: str
    ordinal: int
    content: str
    content_sha256: str
    section_path: tuple[str, ...]
    physical_page_start: int
    physical_page_end: int
    printed_page_label: str | None
    metadata: dict[str, Any]


def _split_text(value: str) -> list[str]:
    value = value.strip()
    if len(value) <= MAX_CHUNK_CHARS:
        return [value] if value else []
    chunks: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + MAX_CHUNK_CHARS)
        if end < len(value):
            boundary = value.rfind("\n", start, end)
            if boundary <= start:
                boundary = value.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
    return chunks


def build_chunks(extracted: ExtractedDocument) -> list[StagedChunk]:
    staged: list[StagedChunk] = []
    for element in extracted.elements:
        section = tuple(element.get("section_path") or ())
        page = int(element["physical_page"])
        label = element.get("printed_page_label")
        for part_index, content in enumerate(_split_text(element["content"])):
            content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            stable = hashlib.sha256(
                f"{element['sequence']}\0{part_index}\0{page}\0{'/'.join(section)}\0{content_sha}".encode()
            ).hexdigest()
            staged.append(
                StagedChunk(
                    stable_key=stable,
                    ordinal=len(staged),
                    content=content,
                    content_sha256=content_sha,
                    section_path=section,
                    physical_page_start=page,
                    physical_page_end=page,
                    printed_page_label=label,
                    metadata={"element_type": element["element_type"], **dict(element.get("metadata") or {})},
                )
            )
    return staged
