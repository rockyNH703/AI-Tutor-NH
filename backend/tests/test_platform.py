"""Cross-cutting behaviour: limits, errors, health, database failures, production safety."""

import httpx
import pytest

from app.auth.emails import MemoryEmailSender
from app.llm.fake import FakeProvider
from app.main import create_app
from tests.conftest import make_settings, register


async def test_health_ready_and_request_id(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "release": "development", "files": "available"}
    assert len(r.headers["x-request-id"]) == 32
    r = await client.get("/ready", headers={"X-Request-ID": "trace-123"})
    assert r.status_code == 200 and r.json()["database"] is True
    assert r.headers["x-request-id"] == "trace-123"
    # a malformed incoming id is replaced, never reflected
    r = await client.get("/health", headers={"X-Request-ID": "bad id\r\nx"})
    assert r.headers["x-request-id"] != "bad id\r\nx"


async def test_oversized_body_is_413(client, app):
    account = await register(client, app)
    big = {"message": "x" * 70_000}
    r = await client.post("/v1/chat/stream", json=big, headers=account["headers"])
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


async def test_unknown_route_uses_error_envelope(client):
    r = await client.get("/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_database_unavailable_is_503_without_details():
    settings = make_settings(
        database_url="postgresql+asyncpg://aichat:aichat@127.0.0.1:1/aichat_test", db_connect_timeout_s=1.0
    )
    application = create_app(settings, llm=FakeProvider(), email_sender=MemoryEmailSender())
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://t") as client:
            r = await client.get("/v1/me", headers={"Authorization": "Bearer any-token"})
            assert r.status_code == 503
            assert r.json()["error"]["code"] == "service_unavailable"
            assert "127.0.0.1" not in r.text and "asyncpg" not in r.text.lower()
            ready = await client.get("/ready")
            assert ready.status_code == 503 and ready.json()["database"] is False


PRODUCTION_LIKE = dict(
    app_env="production",
    app_name="NetSec AI",
    secret_key="s" * 40,
    auth_internal_token="t" * 40,
    llm_provider="openai_compatible",
    llm_primary_api_key="primary-key",
    embedding_provider="openai_compatible",
    embedding_api_key="embedding-key",
    email_backend="smtp",
    database_url="postgresql+asyncpg://u:p@db/x",
)


async def test_chat_rejects_u0000_before_storage(client, app):
    account = await register(client, app)
    response = await client.post("/v1/chat/stream", json={"message": "show\u0000run"}, headers=account["headers"])
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


def test_production_refuses_unsafe_configuration():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(make_settings(**{**PRODUCTION_LIKE, "secret_key": "short"}))
    with pytest.raises(RuntimeError, match="AUTH_INTERNAL_TOKEN"):
        create_app(make_settings(**{**PRODUCTION_LIKE, "auth_internal_token": ""}))
    with pytest.raises(RuntimeError, match="LLM_PROVIDER=fake"):
        create_app(make_settings(**{**PRODUCTION_LIKE, "llm_provider": "fake"}))
    with pytest.raises(RuntimeError, match="EMBEDDING_PROVIDER=fake"):
        create_app(make_settings(**{**PRODUCTION_LIKE, "embedding_provider": "fake"}))
    with pytest.raises(RuntimeError, match="MEMORY_RECALL_ENABLED requires MEMORY_ENABLED"):
        create_app(make_settings(**{**PRODUCTION_LIKE, "memory_enabled": False, "memory_recall_enabled": True}))
    with pytest.raises(RuntimeError, match="APP_NAME must be NetSec AI"):
        create_app(make_settings(**{**PRODUCTION_LIKE, "app_name": "Assistant"}))
    with pytest.raises(RuntimeError, match="PUBLIC_APP_URL must be a bare HTTPS origin"):
        create_app(make_settings(**{**PRODUCTION_LIKE, "public_app_url": "https://padhai.example/path"}))


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"llm_primary_api_key": ""}, "LLM primary provider needs LLM_PRIMARY_API_KEY"),
        ({"llm_primary_base_url": "http://provider.example/v1"}, "LLM_PRIMARY_BASE_URL"),
        (
            {"llm_fallback_model": "fallback", "llm_fallback_api_key": ""},
            "enabled LLM fallback needs LLM_FALLBACK_API_KEY",
        ),
        ({"embedding_api_key": ""}, "enabled embeddings need EMBEDDING_API_KEY"),
        ({"embedding_base_url": "http://provider.example/v1"}, "EMBEDDING_BASE_URL"),
        ({"ncert_kb_base_url": "http://127.0.0.1:4200", "ncert_kb_api_key": "key"}, "NCERT_KB_"),
        ({"intent_router": "on", "ollalink_api_key": "key"}, "INTENT_ROUTER=on"),
        ({"study_scope": "shadow", "ollalink_api_key": "key"}, "STUDY_SCOPE must be off"),
    ],
)
def test_production_refuses_unsafe_provider_configuration(overrides, problem):
    with pytest.raises(RuntimeError, match=problem):
        make_settings(**{**PRODUCTION_LIKE, **overrides}).validate_for_env()


def test_production_allows_shadow_intent_and_explicit_loopback_provider_urls():
    make_settings(
        **{
            **PRODUCTION_LIKE,
            "intent_router": "shadow",
            "ollalink_api_key": "key",
            "llm_primary_base_url": "http://127.0.0.1:11434/v1",
            "embedding_base_url": "http://localhost:11434/v1",
        }
    ).validate_for_env()


@pytest.mark.parametrize("name", ["LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"])
def test_production_refuses_active_tracing_names_without_values(monkeypatch, name):
    secret = "trace-value-must-not-appear"
    monkeypatch.setenv(name, secret)
    make_settings(**PRODUCTION_LIKE).validate_for_env()
    monkeypatch.setenv(name, "true")
    with pytest.raises(RuntimeError) as error:
        make_settings(**PRODUCTION_LIKE).validate_for_env()
    assert name not in str(error.value) and secret not in str(error.value)


def test_production_hides_api_docs():
    app = create_app(make_settings(**PRODUCTION_LIKE))
    assert app.docs_url is None and app.openapi_url is None and app.redoc_url is None


def test_release_is_read_from_the_release_root(tmp_path):
    from app.health import RELEASE_FILE, read_release

    # the file sits at the root of the release: backend/app/health.py -> ../../RELEASE
    assert RELEASE_FILE.parent.name != "backend" and (RELEASE_FILE.parent / "backend").is_dir()
    release = tmp_path / "RELEASE"
    release.write_text("commit=0123456789abcdef0123456789abcdef01234567\nprepared=2026-09-24T00:00:00Z\n")
    assert read_release(release) == "0123456789abcdef0123456789abcdef01234567"
    assert read_release(tmp_path / "missing") == "development"
    release.write_text("prepared=2026-09-24T00:00:00Z\n")
    assert read_release(release) == "development"
