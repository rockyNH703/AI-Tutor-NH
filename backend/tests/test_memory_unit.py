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


def test_engineering_facts_are_allowed_and_network_secrets_and_config_dumps_are_not():
    for fact in (
        "Uses a QoS class-map to prioritise voice on the WAN edge.",
        "Runs carrier-grade NAT for mobile subscribers.",
        "Manages a medium-sized site with two FortiGate firewalls in HA.",
        "Wants a board-level summary after every change.",
        "Prefers Junos set commands in examples.",
        "Works on secrets management with HashiCorp Vault.",
        "Studies for the CCNP Enterprise exam.",
    ):
        validate_memory_policy(fact)  # no "my ...": an engineering fact, not a profile setting
    for blocked in (
        "The SNMP community on the core switches is n0tpublic",
        "Our IPsec pre-shared key is Hunter2Hunter2",
        "psk: correct-horse-battery",
        "enable secret 9 $9$abcdefghijklmnop",
        "key chain OSPF key 1 key-string S3cret",
        "token=" + "abc123def456",  # split: a fake, but scanners would flag it
        "Repo token ghp_abcdefghijklmnopqrstuvwxyz0123",
        "Backups go to sftp://backup:Sup3rS3cret@10.0.0.5/configs",
        "-----BEGIN " + "OPENSSH PRIVATE KEY----- b3BlbnNzaC1rZXktdjE=",
        "hostname edge1 interface GigabitEthernet0/1 ip address 10.1.1.1 255.255.255.0",
        "set interfaces ge-0/0/0 unit 0 family inet address 192.0.2.1/24 set system host-name fw1",
        "My target exam is CCNA",
        "My streak is 12 days",
    ):
        with pytest.raises(ValidationFailed):
            validate_memory_policy(blocked)


class _Item:
    def __init__(self, row_id, kind, content):
        self.id = row_id
        self.kind = kind
        self.content = content


def test_stored_memory_preferences_parse_defensively():
    from types import SimpleNamespace

    from app.memory.preferences import MemoryPreferences

    stamp = datetime(2026, 10, 1, tzinfo=UTC)
    good = MemoryPreferences.from_settings(
        SimpleNamespace(
            preferences={
                "memoryAutomaticProposals": True,
                "memoryConsentEpoch": 3,
                "memoryClearedBefore": stamp.isoformat(),
                "memoryVersion": 2,
            },
            updated_at=stamp,
        )
    )
    assert (good.automatic_proposals, good.consent_epoch, good.cleared_before, good.version) == (True, 3, stamp, 2)
    for bad in (
        {"memoryAutomaticProposals": "false", "memoryConsentEpoch": "x", "memoryClearedBefore": "nope"},
        {"memoryAutomaticProposals": 1, "memoryConsentEpoch": [1], "memoryClearedBefore": 5, "memoryVersion": -4},
        {"memoryConsentEpoch": 10**30, "memoryVersion": float("inf"), "memoryUpdatedAt": {"a": 1}},
    ):
        parsed = MemoryPreferences.from_settings(SimpleNamespace(preferences=bad, updated_at=stamp))
        assert (parsed.automatic_proposals, parsed.consent_epoch, parsed.cleared_before, parsed.version) == (
            False,
            0,
            None,
            0,
        )
        assert parsed.updated_at == stamp


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
