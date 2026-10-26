from __future__ import annotations

import re
from typing import Literal

from app.db.models import MEMORY_KINDS, MEMORY_MAX_CONTENT_CHARS
from app.errors import ValidationFailed

MemoryKind = Literal["learning_context", "study_context"]

_SPACE = re.compile(r"\s+")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|private[_ -]?key)\b", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:bearer\s+)[A-Za-z0-9._~+/-]{16,}=*\b", re.IGNORECASE),
)
_AUTHORITATIVE_PATTERNS = (
    re.compile(r"\b(?:my )?(?:grade|class|board|medium|target exam|target score|exam date)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:my )?(?:pace|difficulty|response length|answer mode|learning preference|teaching preference)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:my )?(?:weak topic|mastery|practice score|assessment score|streak)\b", re.IGNORECASE),
)

assert set(MemoryKind.__args__) == set(MEMORY_KINDS)


def normalize_content(content: str, *, max_chars: int = MEMORY_MAX_CONTENT_CHARS) -> str:
    normalized = _SPACE.sub(" ", content).strip()
    if not normalized:
        raise ValidationFailed("Memory content must not be blank.")
    if len(normalized) > max_chars:
        raise ValidationFailed("Memory content is too long.")
    return normalized


def validate_memory_policy(content: str) -> None:
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
        raise ValidationFailed("Secrets and credentials cannot be stored in memory.")
    if any(pattern.search(content) for pattern in _AUTHORITATIVE_PATTERNS):
        raise ValidationFailed("Use the learner profile or learning settings for this information.")
