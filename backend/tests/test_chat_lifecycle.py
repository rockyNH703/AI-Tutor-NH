"""A chat turn's lifecycle under load and failure (second deep audit, G2). Each test failed on a1c1501 first."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager

import asyncpg
import httpx
import pytest
from sqlalchemy import select

from app.auth.emails import MemoryEmailSender
from app.chat.service import ChatService
from app.chat.streams import ActiveGenerations, Generation
from app.db.models import Conversation, Message, UsageEvent
from app.llm.base import ChatMessage, GenerationOptions, LLMTimeout, LLMUnavailable
from app.llm.fake import FakeProvider
from app.main import create_app
from tests.conftest import TEST_DATABASE_URL, _asyncpg_dsn, make_settings, register, stream_chat
from tests.test_llm import chunk, provider, sse_body


class _ProbingLLM(FakeProvider):
    """A fake model that, when asked for a reply, counts the connections left idle in a transaction."""

    def __init__(self) -> None:
        super().__init__()
        self.idle_in_transaction: int | None = None

    async def stream(self, messages, options):
        conn = await asyncpg.connect(_asyncpg_dsn(TEST_DATABASE_URL))
        try:
            self.idle_in_transaction = await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
            )
        finally:
            await conn.close()
        return await super().stream(messages, options)


@pytest.fixture
def fake_llm() -> _ProbingLLM:
    return _ProbingLLM()


@asynccontextmanager
async def _app_with(**overrides):
    application = create_app(make_settings(**overrides), llm=FakeProvider(), email_sender=MemoryEmailSender())
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield application, http


async def _rows(app, model, *where):
    async with app.state.db.sessionmaker() as db:
        return list(await db.scalars(select(model).where(*where)))


async def _seed_streaming(app, user_id: uuid.UUID, count: int) -> None:
    """`count` replies of this user being generated right now, each in its own chat."""
    async with app.state.db.sessionmaker() as db, db.begin():
        for _ in range(count):
            conversation = Conversation(user_id=user_id, title="Busy")
            db.add(conversation)
            await db.flush()
            message = Message(conversation_id=conversation.id, role="assistant", content="", status="streaming")
            db.add(message)
            await db.flush()
            db.add(
                UsageEvent(user_id=user_id, conversation_id=conversation.id, message_id=message.id, outcome="streaming")
            )


# ------------------------------------------------------------- K-07a: no connection held through a stream
async def test_a_reply_being_generated_leaves_no_connection_idle_in_a_transaction(client, app, fake_llm):
    headers = (await register(client, app))["headers"]
    response, events = await stream_chat(client, headers, "Explain OSPF areas")
    assert response.status_code == 200 and events[-1][0] == "done", events[-1:]
    assert fake_llm.idle_in_transaction == 0


# ------------------------------------------------------------- MISS-chat-1: an empty completion is an error
async def test_an_empty_completion_is_an_error_not_a_blank_reply(client, app, fake_llm):
    fake_llm.reply = lambda messages: ""
    headers = (await register(client, app))["headers"]
    _, events = await stream_chat(client, headers, "Explain OSPF areas")
    kind, data = events[-1]
    assert kind == "error" and data["code"] == "llm_unavailable", events[-1]
    [message] = await _rows(app, Message, Message.id == uuid.UUID(data["messageId"]))
    assert message.status == "error"


async def test_a_provider_stream_that_ends_without_text_is_unavailable_so_the_fallback_answers():
    def handler(request):
        body = sse_body([chunk(role="assistant", content=""), chunk(finish="stop")])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    with pytest.raises(LLMUnavailable):
        await provider(handler).stream([ChatMessage("user", "hi")], GenerationOptions())


# ------------------------------------------------------------- K-18b: one deadline to the first token
async def test_the_first_token_has_one_deadline_and_a_stalled_response_is_closed():
    closed = asyncio.Event()

    async def drip():
        try:  # role-only chunks, each well inside the timeout, never any text
            for _ in range(12):
                await asyncio.sleep(0.1)
                yield f"data: {json.dumps(chunk(role='assistant', content=''))}\n\n".encode()
            yield b"data: [DONE]\n\n"
        finally:
            closed.set()

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=drip())

    started = time.monotonic()
    with pytest.raises(LLMTimeout):
        await provider(handler, first_token_timeout_s=0.35).stream([ChatMessage("user", "hi")], GenerationOptions())
    assert time.monotonic() - started < 0.9
    await asyncio.wait_for(closed.wait(), 2)  # the HTTP response was closed: the provider stops generating


# ------------------------------------------------------------- MISS-chat-2: the cancel poll cannot hang a turn
class _HangingSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, *args, **kwargs):
        await asyncio.Event().wait()  # a database that never answers


class _HangingDatabase:
    def sessionmaker(self):
        return _HangingSession()


async def test_a_cancel_poll_against_a_database_that_never_answers_gives_up():
    settings = make_settings(stream_cancel_poll_s=0.1)
    service = ChatService(_HangingDatabase(), settings, rag_graph=None, generations=ActiveGenerations())
    assert await asyncio.wait_for(service._cancel_requested(uuid.uuid4()), 2) is False


# ------------------------------------------------------------- MISS-chat-3: deleting a chat stops its reply
async def test_deleting_a_chat_while_its_reply_streams_stops_the_generation(client, app, fake_llm):
    fake_llm.token_delay_s = 0.05
    account = await register(client, app)
    headers = account["headers"]
    conversation = (await client.post("/v1/conversations", json={}, headers=headers)).json()
    streaming = asyncio.create_task(stream_chat(client, headers, " ".join(["word"] * 200), conversation["id"]))
    for _ in range(200):
        if fake_llm.emitted >= 3:
            break
        await asyncio.sleep(0.02)
    deleted = await client.delete(f"/v1/conversations/{conversation['id']}", headers=headers)
    assert deleted.status_code == 204
    await asyncio.wait_for(streaming, 15)
    assert fake_llm.emitted < 100  # the reply has 202 words: it stopped soon after its chat went away
    assert await _rows(app, UsageEvent, UsageEvent.outcome == "streaming") == []  # nothing left counting as live


# ------------------------------------------------------------- MISS-chat-5: what the model sees of earlier turns
async def test_a_stopped_reply_is_marked_and_a_failed_one_leaves_no_two_questions_in_a_row(client, app, fake_llm):
    account = await register(client, app)
    headers = account["headers"]
    conversation = (await client.post("/v1/conversations", json={}, headers=headers)).json()
    conversation_id = uuid.UUID(conversation["id"])
    async with app.state.db.sessionmaker() as db, db.begin():
        for role, content, status in (
            ("user", "What is OSPF?", "complete"),
            ("assistant", "OSPF is a link-state", "cancelled"),
            ("user", "And BGP?", "complete"),
            ("assistant", "", "error"),
        ):
            db.add(Message(conversation_id=conversation_id, role=role, content=content, status=status))
            await db.flush()
    _, events = await stream_chat(client, headers, "Compare them", conversation["id"])
    assert events[-1][0] == "done"
    sent = [message for message in fake_llm.calls[-1] if message.role != "system"]
    roles = [message.role for message in sent]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False)), roles  # roles alternate
    stopped = next(message for message in sent if message.content.startswith("OSPF is a link-state"))
    assert "stopped" in stopped.content.lower()


# ------------------------------------------------------------- MISS-chat-6: a memory acknowledgement is not regenerated
async def test_regenerating_a_memory_command_acknowledgement_is_refused(client, app, fake_llm):
    headers = (await register(client, app))["headers"]
    _, events = await stream_chat(client, headers, "Remember that I run IOS XE 17 in my lab")
    assert events[-1][0] == "done", events[-1]
    conversation_id = events[0][1]["conversationId"]
    calls = len(fake_llm.calls)
    response = await client.post(f"/v1/conversations/{conversation_id}/regenerate/stream", headers=headers)
    assert response.status_code == 409, response.text
    assert len(fake_llm.calls) == calls  # the command text never went to the model


# ------------------------------------------------------------- K-47d: a failed regeneration keeps the good answer
async def test_a_failed_regeneration_keeps_the_reply_it_was_meant_to_replace(client, app, fake_llm):
    headers = (await register(client, app))["headers"]
    _, events = await stream_chat(client, headers, "Explain OSPF areas")
    assert events[-1][0] == "done"
    good = uuid.UUID(events[-1][1]["message"]["id"])
    conversation_id = events[0][1]["conversationId"]
    fake_llm.fail_before_first = LLMUnavailable("down")
    response = await client.post(f"/v1/conversations/{conversation_id}/regenerate/stream", headers=headers)
    assert response.status_code == 200
    [kept] = await _rows(app, Message, Message.id == good)
    assert kept.status == "complete"


# ------------------------------------------------------------- K-07b: a cap on one account's live replies
async def test_one_account_cannot_have_more_than_three_replies_generating_at_once(client, app):
    account = await register(client, app)
    await _seed_streaming(app, uuid.UUID(account["user"]["id"]), 3)
    response = await client.post("/v1/chat/stream", json={"message": "Explain OSPF areas"}, headers=account["headers"])
    assert response.status_code == 429, response.text


async def test_the_process_refuses_new_replies_beyond_its_live_generation_cap():
    async with _app_with(chat_live_generations_max=1) as (application, http):
        headers = (await register(http, application))["headers"]
        application.state.chat.generations.add(Generation(message_id=uuid.uuid4(), queue=asyncio.Queue()))
        response = await http.post("/v1/chat/stream", json={"message": "Explain OSPF areas"}, headers=headers)
        assert response.status_code == 503, response.text


# ------------------------------------------------------------- K-11a: a kill switch and a global daily cap
async def test_chat_can_be_switched_off_without_a_deploy():
    async with _app_with(chat_enabled=False) as (application, http):
        headers = (await register(http, application))["headers"]
        response = await http.post("/v1/chat/stream", json={"message": "Explain OSPF areas"}, headers=headers)
        assert response.status_code == 503, response.text


async def test_a_global_daily_cap_stops_new_replies_for_everyone():
    async with _app_with(chat_generations_global_per_day=1) as (application, http):
        first = (await register(http, application))["headers"]
        second = (await register(http, application))["headers"]
        assert (
            await http.post("/v1/chat/stream", json={"message": "Explain OSPF areas"}, headers=first)
        ).status_code == 200
        refused = await http.post("/v1/chat/stream", json={"message": "Explain BGP"}, headers=second)
        assert refused.status_code == 429, refused.text


# ------------------------------------------------------------- plan E: question received -> first model request
async def test_the_time_to_the_first_model_request_is_recorded_from_the_bff_stamp_and_kept_internal(
    client, app, fake_llm
):
    account = await register(client, app)
    stamped = {**account["headers"], "X-NetSec-Received-At": str(int(time.time() * 1000) - 400)}
    _, events = await stream_chat(client, stamped, "Explain OSPF areas")
    assert events[-1][0] == "done", events[-1:]
    forged = {**account["headers"], "X-NetSec-Received-At": "1"}  # implausible: the API's own clock is used
    _, events = await stream_chat(client, forged, "Explain BGP")
    assert events[-1][0] == "done", events[-1:]

    replies = await _rows(app, Message, Message.role == "assistant")
    first, second = sorted(replies, key=lambda m: m.created_at)
    assert 400 <= first.metadata_["timing"]["preLlmMs"] < 30_000
    assert 0 <= second.metadata_["timing"]["preLlmMs"] < 30_000
    page = await client.get(f"/v1/conversations/{first.conversation_id}/messages", headers=account["headers"])
    assert all("timing" not in item["metadata"] for item in page.json()["items"])  # internal, never served


async def test_the_pre_llm_report_reads_the_recorded_intervals_and_judges_p99_against_the_budget(client, app):
    from datetime import UTC, datetime, timedelta

    from scripts import pre_llm_report

    headers = (await register(client, app))["headers"]
    for question in ("Explain OSPF areas", "Explain BGP"):
        _, events = await stream_chat(client, headers, question)
        assert events[-1][0] == "done", events[-1:]
    async with app.state.db.sessionmaker() as db:
        values = await pre_llm_report.collect(db, datetime.now(UTC) - timedelta(hours=1))
    assert len(values) == 2 and all(value >= 0 for value in values)
    assert pre_llm_report.summary([100] * 98 + [900, 1500], 1000) == {
        "turns": 100,
        "p50Ms": 100,
        "p95Ms": 100,
        "p99Ms": 900,
        "maxMs": 1500,
        "overBudget": 1,
        "budgetMs": 1000,
        "pass": True,
    }
    assert pre_llm_report.summary([], 1000) == {"turns": 0}
