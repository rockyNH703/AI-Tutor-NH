"""Practice tests over HTTP (POST /v1/messages/{id}/assessment-responses, schemaVersion 2): each action's answer
equals the reloaded reply, the mock-exam and v1 rules hold end to end, the endpoint is rate limited per user, and
no log line holds an answer. Uses only what the API and the test helpers expose (it also runs against the code
before v2, for the fail-first evidence)."""

import asyncio
import json
import logging
import uuid

from sqlalchemy import update

from app.db.models import Message
from app.ratelimit import SlidingWindowLimiter
from tests.conftest import register, stream_chat
from tests.test_assessments import QUIZ, quiz_reply
from tests.test_chat import _log_blob

TOPOLOGY = {
    "nodes": [{"id": "r1", "label": "R1", "kind": "router"}, {"id": "zfw9", "label": "FW", "kind": "firewall"}],
    "links": [{"id": "l1", "from": "r1", "to": "zfw9"}],
}
QUESTIONS = [
    {"question": "Which port does SSH use?", "options": ["22", "23", "443"], "answer": "A", "explanation": "Port 22."},
    {"type": "multi", "question": "Which are private?", "options": ["10.0.0.0/8", "8.8.8.0/24", "192.168.0.0/16"],
     "answer": [0, 2]},
    {"type": "fill", "text": "SSH uses TCP [[22]]."},
    {"type": "numeric", "question": "Usable hosts in a /26?", "answer": 62, "unit": "hosts"},
    {"type": "short", "question": "Why VLANs?", "keyPoints": [["broadcast domain", "broadcast domains"], "segment"]},
    {"type": "long", "question": "Explain the TCP handshake.", "keyPoints": ["SYN", "SYN-ACK", "ACK"]},
    {"type": "match", "question": "Match the ports.", "pairs": [["SSH", "22"], ["HTTPS", "443"], ["DNS", "53"]]},
    {"type": "order", "question": "Order the handshake.", "items": ["SYN", "SYN-ACK", "ACK"]},
    {"type": "cli", "question": "Set the name.", "prompt": "R1(config)#", "answers": ["hostname QUOKKA77"]},
    {"question": "Which line failed?", "listing": {"content": "BGP up\nBGP down\n"}, "select": "lines", "answer": [2]},
    {"type": "diagram", "question": "Pick the firewall.", "topology": TOPOLOGY, "select": "node", "answer": ["zfw9"]},
]  # fmt: skip
# A value for each question (well formed; right or not). The texts are distinctive, for the log check.
VALUES = [
    1, [0, 2], ["PANGOLIN"], "6262.5", "zebracorn broadcast domains", "SYN then wombatix", [0, None, 1], [2, 0, 1],
    "hostname QUOKKA77", [1], ["zfw9"],
]  # fmt: skip
SECRETS = ("PANGOLIN", "6262.5", "zebracorn", "wombatix", "QUOKKA77", "98765.", "55555")


def quiz_text(mode: str, questions: list = QUESTIONS) -> str:
    return (
        "Here is your test.\n\n```quiz\n" + json.dumps({"title": "Net", "mode": mode, "questions": questions}) + "\n```"
    )


async def send(client, headers, message: dict, test: dict, question: int, action: str, value=None, revision=None):
    body = {"position": 0, "hash": test["hash"], "question": question, "action": action}
    body |= {"value": value} if value is not None else {}
    body |= {"revision": revision} if revision is not None else {}
    return await client.post(f"/v1/messages/{message['id']}/assessment-responses", json=body, headers=headers)


async def reloaded(client, headers, message: dict) -> dict:
    page = await client.get(f"/v1/conversations/{message['conversationId']}/messages", headers=headers)
    return page.json()["items"][-1]["metadata"]["assessments"][0]


async def started(client, app, fake_llm, mode: str, questions: list = QUESTIONS):
    account, message = await quiz_reply(client, app, fake_llm, reply=quiz_text(mode, questions), ask="Test me")
    return account["headers"], message, message["metadata"]["assessments"][0]


def without_learning(body: dict) -> dict:
    assert body.pop("learning") is None
    return body


async def test_a_wrong_numeric_try_on_the_first_question_is_pending_and_reveals_nothing(client, app, fake_llm):
    h, message, test = await started(client, app, fake_llm, "practice", [QUESTIONS[3], QUESTIONS[0]])
    tried = await send(client, h, message, test, 0, "answer", "60")
    assert tried.status_code == 200, tried.text
    body = tried.json()
    assert (body["pending"], body["answered"], body["current"], body["state"]) == (
        {"question": 0, "tries": ["60"]},
        0,
        0,
        "active",
    )
    assert body["questions"][0]["accepted"] is None and body["questions"][0]["response"] is None
    assert without_learning(body) == await reloaded(client, h, message)


async def test_each_answer_equals_the_reloaded_reply_for_every_kind_and_mode(client, app, fake_llm):
    h, message, test = await started(client, app, fake_llm, "practice")
    assert test["schemaVersion"] == 2 and [q["kind"] for q in test["questions"]] == [
        "mcq", "multi", "fill_blank", "numeric", "short", "long", "match", "order", "cli", "lines", "diagram",
    ]  # fmt: skip
    for question, value in enumerate(VALUES):
        action = "skip" if question == 3 else "answer"  # numeric: Show answer (a wrong try would be pending)
        answered = await send(client, h, message, test, question, action, None if action == "skip" else value)
        assert answered.status_code == 200, (question, answered.text)
        assert without_learning(answered.json()) == await reloaded(client, h, message), question
    assert answered.json()["state"] == "completed"

    h, message, test = await started(client, app, fake_llm, "exam")
    revision = test["revision"]
    for question in reversed(range(len(VALUES))):  # any order
        saved = await send(client, h, message, test, question, "save", VALUES[question])
        assert saved.status_code == 200, (question, saved.text)
        assert without_learning(saved.json()) == await reloaded(client, h, message), question
        revision = saved.json()["revision"]
    assert all(q["response"] is None and q["correct"] is None for q in saved.json()["questions"])
    submitted = await send(client, h, message, test, 0, "submit", revision=revision)
    assert submitted.status_code == 200, submitted.text
    body = without_learning(submitted.json())
    assert (body["submitted"], body["state"], body["questions"][0]["correct"]) == (True, "completed", 0)
    assert body == await reloaded(client, h, message)


async def test_a_mock_exam_over_http(client, app, fake_llm):
    h, message, test = await started(client, app, fake_llm, "exam", [QUESTIONS[3], QUESTIONS[6]])
    assert (await send(client, h, message, test, 1, "save", [1, None, 0])).status_code == 200
    first = await send(client, h, message, test, 0, "save", "61")
    malformed = await send(client, h, message, test, 0, "save", "12.")
    assert malformed.status_code == 422
    error = malformed.json()["error"]
    assert error["message"].startswith("Question 1: ") and error["fields"] == [
        {"field": "value", "reason": "number", "question": 0}
    ]
    assert (await reloaded(client, h, message))["questions"][0]["draft"] == "61"  # the draft is unchanged
    answer = await send(client, h, message, test, 0, "answer", "62")
    assert answer.status_code == 409 and answer.json()["error"]["code"] == "assessment_exam"
    stale = await send(client, h, message, test, 0, "submit", revision=first.json()["revision"] - 1)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "assessment_stale"
    revision = first.json()["revision"]
    once = await send(client, h, message, test, 0, "submit", revision=revision)
    twice = await send(client, h, message, test, 0, "submit", revision=revision)
    assert once.status_code == twice.status_code == 200 and once.json() == twice.json()
    late = await send(client, h, message, test, 1, "save", [0, 1, 2])
    assert late.status_code == 409 and late.json()["error"]["code"] == "assessment_submitted"


async def test_two_tabs_saving_and_submitting_at_once_record_one_consistent_outcome(client, app, fake_llm):
    h, message, test = await started(client, app, fake_llm, "exam", [QUESTIONS[3], QUESTIONS[0]])
    base = (await send(client, h, message, test, 0, "save", "61")).json()["revision"]
    save, submit = await asyncio.gather(
        send(client, h, message, test, 0, "save", "62"), send(client, h, message, test, 0, "submit", revision=base)
    )
    assert sorted([save.status_code, submit.status_code]) == [200, 409]
    final = await reloaded(client, h, message)
    if submit.status_code == 200:  # the submit was first: the later save was refused, nothing changed after it
        assert save.json()["error"]["code"] == "assessment_submitted"
        assert final["submitted"] and final["questions"][0]["response"]["answer"] == "61"
    else:  # the save was first: the submit named a revision that was no longer current
        assert submit.json()["error"]["code"] == "assessment_stale"
        assert not final["submitted"] and final["questions"][0]["draft"] == "62"


async def test_save_clear_and_submit_are_refused_on_a_v1_test(client, app, fake_llm):
    account, message = await quiz_reply(client, app, fake_llm, reply=QUIZ)
    h, test = account["headers"], message["metadata"]["assessments"][0]
    for action, value in (("save", 1), ("clear", None), ("submit", None)):
        refused = await send(client, h, message, test, 0, action, value)
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "assessment_not_exam", refused.text
    shown = await reloaded(client, h, message)
    assert shown == test and "mode" not in shown and "draft" not in shown["questions"][0]  # v1: as before v2


async def test_the_endpoint_is_rate_limited_per_user(client, app, fake_llm):
    h, message, test = await started(client, app, fake_llm, "exam", [QUESTIONS[3]])
    app.state.assessment_limiter = SlidingWindowLimiter(limit=2, window_s=60)
    codes = [(await send(client, h, message, test, 0, "save", str(n))).status_code for n in range(3)]
    assert codes == [200, 200, 429]
    limited = await send(client, h, message, test, 0, "save", "9")
    assert limited.json()["error"]["code"] == "rate_limited"
    other = (await register(client, app))["headers"]  # another user's window is their own: 404 (not theirs), not 429
    assert (await send(client, other, message, test, 0, "save", "9")).status_code == 404


async def test_no_log_line_holds_an_answer(client, app, fake_llm, caplog):
    caplog.set_level(logging.INFO)
    h, message, test = await started(client, app, fake_llm, "practice")
    for question, value in enumerate(VALUES):
        await send(client, h, message, test, question, "answer", value)
    h, exam, test = await started(client, app, fake_llm, "exam")
    for question, value in enumerate(VALUES):
        await send(client, h, exam, test, question, "save", value)
    assert (await send(client, h, exam, test, 3, "save", "98765.")).status_code == 422
    revision = (await reloaded(client, h, exam))["revision"]
    assert (await send(client, h, exam, test, 0, "submit", revision=revision)).status_code == 200
    assert (await send(client, h, exam, test, 3, "save", "55555")).status_code == 409
    blob = _log_blob(caplog.records)
    assert "assessment invalid" in blob and "assessment conflict" in blob and "assessment response" in blob
    assert [secret for secret in SECRETS if secret in blob] == []


async def test_an_unreadable_record_never_breaks_the_reply_or_the_next_turn(client, app, fake_llm):
    h, message, _ = await started(client, app, fake_llm, "exam", [QUESTIONS[3]])
    broken = {"assessments": [{"schemaVersion": 2, "mode": "exam", "questions": "garbled", "position": 0}]}
    async with app.state.db.sessionmaker() as session:
        await session.execute(update(Message).where(Message.id == uuid.UUID(message["id"])).values(metadata_=broken))
        await session.commit()
    page = await client.get(f"/v1/conversations/{message['conversationId']}/messages", headers=h)
    assert page.status_code == 200 and page.json()["items"][-1]["metadata"]["assessments"] == []
    fake_llm.reply = lambda messages: "Still here."
    response, events = await stream_chat(client, h, "how did I do?", message["conversationId"])
    assert response.status_code == 200 and events[-1][0] == "done"
