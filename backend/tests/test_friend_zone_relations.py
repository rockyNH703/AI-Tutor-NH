"""NH Friend Zone, P1.2 (owner-approved plan, revision 3): invoice search, friend requests, friends, blocks, reports
and the player view. Privacy matrix (section 4), races (section 9) and invariants I14, I17, I19, I21-I23."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import FzBlock, FzFriendship, FzReport, UserNotification
from app.friendzone import friends
from tests.conftest import image_bytes, make_settings
from tests.test_programs import PROGRAM, change, enrolled

BASE = "/v1/friend-zone"
PLAYER_FIELDS = {"playerId", "displayName", "initials", "relation", "avatarUrl", "programs", "stats"}


@pytest.fixture
def settings():
    return make_settings(programs_mode="on", friend_zone_mode="on")


@pytest.fixture(autouse=True)
def no_floor(monkeypatch):
    monkeypatch.setattr(friends, "SEARCH_FLOOR_SECONDS", 0.0)


async def member(client, app, number: str, name: str = "Riya K", *, findable: bool = True) -> dict:
    account = await enrolled(client, app, number)
    joined = await client.post(
        f"{BASE}/me", json={"displayName": name, "discoverable": findable}, headers=account["headers"]
    )
    assert joined.status_code == 201, joined.text
    return {**account, "number": number, "player": joined.json()["playerId"], "name": name}


async def search(client, who: dict, number: str):
    return await client.post(f"{BASE}/search", json={"invoiceNumber": number}, headers=who["headers"])


async def ask(client, who: dict, other: dict):
    return await client.post(f"{BASE}/requests", json={"playerId": other["player"]}, headers=who["headers"])


async def block(client, who: dict, other: dict):
    return await client.post(f"{BASE}/blocks", json={"playerId": other["player"]}, headers=who["headers"])


async def requests_of(client, who: dict) -> dict:
    return (await client.get(f"{BASE}/requests", headers=who["headers"])).json()


async def befriend(client, a: dict, b: dict) -> None:
    assert (await ask(client, a, b)).status_code == 201
    request_id = (await requests_of(client, b))["incoming"][0]["requestId"]
    assert (await client.post(f"{BASE}/requests/{request_id}/accept", headers=b["headers"])).status_code == 200


def miss(response) -> dict:
    assert response.status_code == 404, response.text
    body = response.json()["error"]
    return {key: value for key, value in body.items() if key != "requestId"}


async def notifications(app, account: dict) -> list[UserNotification]:
    """The account's Friend Zone notifications (sign-in alerts left out)."""
    async with app.state.db.sessionmaker() as db:
        rows = await db.scalars(
            select(UserNotification).where(
                UserNotification.user_id == uuid.UUID(account["user"]["id"]),
                UserNotification.kind.startswith("friend_"),
            )
        )
        return list(rows)


async def test_search_finds_only_a_findable_member_and_every_miss_is_identical(client, app, caplog):
    caplog.set_level(logging.INFO)
    findable = await member(client, app, "NH-FZ-0101", "Asha Menon")
    await member(client, app, "NH-FZ-0102", findable=False)
    await enrolled(client, app, "NH-FZ-0103")  # enrolled, never joined
    seeker = await member(client, app, "NH-FZ-0104")

    hit = await search(client, seeker, " nh-fz-0101 ")  # normalised: spaces and case
    assert hit.status_code == 200, hit.text
    found = hit.json()
    assert set(found) == PLAYER_FIELDS and "NH-FZ" not in hit.text and findable["email"] not in hit.text
    assert (found["displayName"], found["initials"], found["relation"]) == ("Asha Menon", "AM", "none")
    assert found["avatarUrl"] is None and found["programs"] is None and found["stats"] is None

    numbers = ("NH-FZ-0102", "NH-FZ-0103", "NH-NONE-9", "!!")
    misses = [miss(await search(client, seeker, number)) for number in numbers]
    assert misses[0]["code"] == "no_match" and all(each == misses[0] for each in misses)
    assert (await search(client, seeker, "NH-FZ-0104")).json()["relation"] == "self"

    assert (await block(client, findable, seeker)).status_code == 204
    assert miss(await search(client, seeker, "NH-FZ-0101")) == misses[0]  # blocked: the same miss
    logged = " ".join(str(record.__dict__) for record in caplog.records)
    assert "NH-FZ-01" not in logged and "Asha" not in logged  # I17: fingerprints only, never a number or a name


async def test_search_takes_the_floor_and_is_throttled(client, app, monkeypatch):
    seeker = await member(client, app, "NH-FZ-0111")
    monkeypatch.setattr(friends, "SEARCH_FLOOR_SECONDS", 0.4)
    started = time.monotonic()
    assert (await search(client, seeker, "NH-NONE-1")).status_code == 404
    assert time.monotonic() - started >= 0.4
    monkeypatch.setattr(friends, "SEARCH_FLOOR_SECONDS", 0.0)
    for _ in range(9):
        assert (await search(client, seeker, "NH-NONE-1")).status_code == 404
    refused = await search(client, seeker, "NH-NONE-1")
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) > 0


async def test_a_request_accepted_makes_friends_and_tells_the_sender_without_names(client, app):
    a = await member(client, app, "NH-FZ-0121", "Asha Menon")
    b = await member(client, app, "NH-FZ-0122", "Bilal Khan")
    sent = await ask(client, a, b)
    assert sent.status_code == 201 and sent.json()["relation"] == "outgoing"
    assert [r["player"]["playerId"] for r in (await requests_of(client, a))["outgoing"]] == [b["player"]]
    incoming = (await requests_of(client, b))["incoming"]
    assert [r["player"]["playerId"] for r in incoming] == [a["player"]]

    (bell,) = await notifications(app, b)
    assert bell.kind == "friend_request" and "Asha" not in bell.title + bell.body
    assert bell.link == f"/programs/{PROGRAM}/quizzes?zone=friends"
    accept = f"{BASE}/requests/{incoming[0]['requestId']}/accept"
    assert (await client.post(accept, headers=b["headers"])).json()["relation"] == "friend"
    assert (await client.post(accept, headers=b["headers"])).status_code == 200  # twice: the same answer
    (told,) = await notifications(app, a)
    assert told.kind == "friend_accepted" and "Bilal" not in told.title + told.body

    for who, other in ((a, b), (b, a)):
        listed = (await client.get(f"{BASE}/friends", headers=who["headers"])).json()["items"]
        assert [f["playerId"] for f in listed] == [other["player"]] and listed[0]["programs"] is None
    assert (await ask(client, a, b)).json()["error"]["code"] == "already_friends"
    me = (await client.get(f"{BASE}/me", headers=b["headers"])).json()["profile"]
    edit = {"version": me["version"], "set": {"showProgram": True}}
    assert (await client.patch(f"{BASE}/me", json=edit, headers=b["headers"])).status_code == 200
    seen = (await client.get(f"{BASE}/players/{b['player']}", headers=a["headers"])).json()
    assert [p["slug"] for p in seen["programs"]] == [PROGRAM]


async def test_a_decline_is_silent_and_the_sender_can_cancel(client, app):
    a = await member(client, app, "NH-FZ-0131")
    b = await member(client, app, "NH-FZ-0132")
    outsider = await member(client, app, "NH-FZ-0133")
    await ask(client, a, b)
    request_id = (await requests_of(client, b))["incoming"][0]["requestId"]
    stranger = await client.post(f"{BASE}/requests/{request_id}/accept", headers=outsider["headers"])
    assert stranger.status_code == 404
    assert (await client.post(f"{BASE}/requests/{request_id}/decline", headers=b["headers"])).status_code == 204
    assert (await requests_of(client, b))["incoming"] == []
    assert len((await requests_of(client, a))["outgoing"]) == 1  # to the sender it still looks unanswered
    assert (await ask(client, a, b)).json()["error"]["code"] == "request_pending"
    assert (await client.post(f"{BASE}/requests/{request_id}/cancel", headers=a["headers"])).status_code == 204
    assert (await requests_of(client, a))["outgoing"] == []


async def test_only_a_findable_member_can_be_asked_but_any_request_can_be_answered(client, app):
    open_ = await member(client, app, "NH-FZ-0141")
    private = await member(client, app, "NH-FZ-0142", findable=False)
    assert miss(await ask(client, open_, private))["code"] == "not_found"
    assert (await ask(client, private, open_)).status_code == 201
    mutual = await ask(client, open_, private)  # their request to me: we are friends at once
    assert mutual.status_code == 201 and mutual.json()["relation"] == "friend"


async def test_unfriend_and_block_end_access_at_once_and_look_the_same(client, app):
    a = await member(client, app, "NH-FZ-0151")
    b = await member(client, app, "NH-FZ-0152")
    c = await member(client, app, "NH-FZ-0153")
    await befriend(client, a, b)
    await client.put("/v1/me/avatar", content=image_bytes(), headers={**b["headers"], "Content-Type": "image/png"})
    photo = (await client.get(f"{BASE}/players/{b['player']}", headers=a["headers"])).json()["avatarUrl"]
    shown = await client.get(photo, headers=a["headers"])
    assert shown.status_code == 200 and shown.headers["Cache-Control"] == "private, no-store"
    assert (await client.get(photo, headers=c["headers"])).status_code == 404  # not a friend
    assert (await client.delete(f"{BASE}/friends/{b['player']}", headers=a["headers"])).status_code == 204
    assert (await client.get(photo, headers=a["headers"])).status_code == 404  # I23: at the next request
    after_unfriend = miss(await client.get(f"{BASE}/players/{a['player']}", headers=b["headers"]))

    await befriend(client, c, a)
    await ask(client, b, a)
    assert (await block(client, a, c)).status_code == 204
    assert (await block(client, a, b)).status_code == 204
    assert miss(await client.get(f"{BASE}/players/{a['player']}", headers=c["headers"])) == after_unfriend  # I21
    assert (await client.get(f"{BASE}/friends", headers=c["headers"])).json()["items"] == []
    assert (await requests_of(client, b))["outgoing"] == []
    assert miss(await ask(client, b, a)) == after_unfriend
    blocked = (await client.get(f"{BASE}/blocks", headers=a["headers"])).json()["items"]
    assert {p["playerId"] for p in blocked} == {b["player"], c["player"]}
    assert (await client.delete(f"{BASE}/blocks/{c['player']}", headers=a["headers"])).status_code == 204
    assert (await client.delete(f"{BASE}/blocks/{c['player']}", headers=a["headers"])).status_code == 404
    assert (await client.get(f"{BASE}/friends", headers=a["headers"])).json()["items"] == []  # no comeback


async def test_races_end_in_one_consistent_state(client, app):
    a = await member(client, app, "NH-FZ-0161")
    b = await member(client, app, "NH-FZ-0162")
    both = await asyncio.gather(ask(client, a, b), ask(client, b, a))  # crossing requests: one friendship
    assert sorted(r.status_code for r in both) == [201, 201]
    assert {r.json()["relation"] for r in both} <= {"outgoing", "friend"}
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(FzFriendship)) == 1

    c = await member(client, app, "NH-FZ-0163")
    await ask(client, c, a)
    request_id = (await requests_of(client, a))["incoming"][0]["requestId"]
    await asyncio.gather(  # accept vs the sender's block: the block wins, whatever the order
        client.post(f"{BASE}/requests/{request_id}/accept", headers=a["headers"]),
        block(client, c, a),
    )
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(FzFriendship)) == 1  # only a and b
        assert await db.scalar(select(func.count()).select_from(FzBlock)) == 1


async def test_a_qa_member_and_a_live_member_never_meet(client, app):
    live = await member(client, app, "NH-FZ-0171")
    qa_account = await enrolled(client, app, "NH-FZ-0172")
    app.state.settings = make_settings(
        programs_mode="on", friend_zone_mode="on", friend_zone_qa_emails=qa_account["email"]
    )
    joined = await client.post(
        f"{BASE}/me", json={"displayName": "QA one", "discoverable": True}, headers=qa_account["headers"]
    )
    assert joined.json()["qa"] is True
    qa = {**qa_account, "player": joined.json()["playerId"]}
    for who, other, number in ((qa, live, "NH-FZ-0171"), (live, qa, "NH-FZ-0172")):
        assert miss(await search(client, who, number))["code"] == "no_match"
        assert (await client.get(f"{BASE}/players/{other['player']}", headers=who["headers"])).status_code == 404
        assert (await ask(client, who, other)).status_code == 404
        assert (await block(client, who, other)).status_code == 404
        report = {"playerId": other["player"], "reason": "spam"}
        assert (await client.post(f"{BASE}/reports", json=report, headers=who["headers"])).status_code == 404


async def test_reports_are_recorded_for_the_owner_only(client, app):
    a = await member(client, app, "NH-FZ-0181")
    b = await member(client, app, "NH-FZ-0182", findable=False)
    body = {"playerId": b["player"], "reason": "harassment", "details": "  rude in a battle  "}
    filed = await client.post(f"{BASE}/reports", json=body, headers=a["headers"])
    assert filed.status_code == 201, filed.text
    async with app.state.db.sessionmaker() as db:
        row = await db.get(FzReport, uuid.UUID(filed.json()["reportId"]))
        assert (row.reason, row.details, row.status) == ("harassment", "rude in a battle", "open")
    assert await notifications(app, b) == []  # the member is not told
    for each in ({**body, "reason": "boring"}, {**body, "details": "x" * 1001}, {**body, "userId": str(uuid.uuid4())}):
        assert (await client.post(f"{BASE}/reports", json=each, headers=a["headers"])).status_code == 422
    for each in ({**body, "playerId": a["player"]}, {**body, "battleId": str(uuid.uuid4())}):
        assert (await client.post(f"{BASE}/reports", json=each, headers=a["headers"])).status_code == 404


async def test_a_friend_whose_invoice_ends_disappears(client, app):
    a = await member(client, app, "NH-FZ-0191")
    b = await member(client, app, "NH-FZ-0192")
    await befriend(client, a, b)
    await change(app, "NH-FZ-0192", status="revoked")
    assert (await client.get(f"{BASE}/friends", headers=a["headers"])).json()["items"] == []
    assert (await client.get(f"{BASE}/players/{b['player']}", headers=a["headers"])).status_code == 404
    assert miss(await search(client, a, "NH-FZ-0192"))["code"] == "no_match"
