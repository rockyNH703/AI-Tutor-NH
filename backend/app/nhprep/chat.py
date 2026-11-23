"""NHprep's chat turn (plan rev 4 §8.4): a typed message that asks for an NHprep lesson, its sections, notes, cheat
sheet, flashcards or interview questions is answered by the app itself with a lesson card. The chat calls this module
only (app/chat/service.py `_app_turn`, after the question bank's parse).

`answer()` never raises and never takes longer than NHPREP_FIND_TIMEOUT_S: anything but a confident or ambiguous
match is None, and the normal turn runs. The reply's text is a fixed line (COPY, never NHprep text); what the card
shows is ids only (`Card.public` -> messages.metadata.nhprep, pinned by tests/fixtures/nhprep/cards.json), and why
it was chosen is internal (`Card.trace` -> metadata.nhprepTrace). Logs: ids, counts and reason codes only."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, get_args

from app.db.session import Database
from app.logging import get_logger
from app.nhprep.access import nhprep_allows
from app.nhprep.types import FinderResult, NhprepQuery, View
from app.settings import Settings

__all__ = ["COPY", "Card", "NhprepChat", "build_chat", "nhprep_allows"]  # the chat imports only this module

log = get_logger("app.nhprep")

# Never NHprep text, and none of ` ~ [ ] $ :: [[ "chapter question-bank: the renderer would read them as code, links,
# maths or directives, and chat_tests.fill as a practice test (tests/test_nhprep_chat.py pins it).
COPY = {
    "confident": "Here is the NHprep lesson that matches your request.",
    "ambiguous": "Several NHprep lessons match your request. The closest one is first, with the others below it.",
}
VIEWS = frozenset(get_args(View))
MAX_ALTERNATIVES = 5

Parser = Callable[[str, Sequence[str]], NhprepQuery | None]  # (message, the earlier user messages, newest first)
Finder = Callable[[NhprepQuery], Awaitable[FinderResult]]


@dataclass(frozen=True)
class Card:
    copy: str  # a COPY value: the reply's whole text
    public: dict[str, Any]  # messages.metadata.nhprep: every key always present; ids and literals only
    trace: dict[str, Any]  # messages.metadata.nhprepTrace (internal): reason codes, counts, ms; never text


def card(query: NhprepQuery, result: FinderResult, find_ms: int) -> Card:
    """The card for a confident or ambiguous finder result (`result.primary` set)."""
    primary = result.primary
    assert primary is not None and result.status in COPY
    alternatives: list[str] = []
    for match in result.alternatives:
        lesson = str(match.lesson_id)
        if lesson != str(primary.lesson_id) and lesson not in alternatives:
            alternatives.append(lesson)
    views = [view for view in dict.fromkeys(query.views) if view in VIEWS] or ["video"]
    public = {
        "version": 1,
        "views": views,
        "status": result.status,
        "primaryLessonId": str(primary.lesson_id),
        "alternativeLessonIds": alternatives[:MAX_ALTERNATIVES],
        "matchedItemId": str(primary.item_id) if primary.item_id else None,
    }
    # JSON-safe whatever the finder put in its trace (a UUID or a datetime must not fail the reply's save).
    trace = json.loads(json.dumps({**result.trace, "status": result.status, "findMs": find_ms}, default=str))
    return Card(COPY[result.status], public, trace)


class NhprepChat:
    def __init__(self, settings: Settings, parse: Parser, find: Finder) -> None:
        self.settings = settings
        self.parse = parse  # app.nhprep.intent (lane L3): the cue parse, pure
        self.find = find  # app.nhprep.search (lane L3): the finder, bound to the database and masked embeddings

    async def answer(
        self,
        user: Any,
        message: str,
        earlier: Sequence[str],
        reply_id: uuid.UUID,
        *,
        within: Sequence[uuid.UUID] | None = None,
    ) -> Card | None:
        """The card for this message, or None (not for this account, no NHprep cue, nothing found in time, an
        error): the normal turn then runs. Never raises. `within`: a program chat's month (app/programs, whose access
        the chat checked): only these courses, for an NH student whatever the email domain (owner decision D3)."""
        extra: dict[str, Any] = {"message_id": str(reply_id)}
        if within is None and not nhprep_allows(user, self.settings):
            return None
        if within is not None and not within:
            return None
        try:
            query = self.parse(message, earlier)
        except Exception as error:  # noqa: BLE001 - a parse bug must never fail a turn
            log.warning("nhprep.intent failed", extra={**extra, "error_type": type(error).__name__})
            return None
        if query is None:
            return None
        log.info(
            "nhprep.intent",
            extra={
                **extra,
                "views": list(query.views),
                "course_hint": query.course_hint is not None,
                "trainer_hint": query.trainer_hint is not None,
                "day": query.day is not None,
            },
        )
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.nhprep_find_timeout_s):
                result = await (self.find(query) if within is None else self.find(query, within))
        except TimeoutError:
            result = FinderResult("timeout")
        except Exception as error:  # noqa: BLE001 - the finder never fails a turn: the model answers instead
            result = FinderResult("error", trace={"errorType": type(error).__name__})
        find_ms = int((time.monotonic() - started) * 1000)
        found = result.status in COPY and result.primary is not None
        log.info(
            "nhprep.finder",
            extra={
                **extra,
                "status": result.status,
                "find_ms": find_ms,
                "lesson_id": str(result.primary.lesson_id) if found else None,
                "alternatives": len(result.alternatives),
                "error_type": result.trace.get("errorType"),
            },
        )
        return card(query, result, find_ms) if found else None


def build_chat(db: Database, settings: Settings, embeddings: Any) -> NhprepChat | None:
    """NHprep's chat turn as create_app builds it: None while NHPREP_MODE=off (a mode change is a restart)."""
    if settings.nhprep_mode == "off":
        return None
    from app.nhprep import intent, search  # lane L3: imported only when NHprep is on

    # The finder keeps its own deadline (the same setting); answer()'s is the backstop around it.
    return NhprepChat(
        settings,
        intent.parse,
        lambda query, within=None: search.answer(db, embeddings, query, settings.nhprep_find_timeout_s, within=within),
    )
