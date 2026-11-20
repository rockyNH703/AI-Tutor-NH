"""/v1/nhprep (plan rev 4 §8.5, Revision 5): NetworkersHome students only, a 404 before any validation for everyone
else; lesson summaries, a lesson's detail and its study aids, with NetSec's own ids only (no NHprep id, page or
video link in a GET); per-user limits. The catalog is the synthetic export v1 sample, imported by the real importer.
Playback has its own file (test_nhprep_playback.py), the chat turn too (test_nhprep_chat.py)."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.auth.emails import MemoryEmailSender
from app.llm.fake import FakeProvider
from app.main import create_app
from app.nhprep.importer import run_import
from app.nhprep.schemas import NhprepItemList, NhprepLessonList, NhprepLessonOut
from tests.conftest import make_settings, register
from tests.test_nhprep_import import bundle, options, records, spy

CARDS = json.loads((Path(__file__).parent / "fixtures" / "nhprep" / "cards.json").read_text(encoding="utf-8"))
# The sample's lessons by NHprep id (its "Routing Lab", lessonType lab, is rejected by the importer): two in one course,
# STATIC with an MP4 video and OSPF with a dormant Bunny one, and ACL in another course with no video.
STATIC, OSPF, ACL = (uuid.UUID(f"{n * 8}-{n * 4}-4{n * 3}-8{n * 3}-{n * 12}") for n in "124")
MP4_VIDEO = "aaaaaaaa-0000-4000-8000-000000000001"


@asynccontextmanager
async def nhprep_app(signer=None, mode: str = "on", **overrides):
    """An app whose NHprep is `mode`. It starts with NHPREP_MODE off, so create_app builds no finder (lane L3's);
    chat tests put their own on app.state.chat.nhprep. Settings are one object: the change reaches every route."""
    settings = make_settings(**overrides)
    application = create_app(settings, llm=FakeProvider(), email_sender=MemoryEmailSender(), nhprep_signer=signer)
    async with application.router.lifespan_context(application):
        settings.nhprep_mode = mode
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://t") as client:
            yield application, client


async def seed(app) -> dict[uuid.UUID, uuid.UUID]:
    """The sample catalog, imported; NHprep's lesson id -> NetSec's."""
    await run_import(app.state.db, bundle(records()), spy(), options())
    async with app.state.db.sessionmaker() as db:
        rows = await db.execute(text("SELECT external_id, id FROM nhprep_lessons"))
        return dict(rows.all())


async def remove(app, lesson: uuid.UUID) -> None:
    """As an import of a complete export without it does."""
    async with app.state.db.sessionmaker() as db:
        await db.execute(text("UPDATE nhprep_lessons SET removed_at = now() WHERE id = :id"), {"id": lesson})
        await db.commit()


async def student(client, app, email: str | None = None) -> dict:
    return (await register(client, app, email=email or f"s-{uuid.uuid4().hex[:8]}@networkershome.com"))["headers"]


def routes(lesson: uuid.UUID) -> list[tuple[str, str, dict | None]]:
    """Every route, with valid and invalid parameters alike."""
    return [
        ("GET", f"/v1/nhprep/lessons?ids={lesson}", None),
        ("GET", "/v1/nhprep/lessons?ids=not-an-id", None),
        ("GET", "/v1/nhprep/lessons", None),
        ("GET", f"/v1/nhprep/lessons/{lesson}", None),
        ("GET", "/v1/nhprep/lessons/not-an-id", None),
        ("GET", f"/v1/nhprep/lessons/{lesson}/items?kind=note", None),
        ("GET", f"/v1/nhprep/lessons/{lesson}/items?kind=quiz", None),
        ("POST", f"/v1/nhprep/lessons/{lesson}/playback", None),
        ("POST", f"/v1/nhprep/lessons/{lesson}/playback", {"unexpected": True}),
        ("POST", "/v1/nhprep/lessons/not-an-id/playback", None),
    ]


async def assert_all_404(client, headers, lesson: uuid.UUID) -> None:
    for method, url, body in routes(lesson):
        response = await client.request(method, url, json=body, headers=headers)
        assert response.status_code == 404, (method, url, response.status_code, response.text)
        assert response.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------------ who it is for
async def test_every_route_is_a_404_before_validation_for_anyone_nhprep_is_not_for():
    async with nhprep_app() as (app, client):
        lessons = await seed(app)
        outsider = (await register(client, app))["headers"]  # example.com: not a NetworkersHome address
        await assert_all_404(client, outsider, lessons[STATIC])
        assert (
            await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}", headers=await student(client, app))
        ).is_success


async def test_mode_off_and_qa_narrow_who_nhprep_is_for():
    async with nhprep_app(mode="off", nhprep_qa_emails="qa@networkershome.com") as (app, client):
        lessons = await seed(app)
        listed, unlisted = await student(client, app, "QA@networkershome.com"), await student(client, app)
        await assert_all_404(client, listed, lessons[STATIC])  # off: nobody
        app.state.settings.nhprep_mode = "qa"
        assert (await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}", headers=listed)).status_code == 200
        await assert_all_404(client, unlisted, lessons[STATIC])


# ------------------------------------------------------------------ reads
async def test_lesson_summaries_keep_the_order_asked_and_leave_out_what_is_missing_or_removed():
    async with nhprep_app() as (app, client):
        lessons = await seed(app)
        headers = await student(client, app)
        await remove(app, lessons[STATIC])
        asked = [lessons[OSPF], uuid.uuid4(), lessons[STATIC], lessons[OSPF], lessons[ACL]]
        response = await client.get(f"/v1/nhprep/lessons?ids={','.join(map(str, asked))}", headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert [item["id"] for item in items] == [str(lessons[OSPF]), str(lessons[ACL])]
        assert set(items[0]) == set(CARDS["lessons"]["items"][0])  # the pinned shape
        assert items[0]["course"]["title"] == "Routing Basics (Ajay)" and items[0]["trainers"] == ["Ajay"]
        assert items[0]["lessonCount"] == 1 and items[0]["position"] == 2  # the removed lesson isn't counted
        eleven = ",".join(str(uuid.uuid4()) for _ in range(11))
        assert (await client.get(f"/v1/nhprep/lessons?ids={eleven}", headers=headers)).status_code == 422


async def test_lesson_detail_has_its_course_neighbours_sections_and_study_aids_but_no_nhprep_ids_or_links():
    async with nhprep_app() as (app, client):
        lessons = await seed(app)
        headers = await student(client, app)
        response = await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}", headers=headers)
        assert response.status_code == 200, response.text
        lesson = response.json()
        assert set(lesson) == set(CARDS["lesson"])  # the pinned shape
        assert lesson["previousLessonId"] is None and lesson["nextLessonId"] == str(lessons[OSPF])
        assert [(c["title"], c["startSeconds"]) for c in lesson["chapters"]][0] == ("Why static routes", 45)
        assert lesson["availability"] == {
            "note": "present",
            "flashcard": "present",
            "cheat_sheet": "present",
            "interview_question": "present",
        }
        assert lesson["video"] == "not_configured" and lesson["removed"] is False  # no signer: nothing plays
        for leaked in (str(STATIC), MP4_VIDEO, "nhprep.com", "http", "day-1"):
            assert leaked not in response.text, leaked
        ospf = (await client.get(f"/v1/nhprep/lessons/{lessons[OSPF]}", headers=headers)).json()
        assert ospf["previousLessonId"] == str(lessons[STATIC]) and ospf["nextLessonId"] is None
        assert ospf["availability"]["cheat_sheet"] == "absent" and ospf["chapters"] == []
        assert ospf["video"] == "not_configured"  # a dormant Bunny reference never plays here
        acl = (await client.get(f"/v1/nhprep/lessons/{lessons[ACL]}", headers=headers)).json()
        assert acl["video"] == "no_video" and acl["course"]["title"] == "Firewall Essentials" and acl["trainers"] == []
        assert acl["availability"] == {
            "note": "present",
            "flashcard": "absent",
            "cheat_sheet": "absent",
            "interview_question": "absent",
        }
        missing = await client.get(f"/v1/nhprep/lessons/{uuid.uuid4()}", headers=headers)
        assert missing.status_code == 404


@pytest.mark.parametrize("kind", ["note", "flashcard", "cheat_sheet", "interview_question"])
async def test_study_aids_by_kind_in_the_pinned_shape(kind):
    async with nhprep_app() as (app, client):
        lessons = await seed(app)
        headers = await student(client, app)
        response = await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}/items?kind={kind}", headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert items and {item["kind"] for item in items} == {kind}
        assert all(set(item) == set(CARDS["items"][kind]["items"][0]) for item in items)
        first = items[0]
        if kind == "flashcard":
            assert first["title"].startswith("Synthetic card 1") and first["bodyMd"].startswith("One (card 1)")
        if kind == "interview_question":
            assert first["title"] == "When would you use a floating static route?"
        if kind == "cheat_sheet":
            assert "ip route 0.0.0.0" in first["bodyMd"]


async def test_chapters_are_part_of_the_detail_not_an_items_kind():
    async with nhprep_app() as (app, client):
        lessons = await seed(app)
        response = await client.get(
            f"/v1/nhprep/lessons/{lessons[STATIC]}/items?kind=chapter", headers=await student(client, app)
        )
        assert response.status_code == 422


async def test_a_removed_lesson_keeps_its_title_and_nothing_else():
    async with nhprep_app() as (app, client):
        lessons = await seed(app)
        headers = await student(client, app)
        await remove(app, lessons[STATIC])
        lesson = (await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}", headers=headers)).json()
        assert lesson["removed"] is True and lesson["title"] == "Static Routes and Floating Statics"
        assert lesson["chapters"] == [] and lesson["video"] == "not_configured"
        items = await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}/items?kind=note", headers=headers)
        assert items.status_code == 404
        playback = await client.post(f"/v1/nhprep/lessons/{lessons[STATIC]}/playback", headers=headers)
        assert playback.status_code == 404


async def test_reads_are_limited_per_user_and_an_outsider_never_takes_a_slot():
    async with nhprep_app(nhprep_requests_per_minute=3) as (app, client):
        lessons = await seed(app)
        outsider, headers = (await register(client, app))["headers"], await student(client, app)
        url = f"/v1/nhprep/lessons/{lessons[STATIC]}"
        for _ in range(5):
            assert (await client.get(url, headers=outsider)).status_code == 404
        assert [(await client.get(url, headers=headers)).status_code for _ in range(4)] == [200, 200, 200, 429]
        other = await student(client, app)
        assert (await client.get(url, headers=other)).status_code == 200  # per user


def test_the_pinned_api_shapes_are_what_the_models_send():
    for model, sample in (
        (NhprepLessonList, CARDS["lessons"]),
        (NhprepLessonOut, CARDS["lesson"]),
        *((NhprepItemList, items) for items in CARDS["items"].values()),
    ):
        assert model.model_validate(sample).model_dump(mode="json", by_alias=True) == sample
