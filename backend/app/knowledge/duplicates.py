"""Conservative duplicate candidate signals; never a deletion decision."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

_SPACE = re.compile(r"\s+")
_PAGE_NUMBER = re.compile(r"^(?:page\s+)?(?:[ivxlcdm]+|\d+)$", re.IGNORECASE)


class DuplicateClass(StrEnum):
    EXACT = "exact"
    CONFIRMED_CONTENT = "confirmed_content"
    PROBABLE = "probable"
    SAME_EDITION_REPRESENTATION = "same_edition_representation"
    DIFFERENT_EDITION = "different_edition"
    RELATED = "related"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class DuplicateEvidence:
    classification: DuplicateClass
    confidence: int
    exact_sha256: bool
    ordered_page_match: float
    shared_pages: int
    page_count_delta: int


def normalize_page(text: str) -> str:
    lines = []
    for line in text.splitlines():
        value = _SPACE.sub(" ", line).strip()
        if not value or _PAGE_NUMBER.fullmatch(value):
            continue
        lines.append(value.casefold())
    return "\n".join(lines)


def page_hashes(pages: list[str]) -> tuple[str, ...]:
    return tuple(hashlib.sha256(normalize_page(page).encode("utf-8")).hexdigest() for page in pages)


def classify(
    original_sha256: str, candidate_sha256: str, original_pages: list[str], candidate_pages: list[str]
) -> DuplicateEvidence:
    if original_sha256 == candidate_sha256:
        return DuplicateEvidence(DuplicateClass.EXACT, 100, True, 1.0, len(original_pages), 0)
    left, right = page_hashes(original_pages), page_hashes(candidate_pages)
    compared = max(len(left), len(right), 1)
    ordered = sum(a == b for a, b in zip(left, right, strict=False)) / compared
    shared = len(set(left) & set(right))
    delta = abs(len(left) - len(right))
    if left and left == right:
        classification, confidence = DuplicateClass.CONFIRMED_CONTENT, 100
    elif ordered >= 0.95 and delta <= 1:
        classification, confidence = DuplicateClass.SAME_EDITION_REPRESENTATION, 95
    elif shared >= 3 and shared / max(min(len(left), len(right)), 1) >= 0.8:
        classification, confidence = DuplicateClass.PROBABLE, 85
    elif shared >= 2:
        classification, confidence = DuplicateClass.RELATED, 65
    elif left and right and (left[0] == right[0] or left[-1] == right[-1]):
        classification, confidence = DuplicateClass.DIFFERENT_EDITION, 60
    else:
        classification, confidence = DuplicateClass.INCONCLUSIVE, 20
    return DuplicateEvidence(classification, confidence, False, ordered, shared, delta)
