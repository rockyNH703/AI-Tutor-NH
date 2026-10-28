from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, model_validator

from app.memory.policy import MemoryKind
from app.schemas import ApiModel, RequestModel


class ProposalOut(ApiModel):
    id: uuid.UUID
    operation: str
    target_memory_id: uuid.UUID | None
    kind: MemoryKind
    content: str
    status: str
    version: int
    confidence: float
    evidence_reason: str
    source_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    observed_at: datetime
    expires_at: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    applied_memory_id: uuid.UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProposalPage(ApiModel):
    items: list[ProposalOut]
    next_cursor: str | None


class ApproveProposalRequest(RequestModel):
    expected_version: int = Field(ge=1)
    kind: MemoryKind | None = None
    content: str | None = None
    expires_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def _non_null_edits(self) -> ApproveProposalRequest:
        for name in ("kind", "content"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} must be omitted rather than null")
        return self


class RejectProposalRequest(RequestModel):
    expected_version: int = Field(ge=1)


class MemoryPreferencesOut(ApiModel):
    automatic_proposals: bool
    consent_epoch: int
    cleared_before: datetime | None
    version: int
    updated_at: datetime | None


class UpdateMemoryPreferencesRequest(RequestModel):
    automatic_proposals: bool
    expected_version: int = Field(ge=0)


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
    origin: str
    proposal_id: uuid.UUID | None
    last_confirmed_at: datetime
    valid_from: datetime | None
    valid_to: datetime | None
    processor_version: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryPage(ApiModel):
    items: list[MemoryOut]
    next_cursor: str | None


class MemoryExport(ApiModel):
    exported_at: datetime
    items: list[MemoryOut]
