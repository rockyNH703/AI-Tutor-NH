"""Deterministic structure-aware chunks (publication itself is app.knowledge.passages.publish)."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.knowledge.protocol import ExtractedDocument

# Five passages (RAG_TOP_K) with their headings fit RAG_MAX_CONTEXT_CHARS (8,000): about 1,550 characters each.
MAX_CHUNK_CHARS = 1_400
CHUNK_OVERLAP_CHARS = 200
CHUNKING = "page-groups"  # small elements of one page and top section share a chunk (part of the signature)


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


def _path(element: dict[str, Any]) -> tuple[str, ...]:
    return tuple(element.get("section_path") or ())


_MAJOR = re.compile(r"(\d{1,2})\.\d")


def _top(path: tuple[str, ...]) -> str:
    """What may share a chunk: a chapter, or a topic number's major part (1.0 and its 1.x; 1.1 and 1.2 without
    their domain), so an exam's domains never mix."""
    major = _MAJOR.match(path[0])
    return f"topic {major.group(1)}" if major else path[0]


def _groups(elements: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Consecutive elements of one page and one top-level section that fit one chunk together (exam topics, one per
    line; a heading and its short paragraph). A longer element stands alone and is split."""
    groups: list[list[dict[str, Any]]] = []
    size = 0
    for element in elements:
        length = len(element["content"].strip())
        group = groups[-1] if groups else None
        tops = {_top(path) for member in [*(group or ()), element] if (path := _path(member))}
        if (
            group is not None
            and size + 1 + length <= MAX_CHUNK_CHARS
            and element["physical_page"] == group[0]["physical_page"]
            and len(tops) <= 1
        ):
            group.append(element)
            size += 1 + length
        else:
            groups.append([element])
            size = length
    return groups


def build_chunks(extracted: ExtractedDocument) -> list[StagedChunk]:
    staged: list[StagedChunk] = []
    for group in _groups(extracted.elements):
        element = group[0]
        # The section the members share (tuples compare item by item): 1.0 for its topics 1.1 and 1.2.
        section = tuple(os.path.commonprefix([path for member in group if (path := _path(member))] or [()]))
        page = int(element["physical_page"])
        label = element.get("printed_page_label")
        text = "\n".join(member["content"].strip() for member in group)
        for part_index, content in enumerate(_split_text(text)):
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
