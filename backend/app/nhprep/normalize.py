"""From validated export v1 records to the rows and search texts NetSec stores. Pure: no database, no provider.

Normalisation (every change counted, never logged as text):
- invisible, bidi and control characters are stripped (U+200B, U+202E, NUL, ...);
- fenced-code info strings are mapped to an allowlist (```video, ```quiz, ```topology become ```text), so imported
  Markdown can never carry a tutor widget;
- every line starting with `::` (also inside `>` quotes and list items) is escaped, so no directive survives;
- every nhprep.com URL (any subdomain, also URL-shaped text and reference definitions) becomes plain text.
Trainers are resolved only through TRAINER_ALIASES; anything else is unknown (never "Admin").
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db.models import NHPREP_ITEM_KEY
from app.nhprep.validate import CourseRecord, LessonRecord
from app.nhprep.vectors import text_key

# Owner-confirmable (spec B5): lowercase letters-only token -> trainer. Honorific forms included ("Mr.AJAY" -> mrajay).
TRAINER_ALIASES = {
    "ajay": "Ajay",
    "mrajay": "Ajay",
    "sushil": "Sushil",
    "mrsushil": "Sushil",
    "amit": "Amit",
    "mohnish": "Mohnish",
    "nikunj": "Nikunj",
    "vishal": "Vishal",
    "chetan": "Chetan",
}
# Explicit course slug -> track (plan §8.1); a course not listed has no track.
COURSE_TRACKS = {
    "ccna-by-mrajay": "ccna",
    "tamil-ccna": "ccna",
    "ccnp-routing-by-nikunj": "ccnp",
    "ccnp-sd-wan-mohnish": "ccnp",
    "ise-by-sushil": "security",
    "vpn-amit": "security",
    "ceh-b30": "security",
}
FENCE_LANGUAGES = frozenset(
    "text plaintext txt bash sh shell zsh console powershell ps1 cmd bat cisco ios json yaml yml xml html css "
    "javascript js typescript ts python py go java c cpp csharp sql ini conf toml diff dockerfile hcl terraform".split()
)
# Export artifact name -> (item kind, chunk packing: items per chunk, characters per chunk, chunk title label)
AID_KINDS = {
    "notes": "note",
    "chapters": "chapter",
    "cheatsheet": "cheat_sheet",
    "flashcards": "flashcard",
    "interviewQuestions": "interview_question",
}
AUTHORITATIVE = ("available", "not_applicable")  # NHprep says what the kind holds; anything else keeps what we have
PACKING = {
    "note": (100, 1500, "Notes"),
    "cheat_sheet": (100, 1500, "Cheat sheet"),
    "flashcard": (10, 3500, "Flashcards"),
    "interview_question": (5, 3500, "Interview questions"),
}
CHUNK_CONTENT_MAX = 4000
CHUNK_TITLE_MAX = 700

INVISIBLE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f­​-‏‪-‮⁠-⁤⁦-⁩﻿]")
PREFIX = r"^([ \t>*+\-]*(?:\d{1,9}[.)][ \t>]*)?)"  # indentation, quotes and list markers before a block
FENCE = re.compile(PREFIX + r"(`{3,}|~{3,})[ \t]*([^\s`]*)(.*)$")
DIRECTIVE = re.compile(PREFIX + r"(:{2,})")
REFERENCE = re.compile(r"^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*<?(\S+?)>?(?:[ \t].*)?$")
MD_LINK = re.compile(r"!?\[([^\]\n]*)\]\([ \t]*<?([^)\s>]*)>?(?:[ \t]+\"[^\"\n]*\")?[ \t]*\)")
AUTOLINK = re.compile(r"<([a-zA-Z][a-zA-Z0-9+.-]*:[^>\s]*)>")
NHPREP_URL = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*:)?(?://)?(?:[a-z0-9-]+\.)*nhprep\.com(?![a-z0-9-])[^\s<>()\[\]\"']*")
SCHEME = re.compile(r"(?i)^(?:[a-z][a-z0-9+.-]*:)?(?://)?(?:www\.)?")


def _is_nhprep(url: str) -> bool:
    return NHPREP_URL.search(url) is not None


def clean(value: str, counts: Counter[str]) -> str:
    """Normalise one imported string (titles and Markdown alike)."""
    value, removed = INVISIBLE.subn("", value)
    counts["invisible"] += removed
    lines, fence = [], None
    for line in value.split("\n"):
        reference = REFERENCE.match(line)
        if reference and _is_nhprep(reference.group(1)):
            counts["nhprep_url"] += 1
            continue
        found = FENCE.match(line)
        if found:
            prefix, marker, info, rest = found.groups()
            if fence is None:
                fence = marker
                language = info.lower()
                safe = language if language in FENCE_LANGUAGES else ("text" if info else "")
                if safe != info or rest.strip():
                    counts["fence_info"] += 1
                    line = prefix + marker + safe
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not (info + rest).strip():
                fence = None
        elif fence is None and DIRECTIVE.match(line):
            counts["directive"] += 1
            line = DIRECTIVE.sub(lambda m: m.group(1) + "\\" + m.group(2), line, count=1)
        lines.append(line)
    value = "\n".join(lines)

    def link(match: re.Match[str]) -> str:
        if not _is_nhprep(match.group(2)):
            return match.group(0)
        counts["nhprep_url"] += 1
        return match.group(1)

    def autolink(match: re.Match[str]) -> str:
        if not _is_nhprep(match.group(1)):
            return match.group(0)
        counts["nhprep_url"] += 1
        return SCHEME.sub("", match.group(1))

    def bare(match: re.Match[str]) -> str:
        plain = SCHEME.sub("", match.group(0))  # "nhprep.com/x": no scheme and no www., so nothing autolinks it
        counts["nhprep_url"] += plain != match.group(0)
        return plain

    value = MD_LINK.sub(link, value)
    value = AUTOLINK.sub(autolink, value)
    return NHPREP_URL.sub(bare, value)


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def resolve_trainer(course: CourseRecord) -> tuple[str, str] | None:
    """(display name, provenance) from the export's hints, in order, each only through TRAINER_ALIASES."""
    hints = course.trainerHints
    for provenance, hint in (("title", hints.titleParens), ("description", hints.aboutBy), ("slug", hints.slugToken)):
        token = re.sub("[^a-z]", "", (hint or "").lower())
        if token in TRAINER_ALIASES:
            return TRAINER_ALIASES[token], provenance
    return None


def trainer_aliases(display: str) -> list[str]:
    return sorted(token for token, name in TRAINER_ALIASES.items() if name == display)


# ------------------------------------------------------------------ study aids
class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str = Field(pattern=NHPREP_ITEM_KEY)


class _Note(_Item):
    title: str = Field(default="", max_length=300)
    markdown: str = Field(max_length=200_000)


class _Chapter(_Item):
    title: str = Field(min_length=1, max_length=300)
    start: float = Field(ge=0, le=86_400)
    end: float | None = Field(default=None, ge=0)


class _Cheat(_Item):
    command: str = Field(min_length=1, max_length=2_000)
    description: str | None = Field(default=None, max_length=5_000)
    mode: str | None = Field(default=None, max_length=200)
    platform: str | None = Field(default=None, max_length=200)


class _Flashcard(_Item):
    front: str = Field(min_length=1, max_length=1_000)
    back: str = Field(min_length=1, max_length=2_000)


class _Interview(_Item):
    question: str = Field(min_length=1, max_length=4_000)
    modelAnswer: str = Field(max_length=20_000)  # export v1 field name
    scenario: str | None = Field(default=None, max_length=4_000)


ITEM_MODELS: dict[str, type[_Item]] = {
    "note": _Note,
    "chapter": _Chapter,
    "cheat_sheet": _Cheat,
    "flashcard": _Flashcard,
    "interview_question": _Interview,
}


def _code(value: str) -> str:
    """Inline code that survives backticks inside it."""
    ticks = "`" * (max((len(run) for run in re.findall("`+", value)), default=0) + 1)
    return f"{ticks} {value} {ticks}"


@dataclass(frozen=True)
class ItemRow:
    kind: str
    item_key: str
    position: int
    title: str
    body_md: str
    start_seconds: int | None
    data: dict[str, Any]
    search_text: str  # what the item contributes to search (not stored)

    def values(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "item_key": self.item_key,
            "position": self.position,
            "title": self.title,
            "body_md": self.body_md,
            "start_seconds": self.start_seconds,
            "data": self.data,
        }


def _item(kind: str, position: int, raw: Any, counts: Counter[str]) -> ItemRow | None:
    try:
        item = ITEM_MODELS[kind].model_validate(raw)
    except ValidationError:
        counts[f"item:{kind}"] += 1
        return None

    def text(value: str | None) -> str:
        return clean(value or "", counts).strip()

    title, body, start, data = "", "", None, {}
    if isinstance(item, _Note):
        title, body = text(item.title), text(item.markdown)
        search = f"{title}\n{body}".strip()
    elif isinstance(item, _Chapter):
        title, start, data = text(item.title), int(item.start), {"end": item.end}
        search = title
    elif isinstance(item, _Cheat):
        data = {"command": text(item.command), "description": text(item.description)}
        data |= {"mode": text(item.mode) or None, "platform": text(item.platform) or None}
        where = ", ".join(filter(None, (data["platform"], data["mode"])))
        body = _code(data["command"]) + (f" — {data['description']}" if data["description"] else "")
        body += f" ({where})" if where else ""
        search = " — ".join(filter(None, (data["command"], data["description"]))) + (f" ({where})" if where else "")
    elif isinstance(item, _Flashcard):
        data = {"front": text(item.front), "back": text(item.back)}
        search = f"Q: {data['front']}\nA: {data['back']}"
    else:
        assert isinstance(item, _Interview)
        data = {"question": text(item.question), "modelAnswer": text(item.modelAnswer), "scenario": text(item.scenario)}
        scenario = f"\nScenario: {data['scenario']}" if data["scenario"] else ""
        search = f"Q: {data['question']}{scenario}\nA: {data['modelAnswer']}"
    if kind == "chapter" and not title:
        counts[f"item:{kind}"] += 1
        return None
    return ItemRow(kind, item.id, position, title, body, start, data, search)


# ------------------------------------------------------------------ lessons, courses, chunks
@dataclass(frozen=True)
class ChunkText:
    kind: str
    item_key: tuple[str, str] | None  # (kind, item_key) of its first item; None for the lesson header
    ordinal: int
    title: str
    content: str

    @property
    def text(self) -> str:
        """Exactly what is embedded; its sha256 is the chunk's content_hash."""
        return f"{self.title}\n\n{self.content}"

    @property
    def key(self) -> str:
        return text_key(self.text)


@dataclass(frozen=True)
class LessonPlan:
    external_id: Any
    course_slug: str
    values: dict[str, Any]  # nhprep_lessons columns but ids and hashes
    items: tuple[ItemRow, ...]
    authoritative: frozenset[str]  # item kinds this export states completely
    content_hash: str


@dataclass(frozen=True)
class CoursePlan:
    slug: str
    values: dict[str, Any]
    trainer: tuple[str, str] | None
    content_hash: str
    lessons: tuple[LessonPlan, ...]


def plan_course(course: CourseRecord, lessons: list[LessonRecord], counts: Counter[str]) -> CoursePlan | None:
    title = clean(course.title, counts).strip()
    if not title:
        counts["course:title"] += 1
        return None
    trainer = resolve_trainer(course)
    values = {
        "external_id": course.slug,
        "slug": course.slug,
        "title": title,
        "description": clean(course.description, counts).strip(),
        "track": COURSE_TRACKS.get(course.slug),
    }
    planned = tuple(lesson for record in lessons if (lesson := plan_lesson(record, counts)) is not None)
    return CoursePlan(course.slug, values, trainer, content_hash([values, trainer]), planned)


def plan_lesson(record: LessonRecord, counts: Counter[str]) -> LessonPlan | None:
    title = clean(record.title, counts).strip()
    if not title:
        counts["lesson:title"] += 1
        return None
    artifacts = record.aids.artifacts if record.aids else {}
    availability: dict[str, Any] = {"status": record.aids.status if record.aids else None}
    items: list[ItemRow] = []
    authoritative: set[str] = set()
    for name, kind in AID_KINDS.items():
        aid = artifacts.get(name)
        state = getattr(aid, "availability", None)
        availability[kind] = state
        if state not in AUTHORITATIVE:
            continue
        authoritative.add(kind)
        keys: set[str] = set()
        for position, raw in enumerate(getattr(aid, "items", [])):
            item = _item(kind, position, raw, counts)
            if item is None:
                continue
            if item.item_key in keys:
                counts[f"item:{kind}:duplicate"] += 1
                continue
            keys.add(item.item_key)
            items.append(item)
    video = record.video
    values = {
        "position": record.position,
        "source_day": record.day,
        "title": title,
        "slug": record.slug,
        "duration_seconds": record.durationSeconds,
        "video_provider": video.provider if video else None,
        "video_id": video.videoId if video else None,
        "video_library_id": video.libraryId if video else None,
        "availability": availability,
    }
    digest = content_hash([values, [item.values() for item in items], sorted(authoritative)])
    return LessonPlan(record.external_id, record.course, values, tuple(items), frozenset(authoritative), digest)


def _split(text: str, budget: int) -> list[str]:
    parts = []
    while len(text) > budget:
        cut = text.rfind(" ", budget // 2, budget)
        cut = cut if cut > 0 else budget
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return [*parts, text] if text else parts


def _pack(items: list[ItemRow], max_items: int, budget: int) -> list[tuple[ItemRow, str]]:
    """Runs of items as chunk contents (≤ max_items pieces, ≤ budget characters); a long item is split."""
    packed: list[tuple[ItemRow, str]] = []
    first: ItemRow | None = None
    current: list[str] = []
    size = 0
    for item in items:
        for part in _split(item.search_text.strip(), budget):
            extra = len(part) + (2 if current else 0)
            if current and (len(current) >= max_items or size + extra > budget):
                packed.append((first, "\n\n".join(current)))  # type: ignore[arg-type]
                current, size, extra = [], 0, len(part)
            if not current:
                first = item
            current.append(part)
            size += extra
    if current:
        packed.append((first, "\n\n".join(current)))  # type: ignore[arg-type]
    return packed


def _clock(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def lesson_chunks(lesson: LessonPlan, course_title: str, trainer: str | None) -> list[ChunkText]:
    """~9 search texts a lesson: a header (course, trainer, lesson, day, de-slugged alias, chapter outline), then
    notes and cheat-sheet blocks, flashcards in tens, interview questions in fives. Duplicates within a lesson go."""
    values = lesson.values
    alias = values["slug"].removesuffix("mp4").replace("-", " ").strip()
    header = [f"Course: {course_title}", f"Trainer: {trainer}" if trainer else "", f"Lesson {values['position']}"]
    header += [f"Day {values['source_day']}" if values["source_day"] is not None else "", alias]
    chapters = [item for item in lesson.items if item.kind == "chapter"]
    if chapters:
        header += ["Chapters:", *(f"{_clock(item.start_seconds or 0)} {item.title}" for item in chapters)]
    texts = [ChunkText("lesson", None, 0, values["title"][:CHUNK_TITLE_MAX], "\n".join(filter(None, header)))]
    for kind, (max_items, budget, label) in PACKING.items():
        title = f"{values['title']} · {label}"[:CHUNK_TITLE_MAX]
        for first, content in _pack([item for item in lesson.items if item.kind == kind], max_items, budget):
            texts.append(ChunkText(kind, (kind, first.item_key), 0, title, content))
    chunks: list[ChunkText] = []
    keys: set[str] = set()
    for chunk in texts:
        chunk = ChunkText(chunk.kind, chunk.item_key, len(chunks), chunk.title, chunk.content[:CHUNK_CONTENT_MAX])
        if chunk.content and chunk.key not in keys:
            keys.add(chunk.key)
            chunks.append(chunk)
    return chunks
