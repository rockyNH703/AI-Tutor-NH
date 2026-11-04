"""Explicit remember/forget commands use one durable receipt and never call the tutor LLM."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.db.models import MemoryCommandReceipt, MemoryItem, Message, UsageEvent
from app.memory.commands import parse_memory_command
from app.rag.errors import EmbeddingError
from tests.conftest import parse_sse, register, stream_chat


async def test_remember_command_persists_once_and_replays(client, app, fake_llm):
    account = await register(client, app)
    request_id = uuid.uuid4()
    body = {"message": "Remember that I prefer concise analogies", "clientRequestId": str(request_id)}

    before = len(fake_llm.calls)
    first = await client.post("/v1/chat/stream", json=body, headers=account["headers"])
    second = await client.post("/v1/chat/stream", json=body, headers=account["headers"])

    assert first.status_code == second.status_code == 200
    assert "I’ll remember that." in first.text and "I’ll remember that." in second.text
    assert len(fake_llm.calls) == before
    async with app.state.db.sessionmaker() as db:
        memories = list(
            await db.scalars(select(MemoryItem).where(MemoryItem.user_id == uuid.UUID(account["user"]["id"])))
        )
        receipts = list(
            await db.scalars(
                select(MemoryCommandReceipt).where(
                    MemoryCommandReceipt.user_id == uuid.UUID(account["user"]["id"]),
                    MemoryCommandReceipt.client_request_id == str(request_id),
                )
            )
        )
        assert len(memories) == len(receipts) == 1
        messages = list(
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == receipts[0].conversation_id)
                .order_by(Message.created_at)
            )
        )
        assert [(message.role, message.status) for message in messages] == [
            ("user", "complete"),
            ("assistant", "complete"),
        ]
        assert memories[0].origin == "explicit_chat" and receipts[0].status == "completed"


async def test_clear_wins_if_remember_embedding_is_in_flight(client, app):
    account = await register(client, app)
    headers = account["headers"]
    started = asyncio.Event()
    release = asyncio.Event()
    original = app.state.embeddings.aembed_documents

    async def blocked(texts):
        started.set()
        await release.wait()
        return await original(texts)

    app.state.embeddings.aembed_documents = blocked
    try:
        remember = asyncio.create_task(
            client.post(
                "/v1/chat/stream",
                json={
                    "message": "Remember that I use OSPF area 10 in the lab",
                    "clientRequestId": str(uuid.uuid4()),
                },
                headers=headers,
            )
        )
        await started.wait()
        cleared = await client.delete("/v1/memories", headers=headers)
        assert cleared.status_code == 204
        release.set()
        response = await remember
    finally:
        release.set()
        app.state.embeddings.aembed_documents = original

    assert response.status_code == 200
    assert "I’ll remember that." not in response.text
    user_id = uuid.UUID(account["user"]["id"])
    async with app.state.db.sessionmaker() as db:
        assert list(await db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id))) == []
        assert list(await db.scalars(select(MemoryCommandReceipt).where(MemoryCommandReceipt.user_id == user_id))) == []


async def test_forget_command_exact_match_and_no_resurrection(client, app, fake_llm):
    account = await register(client, app)
    headers = account["headers"]
    statement = "I prefer concise analogies"
    await client.post(
        "/v1/chat/stream",
        json={"message": f"Remember that {statement}", "clientRequestId": str(uuid.uuid4())},
        headers=headers,
    )
    before = len(fake_llm.calls)
    forgotten, events = await stream_chat(client, headers, f"Forget that {statement}")
    assert forgotten.status_code == 200 and any("I forgot" in str(data) for _, data in events)
    assert len(fake_llm.calls) == before

    restored = await client.post(
        "/v1/chat/stream",
        json={"message": f"Remember that {statement}", "clientRequestId": str(uuid.uuid4())},
        headers=headers,
    )
    assert "won’t restore" in restored.text
    async with app.state.db.sessionmaker() as db:
        assert (
            await db.scalar(select(MemoryItem.id).where(MemoryItem.user_id == uuid.UUID(account["user"]["id"]))) is None
        )


async def test_memory_command_request_id_conflict_and_noncommand_regression(client, app, fake_llm):
    account = await register(client, app)
    request_id = str(uuid.uuid4())
    first = await client.post(
        "/v1/chat/stream",
        json={"message": "Remember that I like train examples", "clientRequestId": request_id},
        headers=account["headers"],
    )
    assert first.status_code == 200
    conflict = await client.post(
        "/v1/chat/stream",
        json={"message": "Remember that I like map examples", "clientRequestId": request_id},
        headers=account["headers"],
    )
    assert conflict.status_code == 409

    before = len(fake_llm.calls)
    response, events = await stream_chat(client, account["headers"], "Explain osmosis")
    assert response.status_code == 200 and events[-1][0] == "done"
    assert len(fake_llm.calls) > before


async def test_with_memory_off_a_remember_command_is_an_ordinary_message(client, app, fake_llm):
    # MEMORY_ENABLED=false turns every /v1/memories route off; chat must not store memory behind it.
    from tests.conftest import make_settings

    app.state.chat.settings = make_settings(memory_enabled=False)
    account = await register(client, app)
    before = len(fake_llm.calls)
    response = await client.post(
        "/v1/chat/stream",
        json={"message": "Remember that I run Junos 23.4 on the core", "clientRequestId": str(uuid.uuid4())},
        headers=account["headers"],
    )
    assert response.status_code == 200 and "I’ll remember that." not in response.text
    assert len(fake_llm.calls) == before + 1  # answered by the assistant like any other message
    async with app.state.db.sessionmaker() as db:
        assert list(await db.scalars(select(MemoryItem))) == []
        assert list(await db.scalars(select(MemoryCommandReceipt))) == []


# Only the explicit one-line forms are commands: questions, pastes and "forget about ..." reach the assistant.
@pytest.mark.parametrize(
    "text",
    [
        "Remember how BGP picks a path?",
        "Remember when we covered OSPF areas? Explain stub areas again",
        "Forget about NAT, explain PAT",
        "Forget about OSPF, how do I configure BGP?",
        "Remember\nrouter ospf 1\n network 10.0.0.0 0.0.0.255 area 0",
        "Remember that this config is ours:\ninterface Gi0/1\n shutdown",
        "Remember the 7 OSI layers",
        "Forget my last question",
    ],
)
def test_questions_and_pastes_are_not_memory_commands(text):
    assert parse_memory_command(text) is None


@pytest.mark.parametrize(
    ("text", "kind", "content"),
    [
        ("Remember that I run Junos 23.4 on the core.", "remember", "I run Junos 23.4 on the core"),
        ("  remember that our lab uses OSPF area 10!  ", "remember", "our lab uses OSPF area 10"),
        ("Forget that I run Junos 23.4 on the core", "forget", "I run Junos 23.4 on the core"),
    ],
)
def test_explicit_one_line_forms_are_memory_commands(text, kind, content):
    command = parse_memory_command(text)
    assert command is not None and (command.kind, command.content) == (kind, content)


async def _command(client, headers, message: str):
    response = await client.post(
        "/v1/chat/stream", json={"message": message, "clientRequestId": str(uuid.uuid4())}, headers=headers
    )
    return response, parse_sse(response.text)


async def _usage_outcomes(app, user_id: uuid.UUID) -> list[str]:
    async with app.state.db.sessionmaker() as db:
        return list(await db.scalars(select(UsageEvent.outcome).where(UsageEvent.user_id == user_id)))


async def test_a_refused_remember_ends_the_turn_and_frees_the_chat(client, app, fake_llm):
    account = await register(client, app)
    headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])

    response, events = await _command(client, headers, "Remember that my enable password is cisco123")

    assert response.status_code == 200 and events[-1][0] == "done"
    reply = events[-1][1]["message"]
    assert reply["status"] == "complete"
    assert reply["content"] == "I can’t save that: Secrets and credentials cannot be stored in memory."
    async with app.state.db.sessionmaker() as db:
        assert list(await db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id))) == []
        receipt = await db.scalar(select(MemoryCommandReceipt).where(MemoryCommandReceipt.user_id == user_id))
        assert receipt.status == "completed"
    assert await _usage_outcomes(app, user_id) == ["complete"]
    follow_up, follow_events = await stream_chat(
        client, headers, "Explain OSPF area types", events[0][1]["conversationId"]
    )
    assert follow_up.status_code == 200 and follow_events[-1][0] == "done"  # the chat is not locked for 300 s


async def test_remembering_the_same_fact_twice_gets_a_reply(client, app):
    account = await register(client, app)
    headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])

    first, _ = await _command(client, headers, "Remember that our lab uses OSPF area 10")
    second, events = await _command(client, headers, "Remember that our lab uses OSPF area 10")

    assert first.status_code == second.status_code == 200
    assert events[-1][0] == "done"
    assert events[-1][1]["message"]["content"] == "That is already in your memory."
    async with app.state.db.sessionmaker() as db:
        assert len(list(await db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id)))) == 1


async def test_a_failed_memory_embedding_ends_with_an_error_and_frees_the_chat(client, app, monkeypatch):
    account = await register(client, app)
    headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])

    async def failing(texts):
        raise EmbeddingError("embedding_failed", "provider down")

    monkeypatch.setattr(app.state.embeddings, "aembed_documents", failing)
    response, events = await _command(client, headers, "Remember that our core runs Junos 23.4")
    monkeypatch.undo()

    assert response.status_code == 200 and events[-1][0] == "error"
    assert events[-1][1]["code"] == "service_unavailable"
    async with app.state.db.sessionmaker() as db:
        receipt = await db.scalar(select(MemoryCommandReceipt).where(MemoryCommandReceipt.user_id == user_id))
        assert receipt.status == "failed"
        statuses = list(
            await db.scalars(
                select(Message.status).where(Message.conversation_id == uuid.UUID(events[0][1]["conversationId"]))
            )
        )
        assert "streaming" not in statuses
    assert await _usage_outcomes(app, user_id) == ["error"]
    follow_up, follow_events = await stream_chat(
        client, headers, "Explain OSPF area types", events[0][1]["conversationId"]
    )
    assert follow_up.status_code == 200 and follow_events[-1][0] == "done"


async def test_a_command_still_ends_its_stream_when_the_database_fails_while_ending_it(client, app, monkeypatch):
    account = await register(client, app)

    async def database_down(turn, refusal):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(app.state.chat, "_end_command", database_down)
    response, events = await _command(client, account["headers"], "Remember that my enable password is cisco123")

    assert response.status_code == 200 and events[-1][0] == "error"
    assert events[-1][1]["code"] == "service_unavailable"


async def test_a_question_starting_with_remember_is_answered_not_stored(client, app, fake_llm):
    account = await register(client, app)
    user_id = uuid.UUID(account["user"]["id"])
    before = len(fake_llm.calls)

    response, events = await _command(client, account["headers"], "Remember how BGP picks a path?")

    assert response.status_code == 200 and events[-1][0] == "done"
    assert len(fake_llm.calls) == before + 1
    async with app.state.db.sessionmaker() as db:
        assert list(await db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id))) == []
        assert list(await db.scalars(select(MemoryCommandReceipt).where(MemoryCommandReceipt.user_id == user_id))) == []


async def test_a_memory_command_closes_its_usage_row(client, app):
    account = await register(client, app)
    user_id = uuid.UUID(account["user"]["id"])

    response, events = await _command(client, account["headers"], "Remember that our lab uses OSPF area 10")

    assert response.status_code == 200 and events[-1][0] == "done"
    assert await _usage_outcomes(app, user_id) == ["complete"]


async def test_the_remember_command_keeps_the_memory_cap(client, app, monkeypatch):
    monkeypatch.setattr("app.memory.service.MAX_ACTIVE_MEMORIES", 1)
    account = await register(client, app)
    made = await client.post(
        "/v1/memories", json={"kind": "study_context", "content": "Our lab runs EVE-NG."}, headers=account["headers"]
    )
    assert made.status_code == 201, made.text

    _, events = await stream_chat(client, account["headers"], "Remember that our lab uses OSPF area 10")

    done = events[-1]
    assert done[0] == "done" and "the most you can keep" in done[1]["message"]["content"]
    listed = (await client.get("/v1/memories", headers=account["headers"])).json()["items"]
    assert [item["content"] for item in listed] == ["Our lab runs EVE-NG."]
