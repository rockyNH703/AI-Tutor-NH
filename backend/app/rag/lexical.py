"""Pure, bounded lexical-query preparation and rank-only result fusion."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import TypeVar

MAX_QUERY_INPUT_CHARS = 8_000
MAX_QUERY_TOKENS = 32
MAX_QUERY_TOKEN_CHARS = 64
DEFAULT_RRF_K = 60
DEFAULT_RRF_LIMIT = 10
DEFAULT_RRF_CANDIDATES_PER_RANKING = 100

_IDENTIFIER_SEPARATORS = frozenset("-./")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class LexicalQuery:
    """Plain tokens suitable for a later parameterized `plainto_tsquery` call."""

    tokens: tuple[str, ...]

    @property
    def text(self) -> str:
        """Return plain query text, never PostgreSQL tsquery syntax."""

        return " ".join(self.tokens)


@dataclass(frozen=True, slots=True)
class FusedCandidate[T]:
    """An original candidate paired with its independently computed RRF score."""

    item: T
    rrf_score: float


def build_lexical_query(text: str) -> LexicalQuery | None:
    """Normalize hostile text into a small, deterministic set of plain search tokens.

    NFKC normalization and case folding happen after the raw input is capped. Letters,
    numbers, and combining marks form words. A single internal hyphen, dot, or slash is
    retained so engineering identifiers remain useful; all other syntax is a boundary.
    Duplicate tokens are removed in first-seen order.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")

    normalized = unicodedata.normalize("NFKC", text[:MAX_QUERY_INPUT_CHARS]).casefold()
    tokens: list[str] = []
    seen: set[str] = set()
    current: list[str] = []
    pending_separator: str | None = None
    saturated = False

    def finish_token() -> bool:
        nonlocal current, pending_separator, saturated
        if current:
            token = "".join(current)
            if token not in seen:
                seen.add(token)
                tokens.append(token)
        current = []
        pending_separator = None
        saturated = False
        return len(tokens) >= MAX_QUERY_TOKENS

    for character in normalized:
        is_mark = unicodedata.category(character).startswith("M")
        if character.isalnum() or (is_mark and current):
            if saturated:
                continue
            required = 1 + (1 if pending_separator is not None else 0)
            if len(current) + required > MAX_QUERY_TOKEN_CHARS:
                saturated = True
                pending_separator = None
                continue
            if pending_separator is not None:
                current.append(pending_separator)
                pending_separator = None
            current.append(character)
            continue

        if character in _IDENTIFIER_SEPARATORS and current and pending_separator is None and not saturated:
            pending_separator = character
            continue

        if finish_token():
            break

    if len(tokens) < MAX_QUERY_TOKENS:
        finish_token()
    if not tokens:
        return None
    return LexicalQuery(tuple(tokens))


def reciprocal_rank_fusion[T](
    rankings: Sequence[Sequence[T]],
    *,
    key: Callable[[T], Hashable],
    k: int = DEFAULT_RRF_K,
    limit: int = DEFAULT_RRF_LIMIT,
    candidates_per_ranking: int = DEFAULT_RRF_CANDIDATES_PER_RANKING,
) -> tuple[FusedCandidate[T], ...]:
    """Fuse ranked lists using only positions, preserving each selected item unchanged.

    A duplicate key contributes at most once per ranking. If different objects with the
    same key occur across rankings, the first supplied object is retained. Equal fusion
    scores are ordered by their best rank, then ranking order, making ties stable and
    preventing one leg's arbitrary precedence from hiding a rank-1 exact match.
    """

    _require_positive_int("k", k)
    _require_positive_int("limit", limit)
    _require_positive_int("candidates_per_ranking", candidates_per_ranking)

    scores: dict[Hashable, float] = {}
    items: dict[Hashable, T] = {}
    first_seen: dict[Hashable, tuple[int, int]] = {}

    for ranking_index, ranking in enumerate(rankings):
        seen_in_ranking: set[Hashable] = set()
        for rank, item in enumerate(ranking[:candidates_per_ranking], start=1):
            candidate_key = key(item)
            if candidate_key in seen_in_ranking:
                continue
            seen_in_ranking.add(candidate_key)
            if candidate_key not in items:
                items[candidate_key] = item
                first_seen[candidate_key] = (ranking_index, rank)
                scores[candidate_key] = 0.0
            scores[candidate_key] += 1.0 / (k + rank)

    ordered_keys = sorted(
        scores,
        key=lambda candidate_key: (
            -scores[candidate_key],
            first_seen[candidate_key][1],
            first_seen[candidate_key][0],
        ),
    )
    return tuple(FusedCandidate(items[candidate_key], scores[candidate_key]) for candidate_key in ordered_keys[:limit])


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
