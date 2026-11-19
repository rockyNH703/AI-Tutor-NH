"""Reading and checking an NHprep export v1 bundle (frozen 2026-11-19, C:\\Users\\test\\netsec-handoffs\\nhprep.md).

No database and no provider. The bundle is export.jsonl (one JSON object a line, each with `type` and `schema: 1`)
plus manifest.json ({schema, exporterVersion, complete: {lessons, quiz}, sha256 of export.jsonl, counts, ...}).
- The bytes must match the manifest's sha256 and/or --sha256; the manifest's counts must match the stream.
- `course` and `lesson` records are validated here; `quiz_question` and `quiz_coverage` are the question bank's
  (its converter reads them) and are only counted.
- A record that fails is a counted reject (a code, never its text), never a crash. Video references are strict: no
  URL, `exp`, `sig` or token can pass.
- Removals are safe only when the export is complete: the manifest says so, every course's lesson count matches,
  and every rejected lesson still had a readable id (so it is "present", not "absent").
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.db.models import NHPREP_SLUG

SCHEMA = 1
IGNORED_TYPES = ("quiz_question", "quiz_coverage")  # the question bank's
VIDEO_LESSON_TYPES = (None, "video")  # the exporter leaves the type null when the course page shows none


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class TrainerHints(_Record):
    titleParens: str | None = Field(default=None, max_length=200)  # export v1 field names (camelCase)
    aboutBy: str | None = Field(default=None, max_length=200)
    slugToken: str | None = Field(default=None, max_length=200)


class CourseRecord(_Record):
    type: Literal["course"]
    schema_: Literal[1] = Field(alias="schema")
    slug: str = Field(pattern=NHPREP_SLUG, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    trainerHints: TrainerHints = TrainerHints()
    lessonCount: int = Field(ge=0, le=10_000)


class Video(BaseModel):
    """A stable reference only. extra="forbid": a `url`, `exp`, `sig` or token field refuses the lesson."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["nhprep-mp4", "bunny"]
    videoId: uuid.UUID
    libraryId: int | None = Field(default=None, gt=0, le=2_147_483_647)

    @model_validator(mode="after")
    def _library_iff_bunny(self) -> Video:
        if (self.libraryId is not None) != (self.provider == "bunny"):
            raise ValueError("libraryId is required for bunny and only for bunny")
        return self


class AidKind(_Record):
    availability: str | None = Field(default=None, max_length=40)
    reason: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=2_000)


class Aids(_Record):
    status: str | None = Field(default=None, max_length=40)
    artifacts: dict[str, AidKind | int | None] = Field(default_factory=dict)  # schemaVersion is an int beside the kinds


class LessonRecord(_Record):
    type: Literal["lesson"]
    schema_: Literal[1] = Field(alias="schema")
    external_id: uuid.UUID
    course: str = Field(pattern=NHPREP_SLUG, max_length=200)
    slug: str = Field(pattern=NHPREP_SLUG, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    position: int = Field(ge=1, le=32_767)
    day: int | None = Field(default=None, ge=0, le=999)
    lessonType: str | None = None
    durationSeconds: int | None = Field(default=None, ge=0, le=10**7)
    video: Video | None = None
    aids: Aids | None = None


@dataclass
class Export:
    manifest_sha256: str
    exporter_version: str
    complete: bool
    courses: dict[str, CourseRecord] = field(default_factory=dict)
    lessons: list[LessonRecord] = field(default_factory=list)
    present_lesson_ids: set[uuid.UUID] = field(default_factory=set)  # valid or not: never "absent"
    rejects: Counter[str] = field(default_factory=Counter)
    ignored: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)  # any error refuses the whole import


def _reject_code(kind: str, error: ValidationError) -> str:
    location = error.errors()[0]["loc"]
    return f"{kind}:{location[0] if location else 'record'}"


def _sections_complete(manifest: dict[str, Any] | None) -> bool:
    if manifest is None:
        return False
    complete = manifest.get("complete")
    return complete is True or (isinstance(complete, dict) and complete.get("lessons") is True)


def read_export(data: bytes, manifest: dict[str, Any] | None, expected_sha256: str | None) -> Export:
    """Check and parse a bundle's bytes. `manifest` is None for a stream (then it is never complete)."""
    digest = hashlib.sha256(data).hexdigest()
    export = Export(
        manifest_sha256=digest,
        exporter_version=str((manifest or {}).get("exporterVersion", "unknown"))[:40],
        complete=_sections_complete(manifest),
    )
    if manifest is None and expected_sha256 is None:
        export.errors.append("sha256_required: give --sha256 when there is no manifest.json")
    if manifest is not None and (manifest.get("schema") != SCHEMA or manifest.get("sha256") != digest):
        export.errors.append("manifest_mismatch: schema or sha256 differs from export.jsonl")
    if expected_sha256 is not None and expected_sha256.lower() != digest:
        export.errors.append("sha256_mismatch: --sha256 differs from the bytes read")
    if export.errors:
        return export

    seen_types: Counter[str] = Counter()
    per_course: Counter[str] = Counter()
    ids: set[uuid.UUID] = set()
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            kind = record["type"]
            if not isinstance(kind, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            export.errors.append(f"line {number}: not an export v1 record")
            continue
        seen_types[kind] += 1
        if kind in IGNORED_TYPES:
            export.ignored[kind] += 1
        elif kind == "course":
            _read_course(export, record)
        elif kind == "lesson":
            per_course[str(record.get("course"))] += 1
            _read_lesson(export, record, ids)
        else:
            export.rejects["unknown_type"] += 1

    if manifest is not None and {k: v for k, v in (manifest.get("counts") or {}).items() if v} != dict(seen_types):
        export.errors.append("manifest_mismatch: counts differ from export.jsonl")
    for slug, course in export.courses.items():
        if per_course[slug] != course.lessonCount:
            export.complete = False  # this course's lesson list was not exported in full
    for lesson in export.lessons:
        if lesson.course not in export.courses:
            export.rejects["lesson:orphan"] += 1
    export.lessons = [lesson for lesson in export.lessons if lesson.course in export.courses]
    return export


def _read_course(export: Export, record: dict[str, Any]) -> None:
    try:
        course = CourseRecord.model_validate(record)
    except ValidationError as error:
        export.rejects[_reject_code("course", error)] += 1
        export.complete = False  # its lessons can no longer be told apart from removed ones
        return
    if course.slug in export.courses:
        export.rejects["course:duplicate"] += 1
        return
    export.courses[course.slug] = course


def _read_lesson(export: Export, record: dict[str, Any], ids: set[uuid.UUID]) -> None:
    try:
        lesson_id = uuid.UUID(str(record.get("external_id")))
    except ValueError:
        lesson_id = None
        export.complete = False  # an unreadable id: a removal could hit a lesson that is still there
    try:
        lesson = LessonRecord.model_validate(record)
    except ValidationError as error:
        export.rejects[_reject_code("lesson", error)] += 1
        if lesson_id is not None:
            export.present_lesson_ids.add(lesson_id)
        return
    if lesson.lessonType not in VIDEO_LESSON_TYPES:
        export.rejects["lesson:lessonType"] += 1  # not a video lesson: absent from the video catalogue
        return
    if lesson.external_id in ids:
        export.rejects["lesson:duplicate"] += 1
        return
    ids.add(lesson.external_id)
    export.present_lesson_ids.add(lesson.external_id)
    export.lessons.append(lesson)
