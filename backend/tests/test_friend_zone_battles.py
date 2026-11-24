"""NH Friend Zone, P1.3 (owner-approved plan, revision 3): competitive eligibility (section 5), the battle lifecycle
and its terminal outcomes (section 9), scoring, secrecy and isolation (I15, I16, I18, I19, I20).

Time is moved by shifting a battle's stored windows against the database clock (`at`), never by sleeping."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.db.models import (
    FzBattle,
    LearningDailyActivity,
    Program,
    ProgramModule,
    ProgramModuleBook,
    QbQuestion,
    QbSet,
    UserNotification,
)
from app.friendzone import battles, friends
from app.qbank.importer import import_bank
from tests.conftest import make_settings
from tests.test_friend_zone_relations import befriend, block, member
from tests.test_programs import PROGRAM, enrolled
from tests.test_qbank_certification import netsec_bank

BASE = "/v1/friend-zone"


@pytest.fixture
def settings():
    return make_settings(programs_mode="on", friend_zone_mode="on")


@pytest.fixture(autouse=True)
def no_floor(monkeypatch):
    monkeypatch.setattr(friends, "SEARCH_FLOOR_SECONDS", 0.0)


@pytest.fixture
async def bank(app, tmp_path):
    """The synthetic certification bank, its single-answer questions approved by a person (the only verification
    that counts, R-E1), mapped to the program's month 1. Returns that month's module id."""
    async with app.state.db.sessionmaker() as db:
        report = await import_bank(db, netsec_bank(tmp_path), apply=True)
        assert report.applied, report.errors
        await db.execute(
            text(
                "UPDATE qb_questions SET review_status = 'approved', reviewed_by = 'trainer', "
                "reviewed_at = now(), reviewed_hash = content_hash WHERE question_format = 'mcq'"
            )
        )
        module = await db.scalar(
            select(ProgramModule.id)
            .join(Program, Program.id == ProgramModule.program_id)
            .where(Program.slug == PROGRAM, ProgramModule.month == 1)
        )
        db.add(ProgramModuleBook(module_id=module, book_id="ccna"))
        await db.commit()
        return module


async def pair(client, app, base: int) -> tuple[dict, dict]:
    a = await member(client, app, f"NH-FZ-{base:04d}", "Asha Menon")
    b = await member(client, app, f"NH-FZ-{base + 1:04d}", "Bilal Khan")
    await befriend(client, a, b)
    return a, b


async def challenge(client, a: dict, b: dict, **extra):
    body = {"opponentId": b["player"], "program": PROGRAM, "month": 1, "length": 5, "requestId": str(uuid.uuid4())}
    return await client.post(f"{BASE}/battles", json={**body, **extra}, headers=a["headers"])


async def state(client, who: dict, battle_id: str) -> dict:
    response = await client.get(f"{BASE}/battles/{battle_id}", headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def started(client, a: dict, b: dict) -> str:
    battle_id = (await challenge(client, a, b)).json()["battleId"]
    accepted = await client.post(f"{BASE}/battles/{battle_id}/accept", headers=b["headers"])
    assert accepted.json()["status"] == "waiting", accepted.text
    ready = await client.post(f"{BASE}/battles/{battle_id}/ready", headers=a["headers"])
    assert ready.json()["status"] == "active", ready.text
    return battle_id


async def at(app, battle_id: str, round_: int, column: str, seconds: float) -> None:
    """Move the battle's clock so that round `round_`'s `column` (opened_at or closes_at) is `seconds` from now."""
    assert column in ("opened_at", "closes_at")
    async with app.state.db.sessionmaker() as db:
        shift = await db.scalar(
            text(
                f"SELECT {column} - clock_timestamp() - make_interval(secs => :s) FROM fz_battle_rounds "
                "WHERE battle_id = :b AND round = :r"
            ),
            {"b": uuid.UUID(battle_id), "r": round_, "s": seconds},
        )
        params = {"b": uuid.UUID(battle_id), "d": shift}
        await db.execute(
            text(
                "UPDATE fz_battle_rounds SET opened_at = opened_at - CAST(:d AS interval), "
                "closes_at = closes_at - CAST(:d AS interval), closed_at = closed_at - CAST(:d AS interval) "
                "WHERE battle_id = :b"
            ),
            params,
        )
        await db.execute(
            text(
                "UPDATE fz_battles SET starts_at = starts_at - CAST(:d AS interval), "
                "ends_at = ends_at - CAST(:d AS interval) WHERE id = :b"
            ),
            params,
        )
        await db.commit()


async def key_of(app, battle_id: str, round_: int) -> str:
    async with app.state.db.sessionmaker() as db:
        keys = await db.scalar(
            text(
                "SELECT q.correct_keys FROM fz_battle_rounds r JOIN qb_questions q USING (question_id) "
                "WHERE r.battle_id = :b AND r.round = :r"
            ),
            {"b": uuid.UUID(battle_id), "r": round_},
        )
        return keys[0]


async def answer(client, who: dict, battle_id: str, round_: int, choice: str):
    body = {"round": round_, "choice": choice}
    return await client.post(f"{BASE}/battles/{battle_id}/answers", json=body, headers=who["headers"])


def wrong(key: str) -> str:
    return "A" if key != "A" else "B"


async def kinds(app, who: dict) -> list[str]:
    async with app.state.db.sessionmaker() as db:
        rows = await db.scalars(
            select(UserNotification.kind).where(
                UserNotification.user_id == uuid.UUID(who["user"]["id"]), UserNotification.kind.startswith("battle_")
            )
        )
        return sorted(rows)


# ------------------------------------------------------------------ eligibility (section 5)
async def test_only_human_approved_single_answer_short_non_nhprep_questions_are_competitive(app, bank):
    async with app.state.db.sessionmaker() as db:
        approved_only = QbQuestion.review_status == "approved"
        approved = await db.scalar(select(func.count()).select_from(QbQuestion).where(approved_only))
        assert approved > 10 and await battles.pool_size(db, bank, None, "mixed") == approved
        first, second, third = (await db.scalars(select(QbQuestion.question_id).where(approved_only).limit(3))).all()
        await db.execute(text("UPDATE qb_questions SET key_origin = 'nhprep' WHERE question_id = :q"), {"q": first})
        await db.execute(
            text("UPDATE qb_questions SET stem = repeat('word ', 120) WHERE question_id = :q"), {"q": second}
        )
        await db.execute(  # back for review (what an edit does): the approval no longer holds
            text("UPDATE qb_questions SET review_status = 'needs_review' WHERE question_id = :q"), {"q": third}
        )
        await db.commit()
        assert await battles.pool_size(db, bank, None, "mixed") == approved - 3


# ------------------------------------------------------------------ a whole battle
async def test_a_battle_runs_round_by_round_and_the_server_decides_it(client, app, bank):
    a, b = await pair(client, app, 201)
    created = await challenge(client, a, b)
    assert created.status_code == 201, created.text
    battle_id = created.json()["battleId"]
    assert created.json()["status"] == "invited" and await kinds(app, b) == ["battle_invite"]
    accepted = await client.post(f"{BASE}/battles/{battle_id}/accept", headers=b["headers"])
    assert accepted.json()["status"] == "waiting" and await kinds(app, a) == ["battle_accepted"]
    ready = (await client.post(f"{BASE}/battles/{battle_id}/ready", headers=a["headers"])).json()
    assert ready["status"] == "active" and ready["question"] is None  # the countdown
    assert ready["me"]["ready"] and ready["opponent"]["ready"]

    for round_ in range(1, 6):
        await at(app, battle_id, round_, "opened_at", -1)
        seen = await state(client, a, battle_id)
        question = seen["question"]
        assert question["round"] == round_ and len(question["options"]) == 4 and question["myChoice"] is None
        assert "correct" not in str(question).lower() and "explanation" not in str(seen).lower()  # I15 secrecy
        key = await key_of(app, battle_id, round_)
        assert (await answer(client, a, battle_id, round_, key)).json()["question"]["myChoice"] == key
        assert (await answer(client, a, battle_id, round_, key)).status_code == 200  # the same again: the same
        assert (await answer(client, a, battle_id, round_, wrong(key))).json()["error"]["code"] == "already_answered"
        assert (await answer(client, b, battle_id, round_, wrong(key))).status_code == 200
        assert len((await state(client, a, battle_id))["rounds"]) == round_ - 1  # their pick: only once closed
        await at(app, battle_id, round_, "closes_at", -0.1)
        closed = (await state(client, a, battle_id))["rounds"][-1]
        assert (closed["correctKey"], closed["theirChoice"], closed["theirPoints"]) == (key, wrong(key), 0)
        assert 100 <= closed["myPoints"] <= 150

    done = await state(client, b, battle_id)
    outcome = (done["status"], done["result"], done["opponent"]["winner"], done["me"]["winner"])
    assert outcome == ("completed", "points", True, False)
    assert done["opponent"]["correct"] == 5 and done["me"]["correct"] == 0 and len(done["review"]) == 5
    assert all(item["explanation"] for item in done["review"])
    assert await kinds(app, a) == ["battle_accepted", "battle_finished"]
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(LearningDailyActivity)) == 2  # five live answers each
        modes = set(await db.scalars(select(QbSet.mode).where(QbSet.request_id == uuid.UUID(battle_id))))
        assert modes == {"battle"}
        assert await db.scalar(select(FzBattle.pair_key).where(FzBattle.id == uuid.UUID(battle_id))) is None
    history = (await client.get(f"{BASE}/battles?view=history", headers=a["headers"])).json()["items"]
    assert [item["battleId"] for item in history] == [battle_id]


async def test_a_late_answer_is_refused_and_three_missed_rounds_forfeit(client, app, bank):
    a, b = await pair(client, app, 211)
    battle_id = await started(client, a, b)
    for round_ in range(1, 4):
        await at(app, battle_id, round_, "opened_at", -1)
        assert (await answer(client, a, battle_id, round_, "A")).status_code == 200
        await at(app, battle_id, round_, "closes_at", -0.1)
        if round_ == 1:
            assert (await answer(client, b, battle_id, 1, "A")).json()["error"]["code"] == "round_closed"
    done = await state(client, a, battle_id)
    assert (done["status"], done["result"], done["endReason"], done["me"]["winner"]) == (
        "completed",
        "forfeit",
        "forfeit",
        True,
    )


async def test_a_question_withdrawn_mid_round_scores_nothing_for_either(client, app, bank):
    a, b = await pair(client, app, 221)
    battle_id = await started(client, a, b)
    await at(app, battle_id, 1, "opened_at", -1)
    await answer(client, a, battle_id, 1, await key_of(app, battle_id, 1))
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text(
                "UPDATE qb_questions SET review_status = 'needs_review' WHERE question_id = "
                "(SELECT question_id FROM fz_battle_rounds WHERE battle_id = :b AND round = 1)"
            ),
            {"b": uuid.UUID(battle_id)},
        )
        await db.commit()
    await at(app, battle_id, 1, "closes_at", -0.1)
    seen = await state(client, a, battle_id)
    assert seen["rounds"][0]["voided"] is True and seen["rounds"][0]["myPoints"] == 0
    assert seen["me"]["score"] == 0 and seen["me"]["answered"] == 0  # left out of accuracy (R-E3)


# ------------------------------------------------------------------ lifecycle edges
async def test_who_may_battle_and_the_conflicts(client, app, bank):
    a, b = await pair(client, app, 231)
    stranger = await member(client, app, "NH-FZ-0239")
    assert (await challenge(client, a, stranger)).status_code == 404  # friends only
    assert (await challenge(client, a, b, program="cyber-cloud-security-ai")).status_code == 404  # a shared program
    short = await challenge(client, a, b, topicId="CCNA-D3-T01", length=10)
    assert short.json()["error"]["code"] == "not_enough_questions" and short.json()["error"]["current"]["needed"] == 10

    request_id = str(uuid.uuid4())
    first = await challenge(client, a, b, requestId=request_id)
    again = await challenge(client, a, b, requestId=request_id)
    assert first.json()["battleId"] == again.json()["battleId"]  # idempotent
    taken = await challenge(client, b, a)
    assert taken.json()["error"]["code"] == "battle_exists"
    assert taken.json()["error"]["current"]["battleId"] == first.json()["battleId"]
    battle_id = first.json()["battleId"]
    assert (await client.get(f"{BASE}/battles/{battle_id}", headers=stranger["headers"])).status_code == 404
    assert (await client.post(f"{BASE}/battles/{battle_id}/ready", headers=a["headers"])).status_code == 409
    assert (await client.post(f"{BASE}/battles/{battle_id}/accept", headers=a["headers"])).status_code == 404
    declined = await client.post(f"{BASE}/battles/{battle_id}/decline", headers=b["headers"])
    assert declined.json()["status"] == "declined"

    expiring = (await challenge(client, a, b)).json()["battleId"]
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text("UPDATE fz_battles SET expires_at = clock_timestamp() - interval '1 second' WHERE id = :b"),
            {"b": uuid.UUID(expiring)},
        )
        await db.commit()
    assert (await state(client, b, expiring))["status"] == "expired"
    assert (await client.post(f"{BASE}/battles/{expiring}/accept", headers=b["headers"])).status_code == 409


async def test_a_block_voids_an_active_battle_and_a_member_plays_one_battle_at_a_time(client, app, bank):
    a, b = await pair(client, app, 241)
    c = await member(client, app, "NH-FZ-0243", "Chen Li")
    await befriend(client, c, b)
    battle_id = await started(client, a, b)
    invite = (await challenge(client, c, b)).json()["battleId"]
    busy = await client.post(f"{BASE}/battles/{invite}/accept", headers=b["headers"])
    assert busy.json()["error"]["code"] == "busy_in_battle"
    await at(app, battle_id, 1, "opened_at", -1)
    assert (await answer(client, a, battle_id, 1, "A")).status_code == 200
    assert (await block(client, b, a)).status_code == 204
    voided = await state(client, a, battle_id)
    # I21: a block reads like any other end
    assert (voided["status"], voided["result"], voided["endReason"]) == ("completed", "void", "ended")
    assert await kinds(app, a) == ["battle_accepted"]  # a void battle tells no one
    assert (await client.post(f"{BASE}/battles/{invite}/accept", headers=b["headers"])).json()["status"] == "waiting"


async def test_qa_battles_stay_out_of_every_record(client, app, bank):
    live = await member(client, app, "NH-FZ-0251")  # joined before the QA list exists: live
    accounts = [await enrolled(client, app, number) for number in ("NH-FZ-0252", "NH-FZ-0253")]
    app.state.settings = make_settings(
        programs_mode="on", friend_zone_mode="on", friend_zone_qa_emails=",".join(a["email"] for a in accounts)
    )
    qa = []
    for account, name in zip(accounts, ("QA one", "QA two"), strict=True):
        joined = await client.post(
            f"{BASE}/me", json={"displayName": name, "discoverable": True}, headers=account["headers"]
        )
        qa.append({**account, "player": joined.json()["playerId"]})
    await befriend(client, qa[0], qa[1])
    assert (await challenge(client, live, qa[0])).status_code == 404  # never across cohorts
    battle_id = await started(client, qa[0], qa[1])
    for round_ in range(1, 6):
        await at(app, battle_id, round_, "opened_at", -1)
        await answer(client, qa[0], battle_id, round_, await key_of(app, battle_id, round_))
        await at(app, battle_id, round_, "closes_at", -0.1)
    assert (await state(client, qa[0], battle_id))["status"] == "completed"
    async with app.state.db.sessionmaker() as db:
        assert set(await db.scalars(select(QbSet.mode).where(QbSet.request_id == uuid.UUID(battle_id)))) == {"fz_qa"}
        assert await db.scalar(select(func.count()).select_from(LearningDailyActivity)) == 0
