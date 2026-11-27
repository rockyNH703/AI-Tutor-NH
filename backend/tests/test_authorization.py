"""User isolation: another user's resources behave exactly like missing ones (404)."""

import uuid

from app.notifications.tokens import make_token
from tests.conftest import PASSWORD, image_bytes, last_code, register, stream_chat


async def test_other_users_resources_are_404(client, app):
    alice = await register(client, app)
    bob = await register(client, app)
    _, events = await stream_chat(client, alice["headers"], "private question")
    cid = events[0][1]["conversationId"]
    mid = events[-1][1]["message"]["id"]
    b = bob["headers"]

    checks = [
        ("GET", f"/v1/conversations/{cid}", None),
        ("PATCH", f"/v1/conversations/{cid}", {"title": "stolen"}),
        ("DELETE", f"/v1/conversations/{cid}", None),
        ("GET", f"/v1/conversations/{cid}/messages", None),
        ("POST", f"/v1/conversations/{cid}/regenerate/stream", None),
        ("POST", "/v1/chat/stream", {"conversationId": cid, "message": "hi"}),
        ("POST", "/v1/chat", {"conversationId": cid, "message": "hi"}),
        ("POST", f"/v1/messages/{mid}/cancel", None),
        ("PUT", f"/v1/messages/{mid}/feedback", {"rating": "down"}),
        ("GET", f"/v1/messages/{mid}/feedback", None),
        ("DELETE", f"/v1/messages/{mid}/feedback", None),
        (
            "POST",
            f"/v1/messages/{mid}/assessment-responses",
            {"position": 0, "hash": "0" * 64, "question": 0, "action": "skip"},
        ),
        (  # a mock exam's save and submit: the same 404, before any rule of the test is applied
            "POST",
            f"/v1/messages/{mid}/assessment-responses",
            {"position": 0, "hash": "0" * 64, "question": 0, "action": "save", "value": 1},
        ),
        (
            "POST",
            f"/v1/messages/{mid}/assessment-responses",
            {"position": 0, "hash": "0" * 64, "question": 0, "action": "submit", "revision": 0},
        ),
    ]
    for method, url, body in checks:
        r = await client.request(method, url, json=body, headers=b)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
        assert r.json()["error"]["code"] == "not_found"

    # Bob's list does not include it, and Alice's data is untouched.
    assert (await client.get("/v1/conversations", headers=b)).json()["items"] == []
    mine = await client.get(f"/v1/conversations/{cid}/messages", headers=alice["headers"])
    assert mine.status_code == 200 and len(mine.json()["items"]) == 2


async def test_identity_data_is_per_user(client, app):
    """Photo, devices and email-change codes: Bob's session reaches only Bob's own."""
    alice = await register(client, app)
    bob = await register(client, app)
    a, b = alice["headers"], bob["headers"]
    photo = await client.put("/v1/me/avatar", content=image_bytes(), headers={**a, "Content-Type": "image/png"})
    photo_url = photo.json()["avatar"]["url"]
    alice_session = (await client.get("/v1/auth/sessions", headers=a)).json()["items"][0]["id"]
    new_email = f"moved-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/v1/me/email/change", json={"newEmail": new_email, "password": PASSWORD}, headers=a)
    code = last_code(app, new_email)

    assert (await client.get(photo_url, headers=b)).status_code == 404  # only ever the caller's own photo
    revoke = await client.delete(f"/v1/auth/sessions/{alice_session}", headers=b)
    assert revoke.status_code == 404 and revoke.json()["error"]["code"] == "not_found"
    assert alice_session not in (await client.get("/v1/auth/sessions", headers=b)).text
    # A code sent for Alice's change cannot move Bob's account to that address.
    stolen = await client.post("/v1/me/email/confirm", json={"newEmail": new_email, "code": code}, headers=b)
    assert stolen.status_code == 400 and stolen.json()["error"]["code"] == "invalid_code"
    assert (await client.get("/v1/me", headers=b)).json()["email"] == bob["email"]

    # Alice's data is untouched, and her code still works for her.
    assert (await client.get(photo_url, headers=a)).status_code == 200
    assert (await client.get("/v1/me", headers=a)).status_code == 200
    mine = await client.post("/v1/me/email/confirm", json={"newEmail": new_email, "code": code}, headers=a)
    assert mine.status_code == 200 and mine.json()["email"] == new_email


async def test_learning_data_is_per_user(client, app):
    """Learner profile, subjects, preferences and the tutor preview only ever use the caller's own."""
    alice = await register(client, app)
    bob = await register(client, app)
    a, b = alice["headers"], bob["headers"]
    await client.patch("/v1/learning/profile", json={"version": 0, "set": {"targetExam": "Alice exam"}}, headers=a)
    await client.put("/v1/learning/subjects", json={"version": 0, "items": [{"code": "physics"}]}, headers=a)
    await client.patch("/v1/learning/preferences", json={"version": 0, "set": {"pace": "fast"}}, headers=a)

    assert (await client.get("/v1/learning/profile", headers=b)).json()["targetExam"] is None
    assert (await client.get("/v1/learning/subjects", headers=b)).json() == {"version": 0, "items": []}
    assert (await client.get("/v1/learning/preferences", headers=b)).json()["pace"] == "auto"
    assert (await client.post("/v1/learning/tutor-preview", json={}, headers=b)).json()["instructions"] == ""

    # Bob's writes land on Bob's rows, at Bob's versions, and leave Alice's alone.
    assert (
        await client.patch("/v1/learning/profile", json={"version": 0, "set": {"grade": "class_6"}}, headers=b)
    ).status_code == 200
    assert (await client.put("/v1/learning/subjects", json={"version": 0, "items": []}, headers=b)).status_code == 200
    mine = (await client.get("/v1/learning/profile", headers=a)).json()
    assert (mine["targetExam"], mine["grade"], mine["version"]) == ("Alice exam", None, 1)
    assert (await client.get("/v1/learning/subjects", headers=a)).json()["items"][0]["code"] == "physics"


async def test_the_engineering_profile_is_per_user(client, app):
    """GET and PATCH /v1/engineering/profile and the preview only ever use the caller's own profile."""
    alice = await register(client, app)
    bob = await register(client, app)
    a, b = alice["headers"], bob["headers"]
    first = await client.patch(
        "/v1/engineering/profile",
        json={"version": 0, "set": {"role": "penetration_tester", "platforms": ["palo_alto"]}},
        headers=a,
    )
    assert first.status_code == 200, first.text

    theirs = (await client.get("/v1/engineering/profile", headers=b)).json()
    assert (theirs["version"], theirs["role"], theirs["platforms"]) == (0, None, [])
    assert (await client.post("/v1/learning/tutor-preview", json={}, headers=b)).json()["instructions"] == ""

    # Alice's version means nothing for Bob (his own row is at 0); his writes land on his row only.
    stale_for_bob = await client.patch(
        "/v1/engineering/profile", json={"version": 1, "set": {"role": "soc_analyst"}}, headers=b
    )
    assert stale_for_bob.status_code == 409 and stale_for_bob.json()["error"]["current"]["role"] is None
    bob_write = await client.patch(
        "/v1/engineering/profile", json={"version": 0, "set": {"role": "soc_analyst"}}, headers=b
    )
    assert bob_write.status_code == 200 and bob_write.json()["version"] == 1
    reset = await client.patch("/v1/engineering/profile", json={"version": 1, "reset": ["platforms"]}, headers=b)
    assert reset.status_code == 200
    mine = (await client.get("/v1/engineering/profile", headers=a)).json()
    assert (mine["role"], mine["platforms"], mine["version"]) == ("penetration_tester", ["palo_alto"], 1)
    preview = (await client.post("/v1/learning/tutor-preview", json={}, headers=a)).json()["instructions"]
    assert "Penetration Tester" in preview and "SOC Analyst" not in preview


async def test_progress_is_per_user(client, app):
    alice = await register(client, app)
    bob = await register(client, app)
    await stream_chat(client, alice["headers"], "A question of Alice's")
    assert (await client.post("/v1/progress/activity", headers=alice["headers"])).json()["credited"] is True
    # Bob's minute is his own, even in the minute Alice was just credited.
    assert (await client.post("/v1/progress/activity", headers=bob["headers"])).json()["credited"] is True
    theirs = (await client.get("/v1/progress", headers=bob["headers"])).json()
    assert (theirs["today"]["questions"], theirs["today"]["activeMinutes"]) == (0, 1)
    mine = (await client.get("/v1/progress", headers=alice["headers"])).json()
    assert (mine["today"]["questions"], mine["today"]["activeMinutes"]) == (1, 1)


async def test_notification_preferences_and_unsubscribe_links_are_per_user(client, app):
    alice = await register(client, app)
    bob = await register(client, app)
    for account in (alice, bob):
        await client.patch(
            "/v1/notifications/preferences",
            json={"version": 0, "set": {"dailyReminder": True}},
            headers=account["headers"],
        )
    token = make_token(app.state.settings, uuid.UUID(alice["user"]["id"]), "daily_reminder")
    assert (
        await client.post("/v1/notifications/unsubscribe", json={"token": token}, headers=bob["headers"])
    ).status_code == 200
    # The link names Alice: it switched off Alice's reminder, whoever clicked it, and never Bob's.
    assert (await client.get("/v1/notifications/preferences", headers=alice["headers"])).json()[
        "dailyReminder"
    ] is False
    assert (await client.get("/v1/notifications/preferences", headers=bob["headers"])).json()["dailyReminder"] is True


async def test_client_supplied_identity_fields_are_rejected(client, app):
    account = await register(client, app)
    r = await client.post(
        "/v1/chat/stream", json={"message": "hi", "userId": "someone-else"}, headers=account["headers"]
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


async def test_every_data_endpoint_requires_auth(client, app):
    for method, url in [
        ("GET", "/v1/conversations"),
        ("POST", "/v1/conversations"),
        ("POST", "/v1/chat/stream"),
        ("POST", "/v1/chat"),
        ("GET", "/v1/me"),
        ("GET", "/v1/me/settings"),
        ("POST", "/v1/auth/logout"),
        ("POST", "/v1/practice-exams"),
        ("GET", f"/v1/practice-exams/{uuid.uuid4()}"),
        ("GET", "/v1/programs/networking-ai/months/1/exam-options"),
    ]:
        for body in ({"message": "x"}, {}):
            r = await client.request(method, url, json=body if method == "POST" else None)
            assert r.status_code == 401, (method, url, body, r.status_code)


async def test_files_are_per_user(client, app):
    """Another student's file is a 404 on every route, and cannot be attached to a message."""
    alice = await register(client, app)
    bob = await register(client, app)
    uploaded = await client.post(
        "/v1/files",
        params={"filename": "private.txt"},
        content=b"My private revision notes about the nitrogen cycle and bacteria.",
        headers={**alice["headers"], "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]
    assert await app.state.file_worker.run_once()
    b = bob["headers"]
    for method, url in (
        ("GET", f"/v1/files/{file_id}"),
        ("GET", f"/v1/files/{file_id}/content"),
        ("POST", f"/v1/files/{file_id}/retry"),
        ("DELETE", f"/v1/files/{file_id}"),
    ):
        response = await client.request(method, url, headers=b)
        assert response.status_code == 404, (method, url, response.status_code, response.text)
    assert (await client.get("/v1/files", headers=b)).json()["items"] == []
    stolen = await client.post("/v1/chat/stream", json={"message": "hi", "attachmentIds": [file_id]}, headers=b)
    assert stolen.status_code == 422
    assert stolen.json()["error"]["fields"] == [{"field": "attachmentIds", "reason": "not_found"}]
    mine = await client.get(f"/v1/files/{file_id}", headers=alice["headers"])
    assert mine.status_code == 200 and mine.json()["status"] == "ready"


async def test_nhprep_is_a_404_for_an_account_it_is_not_for():
    """NHprep's catalog is the same for every student; for anyone else every route is a missing resource (404),
    before any id, query or body is checked (tests/test_nhprep_api.py has the full rule)."""
    from tests.test_nhprep_api import assert_all_404, nhprep_app, seed, student

    async with nhprep_app() as (app, client):
        lessons = await seed(app)
        lesson = next(iter(lessons.values()))
        await assert_all_404(client, (await register(client, app))["headers"], lesson)
        found = await client.get(f"/v1/nhprep/lessons/{lesson}", headers=await student(client, app))
        assert found.status_code == 200, found.text


async def test_practice_exams_are_per_user(client, app, settings, tmp_path):
    """Another enrolled student's practice exam is a 404 on every route, exactly like a missing one."""
    from tests.test_friend_zone_battles import load_bank
    from tests.test_programs import PROGRAM, enrolled

    settings.programs_mode = "on"
    await load_bank(app, tmp_path)
    alice = await enrolled(client, app, "NH-AUTH-1")
    bob = await enrolled(client, app, "NH-AUTH-2")
    body = {"requestId": str(uuid.uuid4()), "program": PROGRAM, "month": 1, "topicIds": ["CCNA-D2-T01"]}
    created = await client.post(
        "/v1/practice-exams", json={**body, "difficulty": "mixed", "length": 5, "minutes": 5}, headers=alice["headers"]
    )
    assert created.status_code == 201, created.text
    exam = created.json()["examId"]
    for method, url, payload in [
        ("GET", f"/v1/practice-exams/{exam}", None),
        ("PUT", f"/v1/practice-exams/{exam}/responses", {"baseRev": 0, "items": [{"index": 0, "keys": ["A"]}]}),
        ("POST", f"/v1/practice-exams/{exam}/submit", {"items": []}),
    ]:
        r = await client.request(method, url, json=payload, headers=bob["headers"])
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", (method, url, r.text)
    assert (await client.get(f"/v1/practice-exams/{exam}", headers=alice["headers"])).status_code == 200
