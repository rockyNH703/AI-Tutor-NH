from __future__ import annotations

import uuid
from datetime import datetime
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


class ProgramRef(ApiModel):
    slug: str
    title: str


class ProgramClaimOut(ApiModel):
    """One of this account's invoice claims, as My Programs shows its status; never the invoice number."""

    program: ProgramRef
    # active: it opens its program now; expired: past valid_until or marked expired; revoked: revoked or refunded.
    state: Literal["active", "expired", "revoked"]
    claimed_at: datetime


class ProgramListOut(ApiModel):
    enabled: bool  # PROGRAMS_MODE lets this account use programs (My Programs shows the invoice form only then)
    programs: list[ProgramOut]
    # Ask for an NH invoice on first use: no open program, the prompt not dismissed, PROGRAMS_MODE allows the account.
    invoice_prompt: bool
    claims: list[ProgramClaimOut] = []  # this account's claims, newest first (additive, 2026-11-27)


class ClaimRequest(RequestModel):
    invoice_number: str = Field(min_length=1, max_length=80)


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
