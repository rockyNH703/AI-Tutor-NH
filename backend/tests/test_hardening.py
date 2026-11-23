"""Hardening from the second deep audit (2026-11-05). Each behaviour test failed on a1c1501 first."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.files.processing import BACKEND_DIR, SANDBOX_PROPERTIES
from app.schemas import RequestModel
from tests.conftest import make_settings, register

BACKEND = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------- MISS-db-2: U+0000 never reaches PostgreSQL
class _Sample(RequestModel):
    name: str
    tags: list[str] = []
    notes: dict[str, str] = {}


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "a\x00"},
        {"name": "a", "tags": ["x\x00"]},
        {"name": "a", "notes": {"k": "\x00"}},
        {"name": "a", "notes": {"k\x00": "v"}},
    ],
)
def test_every_request_model_refuses_nul_anywhere_in_its_text(payload):
    with pytest.raises(ValidationError):
        _Sample.model_validate(payload)


def test_a_request_without_nul_is_unchanged():
    assert _Sample.model_validate({"name": "ok", "tags": ["t"], "notes": {"k": "v"}}).tags == ["t"]


async def test_a_nul_in_a_conversation_title_is_a_422_not_a_database_error(client, app):
    headers = (await register(client, app))["headers"]
    refused = await client.post("/v1/conversations", json={"title": "BGP\x00notes"}, headers=headers)
    assert refused.status_code == 422, refused.text
    created = await client.post("/v1/conversations", json={"title": "BGP notes"}, headers=headers)
    assert created.status_code == 201, created.text
    renamed = await client.patch(f"/v1/conversations/{created.json()['id']}", json={"title": "x\x00y"}, headers=headers)
    assert renamed.status_code == 422, renamed.text


# ------------------------------------------------------------- K-27a: SQL parameters never reach a log line
@pytest.mark.parametrize("path", ["alembic/env.py", "scripts/migration_status.py", "app/db/session.py"])
def test_every_engine_hides_its_sql_parameters(path):
    tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "create_async_engine"
    ]
    assert calls, path
    for call in calls:
        assert any(
            keyword.arg == "hide_parameters" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
            for keyword in call.keywords
        ), path


# ------------------------------------------------------------- the extractor sandbox and the backend's .env
def test_the_extractor_sandbox_hides_a_backend_env_file():
    hidden = next(prop for prop in SANDBOX_PROPERTIES if prop.startswith("InaccessiblePaths="))
    assert f"-{BACKEND_DIR}/.env" in hidden.split("=", 1)[1].split()


# ------------------------------------------------------------- K-28d / K-28e: unusable numbers refused at start
@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_connect_timeout_s": 0},
        {"llm_first_token_timeout_s": 0},
        {"llm_total_timeout_s": -1},
        {"llm_first_token_timeout_s": 120, "llm_total_timeout_s": 60},
        {"llm_temperature": 2.5},
        {"llm_max_output_tokens": 0},
        {"chat_generations_per_minute": -1},
        {"chat_generations_per_day": -1},
        {"rag_top_k": 0},
        {"rag_min_score": 1.5},
        {"rag_retrieval_timeout_s": 0},
        {"rag_hnsw_ef_search": 0},
        {"rag_chunk_size": 10},
        {"rag_chunk_overlap": -1},
        {"rag_chunk_size": 300, "rag_chunk_overlap": 300},
    ],
)
def test_unusable_llm_quota_and_retrieval_numbers_are_refused(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_defaults_and_a_zero_quota_kill_switch_are_accepted():
    make_settings()
    # A daily quota of 0 refuses every chat (an operator's switch); chunk sizes used by the ingestion tests.
    make_settings(chat_generations_per_day=0, rag_chunk_size=200, rag_chunk_overlap=0)


# ------------------------------------------------------------- collection: a test file pytest skips cannot exist
def test_every_python_file_in_tests_is_collected_or_a_known_helper():
    helpers = {"__init__.py", "conftest.py", "decision_fakes.py"}
    stray = sorted(
        path.name
        for path in (BACKEND / "tests").glob("*.py")
        if path.name not in helpers and not path.name.startswith("test_")
    )
    assert stray == []  # 7263cf3 renamed eight *_unittest.py / *_unit.py files that never ran
