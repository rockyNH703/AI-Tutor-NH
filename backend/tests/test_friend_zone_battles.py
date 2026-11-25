"""NH Friend Zone battles, as an exam (owner-approved exam plan, revision 2): competitive eligibility (section 5), the
battle lifecycle and its terminal outcomes, secrecy and isolation (I15, I16, I18, I19, I20, I21).

Time is moved by shifting a battle's stored windows against the database clock (`at`), never by sleeping. The
helpers here (pair, challenge, started, begin, pick, save, submit, keys) are shared by the other Friend Zone tests."""

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
    QbSetItem,
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


async def load_bank(app, tmp_path, per_topic: int = 9):
    """The synthetic certification bank, its single-answer questions approved by a person (the only verification
    that counts, R-E1), mapped to the program's month 1. Returns that month's module id. Nine per topic: 24
    competitive questions; fifteen: 40."""
    async with app.state.db.sessionmaker() as db:
        report = await import_bank(db, netsec_bank(tmp_path, per_topic=per_topic), apply=True)
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


@pytest.fixture
async def bank(app, tmp_path):
    return await load_bank(app, tmp_path)


async def pair(client, app, base: int) -> tuple[dict, dict]:
    a = await member(client, app, f"NH-FZ-{base:04d}", "Asha Menon")
    b = await member(client, app, f"NH-FZ-{base + 1:04d}", "Bilal Khan")
    await befriend(client, a, b)
    return a, b


async def challenge(client, a: dict, b: dict, **extra):
    body = {"opponentId": b["player"], "program": PROGRAM, "month": 1, "length": 10, "requestId": str(uuid.uuid4())}
    return await client.post(f"{BASE}/battles", json={**body, **extra}, headers=a["headers"])


async def state(client, who: dict, battle_id: str) -> dict:
    response = await client.get(f"{BASE}/battles/{battle_id}", headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def started(client, a: dict, b: dict, **extra) -> str:
    """Challenged, accepted and readied: the exam is in its countdown."""
    created = await challenge(client, a, b, **extra)
    assert created.status_code == 201, created.text
    battle_id = created.json()["battleId"]
    accepted = await client.post(f"{BASE}/battles/{battle_id}/accept", headers=b["headers"])
    assert accepted.json()["status"] == "waiting", accepted.text
    ready = await client.post(f"{BASE}/battles/{battle_id}/ready", headers=a["headers"])
    assert ready.json()["status"] == "active", ready.text
    return battle_id


async def at(app, battle_id: str, round_: int, column: str, seconds: float) -> None:
    """Move the battle's clock so that round `round_`'s `column` (opened_at or closes_at) is `seconds` from now. In
    an exam every question shares the start (opened_at) and the deadline (closes_at); the cutoff is 2 s later."""
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
        await db.execute(
            text(
                "UPDATE fz_battle_players SET submitted_at = submitted_at - CAST(:d AS interval) WHERE battle_id = :b"
            ),
            params,
        )
        await db.commit()


async def begin(client, app, a: dict, b: dict, **extra) -> str:
    """An exam that started a second ago."""
    battle_id = await started(client, a, b, **extra)
    await at(app, battle_id, 1, "opened_at", -1)
    return battle_id


async def keys(app, battle_id: str) -> dict[int, str]:
    """Every question's key, by round (the test reads the database; the API never says it before the end)."""
    async with app.state.db.sessionmaker() as db:
        rows = await db.execute(
            text(
                "SELECT r.round, coalesce(q.correct_keys[1], q.correct_option::text) AS key "
                "FROM fz_battle_rounds r JOIN qb_questions q USING (question_id) WHERE r.battle_id = :b"
            ),
            {"b": uuid.UUID(battle_id)},
        )
        return {row.round: row.key for row in rows}


async def key_of(app, battle_id: str, round_: int) -> str:
    return (await keys(app, battle_id))[round_]


def wrong(key: str) -> str:
    return "A" if key != "A" else "B"


def pick(round_: int, choice: str | None, base_rev: int | None = None, marked: bool = False) -> dict:
    """A set item. Without base_rev: the revision a fresh exam has (round 1 was visited at the start: rev 1)."""
    rev = (1 if round_ == 1 else 0) if base_rev is None else base_rev
    return {"round": round_, "choice": choice, "marked": marked, "baseRev": rev}


def answers(found: dict[int, str], right=(), wrong_=()) -> list[dict]:
    """Picks for a fresh exam: the key for `right` rounds, a wrong letter for `wrong_` ones."""
    return [pick(r, found[r]) for r in right] + [pick(r, wrong(found[r])) for r in wrong_]


async def save(client, who: dict, battle_id: str, *items: dict):
    return await client.post(
        f"{BASE}/battles/{battle_id}/responses", json={"responses": list(items)}, headers=who["headers"]
    )


async def submit(client, who: dict, battle_id: str, *items: dict, auto: bool = False):
    body = {"responses": list(items), "auto": auto}
    return await client.post(f"{BASE}/battles/{battle_id}/submit", json=body, headers=who["headers"])


async def answer(client, who: dict, battle_id: str, round_: int, choice: str):
    """The retired round-battle route."""
    body = {"round": round_, "choice": choice}
    return await client.post(f"{BASE}/battles/{battle_id}/answers", json=body, headers=who["headers"])


async def kinds(app, who: dict) -> list[str]:
    async with app.state.db.sessionmaker() as db:
        rows = await db.scalars(
            select(UserNotification.kind).where(
                UserNotification.user_id == uuid.UUID(who["user"]["id"]), UserNotification.kind.startswith("battle_")
            )
        )
        return sorted(rows)


async def graded(app, battle_id: str, who: dict | None = None) -> int:
    """Answers in the ledger for this battle (one player's, or both)."""
    async with app.state.db.sessionmaker() as db:
        query = (
            select(func.count())
            .select_from(QbSetItem)
            .join(QbSet, QbSet.id == QbSetItem.set_id)
            .where(QbSet.request_id == uuid.UUID(battle_id), QbSetItem.answered_at.is_not(None))
        )
        if who is not None:
            query = query.where(QbSetItem.user_id == uuid.UUID(who["user"]["id"]))
        return await db.scalar(query)


# ------------------------------------------------------------------ eligibility (section 5)
async def test_only_human_approved_single_answer_short_non_nhprep_questions_are_competitive(app, bank):
    async with app.state.db.sessionmaker() as db:
        approved_only = QbQuestion.review_status == "approved"
        approved = await db.scalar(select(func.count()).select_from(QbQuestion).where(approved_only))
        assert approved > 10 and await battles.pool_size(db, bank, None, "mixed", policy="human_only") == approved
        first, second, third = (await db.scalars(select(QbQuestion.question_id).where(approved_only).limit(3))).all()
        await db.execute(text("UPDATE qb_questions SET key_origin = 'nhprep' WHERE question_id = :q"), {"q": first})
        await db.execute(
            text("UPDATE qb_questions SET stem = repeat('word ', 120) WHERE question_id = :q"), {"q": second}
        )
        await db.execute(  # back for review (what an edit does): the approval no longer holds
            text("UPDATE qb_questions SET review_status = 'needs_review' WHERE question_id = :q"), {"q": third}
        )
        await db.commit()
        assert await battles.pool_size(db, bank, None, "mixed", policy="human_only") == approved - 3


# ------------------------------------------------------------------ a whole exam
async def test_an_exam_runs_on_one_paper_and_the_server_decides_it(client, app, bank):
    a, b = await pair(client, app, 201)
    created = await challenge(client, a, b)
    assert created.status_code == 201, created.text
    battle_id = created.json()["battleId"]
    assert (created.json()["status"], created.json()["format"]) == ("invited", "exam")
    assert await kinds(app, b) == ["battle_invite"]
    accepted = await client.post(f"{BASE}/battles/{battle_id}/accept", headers=b["headers"])
    assert accepted.json()["status"] == "waiting" and await kinds(app, a) == ["battle_accepted"]
    ready = (await client.post(f"{BASE}/battles/{battle_id}/ready", headers=a["headers"])).json()
    assert ready["status"] == "active" and ready["exam"] is None and ready["question"] is None  # the countdown
    assert ready["me"]["ready"] and ready["opponent"]["ready"]

    await at(app, battle_id, 1, "opened_at", -1)
    mine, theirs = await state(client, a, battle_id), await state(client, b, battle_id)
    assert [q["round"] for q in mine["exam"]["questions"]] == list(range(1, 11))
    assert mine["exam"]["questions"] == theirs["exam"]["questions"]  # D4: one paper, the same order
    found = await keys(app, battle_id)
    assert (await save(client, a, battle_id, *answers(found, right=range(1, 11)))).status_code == 200
    assert (await save(client, b, battle_id, *answers(found, wrong_=range(1, 11)))).status_code == 200
    running = await state(client, a, battle_id)
    assert running["rounds"] == [] and running["review"] is None and "explanation" not in str(running).lower()
    assert await graded(app, battle_id) == 0  # nothing graded before the end

    assert (await submit(client, b, battle_id)).json()["status"] == "active"
    assert (await state(client, a, battle_id))["exam"]["opponentSubmitted"] is True
    done = (await submit(client, a, battle_id)).json()  # both have submitted: decided now
    outcome = (done["status"], done["result"], done["endReason"], done["me"]["winner"], done["opponent"]["winner"])
    assert outcome == ("completed", "points", "finished", True, False)
    assert (done["me"]["score"], done["me"]["correct"], done["opponent"]["score"]) == (40, 10, -10)
    assert done["exam"] is None and len(done["review"]) == 10 and done["roundsClosed"] == 10
    assert all(item["explanation"] and item["correctKey"] for item in done["review"])
    assert [round_["myPoints"] for round_ in done["rounds"]] == [4] * 10
    assert await kinds(app, a) == ["battle_accepted", "battle_finished"]
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(LearningDailyActivity)) == 2  # ten live answers each
        modes = set(await db.scalars(select(QbSet.mode).where(QbSet.request_id == uuid.UUID(battle_id))))
        assert modes == {"battle"}
        assert await db.scalar(select(FzBattle.pair_key).where(FzBattle.id == uuid.UUID(battle_id))) is None
        assert await db.scalar(text("SELECT count(*) FROM fz_battle_responses")) == 0  # the drafts are gone
    assert await graded(app, battle_id) == 20
    history = (await client.get(f"{BASE}/battles?view=history", headers=a["headers"])).json()["items"]
    assert [item["battleId"] for item in history] == [battle_id]


async def test_the_retired_answer_route_always_refuses_and_grades_nothing(client, app, bank):
    a, b = await pair(client, app, 211)
    battle_id = await begin(client, app, a, b)
    refused = await answer(client, a, battle_id, 1, await key_of(app, battle_id, 1))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "battle_not_open"
    assert await graded(app, battle_id) == 0
    assert (await state(client, a, battle_id))["status"] == "active"


async def test_a_question_withdrawn_mid_exam_scores_nothing_and_shows_nothing(client, app, bank):
    a, b = await pair(client, app, 221)
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    await save(client, a, battle_id, *answers(found, right=(1, 2)))
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text(
                "UPDATE qb_questions SET review_status = 'needs_review' WHERE question_id = "
                "(SELECT question_id FROM fz_battle_rounds WHERE battle_id = :b AND round = 1)"
            ),
            {"b": uuid.UUID(battle_id)},
        )
        await db.commit()
    await submit(client, b, battle_id)
    done = (await submit(client, a, battle_id)).json()
    first = done["review"][0]
    assert first["voided"] is True and (first["stem"], first["correctKey"], first["explanation"]) == (None, None, None)
    assert done["rounds"][0]["correctKey"] is None and done["rounds"][0]["myPoints"] == 0
    assert (done["me"]["answered"], done["me"]["correct"], done["me"]["score"]) == (1, 1, 4)  # left out (R-E3)
    assert await graded(app, battle_id, a) == 1  # O1: the draft on the voided question is discarded


# ------------------------------------------------------------------ lifecycle edges
async def test_who_may_battle_and_the_conflicts(client, app, bank):
    a, b = await pair(client, app, 231)
    stranger = await member(client, app, "NH-FZ-0239")
    assert (await challenge(client, a, stranger)).status_code == 404  # friends only
    assert (await challenge(client, a, b, program="cyber-cloud-security-ai")).status_code == 404  # a shared program
    assert (await challenge(client, a, b, length=5)).status_code == 422  # O4: 10, 20 or 30 only
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
    assert (await save(client, a, battle_id, pick(1, "A", 0))).json()["error"]["code"] == "battle_not_open"
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


async def test_a_block_voids_a_running_exam_and_a_member_plays_one_battle_at_a_time(client, app, bank):
    a, b = await pair(client, app, 241)
    c = await member(client, app, "NH-FZ-0243", "Chen Li")
    await befriend(client, c, b)
    battle_id = await begin(client, app, a, b)
    invite = (await challenge(client, c, b)).json()["battleId"]
    busy = await client.post(f"{BASE}/battles/{invite}/accept", headers=b["headers"])
    assert busy.json()["error"]["code"] == "busy_in_battle"
    found = await keys(app, battle_id)
    await save(client, a, battle_id, *answers(found, right=(1, 2)))
    assert (await block(client, b, a)).status_code == 204
    voided = await state(client, a, battle_id)
    # I21: a block reads like any other end
    assert (voided["status"], voided["result"], voided["endReason"]) == ("completed", "void", "ended")
    assert voided["review"] == [] and voided["rounds"] == []  # no question was closed: no key, no review
    assert await kinds(app, a) == ["battle_accepted"]  # a void battle tells no one
    assert await graded(app, battle_id, a) == 2  # the drafts given so far stay real practice
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(text("SELECT count(*) FROM fz_battle_responses")) == 0
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
    battle_id = await begin(client, app, qa[0], qa[1])
    found = await keys(app, battle_id)
    await submit(client, qa[0], battle_id, *answers(found, right=range(1, 11)))
    assert (await submit(client, qa[1], battle_id)).status_code == 200
    assert (await state(client, qa[0], battle_id))["status"] == "completed"
    async with app.state.db.sessionmaker() as db:
        assert set(await db.scalars(select(QbSet.mode).where(QbSet.request_id == uuid.UUID(battle_id)))) == {"fz_qa"}
        assert await db.scalar(select(func.count()).select_from(LearningDailyActivity)) == 0
    assert await graded(app, battle_id, qa[0]) == 10
