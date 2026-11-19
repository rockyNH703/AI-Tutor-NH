# ruff: noqa: F811  (pytest fixtures imported by name)
"""Session 1's red team of the certification bank turn (2026-11-19, on a83df21), kept as regression tests: each test
states the property it probed. R1 (ordinary messages taken for quiz requests), R3 (the answer route ignored the hiding
rule) and R4 (a hidden reply went to the model as history) were findings, fixed after a83df21."""

import json
import uuid

import pytest
from sqlalchemy import text

from tests.conftest import make_settings, register, stream_chat
from tests.test_qbank_certification import _student, bank  # noqa: F401  (fixture)


@pytest.fixture
def settings():
    return make_settings(question_bank_tests=True, question_bank_turn="on")


@pytest.mark.parametrize(
    "message",
    [
        "create a test VLAN for the lab",  # a topic matches: a VLAN quiz instead of a config
        "generate test traffic with iperf",  # nothing matches: "no stored questions" instead of an answer
        "I need questions answered about VLANs: why is my trunk not passing vlan 20?",
    ],
)
async def test_r1_an_engineering_message_reaches_the_model(client, app, fake_llm, bank, message):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, message)
    assert events[-1][0] == "done"
    assert len(fake_llm.calls) == 1, events[-1][1]["message"]["content"][:200]


async def test_r2_no_key_or_explanation_anywhere_a_client_reads_before_answering(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "give me 6 multiple correct questions on vlans")
    done = events[-1][1]
    message = done["message"]
    conversation = done["conversation"]["id"]
    listed = (await client.get(f"/v1/conversations/{conversation}/messages", headers=headers)).json()["items"][-1]
    async with app.state.db.sessionmaker() as db:
        stored = await db.execute(
            text("SELECT q.explanation FROM qb_questions q WHERE q.topic_id = 'CCNA-D2-T01'"),
        )
        explanations = [row.explanation for row in stored]
    for shown in (json.dumps(message), json.dumps(listed), json.dumps(events)):
        assert not any(explanation in shown for explanation in explanations)
        assert '"correctList": [' not in shown and '"key"' not in shown
    test = listed["metadata"]["assessments"][0]
    assert {q["kind"] for q in test["questions"]} == {"multi"}  # multiOnly honoured
    assert all(q["correct"] is None and q["correctList"] is None for q in test["questions"])


async def test_r3_a_reader_who_lost_access_cannot_read_the_hidden_test_through_the_answer_route(
    client, app, fake_llm, bank
):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "quiz me on vlans")
    message = events[-1][1]["message"]
    test = message["metadata"]["assessments"][0]
    app.state.settings.nhprep_allowed_email_domains = "example.org"  # no longer a student
    body = {"position": 0, "hash": test["hash"], "question": 0, "action": "skip"}
    reply = await client.post(f"/v1/messages/{message['id']}/assessment-responses", json=body, headers=headers)
    # The list hides this reply ("This reply isn't available."); the answer route should not show it either.
    assert reply.status_code in (403, 404, 409), reply.text[:300]


async def test_r4_a_hidden_reply_is_not_sent_to_the_model_as_history(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "quiz me on vlans")
    conversation = events[-1][1]["conversation"]["id"]
    stem = events[-1][1]["message"]["metadata"]["assessments"][0]["questions"][0]["text"]
    app.state.settings.nhprep_allowed_email_domains = "example.org"
    await stream_chat(client, headers, "explain the first question above", conversation_id=conversation)
    assert len(fake_llm.calls) == 1
    assert not any(stem in m.content for m in fake_llm.calls[0]), "NHprep text reached the model for a non-student"


async def test_r5_next_five_after_losing_access_serves_nothing_nhprep(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "quiz me on vlans")
    conversation = events[-1][1]["conversation"]["id"]
    app.state.settings.nhprep_allowed_email_domains = "example.org"
    _, events = await stream_chat(client, headers, "next 5", conversation_id=conversation)
    message = events[-1][1]["message"]
    assert "assessments" not in message["metadata"], message["content"][:200]


async def test_r6_next_five_in_a_chat_with_no_bank_test_goes_to_the_model(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "explain vlans")
    conversation = events[-1][1]["conversation"]["id"]
    await stream_chat(client, headers, "next 5", conversation_id=conversation)
    assert len(fake_llm.calls) == 2


async def test_r7_another_users_next_five_cannot_continue_my_test(client, app, fake_llm, bank):
    mine = await _student(client, app)
    _, events = await stream_chat(client, mine, "quiz me on vlans")
    conversation = events[-1][1]["conversation"]["id"]
    other = await _student(client, app)
    response, _ = await stream_chat(client, other, "next 5", conversation_id=conversation)
    assert response.status_code == 404


async def test_r8_count_is_capped_and_short_pools_say_so(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "give me 999 questions on vlans")
    message = events[-1][1]["message"]
    total = message["metadata"]["assessments"][0]["total"]
    assert total <= 30 and ("only" in message["content"] or total == 30)


async def test_r9_a_markup_topic_is_echoed_as_plain_text(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "quiz me on [click](https://evil.invalid) <img src=x>")
    content = events[-1][1]["message"]["content"]
    assert not fake_llm.calls  # the bank answered ("quiz me" asks in so many words), so the escaping is the bank's
    assert "[click](https://evil.invalid)" not in content and "<img" not in content


async def test_r10_a_replayed_answer_does_not_double_count(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "quiz me on vlans")
    message = events[-1][1]["message"]
    test = message["metadata"]["assessments"][0]
    value = [0, 1] if test["questions"][0]["kind"] == "multi" else 0
    body = {"position": 0, "hash": test["hash"], "question": 0, "action": "answer", "value": value}
    for _ in range(2):
        reply = await client.post(f"/v1/messages/{message['id']}/assessment-responses", json=body, headers=headers)
        assert reply.status_code == 200, reply.text[:300]
    async with app.state.db.sessionmaker() as db:
        answered = await db.scalar(
            text(
                "SELECT count(*) FROM qb_set_items i JOIN qb_sets s ON s.id = i.set_id "
                "WHERE s.request_id = :m AND i.answered_at IS NOT NULL"
            ),
            {"m": uuid.UUID(message["id"])},
        )
    assert answered == 1


async def test_r11_a_non_student_in_qa_mode_still_gets_no_nhprep(client, app, fake_llm, bank):
    app.state.settings.question_bank_turn = "qa"
    outsider = await register(client, app)
    app.state.settings.question_bank_qa_emails = outsider["email"]
    _, events = await stream_chat(client, outsider["headers"], "quiz me on vlans")
    assert "assessments" not in events[-1][1]["message"]["metadata"]
