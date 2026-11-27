"""Practice exams on the wire (netsec-handoffs/exams-contract.md). `topicId` is a QbTopic id: the UI's Quiz;
`topic` is a QbChapter title: the UI's Topic; `book` is a QbBook title: the UI's Track."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.programs.schemas import SLUG, ProgramRef
from app.schemas import ApiModel, RequestModel

MAX_QUESTIONS = 30
MAX_TOPICS = 50
MAX_REV = 2_147_483_647  # the `rev` column is a PostgreSQL integer
Letter = Annotated[str, Field(pattern=r"^[A-H]$")]


class Rules(ApiModel):
    marks_per_question: int = 1
    negative_marking: int = 0


class Available(ApiModel):
    easy: int
    medium: int
    hard: int
    total: int


class QuizOptionOut(ApiModel):
    topic_id: str
    title: str
    chapter: str
    book: str
    available: Available


class Durations(ApiModel):
    seconds_per_question: int
    multipliers: list[float]
    max_minutes: int


class ExamOptionsOut(ApiModel):
    program: ProgramRef
    month: int
    quizzes: list[QuizOptionOut]
    lengths: list[int]
    max_questions: int
    durations: Durations
    rules: Rules


class ExamStartRequest(RequestModel):
    request_id: uuid.UUID
    program: str = Field(pattern=SLUG, max_length=60)
    month: int = Field(ge=1, le=12)
    topic_ids: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(min_length=1, max_length=MAX_TOPICS)
    difficulty: Literal["mixed", "easy", "medium", "hard"]
    length: int = Field(ge=1, le=MAX_QUESTIONS)
    minutes: int = Field(ge=1, le=60)


class ExamCreatedOut(ApiModel):
    exam_id: uuid.UUID


class PracticeExamResponseItem(RequestModel):
    """One question's whole state: the letters chosen ([] clears), marked for review, seen."""

    index: int = Field(ge=0, lt=MAX_QUESTIONS)
    keys: list[Letter] = Field(max_length=8)
    marked: bool = False
    visited: bool = True


class PracticeExamSaveRequest(RequestModel):
    base_rev: int = Field(ge=0, le=MAX_REV)
    items: list[PracticeExamResponseItem] = Field(min_length=1, max_length=MAX_QUESTIONS)


class PracticeExamSubmitRequest(RequestModel):
    base_rev: int | None = Field(default=None, ge=0, le=MAX_REV)
    items: list[PracticeExamResponseItem] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    auto: bool = False


class SavedOut(ApiModel):
    rev: int
    server_time: datetime


class OptionOut(ApiModel):
    key: str
    text: str


class QuestionOut(ApiModel):
    index: int
    stem: str
    options: list[OptionOut]
    multi: bool
    quiz: str
    topic: str


class ResponseOut(ApiModel):
    index: int
    keys: list[str]
    marked: bool
    visited: bool


class TopicScoreOut(ApiModel):
    topic: str
    correct: int
    total: int


class ReviewOut(ApiModel):
    index: int
    keys: list[str]
    correct_keys: list[str]
    explanation: str
    # "void": the question's content changed after it was served, so it is not graded (and not in `total`)
    outcome: Literal["correct", "incorrect", "unanswered", "void"]


class ResultOut(ApiModel):
    score: int
    total: int
    percentage: int
    correct: int
    incorrect: int
    unanswered: int
    auto_submitted: bool
    submitted_at: datetime
    by_topic: list[TopicScoreOut]
    review: list[ReviewOut]


class ExamOut(ApiModel):
    exam_id: uuid.UUID
    status: Literal["in_progress", "submitted"]
    program: ProgramRef
    month: int
    title: str
    difficulty: str
    length: int
    minutes: int
    started_at: datetime
    ends_at: datetime
    cutoff_at: datetime
    server_time: datetime
    rev: int
    rules: Rules
    questions: list[QuestionOut]
    responses: list[ResponseOut]
    result: ResultOut | None


class SubmittedOut(ExamOut):
    late_items: list[int]  # items sent with this submit that arrived after the cutoff (or after a submit): not applied
