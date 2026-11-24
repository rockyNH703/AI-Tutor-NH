"""The Friend Zone name rules (plan section 4, J1): what friends see instead of an account's real details."""

from __future__ import annotations

import re
import unicodedata

from app.errors import ValidationFailed

PUNCTUATION = frozenset(" ._'-")
# Names that would pass for NetworkersHome or its staff, compared without spaces and punctuation.
RESERVED = ("networkershome", "netsecai", "admin", "moderator", "trainer", "support", "staff", "official")
RESERVED_EXACT = frozenset({"nh", "netsec"})
_SEPARATORS = re.compile(r"[\s._'-]+")


def clean_name(raw: str) -> str:
    """The name as stored: NFKC, spaces collapsed, 2-30 characters of letters, marks, digits and `. _ ' -`; no
    control, format (zero-width, bidirectional) or other characters; no address-like or reserved names."""
    name = " ".join(unicodedata.normalize("NFKC", raw or "").split())
    if not 2 <= len(name) <= 30:
        raise _refused("Use 2 to 30 characters.")
    if any(ch not in PUNCTUATION and unicodedata.category(ch)[0] not in "LMN" for ch in name):
        raise _refused("Use letters, numbers, spaces and . _ ' - only.")
    key = _SEPARATORS.sub("", name.casefold())
    if "www." in name.casefold() or key in RESERVED_EXACT or any(key.startswith(word) for word in RESERVED):
        raise _refused("Choose a different name.")
    return name


def _refused(message: str) -> ValidationFailed:
    return ValidationFailed(message, fields=[{"field": "displayName", "reason": "invalid"}])
