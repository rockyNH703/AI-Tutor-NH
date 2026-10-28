"""Durable automatic-memory extraction jobs and their chat outbox hook."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.db.models import MemoryExtractionJob, MemoryProposal, UserSettings
from app.jobs.memory import process_memory_jobs
from app.llm.base import GenerationOptions, LLMUnavailable
from app.llm.fake import FakeProvider
from app.memory.extraction import ExtractionOutputError, collect_bounded_completion, parse_extraction_output
from tests.conftest import make_settings, register, stream_chat


def extraction_reply(proposals: list[dict]) -> str:
    return json.dumps({"proposals": proposals})


def proposal(content: str = "Use diagrams when explaining electric circuits.") -> dict:
    return {
        "operation": "add",
        "kind": "learning_context",
        "content": content,
        "confidence": 0.93,
        "evidenceReason": "The user explicitly requested this durable explanation style.",
    }


async def enable_automatic_proposals(app, account, epoch: int = 0) -> None:
    async with app.state.db.sessionmaker() as session:
        session.add(
            UserSettings(
                user_id=uuid.UUID(account["user"]["id"]),
                preferences={"memoryAutomaticProposals": True, "memoryConsentEpoch": epoch},
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def rows(app, model):
    async with app.state.db.sessionmaker() as session:
        return list(await session.scalars(select(model).order_by(model.created_at, model.id)))


async def test_chat_enqueues_in_completion_transaction_only_when_on_and_consented(client, app):
    account = await register(client, app)
    await enable_automatic_proposals(app, account, epoch=3)
    app.state.chat.settings = make_settings(memory_automatic_proposals="on")

    response, events = await stream_chat(client, account["headers"], "Please use diagrams for circuit explanations.")
    assert response.status_code == 200 and events[-1][0] == "done"
    [job] = await rows(app, MemoryExtractionJob)
    assert job.status == "queued" and job.consent_epoch == 3
    assert job.source_message_id == uuid.UUID(events[0][1]["userMessage"]["id"])
    assert job.assistant_message_id == uuid.UUID(events[-1][1]["message"]["id"])

    app.state.chat.settings = make_settings(memory_automatic_proposals="shadow")
    await stream_chat(client, account["headers"], "I also like concise examples.")
    assert len(await rows(app, MemoryExtractionJob)) == 1


async def test_worker_uses_only_bounded_user_text_and_inserts_idempotently(client, app):
    account = await register(client, app)
    await enable_automatic_proposals(app, account)
    settings = make_settings(
        memory_automatic_proposals="on",
        memory_worker_max_input_chars=100,
        memory_worker_max_per_run=5,
    )
    app.state.chat.settings = settings
    user_text = "Use diagrams for electricity. " + ("private-user-tail " * 20)
    await stream_chat(client, account["headers"], user_text)
    llm = FakeProvider(reply=lambda messages: extraction_reply([proposal()]))

    report = await process_memory_jobs(app.state.db, settings, llm)
    assert report == {"claimed": 1, "completed": 1, "retryable": 0, "dead": 0, "invalidated": 0, "proposals": 1}
    sent = llm.calls[0]
    assert [message.role for message in sent] == ["system", "user"]
    assert sent[-1].content == user_text[:100]
    assert "You said:" not in sent[-1].content
    [stored] = await rows(app, MemoryProposal)
    assert stored.status == "pending" and stored.content == proposal()["content"]

    async with app.state.db.sessionmaker() as session:
        job = (await session.scalars(select(MemoryExtractionJob))).one()
        job.status = "retryable"
        job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    second = await process_memory_jobs(app.state.db, settings, llm)
    assert second["completed"] == 1 and second["proposals"] == 0
    assert len(await rows(app, MemoryProposal)) == 1


async def test_worker_rechecks_consent_before_provider_and_before_insert(client, app):
    account = await register(client, app)
    await enable_automatic_proposals(app, account, epoch=1)
    settings = make_settings(memory_automatic_proposals="on")
    app.state.chat.settings = settings
    await stream_chat(client, account["headers"], "Use diagrams for optics.")

    async with app.state.db.sessionmaker() as session:
        preferences = await session.get(UserSettings, uuid.UUID(account["user"]["id"]))
        preferences.preferences = {"memoryAutomaticProposals": False, "memoryConsentEpoch": 2}
        await session.commit()
    llm = FakeProvider(reply=lambda messages: extraction_reply([proposal()]))
    report = await process_memory_jobs(app.state.db, settings, llm)
    assert report["invalidated"] == 1 and llm.calls == []

    async with app.state.db.sessionmaker() as session:
        await session.execute(text("TRUNCATE memory_extraction_jobs, memory_proposals CASCADE"))
        preferences = await session.get(UserSettings, uuid.UUID(account["user"]["id"]))
        preferences.preferences = {"memoryAutomaticProposals": True, "memoryConsentEpoch": 2}
        await session.commit()
    await stream_chat(client, account["headers"], "Use diagrams for magnetism.")

    class RevokingProvider(FakeProvider):
        async def stream(self, messages, options):
            self.reply = lambda _: extraction_reply([proposal()])
            async with app.state.db.sessionmaker() as session:
                preferences = await session.get(UserSettings, uuid.UUID(account["user"]["id"]))
                preferences.preferences = {"memoryAutomaticProposals": False, "memoryConsentEpoch": 3}
                await session.commit()
            return await super().stream(messages, options)

    report = await process_memory_jobs(app.state.db, settings, RevokingProvider())
    assert report["invalidated"] == 1 and await rows(app, MemoryProposal) == []


async def test_worker_retry_backoff_attempt_cap_and_shadow_non_persistence(client, app):
    account = await register(client, app)
    await enable_automatic_proposals(app, account)
    settings = make_settings(
        memory_automatic_proposals="on",
        memory_worker_max_attempts=2,
        memory_worker_backoff_base_s=10,
        memory_worker_backoff_max_s=20,
    )
    app.state.chat.settings = settings
    await stream_chat(client, account["headers"], "Use diagrams for waves.")
    failing = FakeProvider(fail_before_first=LLMUnavailable("offline"))
    now = datetime.now(UTC)

    first = await process_memory_jobs(app.state.db, settings, failing, now)
    [job] = await rows(app, MemoryExtractionJob)
    assert first["retryable"] == 1 and job.status == "retryable" and job.attempt_count == 1
    assert job.next_attempt_at == now + timedelta(seconds=10)
    second = await process_memory_jobs(app.state.db, settings, failing, job.next_attempt_at)
    [job] = await rows(app, MemoryExtractionJob)
    assert second["dead"] == 1 and job.status == "dead" and job.attempt_count == 2

    async with app.state.db.sessionmaker() as session:
        await session.execute(text("TRUNCATE memory_extraction_jobs, memory_proposals CASCADE"))
        await session.commit()
    app.state.chat.settings = make_settings(memory_automatic_proposals="shadow")
    await stream_chat(client, account["headers"], "Use diagrams for chemistry.")
    assert await rows(app, MemoryExtractionJob) == []


async def test_strict_json_max_three_and_bounded_collector():
    valid = parse_extraction_output(extraction_reply([proposal()] * 3), max_item_chars=2000)
    assert len(valid) == 3
    with pytest.raises(ExtractionOutputError):
        parse_extraction_output(extraction_reply([proposal()] * 4), max_item_chars=2000)
    with pytest.raises(ExtractionOutputError):
        parse_extraction_output('{"proposals":[],"extra":true}', max_item_chars=2000)

    slow = FakeProvider(reply=lambda _: "x" * 50, token_delay_s=0.02)
    with pytest.raises(asyncio.TimeoutError):
        await collect_bounded_completion(
            slow,
            [],
            GenerationOptions(),
            max_chars=100,
            timeout_s=0.001,
        )
