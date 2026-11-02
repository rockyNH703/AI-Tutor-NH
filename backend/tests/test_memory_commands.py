"""Explicit remember/forget commands use one durable receipt and never call the tutor LLM."""

import asyncio
import uuid

from sqlalchemy import select

from app.db.models import MemoryCommandReceipt, MemoryItem, Message
from tests.conftest import register, stream_chat


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
    forgotten, events = await stream_chat(client, headers, f"Forget {statement}")
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
