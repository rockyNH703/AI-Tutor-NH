from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, model_validator

from app.memory.policy import MemoryKind
from app.schemas import ApiModel, RequestModel


class CreateMemoryRequest(RequestModel):
    kind: MemoryKind
    content: str
    source_message_id: uuid.UUID | None = None
    expires_at: datetime | None = None


class CorrectMemoryRequest(RequestModel):
    expected_version: int = Field(ge=1)
    kind: MemoryKind | None = None
    content: str | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _has_change(self) -> CorrectMemoryRequest:
        changed = {"kind", "content", "expires_at"} & self.model_fields_set
        if not changed:
            raise ValueError("a correction must change kind, content, or expiresAt")
        if "kind" in changed and self.kind is None:
            raise ValueError("kind must be omitted rather than null")
        if "content" in changed and self.content is None:
            raise ValueError("content must be omitted rather than null")
        return self


class DeleteMemoryRequest(RequestModel):
    expected_version: int = Field(ge=1)


class MemoryOut(ApiModel):
    id: uuid.UUID
    kind: MemoryKind
    content: str
    status: str
    version: int
    supersedes_id: uuid.UUID | None
    source_message_id: uuid.UUID | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryPage(ApiModel):
    items: list[MemoryOut]
    next_cursor: str | None


class MemoryExport(ApiModel):
    exported_at: datetime
    items: list[MemoryOut]
