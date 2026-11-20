"""/v1/nhprep's HTTP models (docs/api-reference.md, "NHprep"). Ids are NetSec's own; no NHprep external id, page URL
or video URL is ever in a GET: a video's link only ever comes from POST .../playback."""

from __future__ import annotations

import uuid
from typing import Literal

from app.schemas import ApiModel

NhprepItemKind = Literal["note", "flashcard", "cheat_sheet", "interview_question"]
# present: imported; absent: NHprep has none; unavailable: NHprep hasn't made them; error: NHprep failed to.
NhprepAvailability = Literal["present", "absent", "unavailable", "error"]
NhprepVideo = Literal["ready", "no_video", "not_configured"]


class NhprepCourseOut(ApiModel):
    id: uuid.UUID
    title: str


class NhprepLessonSummary(ApiModel):
    id: uuid.UUID
    title: str
    course: NhprepCourseOut
    trainers: list[str]
    position: int
    lesson_count: int  # the course's lessons ("Lesson 3 of 40")
    duration_seconds: int | None


class NhprepLessonList(ApiModel):
    items: list[NhprepLessonSummary]


class NhprepChapterOut(ApiModel):
    id: uuid.UUID
    title: str
    start_seconds: int


class NhprepLessonOut(NhprepLessonSummary):
    description: str | None  # the course's (a lesson has none of its own)
    chapters: list[NhprepChapterOut]
    availability: dict[NhprepItemKind, NhprepAvailability]
    previous_lesson_id: uuid.UUID | None  # the nearest lesson of the course by position, not removed
    next_lesson_id: uuid.UUID | None
    video: NhprepVideo
    removed: bool  # gone from NHprep since it was imported: its title only, no items and no video


class NhprepItemOut(ApiModel):
    """note: title + Markdown; flashcard: front (title) + back; cheat_sheet: one line of Markdown;
    interview_question: question (title) + model answer (with its scenario first, when there is one)."""

    id: uuid.UUID
    kind: NhprepItemKind
    title: str | None
    body_md: str | None


class NhprepItemList(ApiModel):
    items: list[NhprepItemOut]


class NhprepPlaybackOut(ApiModel):
    """ready: a short-lived https://videos.nhprep.com/{videoId}.mp4 link and its seconds left; else why not."""

    status: Literal["ready", "unavailable"]
    src: str | None = None
    expires_in: int | None = None
    reason: Literal["not_configured", "no_video", "signing_failed"] | None = None
