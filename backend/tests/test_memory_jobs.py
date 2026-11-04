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


async def test_worker_serializes_final_insert_with_consent_withdrawal(client, app):
    account = await register(client, app)
    await enable_automatic_proposals(app, account)
    settings = make_settings(memory_automatic_proposals="on")
    app.state.chat.settings = settings
    await stream_chat(client, account["headers"], "Use diagrams for BGP path selection.")
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockedProvider(FakeProvider):
        async def stream(self, messages, options):
            result = await super().stream(messages, options)
            started.set()
            await release.wait()
            return result

    worker = asyncio.create_task(
        process_memory_jobs(
            app.state.db,
            settings,
            BlockedProvider(reply=lambda messages: extraction_reply([proposal()])),
        )
    )
    await started.wait()
    withdrawn = await client.put(
        "/v1/memories/preferences",
        json={"automaticProposals": False, "expectedVersion": 0},
        headers=account["headers"],
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["consentEpoch"] == 1
    release.set()
    report = await worker

    assert report["invalidated"] == 1 and report["proposals"] == 0
    assert await rows(app, MemoryProposal) == []


async def test_malformed_stored_consent_never_breaks_chat_or_other_users_jobs(client, app):
    # Values written before /v1/me/settings refused the memory keys: one user's bad data stays theirs.
    settings = make_settings(memory_automatic_proposals="on")
    app.state.chat.settings = settings
    accounts = [await register(client, app), await register(client, app)]
    for account in accounts:
        await enable_automatic_proposals(app, account)
        await stream_chat(client, account["headers"], "Use diagrams for optics.")
    async with app.state.db.sessionmaker() as session:
        bad = await session.get(UserSettings, uuid.UUID(accounts[0]["user"]["id"]))
        bad.preferences = {"memoryAutomaticProposals": True, "memoryConsentEpoch": "x", "memoryClearedBefore": "nope"}
        await session.commit()

    response, events = await stream_chat(client, accounts[0]["headers"], "Use diagrams for magnetism.")
    assert response.status_code == 200 and events[-1][0] == "done"

    llm = FakeProvider(reply=lambda messages: extraction_reply([proposal()]))
    report = await process_memory_jobs(app.state.db, settings, llm)
    assert report["claimed"] == 3 and report["completed"] == 3
    assert {job.status for job in await rows(app, MemoryExtractionJob)} == {"completed"}

    # Approving reads the same consent the worker did (MemoryPreferences): never a 500.
    mine = [p for p in await rows(app, MemoryProposal) if p.user_id == uuid.UUID(accounts[0]["user"]["id"])]
    assert mine
    approved = await client.post(
        f"/v1/memories/proposals/{mine[0].id}/approve", json={"expectedVersion": 1}, headers=accounts[0]["headers"]
    )
    assert approved.status_code == 200, approved.text


async def test_one_jobs_unexpected_error_never_stops_the_batch(client, app, monkeypatch):
    settings = make_settings(memory_automatic_proposals="on")
    app.state.chat.settings = settings
    accounts = [await register(client, app), await register(client, app)]
    for account in accounts:
        await enable_automatic_proposals(app, account)
        await stream_chat(client, account["headers"], "Use diagrams for optics.")
    broken = uuid.UUID(accounts[0]["user"]["id"])

    import app.jobs.memory as worker

    real = worker._eligibility

    async def eligibility(session, job):
        if job.user_id == broken:
            raise RuntimeError("unexpected")
        return await real(session, job)

    monkeypatch.setattr(worker, "_eligibility", eligibility)
    llm = FakeProvider(reply=lambda messages: extraction_reply([proposal()]))
    report = await process_memory_jobs(app.state.db, settings, llm)
    assert report["claimed"] == 2 and report["completed"] == 1 and report["retryable"] == 1
    statuses = {job.user_id: job.status for job in await rows(app, MemoryExtractionJob)}
    assert statuses[broken] == "retryable" and statuses[uuid.UUID(accounts[1]["user"]["id"])] == "completed"


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


# ------------------------------------------------------------------ K-25b/c/d, K-06h, K-25f, K-48b, K-05c
def test_one_refused_proposal_is_dropped_and_the_others_are_kept():
    secret = proposal("My enable secret 5 is $1$abcd$EFGHijklmnop on the core switch.")
    kept = parse_extraction_output(extraction_reply([secret, proposal()]), max_item_chars=2000)
    assert [item.content for item in kept] == [proposal()["content"]]


def test_the_memory_policy_refuses_what_provider_masking_would_mask():
    from app.errors import ValidationFailed
    from app.memory.policy import validate_memory_policy

    for sample in ("snmp-server community Lab0nly RO", "crypto isakmp key Lab0nlyKey address 0.0.0.0"):
        with pytest.raises(ValidationFailed):
            validate_memory_policy(sample)
    validate_memory_policy("We run OSPF area 0 on Cisco Catalyst 9300 switches.")


def test_a_memory_is_never_longer_than_what_recall_can_use():
    from app.memory.extraction import recallable_chars

    settings = make_settings()
    assert recallable_chars(settings) == min(settings.memory_max_item_chars, settings.memory_max_context_chars)
    too_long = proposal("I prefer Junos examples. " * 60)  # about 1,500 characters
    assert parse_extraction_output(extraction_reply([too_long]), max_item_chars=recallable_chars(settings)) == ()


async def _job_with(app, client, account, settings, content):
    app.state.chat.settings = settings
    await stream_chat(client, account["headers"], "Please use diagrams for circuit explanations.")
    return FakeProvider(reply=lambda messages: extraction_reply([proposal(content)]))


async def test_the_worker_never_proposes_text_the_user_already_rejected_or_saved(client, app):
    account = await register(client, app)
    await enable_automatic_proposals(app, account)
    settings = make_settings(memory_automatic_proposals="on")
    llm = await _job_with(app, client, account, settings, proposal()["content"])
    await process_memory_jobs(app.state.db, settings, llm)
    [first] = await rows(app, MemoryProposal)
    rejected = await client.post(
        f"/v1/memories/proposals/{first.id}/reject", json={"expectedVersion": first.version}, headers=account["headers"]
    )
    assert rejected.status_code in (200, 204), rejected.text

    llm = await _job_with(app, client, account, settings, proposal()["content"])
    report = await process_memory_jobs(app.state.db, settings, llm)

    assert report["completed"] == 1 and report["proposals"] == 0
    assert [p.status for p in await rows(app, MemoryProposal)] == ["rejected"]


async def test_withdrawing_consent_closes_pending_suggestions(client, app):
    account = await register(client, app)
    await enable_automatic_proposals(app, account)
    settings = make_settings(memory_automatic_proposals="on")
    llm = await _job_with(app, client, account, settings, proposal()["content"])
    await process_memory_jobs(app.state.db, settings, llm)
    current = (await client.get("/v1/memories/preferences", headers=account["headers"])).json()

    withdrawn = await client.put(
        "/v1/memories/preferences",
        json={"automaticProposals": False, "expectedVersion": current["version"]},
        headers=account["headers"],
    )

    assert withdrawn.status_code == 200, withdrawn.text
    assert [p.status for p in await rows(app, MemoryProposal)] == ["invalidated"]
    listed = await client.get("/v1/memories/proposals", headers=account["headers"])
    assert listed.json()["items"] == []


async def test_an_account_keeps_a_bounded_number_of_memories(client, app, monkeypatch):
    monkeypatch.setattr("app.memory.service.MAX_ACTIVE_MEMORIES", 2)
    account = await register(client, app)
    for n in range(2):
        made = await client.post(
            "/v1/memories",
            json={"kind": "study_context", "content": f"Lab {n} runs EVE-NG."},
            headers=account["headers"],
        )
        assert made.status_code == 201, made.text

    third = await client.post(
        "/v1/memories", json={"kind": "study_context", "content": "Lab 3 runs GNS3."}, headers=account["headers"]
    )

    assert third.status_code == 409
    assert third.json()["error"]["code"] == "memory_limit_reached"


async def test_recall_sends_nothing_to_the_provider_for_a_user_with_no_memories(app):
    from app.memory.recall import MemoryRecall

    class Counting:
        model = "keyword-hash"
        calls = 0

        async def aembed_query(self, text):
            Counting.calls += 1
            return [0.0] * 1536

    recall = MemoryRecall(app.state.db, make_settings(memory_recall_enabled=True), Counting())
    assert await recall.recall(uuid.uuid4(), "What is BGP?") == ()
    assert Counting.calls == 0
