from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from app.schemas import ApiModel, RequestModel

SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class ProgramMaterial(ApiModel):
    documents: int  # Knowledge Library documents mapped to the month (books, guides, exam topics)
    lessons: int  # NHprep lessons of the month's courses
    questions: int  # servable question-bank questions of the month's tracks


class ProgramMonthOut(ApiModel):
    month: int
    title: str
    material: ProgramMaterial


class ProgramOut(ApiModel):
    slug: str
    title: str
    months: list[ProgramMonthOut]


class ProgramListOut(ApiModel):
    enabled: bool  # PROGRAMS_MODE lets this account use programs (Settings shows the invoice card only then)
    programs: list[ProgramOut]
    # Ask for an NH invoice on first use: no open program, the prompt not dismissed, PROGRAMS_MODE allows the account.
    invoice_prompt: bool


class ClaimRequest(RequestModel):
    invoice_number: str = Field(min_length=1, max_length=80)


class ProgramRef(ApiModel):
    slug: str
    title: str


class ClaimOut(ApiModel):
    status: Literal["claimed", "already"]
    program: ProgramRef


class BankTopicOut(ApiModel):
    topic_id: str
    title: str
    chapter: str
    book: str


class MonthDetailOut(ApiModel):
    program: ProgramRef
    month: int
    title: str
    material: ProgramMaterial
    lesson_ids: list[uuid.UUID]  # NHprep lessons of the month's courses (Videos & Links), course then position
    topics: list[BankTopicOut]  # question-bank topics of the month's tracks (Quizzes)
    quiz_available: bool
