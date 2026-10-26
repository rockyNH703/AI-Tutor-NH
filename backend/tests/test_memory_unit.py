from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.errors import ValidationFailed
from app.memory.policy import normalize_content, validate_memory_policy
from app.memory.schemas import CorrectMemoryRequest
from app.memory.service import MemoryService
from app.settings import Settings


def test_normalization_policy_and_correction_schema():
    assert normalize_content("  use   train examples \n often ", max_chars=100) == "use train examples often"
    with pytest.raises(ValidationFailed):
        normalize_content("   ", max_chars=100)
    with pytest.raises(ValidationFailed):
        validate_memory_policy("My password is secret")
    with pytest.raises(ValidationFailed):
        validate_memory_policy("My grade is class 10")
    with pytest.raises(ValidationError):
        CorrectMemoryRequest.model_validate({"expectedVersion": 1})
    with pytest.raises(ValidationError):
        CorrectMemoryRequest.model_validate({"expectedVersion": 1, "content": None})
    assert CorrectMemoryRequest.model_validate({"expectedVersion": 1, "expiresAt": None}).expires_at is None


class _Item:
    def __init__(self, row_id, kind, content):
        self.id = row_id
        self.kind = kind
        self.content = content


def test_expiry_must_be_future():
    with pytest.raises(ValidationFailed):
        MemoryService._validate_expiry(datetime.now(UTC) - timedelta(seconds=1))
    MemoryService._validate_expiry(datetime.now(UTC) + timedelta(seconds=1))
    MemoryService._validate_expiry(None)


def test_recall_applies_score_kind_item_and_character_limits():
    from app.memory.service import rank_candidates

    settings = Settings(
        _env_file=None,
        app_env="test",
        memory_recall_enabled=True,
        memory_recall_limit=2,
        memory_recall_candidates=6,
        memory_recall_per_kind=1,
        memory_max_context_chars=100,
        memory_min_score=0.5,
    )
    rows = [
        (_Item(uuid.uuid4(), "learning_context", "train probability examples"), 0.1),
        (_Item(uuid.uuid4(), "learning_context", "another same kind"), 0.2),
        (_Item(uuid.uuid4(), "study_context", "short study note"), 0.3),
        (_Item(uuid.uuid4(), "study_context", "below threshold"), 0.8),
    ]
    recalled = rank_candidates(rows, settings)
    assert [item.kind for item in recalled] == ["learning_context", "study_context"]
    assert sum(len(item.content) for item in recalled) + 1 <= settings.memory_max_context_chars
