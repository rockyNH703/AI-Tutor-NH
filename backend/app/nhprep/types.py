"""The NHprep finder's contract between lanes L3 (intent.py, search.py) and L4 (chat.py, service.py).

Plan rev 4 §8.3-§8.4 and Revision 5 (C:\\Users\\test\\netsec-handoffs\\nhprep.md). Pure data: no I/O, no app imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

View = Literal["video", "chapters", "notes", "cheat_sheet", "flashcards", "interview"]
"""What the student asked to see; the card always draws the player and lesson facts."""

Status = Literal["confident", "ambiguous", "none", "timeout", "error"]
"""`confident`/`ambiguous` draw a card; anything else lets the normal turn run (no `ahead` prefetch)."""


@dataclass(frozen=True)
class NhprepQuery:
    """What intent.parse() read from one typed user message; None from parse() means "not an NHprep request"."""

    text: str  # the topic words the finder searches for (cue words removed)
    views: tuple[View, ...]  # never empty; ("video",) when nothing more specific was asked
    course_hint: str | None = None  # a course slug or name fragment the student named
    trainer_hint: str | None = None  # a trainer name as typed ("ajay", "Mr. Sushil")
    day: int | None = None  # "day 8" → 8 (the slug's day, not the lesson position)


@dataclass(frozen=True)
class Match:
    lesson_id: UUID
    score: float
    item_id: UUID | None = None  # the section/note/card that matched, when one did


@dataclass(frozen=True)
class FinderResult:
    status: Status
    primary: Match | None = None  # set when status is confident or ambiguous
    alternatives: tuple[Match, ...] = ()  # ≤5, deduped by video id, ranked
    trace: dict[str, object] = field(default_factory=dict)  # ids, counts, per-leg ms, reason codes; never text
