"""Daily challenge request and response shapes (plan section 13, G1-G8). Class names start with FriendZone, like
every Friend Zone contract schema."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import Field

from app.friendzone.schemas import FriendZoneOptionOut, FriendZoneProgramOut
from app.schemas import ApiModel, RequestModel


class FriendZoneDailyAnswerRequest(RequestModel):
    position: int = Field(ge=1, le=10)
    choice: str = Field(pattern=r"^[A-H]$")


class FriendZoneDailyAttemptOut(ApiModel):
    status: Literal["not_started", "in_progress", "finished"]
    served: int
    answered: int
    correct: int | None  # None until finished: a running count would give away each answer's key
    score: int | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class FriendZoneDailyQuestionOut(ApiModel):
    """The open question: never its key, its explanation or a later question."""

    position: int
    stem: str
    options: list[FriendZoneOptionOut]
    seconds: int
    served_at: dt.datetime
    closes_at: dt.datetime  # answers are still taken for 2 s after this (network allowance, G6)
    my_choice: str | None


class FriendZoneDailyReviewOut(ApiModel):
    """After finishing: each served question with its answer and why."""

    position: int
    stem: str
    options: list[FriendZoneOptionOut]
    correct_key: str
    explanation: str
    my_choice: str | None
    correct: bool | None  # None: unanswered
    points: int
    voided: bool  # withdrawn or changed since it was served: scores nothing


class FriendZoneDailyFriendOut(ApiModel):
    """A friend's attempt (G8). Score and correct answers only when that friend shows their stats."""

    player_id: uuid.UUID
    display_name: str
    initials: str
    status: Literal["completed", "in_progress"]
    score: int | None
    correct: int | None


class FriendZoneDailyOut(ApiModel):
    available: bool  # False: fewer than 10 checked questions in this program today (G5)
    challenge_id: uuid.UUID | None
    date: dt.date  # the India day the challenge belongs to
    program: FriendZoneProgramOut
    starts_at: dt.datetime
    closes_at: dt.datetime  # the end of the 30-minute grace after the day (G2)
    server_time: dt.datetime
    attempt: FriendZoneDailyAttemptOut | None  # None when not available
    question: FriendZoneDailyQuestionOut | None
    review: list[FriendZoneDailyReviewOut] | None  # only once finished
    friends: list[FriendZoneDailyFriendOut] | None  # only once finished, or after the grace
    next_change_at: dt.datetime
