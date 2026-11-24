from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas import ApiModel, RequestModel


class FriendZoneProfileOut(ApiModel):
    player_id: uuid.UUID  # the public id; the account's own id never leaves the API
    display_name: str
    discoverable: bool  # findable by invoice number (off until the member turns it on)
    show_program: bool
    show_stats: bool
    version: int
    qa: bool  # a QA account (isolated cohort); shown to the member only


class FriendZoneMeOut(ApiModel):
    enabled: bool  # FRIEND_ZONE_MODE lets this account in (the web app shows Friend Zone only then)
    eligible: bool  # and an NH invoice opens a program now
    member: bool
    suspended: bool
    profile: FriendZoneProfileOut | None
    server_time: datetime


class FriendZoneJoinRequest(RequestModel):
    display_name: str = Field(min_length=1, max_length=60)
    discoverable: bool = False
    show_program: bool = False
    show_stats: bool = False


class FriendZoneProfileChanges(RequestModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=60)
    discoverable: bool | None = None
    show_program: bool | None = None
    show_stats: bool | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> FriendZoneProfileChanges:
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("null is not a value in `set`")
        return self


class UpdateFriendZoneProfileRequest(RequestModel):
    version: int = Field(ge=0)
    set: FriendZoneProfileChanges


# ------------------------------------------------------------------ P1.2: friends, requests, blocks, reports
Relation = Literal["self", "friend", "incoming", "outgoing", "blocked", "none"]


class FriendZoneProgramOut(ApiModel):
    slug: str
    title: str


class FriendZoneBadgeOut(ApiModel):
    key: str
    title: str
    description: str


class FriendZoneStatsOut(ApiModel):
    """Battle record and progression: shown to the member, and to friends only when they show their stats."""

    battles: int  # decided by play (points, draw or forfeit); void ones are left out
    wins: int
    losses: int
    draws: int
    correct: int
    answered: int
    xp: int
    level: int
    level_floor: int  # the XP this level starts at
    next_level_at: int
    badges: list[FriendZoneBadgeOut]
    dailies_completed: int
    daily_streak: int


class FriendZoneXpEventOut(ApiModel):
    reason: str  # battle, win, draw, daily, perfect, streak_3, streak_7, streak_14, streak_30
    amount: int
    created_at: datetime


class FriendZoneXpOut(ApiModel):
    xp: int
    level: int
    level_floor: int
    next_level_at: int
    items: list[FriendZoneXpEventOut]
    next_cursor: str | None


class FriendZonePlayerOut(ApiModel):
    """Another member as the caller may see them (plan section 4, privacy matrix): a photo and programs for friends
    only, and programs only when that friend shows them. Never an invoice number, an email or an account id."""

    player_id: uuid.UUID
    display_name: str
    initials: str
    relation: Relation
    avatar_url: str | None = None
    programs: list[FriendZoneProgramOut] | None = None  # None: not shown to the caller
    stats: FriendZoneStatsOut | None = None  # yourself; a friend only when they show their stats


class FriendZonePlayersOut(ApiModel):
    items: list[FriendZonePlayerOut]


class FriendZoneSearchRequest(RequestModel):
    invoice_number: str = Field(min_length=1, max_length=60)


class FriendZoneRequestOut(ApiModel):
    request_id: uuid.UUID
    player: FriendZonePlayerOut
    created_at: datetime
    expires_at: datetime


class FriendZoneRequestsOut(ApiModel):
    incoming: list[FriendZoneRequestOut]
    outgoing: list[FriendZoneRequestOut]


class FriendZonePlayerRef(RequestModel):
    player_id: uuid.UUID


class FriendZoneReportRequest(RequestModel):
    player_id: uuid.UUID
    reason: Literal["harassment", "cheating", "inappropriate_name", "spam", "other"]
    details: str | None = Field(default=None, max_length=1000)
    battle_id: uuid.UUID | None = None


class FriendZoneReportOut(ApiModel):
    report_id: uuid.UUID


# ------------------------------------------------------------------ P1.3: 1v1 battles
BattleStatus = Literal["invited", "waiting", "active", "completed", "declined", "expired", "cancelled"]


class FriendZoneCreateBattleRequest(RequestModel):
    opponent_id: uuid.UUID  # a friend's playerId
    program: str = Field(min_length=1, max_length=60)  # the program slug
    month: int = Field(ge=1, le=12)
    topic_id: str | None = Field(default=None, max_length=120)
    difficulty: Literal["mixed", "easy", "medium", "hard"] = "mixed"
    length: Literal[5, 10] = 5
    request_id: uuid.UUID  # idempotency: the same id twice answers the same battle


class FriendZoneRematchRequest(RequestModel):
    request_id: uuid.UUID  # idempotency: the same id twice answers the same rematch


class FriendZoneAnswerRequest(RequestModel):
    round: int = Field(ge=1, le=10)
    choice: str = Field(pattern=r"^[A-H]$")


class FriendZoneBattlePlayerOut(ApiModel):
    player_id: uuid.UUID | None  # None: the member has left (deleted their account)
    display_name: str | None
    initials: str | None
    role: Literal["challenger", "opponent"]
    ready: bool
    score: int
    correct: int
    answered: int
    winner: bool


class FriendZoneOptionOut(ApiModel):
    key: str
    text: str


class FriendZoneQuestionOut(ApiModel):
    """The open round's question: never its key, its explanation or the opponent's pick."""

    round: int
    stem: str
    options: list[FriendZoneOptionOut]
    seconds: int
    opens_at: datetime
    closes_at: datetime
    my_choice: str | None


class FriendZoneRoundOut(ApiModel):
    """A closed round, as both players may now see it."""

    round: int
    voided: bool  # the question changed or was withdrawn: no points for either player
    correct_key: str | None
    my_choice: str | None
    their_choice: str | None
    my_points: int
    their_points: int


class FriendZoneReviewOut(ApiModel):
    """After the battle: each question with its answer and why."""

    round: int
    stem: str
    options: list[FriendZoneOptionOut]
    correct_key: str
    explanation: str
    voided: bool
    my_choice: str | None
    their_choice: str | None


class FriendZoneBattleOut(ApiModel):
    battle_id: uuid.UUID
    status: BattleStatus
    end_reason: str | None
    result: Literal["points", "forfeit", "draw", "void"] | None
    program: FriendZoneProgramOut
    month: int
    topic_id: str | None
    difficulty: str
    length: int
    rounds_closed: int
    expires_at: datetime | None
    starts_at: datetime | None
    ends_at: datetime | None
    next_change_at: datetime | None  # when polling next can show something new
    server_time: datetime
    me: FriendZoneBattlePlayerOut
    opponent: FriendZoneBattlePlayerOut
    question: FriendZoneQuestionOut | None
    rounds: list[FriendZoneRoundOut]
    review: list[FriendZoneReviewOut] | None  # only once completed


class FriendZoneBattleSummaryOut(ApiModel):
    battle_id: uuid.UUID
    status: BattleStatus
    result: Literal["points", "forfeit", "draw", "void"] | None
    role: Literal["challenger", "opponent"]
    opponent: FriendZoneBattlePlayerOut
    program: FriendZoneProgramOut
    month: int
    length: int
    created_at: datetime
    ended_at: datetime | None
    expires_at: datetime | None


class FriendZoneBattlesOut(ApiModel):
    items: list[FriendZoneBattleSummaryOut]
    next_cursor: str | None
