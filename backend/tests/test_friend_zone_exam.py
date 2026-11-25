"""NH Friend Zone exam battle (owner-approved exam plan, revision 2): the time model (D8), submit, compare-and-set,
the palette's persistence, secrecy, practice records, endings, scoring and the XP table (D1, D2, D7), races, and
overdue completion on every path. Time is moved by shifting the stored windows (`at`), never by sleeping."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select, text

from app.db.models import (
    FzBadgeUnlock,
    FzBattle,
    FzBattlePlayer,
    FzXpEvent,
    LearningDailyActivity,
    QbQuestion,
    QbSet,
    QbSetItem,
    UserNotification,
)
from app.friendzone import battle_routes, battles, friends, progress
from tests.conftest import make_settings
from tests.test_friend_zone_battles import (
    BASE,
    answers,
    at,
    begin,
    challenge,
    graded,
    keys,
    kinds,
    load_bank,
    pair,
    pick,
    save,
    started,
    state,
    submit,
    wrong,
)
from tests.test_friend_zone_relations import befriend, block, member

EXAM_FIELDS = {"secondsPerQuestion", "endsAt", "cutoffAt", "questions", "responses", "submittedAt", "opponentSubmitted"}
VISITED = {"round": 1, "choice": None, "marked": False, "rev": 1}


@pytest.fixture
def settings():
    return make_settings(programs_mode="on", friend_zone_mode="on")


@pytest.fixture(autouse=True)
def no_floor(monkeypatch):
    monkeypatch.setattr(friends, "SEARCH_FLOOR_SECONDS", 0.0)


@pytest.fixture
async def bank(app, tmp_path):
    """40 competitive questions: enough for a 30-question exam."""
    return await load_bank(app, tmp_path, per_topic=15)


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def players_of(app, battle_id: str) -> dict[str, FzBattlePlayer]:
    async with app.state.db.sessionmaker() as db:
        rows = await db.scalars(select(FzBattlePlayer).where(FzBattlePlayer.battle_id == uuid.UUID(battle_id)))
        return {row.role: row for row in rows}


async def row_of(app, battle_id: str) -> FzBattle:
    async with app.state.db.sessionmaker() as db:
        return await db.get(FzBattle, uuid.UUID(battle_id))


async def xp_of(app, who: dict) -> dict[str, int]:
    async with app.state.db.sessionmaker() as db:
        rows = await db.execute(
            select(FzXpEvent.reason, func.sum(FzXpEvent.amount))
            .where(FzXpEvent.user_id == uuid.UUID(who["user"]["id"]))
            .group_by(FzXpEvent.reason)
        )
        return dict(rows.all())


async def badges(app, who: dict) -> set[str]:
    async with app.state.db.sessionmaker() as db:
        return set(
            await db.scalars(select(FzBadgeUnlock.badge).where(FzBadgeUnlock.user_id == uuid.UUID(who["user"]["id"])))
        )


async def interactions(app, who: dict) -> int:
    async with app.state.db.sessionmaker() as db:
        total = await db.scalar(
            select(func.sum(LearningDailyActivity.interactions)).where(
                LearningDailyActivity.user_id == uuid.UUID(who["user"]["id"])
            )
        )
        return int(total or 0)


async def earlier_answers(app, who: dict, count: int) -> None:
    """`count` question-bank answers today, before the exam (recorded without a practice call, as history)."""
    if not count:
        return
    user_id = uuid.UUID(who["user"]["id"])
    async with app.state.db.sessionmaker() as db:
        questions = (
            await db.execute(
                select(QbQuestion.question_id, QbQuestion.topic_id)
                .where(QbQuestion.question_format == "mcq")
                .order_by(QbQuestion.question_id)
                .limit(count)
            )
        ).all()
        set_id = await db.scalar(
            text(
                "INSERT INTO qb_sets (user_id, request_id, mode, filters, requested, served) "
                "VALUES (:u, :r, 'test', '{}', :n, :n) RETURNING id"
            ),
            {"u": user_id, "r": uuid.uuid4(), "n": count},
        )
        for position, row in enumerate(questions, 1):
            db.add(
                QbSetItem(
                    set_id=set_id,
                    position=position,
                    user_id=user_id,
                    question_id=row.question_id,
                    topic_id=row.topic_id,
                )
            )
        await db.flush()
        await db.execute(text("UPDATE qb_set_items SET chosen_keys = ARRAY['A'] WHERE set_id = :s"), {"s": set_id})
        await db.commit()


async def leave(app, who: dict) -> None:
    """The member's Friend Zone profile goes (as an account deletion does): their player row becomes anonymous."""
    async with app.state.db.sessionmaker() as db:
        await db.execute(text("DELETE FROM fz_profiles WHERE user_id = :u"), {"u": uuid.UUID(who["user"]["id"])})
        await db.commit()


async def cap(app, who: dict) -> None:
    """Five rewarded battles today already: the daily XP cap is reached."""
    async with app.state.db.sessionmaker() as db:
        for _ in range(progress.REWARDED_PER_DAY):
            db.add(
                FzXpEvent(
                    user_id=uuid.UUID(who["user"]["id"]),
                    cohort="live",
                    reason="battle",
                    ref_id=str(uuid.uuid4()),
                    amount=20,
                )
            )
        await db.commit()


async def finish(client, app, a, b, *, a_right=0, a_wrong=0, b_right=0, b_wrong=0, auto=False, **extra) -> dict:
    """A whole exam: each player's picks, both submitted (`auto`: both at the deadline). Returns a's final view."""
    battle_id = await begin(client, app, a, b, **extra)
    found = await keys(app, battle_id)
    mine = answers(found, right=range(1, a_right + 1), wrong_=range(a_right + 1, a_right + a_wrong + 1))
    theirs = answers(found, right=range(1, b_right + 1), wrong_=range(b_right + 1, b_right + b_wrong + 1))
    assert (await submit(client, a, battle_id, *mine, auto=auto)).status_code == 200
    assert (await submit(client, b, battle_id, *theirs, auto=auto)).status_code == 200
    view = await state(client, a, battle_id)
    assert view["status"] == "completed", view
    return view


class Refuse:
    """A poll limiter that refuses everything."""

    def allow(self, key: str) -> bool:
        return False


# ------------------------------------------------------------------ the time model (section 2)
async def test_exams_of_10_20_and_30_have_60_seconds_a_question(client, app, bank):
    for base, length in ((501, 10), (503, 20), (505, 30)):
        a, b = await pair(client, app, base)
        battle_id = await started(client, a, b, length=length)
        seen = await state(client, a, battle_id)
        assert seen["length"] == length and seen["format"] == "exam"
        assert (moment(seen["endsAt"]) - moment(seen["startsAt"])).total_seconds() == length * 60
        assert seen["exam"] is None and seen["nextChangeAt"] == seen["startsAt"]  # the countdown
        async with app.state.db.sessionmaker() as db:
            served = await db.scalar(
                select(func.count())
                .select_from(QbSetItem)
                .join(QbSet, QbSet.id == QbSetItem.set_id)
                .where(QbSet.request_id == uuid.UUID(battle_id))
            )
            assert served == 2 * length  # both whole sets, served at the start
    assert (await challenge(client, a, b, length=5)).status_code == 422


async def test_a_save_inside_the_allowance_is_graded_and_the_cutoff_completes(client, app, bank):
    a, b = await pair(client, app, 511)
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    await at(app, battle_id, 1, "closes_at", -1)  # the deadline passed a second ago; the cutoff is a second away
    assert (await state(client, a, battle_id))["status"] == "active"  # never completed before the cutoff
    late = await save(client, a, battle_id, pick(2, found[2]))
    assert late.status_code == 200 and late.json()["items"] == [{"round": 2, "applied": True, "rev": 1}]
    await at(app, battle_id, 1, "closes_at", -2.05)  # 50 ms past the cutoff
    done = await state(client, b, battle_id)  # b never polled before
    assert (done["status"], done["opponent"]["correct"], done["opponent"]["answered"]) == ("completed", 1, 1)
    refused = await save(client, a, battle_id, pick(3, found[3]))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "battle_not_open"
    players = await players_of(app, battle_id)
    ends = (await row_of(app, battle_id)).ends_at
    assert players["challenger"].submitted_at == ends and players["opponent"].submitted_at == ends


async def test_submit_records_the_time_and_an_auto_submit_the_deadline(client, app, bank):
    a, b = await pair(client, app, 521)
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    first = await submit(client, a, battle_id, *answers(found, right=(1, 2, 3)))
    assert first.status_code == 200 and first.json()["exam"]["submittedAt"] is not None
    assert first.json()["status"] == "active"  # the other is still answering
    assert first.json()["submitApplied"] is True
    assert (await state(client, a, battle_id))["submitApplied"] is None  # only on the /submit answer
    after = await save(client, a, battle_id, pick(4, found[4]))
    assert after.status_code == 409 and after.json()["error"]["code"] == "battle_not_open"
    twice = await submit(client, a, battle_id, pick(4, found[4]))  # already submitted: nothing applied
    assert twice.status_code == 200 and twice.json()["submitApplied"] is False

    limiter = app.state.friend_zone_poll_limiter
    app.state.friend_zone_poll_limiter = Refuse()  # an applying submit is never rate limited
    try:
        assert (await client.get(f"{BASE}/battles/{battle_id}", headers=b["headers"])).status_code == 429
        assert (await save(client, b, battle_id, pick(2, found[2]))).status_code == 429
        assert (await submit(client, a, battle_id)).status_code == 429  # already submitted: the poll limit
        done = await submit(client, b, battle_id, pick(1, wrong(found[1])), auto=True)  # early: still the deadline
        assert (await submit(client, b, battle_id, auto=True)).status_code == 429  # completed: the poll limit
    finally:
        app.state.friend_zone_poll_limiter = limiter
    assert done.status_code == 200 and done.json()["status"] == "completed" and done.json()["submitApplied"] is True
    players, row = await players_of(app, battle_id), await row_of(app, battle_id)
    assert players["opponent"].submitted_at == row.ends_at
    assert row.starts_at <= players["challenger"].submitted_at < row.ends_at
    shown = done.json()  # b's view
    assert shown["me"]["timeTakenSeconds"] == 600 and shown["opponent"]["timeTakenSeconds"] <= 5

    again = await submit(client, a, battle_id, pick(5, found[5]))  # after the end: 200, nothing applied
    assert again.status_code == 200 and again.json()["status"] == "completed"
    assert again.json()["submitApplied"] is False
    assert (await submit(client, b, battle_id, auto=True)).status_code == 200
    assert await graded(app, battle_id, a) == 3 and await graded(app, battle_id, b) == 1


# ------------------------------------------------------------------ compare-and-set and the palette (sections 3, 4)
async def test_compare_and_set_keeps_the_latest_intent_and_the_palette_survives_a_reread(client, app, bank):
    a, b = await pair(client, app, 531)
    battle_id = await begin(client, app, a, b)
    assert (await state(client, a, battle_id))["exam"]["responses"] == [VISITED]  # round 1: visited at the start

    async def one(item: dict) -> dict:
        response = await save(client, a, battle_id, item)
        assert response.status_code == 200, response.text
        return response.json()["items"][0]

    assert await one(pick(2, "A", 0)) == {"round": 2, "applied": True, "rev": 1}  # a set on no row inserts it
    assert await one(pick(2, "A", 0)) == {"round": 2, "applied": False, "rev": 1}  # a replay is harmless
    assert await one(pick(2, "B", 1)) == {"round": 2, "applied": True, "rev": 2}
    assert await one(pick(2, "C", 1)) == {"round": 2, "applied": False, "rev": 2}  # aborted, then late: not applied
    assert await one(pick(2, None, 2)) == {"round": 2, "applied": True, "rev": 3}  # Clear
    assert await one(pick(2, "A", 2)) == {"round": 2, "applied": False, "rev": 3}  # a late pick never undoes it
    assert await one({"round": 2, "op": "visit"}) == {"round": 2, "applied": True, "rev": 3}  # never changes a row
    assert await one({"round": 3, "op": "visit"}) == {"round": 3, "applied": True, "rev": 1}
    assert await one(pick(4, None, 0, marked=True)) == {"round": 4, "applied": True, "rev": 1}
    assert await one(pick(5, "D", 0, marked=True)) == {"round": 5, "applied": True, "rev": 1}
    twice = (await save(client, a, battle_id, pick(6, "A", 0), pick(6, "B", 0))).json()["items"]
    assert [(item["applied"], item["rev"]) for item in twice] == [(True, 1), (False, 1)]
    chained = (await save(client, a, battle_id, pick(7, "A", 0), pick(7, "B", 1))).json()["items"]
    assert [(item["applied"], item["rev"]) for item in chained] == [(True, 1), (True, 2)]

    refused = await save(client, a, battle_id, pick(8, "A", 0), pick(9, "H", 0))  # H: not an option
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "invalid_choice"
    outside = await save(client, a, battle_id, pick(11, "A", 0))  # not a question of this 10-question exam
    assert outside.json()["error"]["code"] == "invalid_choice"
    for body in (
        {"round": 2, "choice": "A"},  # a set sends choice, marked and baseRev
        {"round": 2, "op": "visit", "marked": True},  # a visit carries only round
        pick(31, "A", 0),
        pick(2, "Z", 0),
        {**pick(2, "A", 0), "seq": 9},
    ):
        assert (await save(client, a, battle_id, body)).status_code == 422, body

    expected = [
        VISITED,
        {"round": 2, "choice": None, "marked": False, "rev": 3},  # visited, not answered
        {"round": 3, "choice": None, "marked": False, "rev": 1},
        {"round": 4, "choice": None, "marked": True, "rev": 1},  # marked
        {"round": 5, "choice": "D", "marked": True, "rev": 1},  # answered and marked (still counts, D6)
        {"round": 6, "choice": "A", "marked": False, "rev": 1},
        {"round": 7, "choice": "B", "marked": False, "rev": 2},
    ]  # nothing of the refused batch (round 8); rounds 9 and 10 not visited
    assert (await state(client, a, battle_id))["exam"]["responses"] == expected
    reread = (await save(client, a, battle_id, {"round": 2, "op": "visit"})).json()
    assert reread["responses"] == expected and reread["opponentSubmitted"] is False and reread["status"] == "active"
    assert (await state(client, b, battle_id))["exam"]["responses"] == [VISITED]  # never the other's


# ------------------------------------------------------------------ secrecy
async def test_the_paper_comes_at_the_start_and_nothing_secret_before_the_end(client, app, bank):
    a, b = await pair(client, app, 541)
    battle_id = await started(client, a, b)
    countdown = await state(client, a, battle_id)
    assert countdown["exam"] is None and countdown["nextChangeAt"] == countdown["startsAt"]
    assert (await save(client, a, battle_id, pick(1, "A"))).json()["error"]["code"] == "battle_not_open"
    await at(app, battle_id, 1, "opened_at", -1)
    seen = await state(client, a, battle_id)
    exam = seen["exam"]
    assert set(exam) == EXAM_FIELDS and exam["secondsPerQuestion"] == 60 and len(exam["questions"]) == 10
    assert all(set(question) == {"round", "stem", "options"} for question in exam["questions"])
    assert seen["nextChangeAt"] == exam["cutoffAt"] and seen["question"] is None
    assert (moment(exam["cutoffAt"]) - moment(exam["endsAt"])).total_seconds() == 2

    found = await keys(app, battle_id)
    await save(client, b, battle_id, *answers(found, right=range(1, 6)))
    async with app.state.db.sessionmaker() as db:  # even a stored score must not show before the end
        await db.execute(
            text(
                "UPDATE fz_battle_players SET score = 99, correct = 9, answered = 9, winner = true WHERE battle_id = :b"
            ),
            {"b": uuid.UUID(battle_id)},
        )
        await db.commit()
    response = await client.get(f"{BASE}/battles/{battle_id}", headers=a["headers"])
    seen = response.json()
    assert "Because" not in response.text and seen["rounds"] == [] and seen["review"] is None and seen["xp"] is None
    for player in (seen["me"], seen["opponent"]):
        masked = (player["score"], player["correct"], player["answered"], player["winner"], player["timeTakenSeconds"])
        assert masked == (0, 0, 0, False, None)
    summary = (await client.get(f"{BASE}/battles", headers=a["headers"])).json()["items"][0]["opponent"]
    assert (summary["score"], summary["correct"], summary["answered"], summary["winner"]) == (0, 0, 0, False)
    assert seen["exam"]["responses"] == [VISITED]  # never the opponent's
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text(
                "UPDATE fz_battle_players SET score = 0, correct = 0, answered = 0, winner = false WHERE battle_id = :b"
            ),
            {"b": uuid.UUID(battle_id)},
        )
        await db.commit()

    await submit(client, a, battle_id, *answers(found, wrong_=(1, 2)))  # an early submitter learns nothing yet
    assert (await client.get("/v1/progress/mistakes", headers=a["headers"])).json()["concepts"] == []
    assert (await submit(client, b, battle_id)).json()["status"] == "completed"
    assert (await client.get("/v1/progress/mistakes", headers=a["headers"])).json()["concepts"] != []


# ------------------------------------------------------------------ practice records (P0: one row at a time)
async def test_practice_is_recorded_one_answer_at_a_time(client, app, bank):
    for base, earlier, answered, expected in ((551, 3, 7, 2), (553, 3, 10, 2), (555, 0, 5, 1)):
        a, b = await pair(client, app, base)
        await earlier_answers(app, a, earlier)
        battle_id = await begin(client, app, a, b)
        found = await keys(app, battle_id)
        await submit(client, a, battle_id, *answers(found, right=range(1, answered + 1)))
        assert (await submit(client, b, battle_id)).json()["status"] == "completed"
        assert await interactions(app, a) == expected, (earlier, answered)


async def test_a_changed_question_is_void_and_a_shrunk_one_never_aborts_completion(client, app, bank):
    a, b = await pair(client, app, 561)
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    target = next(r for r in range(3, 11) if found[r] != "D")
    await save(client, a, battle_id, pick(1, found[1]), pick(target, "D"))
    await save(client, b, battle_id, pick(2, found[2]))
    async with app.state.db.sessionmaker() as db:
        question = "(SELECT question_id FROM fz_battle_rounds WHERE battle_id = :b AND round = :r)"
        await db.execute(  # changed (and re-approved) after it was served: still competitive, but void for both
            text(
                "UPDATE qb_questions SET content_hash = repeat('f', 64), reviewed_hash = repeat('f', 64) "
                f"WHERE question_id = {question}"
            ),
            {"b": uuid.UUID(battle_id), "r": 1},
        )
        await db.execute(  # one option fewer: the draft's D is no longer an option
            text(f"UPDATE qb_questions SET options = options - 3 WHERE question_id = {question}"),
            {"b": uuid.UUID(battle_id), "r": target},
        )
        await db.commit()
    await submit(client, b, battle_id)
    done = (await submit(client, a, battle_id)).json()
    assert (done["status"], done["result"], done["opponent"]["winner"]) == ("completed", "points", True)
    assert done["review"][0]["voided"] is True and done["me"]["answered"] == 0
    assert await graded(app, battle_id, a) == 0


# ------------------------------------------------------------------ endings
async def _ai_checked_only(app) -> None:
    """Every single-answer question verified by an AI check only: competitive under human_or_ai, never human_only."""
    async with app.state.db.sessionmaker() as db:
        await db.execute(text("UPDATE qb_questions SET review_status = 'needs_review' WHERE question_format = 'mcq'"))
        await db.execute(
            text(
                "INSERT INTO qb_key_checks (question_id, content_hash, method, single_defensible, checked_by) "
                "SELECT question_id, content_hash, 'blind', true, 'test' FROM qb_questions "
                "WHERE question_format = 'mcq'"
            )
        )
        await db.commit()


async def test_a_block_or_unfriend_mid_exam_voids_it_and_keeps_the_drafts_as_practice_under_the_policy(
    client, app, bank
):
    app.state.settings = make_settings(programs_mode="on", friend_zone_mode="on", friend_zone_key_policy="human_or_ai")
    await _ai_checked_only(app)
    for base, ending in ((571, "block"), (573, "unfriend")):
        a, b = await pair(client, app, base)
        battle_id = await begin(client, app, a, b)
        found = await keys(app, battle_id)
        await save(client, a, battle_id, *answers(found, right=(1, 2, 3)))
        if ending == "block":
            ended = await block(client, b, a)
        else:
            ended = await client.delete(f"{BASE}/friends/{a['player']}", headers=b["headers"])
        assert ended.status_code == 204
        seen = await state(client, a, battle_id)
        assert (seen["status"], seen["result"], seen["endReason"]) == ("completed", "void", "ended")
        assert await graded(app, battle_id, a) == 3, ending  # judged under human_or_ai, not a default
        assert await xp_of(app, a) == {} and await kinds(app, a) == ["battle_accepted"]


async def test_after_the_cutoff_a_block_leaves_the_real_result(client, app, bank):
    a, b = await pair(client, app, 575)
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    await save(client, a, battle_id, *answers(found, right=(1, 2)))
    await at(app, battle_id, 1, "closes_at", -2.5)  # past the cutoff, and nobody has read it since
    assert (await block(client, b, a)).status_code == 204
    seen = await state(client, a, battle_id)
    assert (seen["status"], seen["result"], seen["me"]["winner"], seen["me"]["score"]) == (
        "completed",
        "points",
        True,
        8,
    )


async def test_a_deleted_opponent_is_decided_at_completion(client, app, bank):
    for base, right, result in ((577, 1, "forfeit"), (579, 0, "void")):
        a, b = await pair(client, app, base)
        battle_id = await begin(client, app, a, b)
        await leave(app, b)
        assert (await state(client, a, battle_id))["status"] == "active"  # never ended early
        found = await keys(app, battle_id)
        done = (await submit(client, a, battle_id, *answers(found, right=range(1, right + 1)))).json()
        assert (done["status"], done["result"], done["me"]["winner"]) == ("completed", result, right > 0)
        assert done["opponent"]["playerId"] is None
        assert await xp_of(app, a) == {}  # one answer of ten: not half, so no XP even for the forfeit win


# ------------------------------------------------------------------ the XP table (section 8)
async def test_every_row_of_the_xp_table(client, app, bank):
    a, b = await pair(client, app, 601)  # both eligible, by points
    view = await finish(client, app, a, b, a_right=10, b_wrong=10)
    assert (view["result"], view["me"]["score"], view["opponent"]["score"]) == ("points", 40, -10)
    assert await xp_of(app, a) == {"battle": 20, "win": 30} and await xp_of(app, b) == {"battle": 20}
    assert view["xp"] == [{"reason": "battle", "amount": 20}, {"reason": "win", "amount": 30}]
    assert "perfect_score" in await badges(app, a)

    c, d = await pair(client, app, 603)  # only one eligible: no +30
    await finish(client, app, c, d, a_right=10, b_wrong=2)
    assert await xp_of(app, c) == {"battle": 20} and await xp_of(app, d) == {}

    e, f = await pair(client, app, 605)  # the loser is capped: the winner still gets +30
    await cap(app, f)
    await finish(client, app, e, f, a_right=10, b_wrong=10)
    assert await xp_of(app, e) == {"battle": 20, "win": 30} and await xp_of(app, f) == {"battle": 100}

    g, h = await pair(client, app, 607)  # equal marks, equal incorrect, both at the deadline: a draw
    view = await finish(client, app, g, h, a_right=6, b_right=6, auto=True)
    assert (view["result"], view["me"]["winner"], view["opponent"]["winner"]) == ("draw", False, False)
    assert await xp_of(app, g) == {"battle": 20, "draw": 10} == await xp_of(app, h)

    i, j = await pair(client, app, 609)  # both answered nothing: void, absent
    view = await finish(client, app, i, j)
    assert (view["result"], view["endReason"]) == ("void", "absent") and await xp_of(app, i) == {}

    k, m = await pair(client, app, 611)  # nothing answered beats a negative score: a win in the stats only
    view = await finish(client, app, k, m, b_wrong=10)
    assert (view["result"], view["me"]["winner"], view["opponent"]["score"]) == ("points", True, -10)
    assert await xp_of(app, k) == {} and await xp_of(app, m) == {"battle": 20}
    stats = (await client.get(f"{BASE}/players/{k['player']}", headers=k["headers"])).json()["stats"]
    assert (stats["wins"], stats["xp"]) == (1, 0)


async def test_perfect_score_at_20_and_30_questions(client, app, bank):
    for base, length in ((621, 20), (623, 30)):
        a, b = await pair(client, app, base)
        view = await finish(client, app, a, b, a_right=length, length=length)
        assert (view["me"]["correct"], view["me"]["score"]) == (length, 4 * length)
        assert "perfect_score" in await badges(app, a) and "perfect_score" not in await badges(app, b)


# ------------------------------------------------------------------ races (asyncio.gather)
async def _once(app, battle_id: str, answered: int, xp_rows: int) -> None:
    async with app.state.db.sessionmaker() as db:
        assert (await db.get(FzBattle, uuid.UUID(battle_id))).status == "completed"
        finished = await db.scalar(
            select(func.count())
            .select_from(UserNotification)
            .where(
                UserNotification.kind == "battle_finished", UserNotification.dedup_key == f"battle-finished:{battle_id}"
            )
        )
        rows = await db.scalar(select(func.count()).select_from(FzXpEvent).where(FzXpEvent.ref_id == battle_id))
    assert (finished, rows, await graded(app, battle_id)) == (2, xp_rows, answered)


async def test_races_complete_exactly_once_without_deadlock(client, app, bank):
    a, b = await pair(client, app, 631)  # submit + submit
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    both = await asyncio.gather(
        submit(client, a, battle_id, *answers(found, right=range(1, 7))),
        submit(client, b, battle_id, *answers(found, wrong_=range(1, 7))),
    )
    assert [response.status_code for response in both] == [200, 200]
    await _once(app, battle_id, 12, 3)

    c, d = await pair(client, app, 633)  # submit + save
    battle_id = await begin(client, app, c, d)
    found = await keys(app, battle_id)
    await submit(client, d, battle_id, *answers(found, right=range(1, 6), wrong_=range(6, 8)))  # 18 marks
    sent, saved = await asyncio.gather(
        submit(client, c, battle_id, *answers(found, right=range(1, 6))),  # 20 marks, or 24 with the save
        save(client, c, battle_id, pick(6, found[6])),
    )
    assert sent.status_code == 200 and saved.status_code in (200, 409)  # before the submit, or refused after it
    await _once(app, battle_id, 12 + (saved.status_code == 200), 3)

    e, f = await pair(client, app, 635)  # submit + a read past the cutoff
    battle_id = await begin(client, app, e, f)
    found = await keys(app, battle_id)
    await save(client, e, battle_id, *answers(found, right=range(1, 6)))
    await save(client, f, battle_id, *answers(found, right=range(1, 6)))
    await at(app, battle_id, 1, "closes_at", -2.5)
    late, read = await asyncio.gather(
        submit(client, e, battle_id), client.get(f"{BASE}/battles/{battle_id}", headers=f["headers"])
    )
    assert late.status_code == read.status_code == 200 and late.json()["status"] == read.json()["status"] == "completed"
    await _once(app, battle_id, 10, 4)  # a draw: battle and draw for each

    g, h = await pair(client, app, 637)  # a block + a read past the cutoff: the result stands
    battle_id = await begin(client, app, g, h)
    found = await keys(app, battle_id)
    await save(client, g, battle_id, *answers(found, right=range(1, 6)))
    await at(app, battle_id, 1, "closes_at", -2.5)
    blocked, read = await asyncio.gather(
        block(client, h, g), client.get(f"{BASE}/battles/{battle_id}", headers=g["headers"])
    )
    assert blocked.status_code == 204 and read.status_code == 200
    assert (await state(client, g, battle_id))["result"] == "points"

    i, j = await pair(client, app, 639)  # an auto-submit + an explicit one from the same player
    battle_id = await begin(client, app, i, j)
    found = await keys(app, battle_id)
    await submit(client, j, battle_id)
    auto, explicit = await asyncio.gather(
        submit(client, i, battle_id, *answers(found, right=range(1, 6)), auto=True),
        submit(client, i, battle_id, *answers(found, right=range(1, 6))),
    )
    assert auto.status_code == explicit.status_code == 200
    players, row = await players_of(app, battle_id), await row_of(app, battle_id)
    assert row.status == "completed" and players["challenger"].submitted_at <= row.ends_at
    assert await graded(app, battle_id) == 5


async def test_the_answer_is_built_inside_the_transaction(client, app, bank, monkeypatch):
    a, b = await pair(client, app, 641)
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    await save(client, a, battle_id, *answers(found, right=range(1, 6)))
    await at(app, battle_id, 1, "closes_at", -2.5)
    real = battle_routes.battle_out

    async def paused(db, found_):
        await asyncio.sleep(0.3)  # a pause between advancing and the answer: the row stays locked meanwhile
        return await real(db, found_)

    monkeypatch.setattr(battle_routes, "battle_out", paused)
    read, sent = await asyncio.gather(
        client.get(f"{BASE}/battles/{battle_id}", headers=a["headers"]), submit(client, b, battle_id)
    )
    first, second = read.json(), sent.json()
    assert (first["status"], second["status"], first["result"], second["result"]) == (
        "completed",
        "completed",
        "points",
        "points",
    )
    assert first["me"]["winner"] is True and second["me"]["winner"] is False


# ------------------------------------------------------------------ overdue completion on every path
async def _overdue(client, app, base: int) -> tuple[dict, dict, str]:
    a, b = await pair(client, app, base)
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    await save(client, a, battle_id, pick(2, found[2]))
    await at(app, battle_id, 1, "closes_at", -2.5)
    return a, b, battle_id


async def _completed(app, battle_id: str) -> bool:
    return (await row_of(app, battle_id)).status == "completed"


async def test_an_overdue_exam_completes_on_every_path_and_stays_completed(client, app, bank):
    a, b, battle_id = await _overdue(client, app, 651)
    async with app.state.db.sessionmaker() as db:  # busy() is a pure predicate that ignores it
        assert not await battles.busy(db, uuid.UUID(a["user"]["id"]), uuid.uuid4())
    assert not await _completed(app, battle_id)
    assert (await client.get(f"{BASE}/me", headers=a["headers"])).status_code == 200
    assert await _completed(app, battle_id)

    c, d, battle_id = await _overdue(client, app, 653)
    assert (await client.get(f"{BASE}/battles", headers=d["headers"])).status_code == 200
    assert await _completed(app, battle_id)

    e, f, battle_id = await _overdue(client, app, 655)
    assert (await challenge(client, e, f)).status_code == 201  # not battle_exists: it is over
    assert await _completed(app, battle_id)

    g, h, battle_id = await _overdue(client, app, 657)
    newcomer = await member(client, app, "NH-FZ-0659", "Chen Li")
    await befriend(client, newcomer, h)
    invite = (await challenge(client, newcomer, h)).json()["battleId"]
    accepted = await client.post(f"{BASE}/battles/{invite}/accept", headers=h["headers"])
    assert accepted.json()["status"] == "waiting", accepted.text
    assert await _completed(app, battle_id)

    j, k = await pair(client, app, 661)
    battle_id = await begin(client, app, j, k)
    other = await member(client, app, "NH-FZ-0663", "Dev Rao")
    await befriend(client, j, other)
    invite = (await challenge(client, j, other)).json()["battleId"]
    await at(app, battle_id, 1, "closes_at", -2.5)
    async with app.state.db.sessionmaker() as db:  # accepted earlier (the accept would complete it too)
        await db.execute(
            text(
                "UPDATE fz_battles SET status = 'waiting', "
                "expires_at = clock_timestamp() + interval '3 minutes' WHERE id = :b"
            ),
            {"b": uuid.UUID(invite)},
        )
        await db.execute(
            text(
                "UPDATE fz_battle_players SET ready_at = clock_timestamp() WHERE battle_id = :b AND role = 'opponent'"
            ),
            {"b": uuid.UUID(invite)},
        )
        await db.commit()
    readied = await client.post(f"{BASE}/battles/{invite}/ready", headers=j["headers"])
    assert readied.json()["status"] == "active", readied.text
    assert await _completed(app, battle_id)


async def test_a_suspended_profile_s_me_does_not_complete_an_overdue_exam(client, app, bank):
    a, b, battle_id = await _overdue(client, app, 671)
    async with app.state.db.sessionmaker() as db:  # suspended with the exam still running (O5)
        await db.execute(
            text("UPDATE fz_profiles SET suspended_at = clock_timestamp() WHERE user_id = :u"),
            {"u": uuid.UUID(a["user"]["id"])},
        )
        await db.commit()
    me = await client.get(f"{BASE}/me", headers=a["headers"])
    assert me.status_code == 200 and me.json()["suspended"] is True
    assert not await _completed(app, battle_id)


# ------------------------------------------------------------------ review fixes
async def test_a_submit_that_applies_nothing_is_throttled_before_the_row_lock(client, app, bank):
    a, b = await pair(client, app, 691)
    battle_id = await begin(client, app, a, b)
    assert (await submit(client, a, battle_id)).status_code == 200
    limiter = app.state.friend_zone_poll_limiter
    app.state.friend_zone_poll_limiter = Refuse()
    try:
        async with app.state.db.sessionmaker() as holder:  # the row lock the opponent's saves would hold
            await holder.execute(text("SELECT 1 FROM fz_battles WHERE id = :b FOR UPDATE"), {"b": uuid.UUID(battle_id)})
            again = await asyncio.wait_for(submit(client, a, battle_id), timeout=5)
            assert again.status_code == 429  # refused without waiting for the lock
            await holder.rollback()
    finally:
        app.state.friend_zone_poll_limiter = limiter


async def test_an_exam_whose_only_present_player_submitted_completes_without_a_read(client, app, bank):
    a, b = await pair(client, app, 693)
    battle_id = await begin(client, app, a, b)
    found = await keys(app, battle_id)
    assert (await submit(client, a, battle_id, *answers(found, right=range(1, 6)))).json()["status"] == "active"
    await leave(app, b)  # the opponent goes after my submit: every present player has submitted
    async with app.state.db.sessionmaker() as db:
        assert not await battles.busy(db, uuid.UUID(a["user"]["id"]), uuid.uuid4())
    assert (await client.get(f"{BASE}/me", headers=a["headers"])).status_code == 200
    assert await _completed(app, battle_id)
