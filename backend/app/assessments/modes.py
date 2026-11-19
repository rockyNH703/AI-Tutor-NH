"""How a schemaVersion 2 test is answered (docs/api-contract.md, "Practice tests"). state.respond has already
checked the record, the reply and the hash the client was shown; these never change the record they are given.

- **Practice:** `answer` and `skip` (Show answer, 0 points), in order and final, as in schemaVersion 1. A numeric
  or CLI question allows 3 tries: a wrong try is kept in `attempts` (the record's `pending`) and reveals nothing;
  when the question becomes final its tries move into its response.
- **Mock exam:** `save` and `clear` keep an ungraded draft per question, in any order (last write wins);
  `submit` grades the drafts together and records the missing ones as skipped. Nothing is revealed before it.

A request that would not change the record (the same draft, clearing an empty one, a submit after submit, an
exact replay) returns it unchanged before any other rule. Every change bumps `revision`; a replay never does."""

from __future__ import annotations

import json
from typing import Any

from app.assessments.kinds import KINDS
from app.assessments.readers import Invalid, Refused
from app.logging import get_logger

log = get_logger("app.assessments")
DRAFTS_MAX = 64 * 1024  # bytes of a record's stored drafts (JSON)


def _replaced(items: list, index: int, value: Any) -> list:
    return [value if position == index else item for position, item in enumerate(items)]


def _changed(record: dict, **changes: Any) -> dict:
    return {**record, **changes, "revision": record["revision"] + 1}


def final(question: dict, index: int, value: Any, at: str, tries: list | None = None) -> dict:
    """A question's response: graded when it has a value, else skipped (0 points)."""
    kind = KINDS[question["kind"]]
    if value is None:
        status, earned = "skipped", 0
        marks = [False] * len(question["answers"]) if question["kind"] == "fill_blank" else None
    else:
        status, (earned, marks) = "answered", kind.grade(question, value)
    response = {
        "question": index,
        "status": status,
        "value": value if kind.legacy else None,
        "possible": kind.possible(question),
        "at": at,
        "earned": earned,
        "marks": marks,
    }
    if value is not None and not kind.legacy:
        response["answer"] = value
    return response | {"tries": tries} if tries else response


def _replay(done: dict, value: Any, legacy: bool) -> bool:
    """Whether an answer or skip repeats what this final question already recorded (or one of its tries)."""
    if value is None:
        return done["status"] == "skipped"
    recorded = done["value"] if legacy else done.get("answer")
    return done["status"] == "answered" and (recorded == value or value in (done.get("tries") or []))


def practice(record: dict, *, question: int, action: str, value: Any, at: str) -> tuple[dict, bool]:
    if action not in ("answer", "skip"):
        raise Refused("not_exam")
    item, responses = record["questions"][question], record["responses"]
    kind = KINDS[item["kind"]]
    given = kind.check(item, value) if action == "answer" else None
    if question < len(responses):
        if _replay(responses[question], given, kind.legacy):
            return record, False
        raise Refused("answered")
    if question != len(responses):
        raise Refused("out_of_order")
    tried = record["attempts"][question] or []
    if given is not None and given in tried:
        return record, False  # a wrong try sent again
    if given is not None and len(tried) + 1 < kind.tries and kind.grade(item, given)[0] < kind.possible(item):
        return _changed(record, attempts=_replaced(record["attempts"], question, [*tried, given])), True
    response = final(item, question, given, at, tried)
    return _changed(
        record, responses=[*responses, response], attempts=_replaced(record["attempts"], question, None)
    ), True


def _submitted(question: dict, index: int, draft: Any, at: str) -> dict:
    if draft is None:
        return final(question, index, None, at)
    try:
        value = KINDS[question["kind"]].check(question, draft)
    except Exception:  # noqa: BLE001 - a draft stored before a rule changed earns 0; submit never fails on it
        log.warning("assessment draft unreadable", extra={"question": index, "kind": question["kind"]})
        return final(question, index, None, at)
    return final(question, index, value, at)


def exam(record: dict, *, question: int, action: str, value: Any, revision: int | None, at: str) -> tuple[dict, bool]:
    submitted = record.get("submittedAt") is not None
    if action == "submit":
        if submitted:
            return record, False
        if revision is not None and revision != record["revision"]:
            raise Refused("stale")
        drafts = record["drafts"]
        responses = [_submitted(item, index, drafts[index], at) for index, item in enumerate(record["questions"])]
        return _changed(record, responses=responses, submittedAt=at), True
    if action not in ("save", "clear"):
        raise Refused("exam")
    item = record["questions"][question]
    try:
        draft = KINDS[item["kind"]].check(item, value) if action == "save" else None
    except Invalid:
        if submitted:  # the exam is closed: that is the answer, whatever the value
            raise Refused("submitted") from None
        raise
    if record["drafts"][question] == draft:
        return record, False
    if submitted:
        raise Refused("submitted")
    drafts = _replaced(record["drafts"], question, draft)
    if len(json.dumps(drafts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > DRAFTS_MAX:
        raise Invalid("drafts_full")
    return _changed(record, drafts=drafts), True


def exam_in_progress(record: dict) -> bool:
    return record.get("schemaVersion") == 2 and record.get("mode") == "exam" and not record.get("submittedAt")


def points(record: dict) -> tuple[int, int, int, int]:
    """(earned, possible, questions fully right, questions partly right) of the recorded responses."""
    earned = sum(response["earned"] for response in record["responses"])
    possible = sum(KINDS[question["kind"]].possible(question) for question in record["questions"])
    fully = sum(1 for r in record["responses"] if r["earned"] and r["earned"] == r["possible"])
    partly = sum(1 for r in record["responses"] if 0 < r["earned"] < r["possible"])
    return earned, possible, fully, partly
