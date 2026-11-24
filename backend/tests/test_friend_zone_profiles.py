"""NH Friend Zone, P1.1 (owner-approved plan, revision 3): who may use it, joining, the profile, the fixed QA cohort
and QA answers kept out of every practice record."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.errors import ValidationFailed
from app.friendzone.names import clean_name
from app.progress import learning
from tests.conftest import make_settings, register
from tests.test_friend_zone_battles import bank  # noqa: F401 (the bank fixture: five real questions)
from tests.test_programs import change, enrolled

PROFILE_FIELDS = {"playerId", "displayName", "discoverable", "showProgram", "showStats", "version", "qa"}


@pytest.fixture
def settings():
    return make_settings(programs_mode="on", friend_zone_mode="on")


async def join(client, headers, **body):
    return await client.post("/v1/friend-zone/me", json={"displayName": "Riya K", **body}, headers=headers)


def test_names_are_cleaned_and_impersonation_is_refused():
    assert clean_name("  Riya   K ") == "Riya K"
    assert clean_name("Ａｍｉｔ") == "Amit"  # NFKC
    for bad in ("x", "a" * 31, "Riya​K", "Riya‮K", "me@home", "http://x", "www.site", "Admin 2", "N.H", "NH"):
        with pytest.raises(ValidationFailed):
            clean_name(bad)


async def test_me_is_disabled_without_the_mode_or_an_invoice(client, app):
    account = await register(client, app)  # verified, but no NH invoice
    me = (await client.get("/v1/friend-zone/me", headers=account["headers"])).json()
    assert me["enabled"] is True and me["eligible"] is False and me["profile"] is None
    assert (await join(client, account["headers"])).status_code == 404
    app.state.settings = make_settings(programs_mode="on", friend_zone_mode="off")
    enrolled_account = await enrolled(client, app, "NH-FZ-0001")
    me = (await client.get("/v1/friend-zone/me", headers=enrolled_account["headers"])).json()
    assert (me["enabled"], me["eligible"], me["member"]) == (False, False, False)
    assert (await join(client, enrolled_account["headers"])).status_code == 404


async def test_join_is_private_by_default_once_only_and_ends_with_the_invoice(client, app):
    account = await enrolled(client, app, "NH-FZ-0002")
    joined = await join(client, account["headers"])
    assert joined.status_code == 201, joined.text
    profile = joined.json()
    assert set(profile) == PROFILE_FIELDS  # no account id, email or invoice
    assert (profile["discoverable"], profile["showProgram"], profile["showStats"], profile["qa"]) == (False,) * 4
    again = await join(client, account["headers"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_member"
    await change(app, "NH-FZ-0002", status="revoked")  # access ends on the next request
    me = (await client.get("/v1/friend-zone/me", headers=account["headers"])).json()
    assert me["eligible"] is False and me["profile"] is None
    patch = {"version": profile["version"], "set": {"discoverable": True}}
    assert (await client.patch("/v1/friend-zone/me", json=patch, headers=account["headers"])).status_code == 404


async def test_edits_are_versioned_and_reject_client_identity_fields(client, app):
    headers = (await enrolled(client, app, "NH-FZ-0003"))["headers"]
    version = (await join(client, headers)).json()["version"]
    first = await client.patch(
        "/v1/friend-zone/me", json={"version": version, "set": {"discoverable": True}}, headers=headers
    )
    assert first.status_code == 200 and first.json()["discoverable"] is True
    stale = await client.patch(
        "/v1/friend-zone/me", json={"version": version, "set": {"showStats": True}}, headers=headers
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    for body in (
        {"version": version + 1, "set": {"discoverable": None}},
        {"version": version + 1, "set": {"cohort": "live"}},
        {"version": version + 1, "set": {}, "userId": str(uuid.uuid4())},
    ):
        assert (await client.patch("/v1/friend-zone/me", json=body, headers=headers)).status_code == 422, body


async def test_a_qa_account_joins_the_qa_cohort_for_good(client, app):
    account = await enrolled(client, app, "NH-FZ-0004")
    app.state.settings = make_settings(
        programs_mode="on", friend_zone_mode="qa", friend_zone_qa_emails=account["email"]
    )
    assert (await join(client, account["headers"])).json()["qa"] is True
    async with app.state.db.sessionmaker() as db:
        with pytest.raises(DBAPIError):  # the trigger: a cohort never changes
            await db.execute(
                text("UPDATE fz_profiles SET cohort = 'live' WHERE user_id = :u"),
                {"u": uuid.UUID(account["user"]["id"])},
            )
        await db.rollback()


async def test_a_cross_cohort_friendship_cannot_exist(client, app):
    live = await enrolled(client, app, "NH-FZ-0005")
    assert (await join(client, live["headers"])).status_code == 201
    qa = await enrolled(client, app, "NH-FZ-0006")
    app.state.settings = make_settings(programs_mode="on", friend_zone_mode="qa", friend_zone_qa_emails=qa["email"])
    assert (await join(client, qa["headers"])).status_code == 201
    low, high = sorted([uuid.UUID(live["user"]["id"]), uuid.UUID(qa["user"]["id"])])
    async with app.state.db.sessionmaker() as db:
        for cohort in ("live", "qa"):  # whichever cohort the row claims, one side's profile does not match it
            with pytest.raises(DBAPIError):
                await db.execute(
                    text("INSERT INTO fz_friendships (user_low, user_high, cohort) VALUES (:l, :h, :c)"),
                    {"l": low, "h": high, "c": cohort},
                )
            await db.rollback()


async def test_qa_answers_never_make_a_learning_day(client, app, bank):  # noqa: F811
    """Five answered questions make a learning day (record_practice); five in a QA set make none."""
    user = uuid.UUID((await register(client, app))["user"]["id"])
    async with app.state.db.sessionmaker() as db:
        rows = (await db.execute(text("SELECT question_id, topic_id FROM qb_questions WHERE is_active LIMIT 5"))).all()
        if len(rows) < 5:
            pytest.skip("the test bank has fewer than five questions")
        set_id = await db.scalar(
            text(
                "INSERT INTO qb_sets (user_id, request_id, mode, filters, requested, served) "
                "VALUES (:u, gen_random_uuid(), 'fz_qa', '{}', 5, 5) RETURNING id"
            ),
            {"u": user},
        )
        for position, (question, topic) in enumerate(rows, 1):
            await db.execute(
                text(
                    "INSERT INTO qb_set_items (set_id, position, user_id, question_id, topic_id) "
                    "VALUES (:s, :p, :u, :q, :t)"
                ),
                {"s": set_id, "p": position, "u": user, "q": question, "t": topic},
            )
        await db.execute(  # answer each in its own shape (the trigger grades)
            text(
                "UPDATE qb_set_items i SET chosen_keys = CASE WHEN q.correct_keys IS NOT NULL THEN ARRAY['A'] END, "
                "chosen = CASE WHEN q.correct_keys IS NULL THEN 'A' END "
                "FROM qb_questions q WHERE q.question_id = i.question_id AND i.set_id = :s"
            ),
            {"s": set_id},
        )
        await db.commit()
        latest = text("SELECT max(answered_at) FROM qb_set_items WHERE set_id = :s")
        answered_at = await db.scalar(latest, {"s": set_id})
        assert await learning.record_practice(db, user, answered_at, answered_at) is None
        await db.commit()
        days = await db.scalar(text("SELECT count(*) FROM learning_daily_activity WHERE user_id = :u"), {"u": user})
    assert days == 0
