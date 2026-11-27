"""Server-side entitlement on every program-bound route (session 62 plan, revision 3 item 4): the API decides from
`open_program_ids` on every request. An account whose invoice is missing (claim gone), expired or revoked gets the
404 of a missing resource on program data, program chats and practice exams; Quiz me and Friend Zone report it as not
open; NHprep lessons the program opened are hidden again. A practice exam already submitted stays readable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.db.models import NhprepLesson, ProgramModuleCourse
from tests.conftest import make_settings
from tests.test_friend_zone_battles import load_bank
from tests.test_programs import PROGRAM, change, enrolled, module_id, start

EXAMS = "/v1/practice-exams"
ENDINGS = {
    "missing": None,  # the claim row is gone
    "expired": {"valid_until": datetime.now(UTC) - timedelta(minutes=1)},
    "expired-status": {"status": "expired"},
    "revoked": {"status": "revoked"},
    "refunded": {"status": "refunded"},
}


@pytest.fixture
def settings():
    return make_settings(programs_mode="on", friend_zone_mode="on", question_bank_tests=True, question_bank_turn="on")


async def end_access(app, number: str, ending: str) -> None:
    values = ENDINGS[ending]
    if values is not None:
        await change(app, number, **values)
        return
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text("DELETE FROM invoice_claims WHERE invoice_id = (SELECT id FROM nh_invoices WHERE invoice_no = :n)"),
            {"n": number},
        )
        await db.commit()


def exam_body() -> dict:
    return {
        "requestId": str(uuid.uuid4()),
        "program": PROGRAM,
        "month": 1,
        "topicIds": ["CCNA-D2-T01"],
        "difficulty": "mixed",
        "length": 5,
        "minutes": 5,
    }


@pytest.mark.parametrize("ending", list(ENDINGS))
async def test_every_program_route_is_a_404_once_the_invoice_no_longer_opens_the_program(client, app, tmp_path, ending):
    await load_bank(app, tmp_path)
    number = f"NH-MX-{ending.upper()}"
    student = await enrolled(client, app, number)
    headers = student["headers"]
    response, events = await start(client, headers, "What is OSPF?", month=1)
    assert response.status_code == 200, response.text
    chat = events[0][1]["conversationId"]
    running = (await client.post(EXAMS, json=exam_body(), headers=headers)).json()["examId"]
    finished = (await client.post(EXAMS, json=exam_body(), headers=headers)).json()["examId"]
    submitted = await client.post(f"{EXAMS}/{finished}/submit", json={"items": []}, headers=headers)
    assert submitted.status_code == 200, submitted.text

    month = f"/v1/programs/{PROGRAM}/months/1"
    reads = [
        f"/v1/programs/{PROGRAM}",
        month,
        f"{month}/exam-options",
        f"/v1/conversations/{chat}",
        f"/v1/conversations/{chat}/messages",
        f"{EXAMS}/{running}",
    ]
    for url in reads:  # open while the invoice is
        assert (await client.get(url, headers=headers)).status_code == 200, url
    assert (await client.get("/v1/progress/bank", headers=headers)).json()["enabled"] is True
    assert (await client.get("/v1/friend-zone/me", headers=headers)).json()["eligible"] is True

    await end_access(app, number, ending)

    writes = [
        ("POST", "/v1/chat/stream", {"message": "And BGP?", "program": PROGRAM, "month": 1}),
        ("POST", "/v1/chat/stream", {"message": "And BGP?", "conversationId": chat}),
        ("POST", f"/v1/conversations/{chat}/regenerate/stream", None),
        ("GET", f"/v1/conversations/{chat}/share", None),
        ("POST", f"/v1/conversations/{chat}/share", None),
        ("POST", EXAMS, exam_body()),
        ("PUT", f"{EXAMS}/{running}/responses", {"baseRev": 0, "items": [{"index": 0, "keys": ["A"]}]}),
        ("POST", f"{EXAMS}/{running}/submit", {"items": []}),
    ]
    for method, url, body in [("GET", url, None) for url in reads] + writes:
        refused = await client.request(method, url, json=body, headers=headers)
        assert refused.status_code == 404, (ending, method, url, refused.status_code, refused.text)
    assert (await client.get("/v1/progress/bank", headers=headers)).json()["enabled"] is False
    assert (await client.get("/v1/friend-zone/me", headers=headers)).json()["eligible"] is False
    kept = await client.get(f"{EXAMS}/{finished}", headers=headers)  # a submitted exam stays its owner's
    assert kept.status_code == 200 and kept.json()["status"] == "submitted"


@pytest.mark.parametrize("ending", list(ENDINGS))
async def test_nhprep_lessons_a_program_opened_close_with_it(ending):
    from tests.test_nhprep_api import STATIC, nhprep_app, seed

    async with nhprep_app(programs_mode="on") as (app, client):
        lessons = await seed(app)
        async with app.state.db.sessionmaker() as db:
            course = await db.scalar(select(NhprepLesson.course_id).where(NhprepLesson.id == lessons[STATIC]))
            db.add(ProgramModuleCourse(module_id=await module_id(app, PROGRAM, 2), course_id=course))
            await db.commit()
        number = f"NH-MXV-{ending.upper()}"
        headers = (await enrolled(client, app, number))["headers"]  # an example.com address: not NH by domain
        url = f"/v1/nhprep/lessons/{lessons[STATIC]}"
        assert (await client.get(url, headers=headers)).status_code == 200
        await end_access(app, number, ending)
        assert (await client.get(url, headers=headers)).status_code == 404
        listed = await client.get(f"/v1/nhprep/lessons?ids={lessons[STATIC]}", headers=headers)
        assert listed.status_code in (200, 404) and str(lessons[STATIC]) not in listed.text
