from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from app.conversations.schemas import ConversationOut, MessageOut
from app.schemas import ApiModel, RequestModel


class ChatRequest(RequestModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=16_000)
    # Ready files from the student's File Library to attach to this message (app/files).
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)
    # "voice": the message was spoken in voice mode (app/voice) and the reply will be read aloud; for a student
    # voice mode is not for, it is a typed message.
    mode: Literal["text", "voice"] = "text"
    # Voice turns: the audio the browser decodes, for the reply's first words when the server speaks them
    # (VOICE_SERVER_SPEECH); ignored otherwise.
    speech_format: Literal["mp3", "opus", "aac"] | None = None
    client_request_id: uuid.UUID | None = None
    # A new program chat (app/programs): its program, month (none: the program's QnA) and mode. Only when a chat
    # starts: the server checks the account's invoice and stores them; later turns use the stored chat only.
    program: str | None = Field(default=None, min_length=1, max_length=60, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    month: int | None = Field(default=None, ge=1, le=12)
    tutor_mode: Literal["tutor", "interview"] | None = None

    @model_validator(mode="after")
    def _program_only_when_a_chat_starts(self) -> ChatRequest:
        if self.conversation_id is not None and (
            self.program is not None or self.month is not None or self.tutor_mode is not None
        ):
            raise ValueError("program, month and tutorMode are set only when a program chat starts")
        if self.program is None and (self.month is not None or self.tutor_mode is not None):
            raise ValueError("month and tutorMode need a program")
        if self.tutor_mode == "interview" and self.month is None:
            raise ValueError("a mock interview is for one month")
        return self

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("message must not contain U+0000")
        if not v.strip():
            raise ValueError("message must not be blank")
        return v

    @field_validator("attachment_ids")
    @classmethod
    def _unique(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(v))


class ChatResponse(ApiModel):
    conversation: ConversationOut
    user_message: MessageOut | None
    message: MessageOut


class CancelOut(ApiModel):
    status: str


class FeedbackRequest(RequestModel):
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackOut(ApiModel):
    message_id: uuid.UUID
    rating: Literal["up", "down"]
    comment: str | None


BlankText = Annotated[str, Field(max_length=200)]
# One answer's value, as each kind reads it (app/assessments/kinds.py checks the exact shape): an option or
# line index, a list of indices (null = no choice yet, match only), a list of texts (blanks, diagram ids) or
# one text (numeric, cli, written). Strict: JSON true is never 1, "2" is never 2.
Index = Annotated[StrictInt, Field(ge=0, le=199)]
Value = (
    Index
    | Annotated[list[Index | None], Field(max_length=50)]
    | Annotated[list[Annotated[StrictStr, Field(max_length=200)]], Field(max_length=50)]
    | Annotated[StrictStr, Field(max_length=6000)]
)
QuestionKind = Literal[
    "mcq", "multi", "fill_blank", "numeric", "short", "long", "match", "order", "cli", "lines", "diagram"
]


class AssessmentResponseRequest(RequestModel):
    """Act on a practice test or exercise in a reply. Only which question and what was chosen or typed:
    the questions and the grading come from the reply the server saved. `answer`/`skip` work on every
    record; `save`/`clear`/`submit` (mock exams: drafts and the final submit) on schemaVersion 2 only."""

    position: int = Field(ge=0, le=9, description="Which block of the reply (0: replies hold one).")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="The record's `hash`, as it was shown.")
    question: int = Field(
        ge=0, lt=30, description="0-based; the waiting question (practice), any (exam), 0 for submit."
    )
    action: Literal["answer", "skip", "save", "clear", "submit"]
    value: Value | None = Field(
        default=None,
        description="Answer and save only: the chosen option (0-based) of a multiple-choice question, one text "
        "per blank, or the value of a newer kind (see docs/api-contract.md, Practice tests).",
    )
    revision: int | None = Field(
        default=None, ge=0, description="Submit only: the record's `revision` as it was shown (stale: 409)."
    )

    @model_validator(mode="after")
    def _value_only_to_answer(self) -> AssessmentResponseRequest:
        if (self.action in ("answer", "save")) != (self.value is not None):
            raise ValueError("value is required to answer or save, and only then")
        if self.revision is not None and self.action != "submit":
            raise ValueError("revision is sent with submit only")
        return self


class AssessmentAnswerOut(ApiModel):
    status: Literal["answered", "skipped"]
    value: int | list[str] | None  # multiple choice and fill-in-the-blanks (schemaVersion 1 shapes)
    earned: int
    possible: int
    at: str
    marks: list[bool] | None  # per blank, option, pair, position, line or key point: whether it was right
    answer: Value | None = None  # the value of a newer kind (value is null for those)
    tries: list[str] | None = None  # numeric/cli practice: the wrong values tried before this response


class ListingOut(ApiModel):
    """Code, a log or a config shown with line numbers (output analysis, scenario evidence)."""

    title: str | None = None
    language: str | None = None
    content: str


class EvidenceOut(ListingOut):
    label: str


class ScenarioOut(ApiModel):
    title: str | None = None
    summary: str
    symptoms: list[str]
    evidence: list[EvidenceOut]


class DiagramNodeOut(ApiModel):
    id: str
    label: str
    kind: str
    detail: str | None = None


class DiagramLinkOut(ApiModel):
    id: str
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    label: str | None = None
    detail: str | None = None


class DiagramOut(ApiModel):
    nodes: list[DiagramNodeOut]
    links: list[DiagramLinkOut]


class PendingOut(ApiModel):
    """Practice numeric/cli: the waiting question's wrong tries so far (nothing is revealed)."""

    question: int
    tries: list[str]


class AssessmentQuestionOut(ApiModel):
    """One question. Multiple choice (`mcq`) has `text` and `options`; a fill-in-the-blanks exercise
    (`fill_blank`) has `segments` (the text around the blanks) and `blanks`. Newer kinds use the fields
    after `explanation`; every field not used by a kind is null."""

    kind: QuestionKind
    text: str | None
    options: list[str] | None
    segments: list[str] | None
    blanks: int | None
    response: AssessmentAnswerOut | None
    # Only once the question has a response (an exam: once submitted).
    correct: int | None  # multiple choice: the right option
    answers: list[list[str]] | None  # fill-in-the-blanks: the accepted answers of each blank
    explanation: str | None
    # Newer kinds (schemaVersion 2): shown before answering.
    choose: int | None = None  # multi: how many options are right
    unit: str | None = None  # numeric
    prompt: str | None = None  # cli: the device prompt, e.g. "R1(config-if)#"
    left: list[str] | None = None  # match: the items to match
    right: list[str] | None = None  # match: the choices, in the order shown
    items: list[str] | None = None  # order: the items, in the order shown
    listing: ListingOut | None = None  # any kind: code, log or config the question refers to
    diagram: DiagramOut | None = None  # diagram: the topology (no answer in it)
    select: Literal["node", "link", "any"] | None = None  # diagram: what can be chosen
    points: int | None = None  # short/long: how many key points are scored
    max_length: int | None = None  # short/long: the longest accepted answer
    max_tries: int | None = None  # practice numeric/cli: tries before the answer is shown
    draft: Value | None = None  # exam: the saved, ungraded value
    # Newer kinds: only once final (an exam: once submitted).
    correct_list: list[int] | None = None  # multi, match, order, lines: the right indices
    accepted: list[str] | None = None  # numeric (canonical value), cli (accepted forms), diagram (ids)
    tolerance: str | None = None  # numeric
    key_points: list[str] | None = None  # short/long: each key point as the author wrote its first form
    sample: str | None = None  # short/long: a model answer
    output: str | None = None  # cli: the command's output


class AssessmentOut(ApiModel):
    """A practice test or exercise as the student may see it now; also each item of a reply's
    `metadata.assessments`."""

    id: str  # "<message id>:<position>"
    position: int
    schema_version: int
    kind: Literal["test", "exercise"]
    title: str | None
    hash: str
    locator: str  # sha256 of the reply's block, trimmed: which card this is
    state: Literal["not_started", "active", "completed"]
    answerable: bool
    total: int
    answered: int
    current: int | None  # the waiting question; null once completed
    earned: int
    possible: int
    questions: list[AssessmentQuestionOut]
    # schemaVersion 2 only (absent on schemaVersion 1 records).
    mode: Literal["practice", "exam"] | None = None
    submitted: bool | None = None  # exam: graded and revealed
    revision: int | None = None  # bumped by every change; a submit names the one it saw
    scenario: ScenarioOut | None = None  # shown above every question
    pending: PendingOut | None = None


class LearningEventOut(ApiModel):
    """What an answer did to the learning streak: the chat's `done.learning` values."""

    event: Literal["started", "extended", "milestone"]
    streak: int


class AssessmentResponseOut(AssessmentOut):
    """The test as recorded after a response, and `learning` (additive): what this answer did to the
    streak, as the chat's `done.learning` says it, when this answer to a question-bank test made its
    India day a learning day (every 5th of the day; app/progress/learning.py); null otherwise, and
    always on a replayed response."""

    learning: LearningEventOut | None
