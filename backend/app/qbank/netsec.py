"""The NetSec certification bank's file checks (curriculum 'netsec'), used by validate.read_bank.

A book is a certification track, its chapters exam domains, its topics canonical topics that NHprep lessons map to.
A question is single-answer ('mcq') or multiple-answer ('multi_select'), with 2-8 options and its keys as letters.

Rules that differ from the CBSE ones:
- An agent-written key ('authored') is served only when the blind check agreed on it three ways: from the notes,
  closed-book, and only one option defensible.
- NHprep's own questions keep their original text. They are exempt from the authored-style rules (all/none of the
  above, explanation length) but not from the structure.
- Titles carry no newline, fence or directive marker: they go into the app's fixed replies.
No database and no model.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

FAMILIES = ("routing_switching", "security", "cloud", "automation", "wireless", "data_center", "linux", "ai", "general")
TIERS = range(1, 4)  # 1 associate, 2 professional, 3 expert
LETTERS = "ABCDEFGH"
FORMATS = ("mcq", "multi_select")
SOURCE_KINDS = ("nhprep_quiz", "nhprep_lesson", "original")
KEY_ORIGINS = ("nhprep", "authored")
BOOK_ID = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
LESSON_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
UNSAFE_TITLE = re.compile(r"[\r\n`]|::|\[\[|\]\(|<")
NONE_ALL = re.compile(r"\b(?:all|none|both|neither) of the (?:above|options)\b", re.I)
CHOOSE = re.compile(r"\((?:choose|select)\s+(two|three|four|2|3|4)\)", re.I)
CHOOSE_N = {"two": 2, "three": 3, "four": 4, "2": 2, "3": 3, "4": 4}
FIELDS = (
    "question_id",
    "chapter_id",
    "topic_id",
    "concept_tag",
    "difficulty",
    "format",
    "competency",
    "stem",
    "options",
    "correct",
    "explanation",
    "marks",
    "source",
    "lesson_ref",
    "key_origin",
)
EXTRA = ("review", "active", "inactive_reason", "verification", "_where")


def _text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and "\x00" not in value


def _when(value: Any) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def safe_title(value: Any, limit: int = 200) -> bool:
    return _text(value, limit) and not UNSAFE_TITLE.search(value)


def normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w./+-]+", " ", text.casefold()).split())


def content_hash(question: dict[str, Any], *, syllabus_version: str) -> str:
    """Everything a reviewer approves: the question as written (its keys included) and its place."""
    canonical = json.dumps(
        {"question": {name: question.get(name) for name in FIELDS}, "language": "english", "version": syllabus_version},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_vocabulary(root: Path) -> tuple[list[list[str]], list[str]]:
    """Groups of terms that name one thing (an acronym and its expansions): qbank/vocabulary/netsec.json."""
    path = root / "vocabulary" / "netsec.json"
    if not path.exists():
        return [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [], [f"vocabulary/netsec.json: unreadable JSON ({error})"]
    groups, errors = [], []
    for number, group in enumerate(data.get("groups", []) if isinstance(data, dict) else [], 1):
        if not isinstance(group, list) or len(group) < 2 or not all(safe_title(term, 80) for term in group):
            errors.append(f"vocabulary/netsec.json: group {number} needs two or more short terms")
            continue
        groups.append([normal(term) for term in group])
    return groups, errors


def topic_search(topic: dict, chapter: dict, book: dict, concepts: list[dict], groups: list[list[str]]) -> dict:
    """What the finder reads for a topic: aliases (with every vocabulary group a title or alias names),
    lessons, search_title (title + aliases: weight A) and search_doc (weight B)."""
    aliases = [normal(alias) for alias in topic.get("aliases") or []]
    names = {normal(topic["title"]), *aliases}
    for group in groups:  # only a term the topic itself is named by: "vlan" in a title isn't the VLANs topic
        if names.intersection(group):
            aliases += group
    aliases = list(dict.fromkeys(alias for alias in aliases if alias))
    lessons = [
        {"ref": lesson["ref"], "title": lesson["title"], "course": lesson.get("course")}
        for lesson in topic.get("lessons") or []
    ]
    doc = [
        book["title"],
        book.get("edition", ""),
        chapter["title"],
        *(topic.get("paraphrases") or []),
        *(f"{lesson['title']} {lesson.get('course') or ''}" for lesson in lessons),
        *(concept["learning_objective"] for concept in concepts),
    ]
    return {
        "aliases": aliases,
        "lessons": lessons,
        "search_title": " | ".join([topic["title"], *aliases]),
        "search_doc": "\n".join(part for part in doc if part),
    }


def check_topic(topic: dict, where: str) -> list[str]:
    errors = []
    topic_id = topic["topic_id"]
    if not safe_title(topic.get("title")):
        errors.append(f"{where}: topic {topic_id} needs a title without newlines, fences or markup")
    aliases = topic.get("aliases") or []
    if not isinstance(aliases, list) or len(aliases) > 40 or not all(safe_title(a, 80) for a in aliases):
        errors.append(f"{where}: topic {topic_id} aliases must be up to 40 short plain terms")
    paraphrases = topic.get("paraphrases") or []
    if not isinstance(paraphrases, list) or len(paraphrases) > 20 or not all(safe_title(p, 200) for p in paraphrases):
        errors.append(f"{where}: topic {topic_id} paraphrases must be up to 20 plain lines")
    lessons = topic.get("lessons") or []
    if not isinstance(lessons, list) or not all(
        isinstance(lesson, dict)
        and isinstance(lesson.get("ref"), str)
        and LESSON_REF.match(lesson["ref"])
        and safe_title(lesson.get("title"))
        and (lesson.get("course") is None or safe_title(lesson.get("course")))
        for lesson in lessons
    ):
        errors.append(f"{where}: topic {topic_id} lessons need a ref, a plain title and an optional course")
    return errors


def check_book(book: dict, version: dict, where: str) -> list[str]:
    errors = []
    book_id = book.get("book_id")
    if version.get("curriculum") != "netsec":
        errors.append(f"{where}: a netsec book needs a netsec syllabus_version")
    if not isinstance(book_id, str) or not BOOK_ID.match(book_id):
        errors.append(f"{where}: a netsec book needs a book_id like ccna or ccnp_enterprise")
    if book.get("class_level") not in TIERS:
        errors.append(f"{where}: book {book_id} needs a class_level (tier) from 1 to 3")
    if book.get("subject_code") not in FAMILIES:
        errors.append(f"{where}: book {book_id} needs a subject_code (family): {', '.join(FAMILIES)}")
    if book.get("language") != "english":
        errors.append(f"{where}: book {book_id} must be in english")
    if not safe_title(book.get("title")) or not safe_title(book.get("edition")):
        errors.append(f"{where}: book {book_id} needs a plain title and edition")
    return errors


def _keys(question: dict, options: list, prefix: str, errors: list[str]) -> list[str] | None:
    correct, form = question.get("correct"), question.get("format")
    if not isinstance(correct, list) or not all(isinstance(k, str) for k in correct):
        errors.append(f"{prefix}: correct must be a list of letters")
        return None
    letters = LETTERS[: len(options)]
    if len(set(correct)) != len(correct) or not correct or any(k not in letters for k in correct):
        errors.append(f"{prefix}: correct must be distinct letters among {', '.join(letters)}")
        return None
    if form == "mcq" and len(correct) != 1:
        errors.append(f"{prefix}: an mcq has exactly one correct letter")
    if form == "multi_select" and not 2 <= len(correct) < len(options):
        errors.append(f"{prefix}: a multi_select has at least two correct letters and fewer than its options")
    return sorted(correct)


def check_question(question: dict, book: dict, prefix: str) -> tuple[dict | None, list[str], str]:
    """The checked row's netsec fields, the errors, and the content hash (or "" when it can't be made)."""
    errors: list[str] = []
    unknown = set(question) - {*FIELDS, *EXTRA}
    if unknown:
        errors.append(f"{prefix}: unknown fields {sorted(unknown)}")
    if question.get("format") not in FORMATS:
        errors.append(f"{prefix}: format must be mcq or multi_select")
    source = question.get("source")
    kind = source.get("kind") if isinstance(source, dict) else None
    if kind not in SOURCE_KINDS or not _text(source.get("ref"), 500):
        errors.append(f"{prefix}: source needs a kind ({', '.join(SOURCE_KINDS)}) and a ref")
        return None, errors, ""
    options, stem, explanation = question.get("options"), question.get("stem"), question.get("explanation")
    if not isinstance(options, list) or not 2 <= len(options) <= 8 or not all(_text(o, 500) for o in options):
        errors.append(f"{prefix}: 2-8 options, each 1-500 characters")
        return None, errors, ""
    if len({normal(o) for o in options}) != len(options):
        errors.append(f"{prefix}: two options are the same")
    keys = _keys(question, options, prefix, errors)
    if not _text(stem, 2000) or not _text(explanation, 3000):
        errors.append(f"{prefix}: a stem (up to 2000 characters) and an explanation (up to 3000)")
        return None, errors, ""
    original = kind == "nhprep_quiz"
    if not original:
        if NONE_ALL.search(stem) or any(NONE_ALL.search(option) for option in options):
            errors.append(f"{prefix}: no 'all/none of the above' in a written question")
        if len(explanation.strip()) < 40:
            errors.append(f"{prefix}: a written question's explanation needs at least 40 characters")
    asked = CHOOSE.search(stem)
    if keys is not None and asked and CHOOSE_N[asked.group(1).lower()] != len(keys):
        errors.append(f"{prefix}: the stem says '{asked.group(0)}' but there are {len(keys)} correct letters")
    if question.get("format") == "multi_select" and keys is not None and not asked:
        errors.append(f"{prefix}: a multi_select stem says how many to pick, e.g. '(Choose two)'")
    origin = question.get("key_origin")
    if origin not in KEY_ORIGINS:
        errors.append(f"{prefix}: key_origin must be nhprep or authored")
    lesson_ref = question.get("lesson_ref")
    if lesson_ref is not None and not (isinstance(lesson_ref, str) and LESSON_REF.match(lesson_ref)):
        errors.append(f"{prefix}: lesson_ref must be an NHprep lesson id")
    active = question.get("active", True)
    if not isinstance(active, bool):
        errors.append(f"{prefix}: active must be true or false")
    elif not active and not _text(question.get("inactive_reason"), 300):
        errors.append(f"{prefix}: an inactive question says why (inactive_reason)")
    elif active and origin == "authored" and not verified(question.get("verification")):
        errors.append(f"{prefix}: an authored key is served only after the three-way blind check agreed")
    if errors or keys is None:
        return None, errors, ""
    row = {
        "options": list(options),
        "correct_keys": keys,
        "lesson_ref": lesson_ref,
        "key_origin": origin,
        "source_kind": kind,
        "source_ref": source["ref"],
        "source_url": source.get("url"),
    }
    return row, errors, content_hash(question, syllabus_version=book["syllabus_version"])


def verified(check: Any) -> bool:
    """The blind check agreed three ways: from the lesson's notes, closed-book, and one defensible answer."""
    return (
        isinstance(check, dict)
        and check.get("notes") == "agree"
        and check.get("closed_book") == "agree"
        and check.get("single_defensible") is True
        and _text(check.get("by"), 200)
        and _when(check.get("at"))
    )
