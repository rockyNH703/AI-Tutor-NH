"""A certification-bank test's record, built by the server from the stored questions (app/qbank/bank_tests.py).

The reply's ```quiz block holds only the questions and options as shown, never a key. The keys live in this
record and reach a client only once a question is final (state.public). The record is an ordinary schemaVersion 2
practice test: single-answer questions are kind 'mcq', multiple-answer ones 'multi' ("Choose N").
"""

from __future__ import annotations

import json
from typing import Any

from app.assessments.state import RECORD_MAX, _canonical, fingerprint, locator

LETTERS = "ABCDEFGH"


def _options(row: Any) -> list[str]:
    return list(row.options) if row.options is not None else [row.option_a, row.option_b, row.option_c, row.option_d]


def _keys(row: Any) -> list[int]:
    letters = list(row.correct_keys) if row.correct_keys is not None else [row.correct_option]
    return sorted(LETTERS.index(letter) for letter in letters)


def fence_body(rows: list[Any], title: str) -> str:
    """The reply's test block: what a client and the assistant may see before answering (no keys)."""
    shown = {
        "title": title,
        "bank": True,
        "questions": [
            {"id": row.question_id, "question": row.stem, "options": _options(row), "choose": len(_keys(row))}
            for row in rows
        ],
    }
    return json.dumps(shown, ensure_ascii=False, separators=(",", ":"))


def bank_record(rows: list[Any], *, title: str, body: str) -> dict | None:
    """The practice record for these questions, bound to the block whose body is `body`; None when it would be
    too big for a record (it never is with the card limits selection applies)."""
    questions = []
    for row in rows:
        options, keys = _options(row), _keys(row)
        question = {"text": row.stem, "options": options, "explanation": row.explanation}
        if row.question_format == "multi_select":
            questions.append({"kind": "multi", **question, "key": keys})
        else:
            questions.append({"kind": "mcq", **question, "correct": keys[0]})
    total = len(questions)
    record = {
        "schemaVersion": 2,
        "kind": "test",
        "position": 0,
        "title": title,
        "questions": questions,
        "total": total,
        "locator": locator(body),
        "responses": [],
        "mode": "practice",
        "scenario": None,
        "drafts": [None] * total,
        "attempts": [None] * total,
        "revision": 0,
        "submittedAt": None,
    }
    if len(_canonical(record).encode("utf-8")) > RECORD_MAX:
        return None
    record["hash"] = fingerprint(record)
    return record
