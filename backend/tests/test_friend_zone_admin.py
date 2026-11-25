"""NH Friend Zone operator script (scripts/friend_zone_admin.py, plan sections 6 and 7; exam plan section 7):
reports, suspension, the QA cohort's cleanup and ending open battles, each a dry run until --apply and audited
(I24)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.db.models import FzBattle, FzFriendRequest, FzModerationAction, FzProfile, FzReport
from app.friendzone import friends
from scripts import friend_zone_admin as admin
from tests.conftest import make_settings
from tests.test_friend_zone_battles import (  # noqa: F401 (the bank fixture)
    answers,
    at,
    bank,
    begin,
    challenge,
    graded,
    keys,
    pair,
    save,
    state,
)
from tests.test_friend_zone_relations import ask, befriend, member, requests_of
from tests.test_programs import enrolled

BASE = "/v1/friend-zone"


@pytest.fixture
def settings():
    return make_settings(programs_mode="on", friend_zone_mode="on")


@pytest.fixture(autouse=True)
def no_floor(monkeypatch):
    monkeypatch.setattr(friends, "SEARCH_FLOOR_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def api_policy(monkeypatch):
    """The script reads FRIEND_ZONE_KEY_POLICY from the API's settings; here, the test app's."""
    monkeypatch.setattr(admin, "_policy", lambda: "human_only")


async def run(app, *argv: str) -> int:
    async with app.state.db.sessionmaker() as db:
        return await admin.run(db, admin._parser().parse_args(list(argv)))


async def audit(app) -> list[str]:
    async with app.state.db.sessionmaker() as db:
        return list(await db.scalars(select(FzModerationAction.action).order_by(FzModerationAction.created_at)))


async def test_a_report_is_listed_shown_and_resolved_with_an_audit(client, app, capsys):
    a = await member(client, app, "NH-FZ-0301")
    b = await member(client, app, "NH-FZ-0302", "Bilal Khan")
    body = {"playerId": b["player"], "reason": "harassment", "details": "rude messages"}
    report_id = (await client.post(f"{BASE}/reports", json=body, headers=a["headers"])).json()["reportId"]
    assert await run(app, "reports", "--status", "open") == 0
    listed = capsys.readouterr().out
    assert report_id in listed and "rude messages" not in listed  # the text only in `report`
    assert await run(app, "report", report_id) == 0
    shown = capsys.readouterr().out
    assert "rude messages" in shown and "Bilal Khan" in shown and a["email"] not in shown
    assert await run(app, "resolve", report_id, "--outcome", "actioned", "--note", "warned") == 0  # dry run
    async with app.state.db.sessionmaker() as db:
        assert (await db.get(FzReport, uuid.UUID(report_id))).status == "open"
    assert await run(app, "resolve", report_id, "--outcome", "actioned", "--note", "warned", "--apply") == 0
    async with app.state.db.sessionmaker() as db:
        assert (await db.get(FzReport, uuid.UUID(report_id))).status == "actioned"
    assert await audit(app) == ["resolve"]
    assert await run(app, "resolve", report_id, "--outcome", "dismissed", "--note", "again", "--apply") == 1


async def test_suspension_hides_a_member_and_ends_their_requests_and_battles(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0311", "Asha Menon")
    b = await member(client, app, "NH-FZ-0312")
    c = await member(client, app, "NH-FZ-0313")
    await befriend(client, a, b)
    await ask(client, c, a)
    battle_id = (await challenge(client, b, a)).json()["battleId"]
    assert await run(app, "suspend", a["player"], "--reason", "abuse") == 0  # dry run
    assert len((await client.get(f"{BASE}/friends", headers=b["headers"])).json()["items"]) == 1
    assert await run(app, "suspend", a["player"], "--reason", "abuse", "--apply") == 0
    me = (await client.get(f"{BASE}/me", headers=a["headers"])).json()
    assert me["suspended"] is True
    assert (await client.get(f"{BASE}/friends", headers=a["headers"])).status_code == 404
    assert (await client.get(f"{BASE}/friends", headers=b["headers"])).json()["items"] == []
    assert (await requests_of(client, c))["outgoing"] == []
    async with app.state.db.sessionmaker() as db:
        battle = await db.get(FzBattle, uuid.UUID(battle_id))
        assert (battle.status, battle.end_reason, battle.pair_key) == ("cancelled", "suspended", None)
    assert await run(app, "unsuspend", a["player"], "--apply") == 0
    assert len((await client.get(f"{BASE}/friends", headers=b["headers"])).json()["items"]) == 1
    assert await audit(app) == ["suspend", "unsuspend"]


async def test_qa_cleanup_removes_every_qa_row_and_nothing_live(client, app, bank, capsys):  # noqa: F811
    live = await member(client, app, "NH-FZ-0321")
    accounts = [await enrolled(client, app, number) for number in ("NH-FZ-0322", "NH-FZ-0323")]
    app.state.settings = make_settings(
        programs_mode="on", friend_zone_mode="on", friend_zone_qa_emails=",".join(a["email"] for a in accounts)
    )
    qa = []
    for account in accounts:
        joined = await client.post(
            f"{BASE}/me", json={"displayName": "QA member", "discoverable": True}, headers=account["headers"]
        )
        qa.append({**account, "player": joined.json()["playerId"]})
    await befriend(client, qa[0], qa[1])
    battle_id = await begin(client, app, qa[0], qa[1])  # a QA exam with drafts: the cleanup cascades through them
    await save(client, qa[0], battle_id, *answers(await keys(app, battle_id), right=(1, 2)))
    assert await run(app, "qa-verify") == 1
    assert await run(app, "qa-cleanup") == 0  # dry run
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(FzProfile).where(FzProfile.cohort == "qa")) == 2
    capsys.readouterr()
    assert await run(app, "qa-cleanup", "--apply") == 0
    assert await run(app, "qa-verify") == 0 and "NOT CLEAN" not in capsys.readouterr().out
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(FzProfile)) == 1  # the live member stays
    assert (await client.get(f"{BASE}/me", headers=live["headers"])).json()["member"] is True
    assert await audit(app) == ["qa_cleanup"]


async def test_retention_removes_only_what_the_owner_approved(client, app):
    a = await member(client, app, "NH-FZ-0331")
    b = await member(client, app, "NH-FZ-0332")
    c = await member(client, app, "NH-FZ-0333")
    await ask(client, a, b)
    await ask(client, a, c)
    old_id = (await requests_of(client, b))["incoming"][0]["requestId"]
    await client.post(f"{BASE}/requests/{old_id}/decline", headers=b["headers"])
    async with app.state.db.sessionmaker() as db:  # one closed long ago, one still fresh and pending
        await db.execute(
            text("UPDATE fz_friend_requests SET responded_at = now() - interval '31 days' WHERE id = :r"),
            {"r": uuid.UUID(old_id)},
        )
        await db.commit()
    assert await run(app, "retention") == 0  # dry run
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(FzFriendRequest)) == 2
    assert await run(app, "retention", "--apply") == 0
    async with app.state.db.sessionmaker() as db:
        assert (await db.scalars(select(FzFriendRequest.status))).all() == ["pending"]  # the fresh one stays


async def _battle(app, battle_id: str) -> FzBattle:
    async with app.state.db.sessionmaker() as db:
        return await db.get(FzBattle, uuid.UUID(battle_id))


async def test_suspension_voids_a_running_exam_and_one_past_its_cutoff(client, app, bank):  # noqa: F811
    a, b = await pair(client, app, 341)
    running = await begin(client, app, a, b)
    await save(client, a, running, *answers(await keys(app, running), right=(1, 2)))
    c, d = await pair(client, app, 343)
    late = await begin(client, app, c, d)
    await save(client, c, late, *answers(await keys(app, late), right=(1, 2)))
    await at(app, late, 1, "closes_at", -2.5)  # past its cutoff, nobody has read it: O5 voids it all the same
    assert await run(app, "suspend", a["player"], "--reason", "abuse", "--apply") == 0
    assert await run(app, "suspend", c["player"], "--reason", "abuse", "--apply") == 0
    for battle_id in (running, late):
        battle = await _battle(app, battle_id)
        assert (battle.status, battle.result, battle.end_reason) == ("completed", "void", "suspended")
    assert await graded(app, running, a) == 2  # the drafts given so far stay practice
    assert (await state(client, b, running))["endReason"] == "ended"  # I21


async def test_end_open_completes_overdue_exams_and_in_full_ends_the_rest(client, app, bank, capsys):  # noqa: F811
    a, b = await pair(client, app, 361)
    overdue = await begin(client, app, a, b)
    await save(client, a, overdue, *answers(await keys(app, overdue), right=(1, 2)))
    await at(app, overdue, 1, "closes_at", -2.5)
    c, d = await pair(client, app, 363)
    running = await begin(client, app, c, d)
    e, f = await pair(client, app, 365)
    invited = (await challenge(client, e, f)).json()["battleId"]
    g, h = await pair(client, app, 367)
    legacy = (await challenge(client, g, h)).json()["battleId"]
    async with app.state.db.sessionmaker() as db:  # a round battle left open by a rollback period
        await db.execute(text("UPDATE fz_battles SET format = 'rounds' WHERE id = :b"), {"b": uuid.UUID(legacy)})
        await db.commit()
    capsys.readouterr()

    assert await run(app, "end-open") == 0  # dry run: lists, changes nothing
    listed = capsys.readouterr().out
    assert all(battle_id in listed for battle_id in (overdue, running, invited, legacy))
    assert "4 of 4 open battle(s) to end" in listed and a["email"] not in listed and "Asha" not in listed
    assert (await _battle(app, overdue)).status == "active"

    assert await run(app, "end-open", "--complete-only", "--apply") == 0
    battle = await _battle(app, overdue)
    assert (battle.status, battle.result) == ("completed", "points")
    for battle_id, status in ((running, "active"), (invited, "invited"), (legacy, "invited")):
        assert (await _battle(app, battle_id)).status == status

    assert await run(app, "end-open", "--apply") == 0
    ended = {battle_id: await _battle(app, battle_id) for battle_id in (running, invited, legacy)}
    assert (ended[running].status, ended[running].result, ended[running].end_reason) == (
        "completed",
        "void",
        "operator",
    )
    assert (ended[invited].status, ended[invited].end_reason) == ("cancelled", "operator")
    assert (ended[legacy].status, ended[legacy].end_reason) == ("cancelled", "upgraded")
    assert await audit(app) == ["cancel_battle"]
    assert await run(app, "end-open", "--apply") == 0  # nothing open: nothing to do
    assert "0 of 0 open battle(s)" in capsys.readouterr().out
