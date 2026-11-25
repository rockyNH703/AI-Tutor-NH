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
    length: Literal[10, 20, 30] = 10  # questions in the exam, 60 s each (O4: was 5 or 10)
    request_id: uuid.UUID  # idempotency: the same id twice answers the same battle


class FriendZoneRematchRequest(RequestModel):
    request_id: uuid.UUID  # idempotency: the same id twice answers the same rematch


class FriendZoneAnswerRequest(RequestModel):
    round: int = Field(ge=1, le=10)
    choice: str = Field(pattern=r"^[A-H]$")


MAX_EXAM_QUESTIONS = 30
MAX_REV = 2_147_483_647  # the `rev` column is a PostgreSQL integer


class FriendZoneResponseItem(RequestModel):
    """One question's change in an exam (plan sections 3 and 4).

    `op: "set"` (the default) writes the question's whole state: `choice` (a letter, or null to clear it), `marked`
    and `baseRev`, the `rev` it was based on (0 when there is no row yet); all three must be sent. It is applied
    only when `baseRev` still matches (compare-and-set); a submit applies it as the final value without that check.
    `op: "visit"` only records that the question was seen: it never changes an existing row, and carries nothing
    but `round`."""

    round: int = Field(ge=1, le=MAX_EXAM_QUESTIONS)
    op: Literal["set", "visit"] = "set"
    choice: str | None = Field(default=None, pattern=r"^[A-H]$")
    marked: bool = False
    base_rev: int = Field(default=0, ge=0, le=MAX_REV)

    @model_validator(mode="after")
    def _shape(self) -> FriendZoneResponseItem:
        given = self.model_fields_set & {"choice", "marked", "base_rev"}
        if self.op == "set" and len(given) != 3:
            raise ValueError("a set needs choice, marked and baseRev")
        if self.op == "visit" and given:
            raise ValueError("a visit carries only round")
        return self


class FriendZoneBattleResponsesRequest(RequestModel):
    """Save exam answers as they change (picks, clears, marks and visits); nothing is graded until the end."""

    responses: list[FriendZoneResponseItem] = Field(min_length=1, max_length=MAX_EXAM_QUESTIONS)


class FriendZoneSubmitRequest(RequestModel):
    """Submit my exam: any responses still unsaved go with it, as final values. `auto` is the client's own submit at
    the deadline (recorded as submitted at the deadline)."""

    responses: list[FriendZoneResponseItem] = Field(default_factory=list, max_length=MAX_EXAM_QUESTIONS)
    auto: bool = False


class FriendZoneResponseOut(ApiModel):
    """One of my saved exam responses: a row exists once the question was visited."""

    round: int
    choice: str | None
    marked: bool
    rev: int  # the server's revision of this row: send it back as `baseRev`


class FriendZoneResponseResultOut(ApiModel):
    """What happened to one item of a save."""

    round: int
    applied: bool  # false: `baseRev` was stale, or the battle no longer takes changes
    rev: int  # the row's revision now (0: there is no row)


class FriendZoneBattleResponsesOut(ApiModel):
    """The answer to a save: small on purpose (no paper)."""

    server_time: datetime
    status: BattleStatus
    submitted_at: datetime | None  # mine
    opponent_submitted: bool
    responses: list[FriendZoneResponseOut]  # every response row of mine, after this save
    items: list[FriendZoneResponseResultOut]  # one per item sent, in order


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
    time_taken_seconds: int | None = None  # an exam, once completed: whole seconds from the start to the submit


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
    """After the battle: each question with its answer and why. A voided exam question has no stem, key or
    explanation (it changed or was withdrawn after the exam started)."""

    round: int
    stem: str | None
    options: list[FriendZoneOptionOut]
    correct_key: str | None
    explanation: str | None
    voided: bool
    my_choice: str | None
    their_choice: str | None


class FriendZoneExamQuestionOut(ApiModel):
    """A question of the exam paper: never its key or explanation."""

    round: int
    stem: str
    options: list[FriendZoneOptionOut]


class FriendZoneExamOut(ApiModel):
    """The running exam, once it has started: the same paper for both, my own responses only."""

    seconds_per_question: int
    ends_at: datetime  # the deadline: the timer shown counts to this
    cutoff_at: datetime  # the deadline plus the network allowance: saves are accepted until then
    questions: list[FriendZoneExamQuestionOut]
    responses: list[FriendZoneResponseOut]  # mine
    submitted_at: datetime | None  # mine
    opponent_submitted: bool


class FriendZoneBattleXpOut(ApiModel):
    """One line of the XP this battle gave me."""

    reason: str  # battle, win, draw
    amount: int


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
    question: FriendZoneQuestionOut | None  # a round battle's open round; always null for an exam
    rounds: list[FriendZoneRoundOut]
    review: list[FriendZoneReviewOut] | None  # only once completed
    format: Literal["rounds", "exam"] = "rounds"  # rounds: a battle from before the exam (history only)
    repeats_from_previous: int = 0  # a rematch: questions that repeat from the last battle
    exam: FriendZoneExamOut | None = None  # only while active and started
    xp: list[FriendZoneBattleXpOut] | None = None  # mine, only once completed
    # only on the /submit answer: true when that call applied my submission, false when the exam was not running for
    # me (not started, not active, completed, or already submitted)
    submit_applied: bool | None = None


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
