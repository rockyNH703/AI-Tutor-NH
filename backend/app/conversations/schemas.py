from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.schemas import ApiModel, RequestModel

TITLE_MAX = 120


class ConversationProgramOut(ApiModel):
    """A program chat's place (app/programs): its program, its month (None: the program's QnA) and its mode."""

    slug: str
    title: str
    month: int | None
    month_title: str | None
    mode: str


class ConversationOut(ApiModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    program: ConversationProgramOut | None = None  # None: an ordinary chat
    pinned_at: datetime | None = None  # pinned to the top of the sidebar (never with archived_at)
    archived_at: datetime | None = None  # archived: out of the sidebar, in Archived chats


class ConversationPage(ApiModel):
    items: list[ConversationOut]
    next_cursor: str | None


class MessageAttachmentOut(ApiModel):
    file_id: uuid.UUID | None  # None: the file was deleted from the library (its name is kept)
    filename: str


class MessageOut(ApiModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    status: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[MessageAttachmentOut] = Field(default_factory=list)  # user messages only


class MessagePage(ApiModel):
    items: list[MessageOut]
    next_cursor: str | None  # pass as `before` to load older messages


class CreateConversationRequest(RequestModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX)


class UpdateConversationRequest(RequestModel):
    """Rename, pin or archive a chat: any of them, at least one. Archiving unpins; a chat is pinned only when it is not
    archived (archived: true and pinned: true together are refused)."""

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    pinned: bool | None = None
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = " ".join(v.split())
        if not v:
            raise ValueError("title must not be blank")
        return v

    @model_validator(mode="after")
    def _something(self) -> UpdateConversationRequest:
        if self.title is None and self.pinned is None and self.archived is None:
            raise ValueError("nothing to change")
        if self.pinned and self.archived:
            raise ValueError("a chat can't be pinned and archived")
        return self


class ShareStatusOut(ApiModel):
    """The chat's shared link, without its token (shown only when it is made): whether one is live, and its snapshot."""

    shared: bool
    shared_at: datetime | None = None
    message_count: int | None = None


class ShareCreatedOut(ApiModel):
    """A new link: its token (shown once; only its hash is kept), when it was made and how many messages it shows."""

    token: str
    shared_at: datetime
    message_count: int


class SharedMessageOut(ApiModel):
    role: str
    content: str
    created_at: datetime
    attachments: int  # how many files the message carried; the files themselves are never shared


class SharedChatOut(ApiModel):
    """A shared link's read-only snapshot, for anyone with the link: the title and the messages, nothing else."""

    title: str
    shared_at: datetime
    messages: list[SharedMessageOut]
