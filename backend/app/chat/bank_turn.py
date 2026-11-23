"""The certification bank's chat turn: a message that clearly asks for a quiz is answered by the app itself with
stored questions. No model, no embedding, no search.

Steps:
1. parse (app/qbank/request.py);
2. the topics (app/qbank/finder.py), from the message, else the chat's last messages ("test me on this"), else
   the last bank test;
3. a batch of stored questions (app/qbank/bank_tests.py);
4. a short fixed line plus a ```quiz block without keys; the keys stay in the record (app/assessments/bank.py).

Nothing is written here: ChatService._finalize opens the practice set with the reply, in one transaction.
Titles in the fixed copy are escaped, so a manifest title can't become a link or markup.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assessments.bank import bank_record, fence_body
from app.db.models import Message
from app.qbank import bank_tests, finder
from app.qbank.request import Request, parse
from app.qbank.selection import Pool, pick_next

NOT_ENTITLED = "No practice question bank is available for your account yet."
_MARKUP = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~])")


def plain(text: str) -> str:
    """A title as literal text in Markdown: every markup character escaped, newlines flattened."""
    return _MARKUP.sub(r"\\\1", " ".join(text.split()))


@dataclass
class Served:
    """The bank's reply: its text, and (for a test) the batch and record that _finalize stores with it."""

    text: str
    bank: dict
    batch: bank_tests.Batch | None = None
    record: dict | None = None
    question_ids: tuple[str, ...] = field(default_factory=tuple)


def wants(message: str) -> Request | None:
    return parse(message)


async def _last_bank(session: AsyncSession, conversation_id: uuid.UUID) -> dict | None:
    found = await session.scalar(
        select(Message.metadata_["bank"])
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.status == "complete",
            Message.metadata_.has_key("bank"),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return found if isinstance(found, dict) else None


def referent(earlier: list[str]) -> list[tuple[str, str]]:
    """What "this" points at, from the chat's last three user messages (newest first), as steps to try in order:
    ("sentence", text) an ordinary message, read loosely; ("topic", words) an earlier quiz request's named topic;
    ("last", "") the latest bank test's topics. A quiz request settles it: "next 5" or "quiz me on this" means the
    test it got, never its words read as a sentence (session 1, 2026-11-20: "...the BGP in the Real World course",
    "next 5", then "Quiz me on this" served Wireless)."""
    steps: list[tuple[str, str]] = []
    for sentence in earlier[:3]:
        asked = parse(sentence)
        if asked is None:
            steps.append(("sentence", sentence))
            continue
        named = asked.route != "next" and not asked.refers_to_chat and asked.topic
        steps.append(("topic", asked.topic) if named else ("last", ""))
        break
    return steps


async def _topics(
    session: AsyncSession,
    request: Request,
    *,
    earlier: list[str],
    last: dict | None,
    goal: str | None,
    entitled: bool,
    books: tuple[str, ...] | None = None,
) -> finder.Found:
    if request.topic:
        return await finder.find(
            session,
            request.topic,
            track=request.track,
            goal=goal,
            entitled=entitled,
            loose=request.lesson_named,
            books=books,
        )
    if request.refers_to_chat:
        for kind, text in referent(earlier):
            if kind == "last":
                break
            found = await finder.find(
                session,
                text,
                track=request.track,
                goal=goal,
                entitled=entitled,
                loose=kind == "sentence",
                books=books,
            )
            if found.status in ("confident", "broad"):
                return found
        if last and last.get("topicIds"):
            rows = await session.execute(
                finder.TRACK,
                {
                    "track": last.get("bookId"),
                    "entitled": entitled,
                    "topic_ids": last["topicIds"],
                    "books": list(books) if books is not None else None,
                },
            )
            topics = tuple(finder._row(row) for row in rows if row.available)
            return finder.Found("broad" if len(topics) > 1 else "confident", topics) if topics else finder.Found("none")
    # Nothing in the chat names a topic ("quiz me" in a new chat): the student's goal track, else every track.
    return await finder.find(session, "", track=request.track, goal=goal, entitled=entitled, books=books)


def _mix(rows: list[Any]) -> str:
    counts = {level: sum(row.difficulty == level for row in rows) for level in ("easy", "medium", "hard")}
    parts = [f"{n} {level}" for level, n in counts.items() if n]
    multi = sum(row.question_format == "multi_select" for row in rows)
    return ", ".join(parts) + (f"; {multi} with more than one correct answer" if multi else "")


def _title(found: finder.Found) -> str:
    first = found.topics[0]
    if len(found.topics) == 1:
        return f"{first.book_title} · {first.chapter_title} · {first.title}"[:200]
    chapters = {topic.chapter_title for topic in found.topics}
    where = next(iter(chapters)) if len(chapters) == 1 else f"{len(found.topics)} topics"
    return f"{first.book_title} · {where}"[:200]


def _none_text(found: finder.Found, request: Request) -> str:
    # The user's own words are never echoed into the reply: only titles from the manifest are.
    line = f"I don't have stored practice questions on {'that topic' if request.topic else 'this'} yet."
    if found.closest:
        offers = "\n".join(f"- Quiz me on {plain(title)}" for title in found.closest)
        return f"{line} The closest topics I do have:\n\n{offers}"
    return line + " Try naming a topic, for example “quiz me on OSPF”."


async def answer(
    session: AsyncSession,
    request: Request,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    reply_id: uuid.UUID,
    earlier: list[str],
    goal: str | None,
    entitled: bool,
    books: tuple[str, ...] | None = None,
) -> Served | None:
    """The bank's reply to a quiz request, or None to leave the turn to the model (a "next 5" with no bank test
    before it in this chat). Only reads."""
    started = time.perf_counter()
    last = await _last_bank(session, conversation_id)
    accuracy, exclude = None, ()
    if request.route == "next":
        if not last or not last.get("topicIds"):
            return None
        request = Request(
            "bank",
            count=request.count,
            difficulty=last.get("difficulty"),
            multi_only=bool(last.get("multiOnly")),
            track=last.get("bookId"),
        )
        found = await _topics(
            session,
            Request("bank", refers_to_chat=True),
            earlier=[],
            last=last,
            goal=goal,
            entitled=entitled,
            books=books,
        )
        exclude = tuple(last.get("questionIds") or ())
        if last.get("setId"):
            previous = await bank_tests.last_batch(session, user_id, uuid.UUID(last["setId"]))
            accuracy = previous[1] if previous else None
    else:
        found = await _topics(session, request, earlier=earlier, last=last, goal=goal, entitled=entitled, books=books)
    timing = {"findMs": round((time.perf_counter() - started) * 1000, 1), **found.timings}
    base = {"version": 1, "status": found.status, "timing": timing}
    if not request.strong and found.status in ("none", "ambiguous"):
        return None  # "practice questions on my mind": a weak cue with no clear topic is the model's to answer
    if found.status == "none" or not found.topics:
        text = _none_text(found, request) if entitled or found.closest else NOT_ENTITLED
        return Served(text, {**base, "status": "none"})
    if found.status == "ambiguous":
        offers = "\n".join(f"- Quiz me on {plain(topic.title)}" for topic in found.topics[:3])
        return Served(
            f"Which topic should the questions be on?\n\n{offers}",
            {**base, "choices": [topic.title for topic in found.topics[:3]]},
        )
    first = found.topics[0]
    pool = Pool(
        first.language,
        first.class_level,
        first.subject_code,
        tuple(topic.topic_id for topic in found.topics if topic.book_id == first.book_id),
        card=True,
        entitled=entitled,
        question_format="multi_select" if request.multi_only else None,
    )
    rows = await bank_tests.pick_batch(
        session, user_id, pool, str(reply_id), request.count, request.difficulty, accuracy, exclude
    )
    timing["pickMs"] = round((time.perf_counter() - started) * 1000, 1)
    if not rows:
        return Served(_none_text(finder.Found("none"), request), {**base, "status": "none"})
    title = _title(found)
    taken = (*exclude, *(row.question_id for row in rows))
    more = await pick_next(session, user_id, replace(pool, difficulty=None), str(reply_id), exclude=taken) is not None
    body = fence_body(rows, title, more=more)
    record = bank_record(rows, title=title, body=body)
    if record is None:
        return Served(_none_text(finder.Found("none"), request), {**base, "status": "none"})
    source = "NHprep" if any(row.source_kind.startswith("nhprep") for row in rows) else "the NetSec AI question bank"
    fewer = f" (only {len(rows)} matched)" if len(rows) < request.count else ""
    line = f"{len(rows)} questions{fewer} · {plain(title)}: {_mix(rows)}. Source: {source}."
    question_ids = tuple(row.question_id for row in rows)
    filters = {
        "netsec": True,
        "book_id": first.book_id,
        "topic_ids": list(pool.topic_ids),
        "difficulty": request.difficulty,
        "multi_only": request.multi_only,
        "title": title,
        "conversation_id": str(conversation_id),
        "message_id": str(reply_id),
    }
    bank = {
        **base,
        "bookId": first.book_id,
        "topicIds": list(pool.topic_ids),
        "topics": [{"id": topic.topic_id, "title": topic.title} for topic in found.topics],
        "questionIds": list(question_ids),
        "difficulty": request.difficulty,
        "multiOnly": request.multi_only,
        "title": title,
        "source": source,
        "more": more,
    }
    return Served(
        f"{line}\n\n```quiz\n{body}\n```",
        bank,
        batch=bank_tests.Batch(tuple(rows), filters),
        record=record,
        question_ids=question_ids,
    )
