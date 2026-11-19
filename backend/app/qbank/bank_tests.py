"""Certification-bank tests in the chat: a batch of stored questions picked for a student, opened as their
practice set when the reply is saved, and each answer recorded and graded by the database. SQL and fixed rules
only, no model.

A batch is fixed: all its questions are picked when the test is set, so the reply's test block and the record
match. The adapting happens between batches:
- the difficulty mix follows how the last batch on these topics went;
- inside a batch, unseen questions and the weakest concepts come first (selection.ADAPTIVE_PICK);
- the NHprep lesson the request named comes first;
- NHprep-derived questions are only picked for an entitled student (selection.Pool.entitled).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.qbank import selection
from app.qbank.chat_tests import CLOSE, NEW_SET, SERVE, SERVED

LETTERS = "ABCDEFGH"
ANSWER_KEYS = text(
    """
    UPDATE qb_set_items SET chosen_keys = CAST(:chosen_keys AS text[])
    WHERE set_id = :set_id AND position = :position AND user_id = :user_id AND answered_at IS NULL
    RETURNING answered_at, is_correct
    """
)
LAST_BATCH = text(
    """
    SELECT s.id, s.filters, count(i.answered_at) AS answered, count(*) FILTER (WHERE i.is_correct) AS right
    FROM qb_sets s LEFT JOIN qb_set_items i ON i.set_id = s.id AND i.user_id = s.user_id
    WHERE s.id = :set_id AND s.user_id = :user_id AND s.mode = 'chat'
    GROUP BY s.id, s.filters
    """
)


@dataclass(frozen=True)
class Batch:
    """The questions picked for one test (rows of selection.ADAPTIVE_PICK, in order) and how they were asked."""

    rows: tuple[Any, ...]
    filters: dict


def levels(count: int, difficulty: str | None, accuracy: float | None = None) -> list[str | None]:
    """The difficulty of each place in a batch: the asked one everywhere, else a mix that leans easier after a
    weak batch (under 50% right) and harder after a strong one (80% or more). 5 → easy, easy, medium, medium, hard."""
    if difficulty is not None:
        return [difficulty] * count
    easy, hard = round(count * 0.35), round(count * 0.2)
    if accuracy is not None and accuracy >= 0.8:
        easy, hard = round(count * 0.2), round(count * 0.4)
    elif accuracy is not None and accuracy < 0.5:
        easy, hard = round(count * 0.5), 0
    medium = max(count - easy - hard, 0)
    return ["easy"] * easy + ["medium"] * medium + ["hard"] * hard


async def pick_batch(
    session: AsyncSession,
    user_id: uuid.UUID,
    pool: selection.Pool,
    seed: str,
    count: int,
    difficulty: str | None = None,
    accuracy: float | None = None,
    exclude: tuple[str, ...] = (),
) -> list[Any]:
    """Up to `count` questions for one test, none repeated (and none of `exclude`). A place whose difficulty has
    nothing left takes the nearest the pool has. Only reads; never commits."""
    rows: list[Any] = []
    for level in levels(count, difficulty, accuracy):
        taken = (*exclude, *(row.question_id for row in rows))
        row = await selection.pick_next(session, user_id, replace(pool, difficulty=level), seed, exclude=taken)
        if row is None and level is not None and difficulty is None:
            row = await selection.pick_next(session, user_id, replace(pool, difficulty=None), seed, exclude=taken)
        if row is None:
            break
        rows.append(row)
    return rows


async def open_set(session: AsyncSession, user_id: uuid.UUID, request_id: uuid.UUID, batch: Batch) -> uuid.UUID:
    """The student's practice set for this test, with its questions served in order (the reply's id is the
    request id: one set per reply). Never commits: it is saved with the reply."""
    values = {"user_id": user_id, "request_id": request_id, "filters": json.dumps(batch.filters, ensure_ascii=False)}
    total = len(batch.rows)
    set_id = await session.scalar(NEW_SET, {**values, "requested": total, "served": 0})
    for position, row in enumerate(batch.rows, 1):
        await session.execute(
            SERVE,
            {
                "set_id": set_id,
                "position": position,
                "user_id": user_id,
                "question_id": row.question_id,
                "topic_id": row.topic_id,
            },
        )
    await session.execute(SERVED, {"served": total, "requested": total, "set_id": set_id, "user_id": user_id})
    return set_id


async def answer_keys(
    session: AsyncSession, user_id: uuid.UUID, set_id: uuid.UUID, position: int, keys: list[str]
) -> tuple[datetime, bool] | None:
    """Record the letters chosen for the question at `position` (graded by the database from the key):
    (answered_at, is_correct), or None when it already has an answer. Never commits."""
    params = {"chosen_keys": sorted(keys), "set_id": set_id, "position": position, "user_id": user_id}
    found = (await session.execute(ANSWER_KEYS, params)).one_or_none()
    return (found.answered_at, found.is_correct) if found is not None else None


async def last_batch(session: AsyncSession, user_id: uuid.UUID, set_id: uuid.UUID) -> tuple[dict, float | None] | None:
    """A batch's filters (what was asked) and the share answered right (None before any answer)."""
    found = (await session.execute(LAST_BATCH, {"set_id": set_id, "user_id": user_id})).one_or_none()
    if found is None:
        return None
    return found.filters, (found.right / found.answered if found.answered else None)


async def close(session: AsyncSession, user_id: uuid.UUID, set_id: uuid.UUID) -> None:
    await session.execute(CLOSE, {"set_id": set_id, "user_id": user_id, "ended_early": False})


def keys_of(row: Any) -> list[str]:
    """A picked row's correct letters, whichever shape it is stored in."""
    return list(row.correct_keys) if row.correct_keys is not None else [row.correct_option]


def options_of(row: Any) -> list[str]:
    return list(row.options) if row.options is not None else [row.option_a, row.option_b, row.option_c, row.option_d]
