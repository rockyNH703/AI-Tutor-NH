"""How an author's practice block is read: the shared text checks, listings, the scenario and topology, and the
schemaVersion 1 questions (single choice, fill in the blanks). kinds.py builds every kind on these."""

from __future__ import annotations

import re
from typing import Any

LETTERS = "ABCDEFGH"
MAX_OPTIONS = 8
MAX_BLANKS = 20
LISTING_LINES = 200
LISTING_CHARS = 8000
_LETTER = re.compile(r"\(?([A-Ha-h])[).:]?")
# A blank in a fill-blank exercise, as the web app reads it: [[answer|alternative]]. A longer one is a
# malformed key (the exercise is refused whole), never left in the text.
_BLANK = re.compile(r"\[\[([^\]\n]{1,80})\]\]")
_LONG_BLANK = re.compile(r"\[\[[^\]\n]{81,}\]\]")
_LANGUAGE = re.compile(r"[A-Za-z0-9_+#.-]{1,40}")
_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,40}")


class Refused(Exception):
    """The response conflicts with the test as it is (409). `reason` is stable, for logs and clients."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Invalid(Exception):
    """No such question or option in this test, or a value that doesn't fit it (422). Its text is a reason code,
    never the value."""


# ------------------------------------------------------------------ shared readers
def text_ok(value: Any, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and "\x00" not in value


def optional_ok(value: Any, limit: int) -> bool:
    return value is None or text_ok(value, limit)


def explanation_ok(value: Any) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= 2000 and "\x00" not in value)


def quote(text: str, limit: int) -> str:
    """Text on one line, in quotes it cannot close, at most `limit` characters. User-typed text
    is only ever shown this way: information, never instructions (as in app/learning/tutor.py)."""
    flat = " ".join(str(text).split()).replace('"', "'")
    return f'"{flat if len(flat) <= limit else flat[: limit - 1] + "…"}"'


def listing_lines(content: str) -> list[str]:
    """A listing's lines, as the web app numbers them (ListingView): CR LF or CR read as LF, one trailing newline
    dropped."""
    text = re.sub(r"\r\n?", "\n", content)
    return (text[:-1] if text.endswith("\n") else text).split("\n")


def block_ok(value: Any) -> bool:
    return text_ok(value, LISTING_CHARS) and len(listing_lines(value)) <= LISTING_LINES


def parse_listing(raw: Any, *, labelled: bool = False) -> dict | None:
    """Code, a log or a config shown with line numbers: {title?, language?, content}, and a label for evidence."""
    if not isinstance(raw, dict):
        return None
    title, language, content = raw.get("title"), raw.get("language"), raw.get("content")
    if not block_ok(content) or not optional_ok(title, 200):
        return None
    if language is not None and not (isinstance(language, str) and _LANGUAGE.fullmatch(language)):
        return None
    listing = {"title": title, "language": language, "content": content}
    if labelled:
        if not text_ok(raw.get("label"), 80):
            return None
        listing["label"] = raw["label"]
    return listing


def parse_scenario(raw: Any) -> dict | None:
    """The quiz-level scenario shown above every question: {title?, summary, symptoms[], evidence[]}."""
    if not isinstance(raw, dict) or not text_ok(raw.get("summary"), 1000) or not optional_ok(raw.get("title"), 200):
        return None
    symptoms = raw.get("symptoms") if raw.get("symptoms") is not None else []
    evidence = raw.get("evidence") if raw.get("evidence") is not None else []
    if not isinstance(symptoms, list) or len(symptoms) > 8 or not all(text_ok(item, 200) for item in symptoms):
        return None
    if not isinstance(evidence, list) or len(evidence) > 4:
        return None
    items = [parse_listing(item, labelled=True) for item in evidence]
    if any(item is None for item in items):
        return None
    return {"title": raw.get("title"), "summary": raw["summary"], "symptoms": list(symptoms), "evidence": items}


def parse_topology(raw: Any) -> dict | None:
    """{nodes[{id, label, kind, detail?}], links[{id, from, to, label?, detail?}]}: ids distinct ignoring case,
    every link between two of its nodes."""
    if not isinstance(raw, dict):
        return None
    nodes, links = raw.get("nodes"), raw.get("links") if raw.get("links") is not None else []
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 30 or not isinstance(links, list) or len(links) > 40:
        return None
    shaped_nodes, shaped_links = [], []
    for node in nodes:
        if not (isinstance(node, dict) and isinstance(node.get("id"), str) and _ID.fullmatch(node["id"])):
            return None
        if not (
            text_ok(node.get("label"), 40) and text_ok(node.get("kind"), 40) and optional_ok(node.get("detail"), 300)
        ):
            return None
        shaped_nodes.append({key: node.get(key) for key in ("id", "label", "kind", "detail")})
    names = {node["id"] for node in shaped_nodes}
    for link in links:
        if not (isinstance(link, dict) and isinstance(link.get("id"), str) and _ID.fullmatch(link["id"])):
            return None
        if link.get("from") not in names or link.get("to") not in names:
            return None
        if not (optional_ok(link.get("label"), 40) and optional_ok(link.get("detail"), 300)):
            return None
        shaped_links.append({key: link.get(key) for key in ("id", "from", "to", "label", "detail")})
    ids = [item["id"].casefold() for item in shaped_nodes + shaped_links]
    return {"nodes": shaped_nodes, "links": shaped_links} if len(set(ids)) == len(ids) else None


def distinct_texts(values: Any, low: int, high: int, limit: int) -> bool:
    """A list of `low`-`high` texts of at most `limit` characters, no two the same ignoring case."""
    return (
        isinstance(values, list)
        and low <= len(values) <= high
        and all(text_ok(value, limit) for value in values)
        and len({value.strip().casefold() for value in values}) == len(values)
    )


# ------------------------------------------------------------------ schemaVersion 1 questions
def correct_option(answer: Any, options: list[str]) -> int | None:
    """The one option the answer names: a 0-based number, a letter ("B") or the option's own
    text. An answer that could mean two options means none."""
    if isinstance(answer, bool):
        return None
    if isinstance(answer, int):
        return answer if 0 <= answer < len(options) else None
    if not isinstance(answer, str) or len(answer) > 400:
        return None
    text = answer.strip().casefold()
    found = {index for index, option in enumerate(options) if option.strip().casefold() == text}
    letter = _LETTER.fullmatch(answer.strip())
    if letter and LETTERS.index(letter[1].upper()) < len(options):
        found.add(LETTERS.index(letter[1].upper()))
    return found.pop() if len(found) == 1 else None


def options_ok(options: Any) -> bool:
    return distinct_texts(options, 2, MAX_OPTIONS, 400)


def single_choice(raw: Any) -> dict | None:
    """A multiple-choice question in a ```quiz block's form (also the question bank's)."""
    if not isinstance(raw, dict):
        return None
    text, options, explanation = raw.get("question"), raw.get("options"), raw.get("explanation")
    if not text_ok(text, 1000) or not options_ok(options) or not explanation_ok(explanation):
        return None
    correct = correct_option(raw.get("answer"), options)
    if correct is None:
        return None
    return {
        "kind": "mcq",
        "text": text,
        "options": list(options),
        "correct": correct,
        "explanation": explanation if explanation and explanation.strip() else None,
    }


def fill_blank(spec: Any) -> dict | None:
    """A fill-in-the-blanks exercise, read the way the web app reads it: blanks `[[answer|alternative]]`
    of 1-80 characters, 1-20 of them, every alternative an accepted answer (none empty). The key is the
    tutor's: anything malformed refuses the exercise whole."""
    if not isinstance(spec, dict):
        return None
    text, explanation = spec.get("text"), spec.get("explanation")
    if not text_ok(text, 3000) or _LONG_BLANK.search(text) or not explanation_ok(explanation):
        return None
    segments: list[str] = []
    answers: list[list[str]] = []
    last = 0
    for match in _BLANK.finditer(text):
        accepted = [answer.strip() for answer in match[1].split("|")]
        if not all(accepted):  # an empty alternative, e.g. [[a|]]
            return None
        segments.append(text[last : match.start()])
        answers.append(accepted)
        last = match.end()
    segments.append(text[last:])
    if not 1 <= len(answers) <= MAX_BLANKS:
        return None
    return {
        "kind": "fill_blank",
        "segments": segments,
        "answers": answers,
        "explanation": explanation if explanation and explanation.strip() else None,
    }


def same_text(given: str, expected: str) -> bool:
    """The web app's rule (widgets/fill-blank.tsx): ignoring case, spacing and trailing `.;,!`."""

    def norm(value: str) -> str:
        return re.sub(r"[.;,!]+$", "", re.sub(r"\s+", " ", value.strip().lower()))

    return norm(given) == norm(expected)
