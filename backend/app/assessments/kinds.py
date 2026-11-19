"""Each kind of question a practice test may hold (docs/api-contract.md, "Practice tests"): how the author writes
it (parse), what a user may send for it (check), how it is graded (grade), what is shown before and once it is
final, and how the assistant is told about it. app/assessments/state.py dispatches here; the grading rules
themselves are in rules.py.

A question's keys (`correct`, `key`, `answers`, `answer`, `keyPoints`...) stay in the stored record and reach a
client only once the question is final (practice) or the test is submitted (exam)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from app.assessments import rules
from app.assessments.readers import (
    LETTERS,
    Invalid,
    block_ok,
    correct_option,
    distinct_texts,
    explanation_ok,
    fill_blank,
    listing_lines,
    optional_ok,
    options_ok,
    parse_listing,
    parse_scenario,
    parse_topology,
    quote,
    same_text,
    single_choice,
    text_ok,
)

MODES = ("practice", "exam")
# What public() adds to every question for the newer kinds: null wherever a kind does not use it.
EXTRA_KEYS = (
    "choose", "unit", "prompt", "left", "right", "items", "listing", "diagram", "select", "points", "maxLength",
    "maxTries", "draft", "correctList", "accepted", "tolerance", "keyPoints", "sample", "output",
)  # fmt: skip


# ------------------------------------------------------------------ value checks (422 reasons)
def _indices(value: Any, limit: int, *, nullable: bool = False) -> list:
    if not isinstance(value, list):
        raise Invalid("value")
    for item in value:
        if item is None and nullable:
            continue
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < limit:
            raise Invalid("value")
    return list(value)


def _distinct(values: list) -> list:
    if len(set(values)) != len(values):
        raise Invalid("duplicate")
    return sorted(values)


def _raise(reason: str) -> Any:
    raise Invalid(reason)


def _string(value: Any, limit: int) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise Invalid("value")
    if len(value) > limit:
        raise Invalid("too_long")
    return value


def _blanks(question: dict, value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) != len(question["answers"]):
        raise Invalid("value")
    return [_string(given, 200) for given in value]


def _letters(indices: list[int]) -> str:
    return ", ".join(LETTERS[index] for index in indices) or "none"


# ------------------------------------------------------------------ the kinds
class Where(NamedTuple):
    locator: str  # which block of the reply: seeds the shown order
    index: int  # the question's place in the test
    listing: dict | None


def _multi(raw: dict, where: Where) -> dict | None:
    options, answer = raw.get("options"), raw.get("answer")
    if not options_ok(options) or not isinstance(answer, list) or not answer:
        return None
    key = [correct_option(item, options) for item in answer]
    if None in key or len(set(key)) != len(key):
        return None
    return {"options": list(options), "key": sorted(key)}  # type: ignore[type-var]  # no None left


def _numeric(raw: dict, where: Where) -> dict | None:
    answer = rules.fixed_point(raw.get("answer"))
    tolerance = rules.tolerance_of(raw.get("tolerance"), answer) if answer is not None else None
    if tolerance is None or not optional_ok(raw.get("unit"), 20):
        return None
    return {"answer": answer, "tolerance": tolerance, "unit": raw.get("unit")}


def _written(points_max: int, length: int) -> Callable[[dict, Where], dict | None]:
    def parse(raw: dict, where: Where) -> dict | None:
        points, sample = raw.get("keyPoints"), raw.get("sample")
        if not isinstance(points, list) or not 1 <= len(points) <= points_max or not optional_ok(sample, length):
            return None
        alternatives = [[point] if isinstance(point, str) else point for point in points]
        seen: set[str] = set()
        for forms in alternatives:
            if not isinstance(forms, list) or not 1 <= len(forms) <= 8 or not all(text_ok(f, 200) for f in forms):
                return None
            normalised = {rules.normalise(form) for form in forms}
            if any(not 2 <= len(form) <= 80 for form in normalised) or normalised & seen:
                return None  # 'a' matches almost anything; one form in two points scores twice
            seen |= normalised
        return {"keyPoints": [list(forms) for forms in alternatives], "sample": sample}

    return parse


def _match(raw: dict, where: Where) -> dict | None:
    pairs, extra = raw.get("pairs"), raw.get("extra") if raw.get("extra") is not None else []
    if not isinstance(pairs, list) or not 2 <= len(pairs) <= 8 or not isinstance(extra, list) or len(extra) > 3:
        return None
    if not all(isinstance(pair, list) and len(pair) == 2 for pair in pairs):
        return None
    left, right = [pair[0] for pair in pairs], [pair[1] for pair in pairs] + extra
    if not distinct_texts(left, 2, 8, 200) or not distinct_texts(right, 2, 11, 200):
        return None
    order = rules.shown_order(where.locator, where.index, len(right), len(left))
    return {"left": left, "right": [right[i] for i in order], "key": [order.index(i) for i in range(len(left))]}


def _order(raw: dict, where: Where) -> dict | None:
    items = raw.get("items")
    if not distinct_texts(items, 3, 8, 200):
        return None
    order = rules.shown_order(where.locator, where.index, len(items), len(items))
    return {"items": [items[i] for i in order], "key": [order.index(i) for i in range(len(items))]}


def _cli(raw: dict, where: Where) -> dict | None:
    prompt, answers, output = raw.get("prompt"), raw.get("answers"), raw.get("output")
    if not text_ok(prompt, 60) or not isinstance(answers, list) or not 1 <= len(answers) <= 8:
        return None
    if not all(text_ok(form, 300) and "\n" not in form and "\r" not in form for form in answers):
        return None
    if output is not None and not block_ok(output):
        return None
    return {"prompt": prompt, "answers": list(answers), "output": output}


def _lines(raw: dict, where: Where) -> dict | None:
    answer = raw.get("answer")
    if where.listing is None or not isinstance(answer, list) or not answer:
        return None
    count = len(listing_lines(where.listing["content"]))
    if not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= count for n in answer):
        return None
    return {"key": sorted(n - 1 for n in answer)} if len(set(answer)) == len(answer) else None


def _selectable(question: dict) -> set[str]:
    diagram, select = question["diagram"], question["select"]
    return {node["id"] for node in diagram["nodes"] if select != "link"} | {
        link["id"] for link in diagram["links"] if select != "node"
    }


def _diagram(raw: dict, where: Where) -> dict | None:
    diagram, select, answer = parse_topology(raw.get("topology")), raw.get("select", "any"), raw.get("answer")
    if diagram is None or select not in ("node", "link", "any") or not isinstance(answer, list) or not answer:
        return None
    question = {"diagram": diagram, "select": select, "key": answer}
    if not all(isinstance(item, str) for item in answer) or len(set(answer)) != len(answer):
        return None
    return {**question, "key": sorted(answer)} if set(answer) <= _selectable(question) else None


def _numbers(indices: list[int]) -> str:
    return ", ".join(str(index + 1) for index in indices) or "none"


@dataclass(frozen=True)
class Kind:
    parse: Callable[[dict, Where], dict | None]  # the kind's own fields (text, listing and explanation are shared)
    check: Callable[[dict, Any], Any]  # the value in its canonical form, or Invalid(reason)
    grade: Callable[[dict, Any], tuple[int, list[bool] | None]]  # (earned, marks) of a checked value
    possible: Callable[[dict], int]
    shown: Callable[[dict], dict]  # extra public fields before it is final
    revealed: Callable[[dict], dict]  # extra public fields once it is final
    given: Callable[[dict, dict], str]  # what an answered response gave, for the assistant (about 30 characters)
    right: Callable[[dict], str]  # the right answer, for the assistant
    tries: int = 1  # practice: tries before the answer is shown
    legacy: bool = False  # its value is the response's `value` (schemaVersion 1 shapes); else `answer`


def _none(question: dict) -> dict:
    return {}


def _one(question: dict) -> int:
    return 1


def _same_set(question: dict, value: list) -> tuple[int, list[bool] | None]:
    return int(value == question["key"]), None


def _per_item(question: dict, value: list, size: int) -> list[bool]:
    return [(index in value) == (index in question["key"]) for index in range(size)]


def _wrote(question: dict, response: dict) -> str:
    missed = [forms[0] for forms, mark in zip(question["keyPoints"], response["marks"], strict=True) if not mark]
    return f"wrote {quote(response['answer'], 80)}; missed key points " + (
        ", ".join(quote(point, 30) for point in missed[:3]) or "none"
    )


def _written_kind(points: int, length: int) -> Kind:
    return Kind(
        parse=_written(points, length),
        check=lambda q, v: _string(v, length),
        grade=lambda q, v: (sum(m := rules.key_points_found(v, q["keyPoints"])), m),
        possible=lambda q: len(q["keyPoints"]),
        shown=lambda q: {"points": len(q["keyPoints"]), "maxLength": length},
        revealed=lambda q: {"keyPoints": [forms[0] for forms in q["keyPoints"]], "sample": q["sample"]},
        given=_wrote,
        right=lambda q: "key points " + ", ".join(quote(forms[0], 30) for forms in q["keyPoints"][:3]),
    )


KINDS: dict[str, Kind] = {
    "mcq": Kind(
        parse=lambda raw, where: single_choice(raw),  # the whole question (schemaVersion 1 reader)
        check=lambda q, v: _indices([v], len(q["options"]))[0],
        grade=lambda q, v: (int(v == q["correct"]), None),
        possible=_one,
        shown=_none,
        revealed=_none,
        given=lambda q, r: f"chose {quote(q['options'][r['value']], 30)}",
        right=lambda q: quote(q["options"][q["correct"]], 30),
        legacy=True,
    ),
    "fill_blank": Kind(
        parse=lambda raw, where: fill_blank(raw),  # the whole question (schemaVersion 1 reader)
        check=_blanks,
        grade=lambda q, v: (
            sum(m := [any(same_text(g, a) for a in acc) for g, acc in zip(v, q["answers"], strict=True)]),
            m,
        ),
        possible=lambda q: len(q["answers"]),
        shown=_none,
        revealed=_none,
        given=lambda q, r: f"typed {quote(' / '.join(r['value']), 30)}",
        right=lambda q: quote(" / ".join(accepted[0] for accepted in q["answers"]), 30),
        legacy=True,
    ),
    "multi": Kind(
        parse=_multi,
        check=lambda q, v: _distinct(_indices(v, len(q["options"]))),
        grade=lambda q, v: (int(v == q["key"]), _per_item(q, v, len(q["options"]))),
        possible=_one,
        shown=lambda q: {"choose": len(q["key"])},
        revealed=lambda q: {"correctList": q["key"]},
        given=lambda q, r: f"chose {_letters(r['answer'])}",
        right=lambda q: _letters(q["key"]),
    ),
    "numeric": Kind(
        parse=_numeric,
        check=lambda q, v: v if isinstance(v, str) and rules.NUMBER.fullmatch(v) else _raise("number"),
        grade=lambda q, v: (int(rules.within(v, q["answer"], q["tolerance"])), None),
        possible=_one,
        shown=lambda q: {"unit": q["unit"]},
        revealed=lambda q: {"accepted": [q["answer"]], "tolerance": q["tolerance"]},
        given=lambda q, r: f"gave {quote(r['answer'], 30)}",
        right=lambda q: quote(
            q["answer"] + ("" if q["tolerance"] == "0" else f" ± {q['tolerance']}") + f" {q['unit'] or ''}", 30
        ),
        tries=3,
    ),
    "short": _written_kind(6, 1000),
    "long": _written_kind(10, 6000),
    "match": Kind(
        parse=_match,
        check=lambda q, v: (
            _indices(v, len(q["right"]), nullable=True)
            if isinstance(v, list) and len(v) == len(q["left"])
            else _raise("value")
        ),
        grade=lambda q, v: (sum(m := [a == b for a, b in zip(v, q["key"], strict=True)]), m),
        possible=lambda q: len(q["left"]),
        shown=lambda q: {"left": q["left"], "right": q["right"]},
        revealed=lambda q: {"correctList": q["key"]},
        given=lambda q, r: f"{sum(r['marks'])} of {len(q['left'])} pairs right",
        right=lambda q: quote(
            "; ".join(f"{a} = {q['right'][k]}" for a, k in zip(q["left"], q["key"], strict=True)), 30
        ),
    ),
    "order": Kind(
        parse=_order,
        check=lambda q, v: (
            v if sorted(_indices(v, len(q["items"]))) == list(range(len(q["items"]))) else _raise("value")
        ),
        grade=lambda q, v: (int(v == q["key"]), [a == b for a, b in zip(v, q["key"], strict=True)]),
        possible=_one,
        shown=lambda q: {"items": q["items"]},
        revealed=lambda q: {"correctList": q["key"]},
        given=lambda q, r: f"{sum(r['marks'])} of {len(q['items'])} in place",
        right=lambda q: quote(", ".join(q["items"][k] for k in q["key"]), 30),
    ),
    "cli": Kind(
        parse=_cli,
        check=lambda q, v: _string(v, 300),
        grade=lambda q, v: (int(rules.command_matches(v, q["answers"])), None),
        possible=_one,
        shown=lambda q: {"prompt": q["prompt"]},
        revealed=lambda q: {"accepted": q["answers"], "output": q["output"]},
        given=lambda q, r: f"typed {quote(r['answer'], 30)}",
        right=lambda q: quote(q["answers"][0], 30),
        tries=3,
    ),
    "lines": Kind(
        parse=_lines,
        check=lambda q, v: _distinct(_indices(v, len(listing_lines(q["listing"]["content"])))),
        grade=lambda q, v: (int(v == q["key"]), _per_item(q, v, len(listing_lines(q["listing"]["content"])))),
        possible=_one,
        shown=_none,
        revealed=lambda q: {"correctList": q["key"]},
        given=lambda q, r: f"chose lines {_numbers(r['answer'])}",
        right=lambda q: f"lines {_numbers(q['key'])}",
    ),
    "diagram": Kind(
        parse=_diagram,
        check=lambda q, v: (
            _distinct(v)
            if isinstance(v, list) and all(isinstance(item, str) and item in _selectable(q) for item in v)
            else _raise("value")
        ),
        grade=_same_set,
        possible=_one,
        shown=lambda q: {"diagram": q["diagram"], "select": q["select"]},
        revealed=lambda q: {"accepted": q["key"]},
        given=lambda q, r: f"chose {quote(', '.join(r['answer']), 30)}",
        right=lambda q: quote(", ".join(q["key"]), 30),
    ),
}
# The author's `type` for each newer kind; an absent or unknown type is a single-choice question (v1).
TYPES = {
    "multi": "multi", "fill": "fill_blank", "numeric": "numeric", "short": "short", "long": "long",
    "match": "match", "order": "order", "cli": "cli", "lines": "lines", "diagram": "diagram",
}  # fmt: skip


def kind_of(raw: dict) -> str:
    if raw.get("select") == "lines":
        return "lines"
    kind = raw.get("type")
    return TYPES.get(kind, "mcq") if isinstance(kind, str) else "mcq"


def is_v2(spec: dict) -> bool:
    """Whether a ```quiz uses anything beyond the schemaVersion 1 shape: a mode, a scenario, a newer type or a
    listing. A v1 quiz keeps today's record and hash exactly."""
    return (
        spec.get("mode") is not None
        or spec.get("scenario") is not None
        or any(
            isinstance(raw, dict) and (kind_of(raw) != "mcq" or raw.get("listing") is not None)
            for raw in spec["questions"]
        )
    )


def quiz_head(spec: dict) -> tuple[str, dict | None] | None:
    """A v2 quiz's mode (practice by default) and scenario, or None when either is malformed."""
    mode = spec.get("mode") if spec.get("mode") is not None else "practice"
    scenario = parse_scenario(spec["scenario"]) if spec.get("scenario") is not None else None
    if mode not in MODES or (spec.get("scenario") is not None and scenario is None):
        return None
    return mode, scenario


def parse_question(raw: Any, locator: str, index: int) -> dict | None:
    """One question of a v2 quiz, as stored: its kind's fields, an optional listing and explanation."""
    if not isinstance(raw, dict):
        return None
    kind = kind_of(raw)
    listing = parse_listing(raw["listing"]) if raw.get("listing") is not None else None
    if raw.get("listing") is not None and listing is None:
        return None
    body = KINDS[kind].parse(raw, Where(locator, index, listing))
    if kind in ("mcq", "fill_blank"):
        question = body
    else:
        explanation = raw.get("explanation")
        if body is None or not text_ok(raw.get("question"), 1000) or not explanation_ok(explanation):
            return None
        question = {
            "kind": kind,
            "text": raw["question"],
            **body,
            "explanation": explanation if explanation and explanation.strip() else None,
        }
    if question is None:
        return None
    return {**question, "listing": listing} if listing is not None else question


# ------------------------------------------------------------------ what a client and the assistant see
def public_response(response: dict | None) -> dict | None:
    if response is None:
        return None
    shown = {key: response[key] for key in ("status", "value", "earned", "possible", "at")}
    shown["marks"] = response.get("marks")
    # Newer kinds only: absent (not null) otherwise, so a v1 record's responses keep their exact shape.
    return shown | {key: response[key] for key in ("answer", "tries") if response.get(key) is not None}


def public_question(
    question: dict, response: dict | None, *, v2: bool, draft: Any = None, practice: bool = False
) -> dict:
    """One question as a client may show it: every field present (null where its kind doesn't use it), keys only
    once it has a response. A schemaVersion 1 record's questions keep exactly their v1 fields."""
    kind, blank, final = KINDS[question["kind"]], question["kind"] == "fill_blank", response is not None
    shown = {
        "kind": question["kind"],
        "text": None if blank else question["text"],
        "options": None if blank else question.get("options"),
        "segments": question["segments"] if blank else None,
        "blanks": len(question["answers"]) if blank else None,
        "response": public_response(response),
        "correct": question["correct"] if final and question["kind"] == "mcq" else None,
        "answers": question["answers"] if blank and final else None,
        "explanation": question.get("explanation") if final else None,
    }
    if not v2:
        return shown
    shown |= {
        **dict.fromkeys(EXTRA_KEYS),
        "listing": question.get("listing"),
        "draft": draft,
        "maxTries": kind.tries if practice and kind.tries > 1 else None,
        **kind.shown(question),
    }
    return shown | kind.revealed(question) if final else shown


def model_question(question: dict, *, practice: bool) -> dict:
    """What the assistant is shown of a question: exactly what a client sees before answering it."""
    shown = public_question(question, None, v2=True, practice=practice)
    return {key: value for key, value in shown.items() if value is not None}
