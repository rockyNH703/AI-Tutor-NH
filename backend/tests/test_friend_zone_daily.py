"""NH Friend Zone, daily challenges (owner-approved plan, revision 3: J8, sections 5, 6 and 13, G1-G8; CD16).

Time is moved by shifting stored timestamps against the database clock (a question's `served_at`, a challenge's
date), never by sleeping."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select, text

from app.db.models import (
    FzDailyAttempt,
    FzDailyChallenge,
    FzXpEvent,
    LearningDailyActivity,
    Program,
    QbQuestion,
    QbSet,
    UserNotification,
)
from app.friendzone import daily, friends, scoring
from app.friendzone.eligibility import WORDS, competitive
from tests.conftest import make_settings
from tests.test_friend_zone_battles import bank  # noqa: F401  (the fixture)
from tests.test_friend_zone_relations import befriend, block, member
from tests.test_programs import PROGRAM, enrolled

BASE = "/v1/friend-zone"


@pytest.fixture
def settings():
    return make_settings(programs_mode="on", friend_zone_mode="on")


@pytest.fixture(autouse=True)
def no_floor(monkeypatch):
    monkeypatch.setattr(friends, "SEARCH_FLOOR_SECONDS", 0.0)
    # The in-process "no challenge today" memory (daily._empty) must not leak between tests: each test seeds its own
    # bank, so a program empty in one test may have a challenge in the next.
    monkeypatch.setattr(daily, "_empty", {})


# ------------------------------------------------------------------ helpers
async def today(client, who: dict, program: str = PROGRAM):
    return await client.get(f"{BASE}/daily", params={"program": program}, headers=who["headers"])


async def nxt(client, who: dict, challenge_id: str):
    return await client.post(f"{BASE}/daily/{challenge_id}/next", headers=who["headers"])


async def answer(client, who: dict, challenge_id: str, position: int, choice: str):
    body = {"position": position, "choice": choice}
    return await client.post(f"{BASE}/daily/{challenge_id}/answers", json=body, headers=who["headers"])


async def challenge_id(client, who: dict) -> str:
    seen = await today(client, who)
    assert seen.status_code == 200, seen.text
    assert seen.json()["available"], seen.text
    return seen.json()["challengeId"]


async def key_at(app, challenge: str, position: int) -> str:
    async with app.state.db.sessionmaker() as db:
        question_id = await db.scalar(
            select(FzDailyChallenge.question_ids[position]).where(FzDailyChallenge.id == uuid.UUID(challenge))
        )
        found = await db.get(QbQuestion, question_id)
        return found.correct_keys[0] if found.correct_keys is not None else found.correct_option


def wrong(key: str) -> str:
    return "A" if key != "A" else "B"


async def served_ago(app, who: dict, challenge: str, position: int, seconds: float) -> None:
    """Move a served question's `served_at` `seconds` into the past. The ledger's trigger keeps a served question
    unchangeable, so it is set aside for this one statement (the test database only)."""
    async with app.state.db.sessionmaker() as db:
        set_id = await db.scalar(
            select(FzDailyAttempt.set_id).where(
                FzDailyAttempt.challenge_id == uuid.UUID(challenge),
                FzDailyAttempt.user_id == uuid.UUID(who["user"]["id"]),
            )
        )
        await db.execute(text("ALTER TABLE qb_set_items DISABLE TRIGGER qb_set_items_answer"))
        await db.execute(
            text(
                "UPDATE qb_set_items SET served_at = clock_timestamp() - make_interval(secs => :s) "
                "WHERE set_id = :x AND position = :p"
            ),
            {"s": seconds, "x": set_id, "p": position},
        )
        await db.execute(text("ALTER TABLE qb_set_items ENABLE TRIGGER qb_set_items_answer"))
        await db.commit()


async def shift_day(app, challenge: str, days: int) -> None:
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text("UPDATE fz_daily_challenges SET challenge_date = challenge_date - CAST(:d AS integer) WHERE id = :c"),
            {"d": days, "c": uuid.UUID(challenge)},
        )
        await db.commit()


async def play(client, app, who: dict, challenge: str, *, correct: int = 10) -> dict:
    """Serve and answer all ten: the first `correct` right, the rest wrong. Returns the last response body."""
    body = {}
    for position in range(1, 11):
        served = await nxt(client, who, challenge)
        assert served.json()["question"]["position"] == position, served.text
        key = await key_at(app, challenge, position)
        response = await answer(client, who, challenge, position, key if position <= correct else wrong(key))
        assert response.status_code == 200, response.text
        body = response.json()
    return body


async def xp(app, who: dict) -> dict[str, int]:
    async with app.state.db.sessionmaker() as db:
        rows = await db.execute(
            select(FzXpEvent.reason, FzXpEvent.amount).where(FzXpEvent.user_id == uuid.UUID(who["user"]["id"]))
        )
        return dict(rows.all())


async def kinds(app, who: dict) -> list[str]:
    async with app.state.db.sessionmaker() as db:
        rows = await db.scalars(
            select(UserNotification.kind).where(
                UserNotification.user_id == uuid.UUID(who["user"]["id"]), UserNotification.kind.startswith("daily_")
            )
        )
        return sorted(rows)


async def learning_days(app, who: dict) -> int:
    async with app.state.db.sessionmaker() as db:
        return await db.scalar(
            select(func.count())
            .select_from(LearningDailyActivity)
            .where(LearningDailyActivity.user_id == uuid.UUID(who["user"]["id"]))
        )


# ------------------------------------------------------------------ the challenge
def test_the_day_closes_thirty_minutes_after_india_midnight():
    assert daily.starts_at(date(2026, 11, 24)) == datetime(2026, 11, 23, 18, 30, tzinfo=UTC)
    assert daily.closes_at(date(2026, 11, 24)) == datetime(2026, 11, 24, 19, 0, tzinfo=UTC)


async def test_the_first_reads_make_one_challenge_of_ten_fixed_questions(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0301", "Asha Menon")
    b = await member(client, app, "NH-FZ-0302", "Bilal Khan")
    first, second = await asyncio.gather(today(client, a), today(client, b))
    assert first.status_code == second.status_code == 200, first.text + second.text
    assert first.json()["challengeId"] == second.json()["challengeId"]
    seen = first.json()
    assert seen["available"] and seen["attempt"]["status"] == "not_started" and seen["question"] is None
    assert seen["review"] is None and seen["friends"] is None
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(FzDailyChallenge)) == 1
        made = await db.get(FzDailyChallenge, uuid.UUID(seen["challengeId"]))
        program_id = await db.scalar(select(Program.id).where(Program.slug == PROGRAM))
        eligible = dict(
            (await db.execute(select(QbQuestion.question_id, WORDS).where(competitive("human_only")))).all()
        )
    day = seen["date"]
    assert day == made.challenge_date.isoformat()
    expected = sorted(
        eligible, key=lambda q: hashlib.md5(f"{q}{day}{program_id}".encode(), usedforsecurity=False).hexdigest()
    )[:10]
    assert made.question_ids == expected  # the deterministic ten, from checked questions only
    assert made.seconds == [scoring.round_seconds(eligible[q]) for q in expected]


async def test_fewer_than_ten_checked_questions_means_no_challenge_and_no_notice(client, app, bank):  # noqa: F811
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text(
                "UPDATE qb_questions SET review_status = 'needs_review' WHERE review_status = 'approved' "
                "AND question_id NOT IN (SELECT question_id FROM qb_questions WHERE review_status = 'approved' "
                "ORDER BY question_id LIMIT 9)"
            )
        )
        await db.commit()
    a = await member(client, app, "NH-FZ-0311")
    seen = (await today(client, a)).json()
    assert (seen["available"], seen["challengeId"], seen["attempt"], seen["question"]) == (False, None, None, None)
    assert (await client.get(f"{BASE}/me", headers=a["headers"])).status_code == 200
    assert await kinds(app, a) == []
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(FzDailyChallenge)) == 0  # never unchecked ones


async def test_the_availability_notice_comes_once_a_day(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0321")
    for _ in range(3):
        assert (await client.get(f"{BASE}/me", headers=a["headers"])).status_code == 200
    assert await kinds(app, a) == ["daily_available"]
    async with app.state.db.sessionmaker() as db:
        link = await db.scalar(select(UserNotification.link).where(UserNotification.kind == "daily_available"))
        assert link == f"/programs/{PROGRAM}/quizzes?zone=friends"
        assert await db.scalar(select(func.count()).select_from(FzDailyChallenge)) == 1


# ------------------------------------------------------------------ playing
async def test_a_perfect_daily_from_start_to_review(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0331")
    challenge = await challenge_id(client, a)
    started = await asyncio.gather(nxt(client, a, challenge), nxt(client, a, challenge))  # start is idempotent
    assert [r.json()["question"]["position"] for r in started] == [1, 1], started[0].text
    resumed = (await nxt(client, a, challenge)).json()  # G1: the open, unanswered question again
    question = resumed["question"]
    assert question["position"] == 1 and len(question["options"]) == 4 and question["myChoice"] is None
    assert "correct" not in str(question).lower() and "explanation" not in str(resumed).lower()  # secrecy
    assert resumed["attempt"]["correct"] is None and resumed["attempt"]["score"] is None
    assert resumed["review"] is None and resumed["friends"] is None

    key = await key_at(app, challenge, 1)
    assert (await answer(client, a, challenge, 2, key)).json()["error"]["code"] == "round_closed"  # not served yet
    assert (await answer(client, a, challenge, 1, "H")).json()["error"]["code"] == "invalid_choice"
    assert (await answer(client, a, challenge, 1, "Z")).status_code == 422
    assert (await answer(client, a, challenge, 1, key)).json()["question"]["myChoice"] == key
    assert (await answer(client, a, challenge, 1, key)).status_code == 200  # the same again: the same
    assert (await answer(client, a, challenge, 1, wrong(key))).json()["error"]["code"] == "already_answered"
    after = (await today(client, a)).json()
    assert after["attempt"]["answered"] == 1 and after["attempt"]["correct"] is None  # no running count

    for position in range(2, 11):
        assert (await nxt(client, a, challenge)).json()["question"]["position"] == position
        assert (await answer(client, a, challenge, position, await key_at(app, challenge, position))).status_code == 200
    done = (await today(client, a)).json()
    attempt = done["attempt"]
    assert (attempt["status"], attempt["served"], attempt["answered"], attempt["correct"]) == ("finished", 10, 10, 10)
    assert 1000 <= attempt["score"] <= 1500 and done["question"] is None
    assert len(done["review"]) == 10 and all(item["explanation"] and item["correctKey"] for item in done["review"])
    assert all(item["myChoice"] == item["correctKey"] and not item["voided"] for item in done["review"])
    assert sum(item["points"] for item in done["review"]) == attempt["score"]
    assert done["friends"] == []
    # finished once: a replayed last answer, more reads and another next change nothing
    assert (await answer(client, a, challenge, 10, await key_at(app, challenge, 10))).status_code == 200
    assert (await nxt(client, a, challenge)).json()["attempt"]["status"] == "finished"
    assert await xp(app, a) == {"daily": 25, "perfect": 15}
    assert await kinds(app, a) == ["daily_completed"]
    assert await learning_days(app, a) == 1  # ten live answers count as practice
    async with app.state.db.sessionmaker() as db:
        made = (await db.execute(select(QbSet).where(QbSet.request_id == uuid.UUID(challenge)))).scalars().all()
        assert [(s.mode, s.requested, s.served, s.closed_at is not None) for s in made] == [("daily", 10, 10, True)]


async def test_the_window_has_a_two_second_allowance_and_a_missed_question_stays_missed(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0341")
    challenge = await challenge_id(client, a)
    seconds = (await nxt(client, a, challenge)).json()["question"]["seconds"]
    key = await key_at(app, challenge, 1)
    await served_ago(app, a, challenge, 1, seconds + 1)  # past the time shown, inside the allowance
    assert (await answer(client, a, challenge, 1, key)).status_code == 200

    seconds = (await nxt(client, a, challenge)).json()["question"]["seconds"]
    await served_ago(app, a, challenge, 2, seconds + 2.5)  # past the allowance
    assert (await answer(client, a, challenge, 2, await key_at(app, challenge, 2))).json()["error"]["code"] == (
        "round_closed"
    )
    assert (await today(client, a)).json()["question"] is None
    for position in range(3, 11):  # every later question left to time out
        seconds = (await nxt(client, a, challenge)).json()["question"]["seconds"]
        await served_ago(app, a, challenge, position, seconds + 3)
    done = (await today(client, a)).json()  # the tenth window has passed: the read finishes it
    attempt = done["attempt"]
    assert (attempt["status"], attempt["served"], attempt["answered"], attempt["correct"]) == ("finished", 10, 1, 1)
    assert attempt["score"] == scoring.BASE_POINTS  # correct, no time left
    assert [item["myChoice"] for item in done["review"]][:2] == [key, None]
    assert await xp(app, a) == {}  # one of ten answered is not a completed daily: no XP, streak or badge


async def test_the_grace_closes_an_attempt_and_a_past_day_cannot_be_started(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0351")
    b = await member(client, app, "NH-FZ-0352")
    challenge = await challenge_id(client, a)
    await nxt(client, a, challenge)
    await answer(client, a, challenge, 1, await key_at(app, challenge, 1))
    await nxt(client, a, challenge)
    await shift_day(app, challenge, 2)  # its day, and the 30-minute grace after it, are over

    assert (await nxt(client, b, challenge)).json()["error"]["code"] == "challenge_closed"
    fresh = (await today(client, a)).json()  # the next read closes the old attempt, and shows today's
    assert fresh["challengeId"] != challenge and fresh["attempt"]["status"] == "not_started"
    async with app.state.db.sessionmaker() as db:
        old = await db.get(FzDailyAttempt, (uuid.UUID(challenge), uuid.UUID(a["user"]["id"])))
        assert old.finished_at is not None and (old.served, old.answered) == (2, 1)
        assert await db.scalar(select(func.count()).select_from(FzDailyAttempt)) == 1  # b never started
    assert (await answer(client, a, challenge, 2, "A")).json()["error"]["code"] == "round_closed"
    assert await xp(app, a) == {} and await kinds(app, a) == []  # closed early: nothing awarded


# ------------------------------------------------------------------ friends (G8)
async def test_friends_results_show_after_finishing_and_respect_show_stats(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0361", "Asha Menon")
    open_book = await member(client, app, "NH-FZ-0362", "Bilal Khan")
    private = await member(client, app, "NH-FZ-0363", "Chen Li")
    stranger = await member(client, app, "NH-FZ-0364", "Dev Rao")
    blocked = await member(client, app, "NH-FZ-0365", "Esha Pal")
    hidden = await member(client, app, "NH-FZ-0366", "Farah Iqbal")  # completes, without showing stats
    for friend in (open_book, private, blocked, hidden):
        await befriend(client, a, friend)
    assert (await block(client, a, blocked)).status_code == 204
    async with app.state.db.sessionmaker() as db:
        for shows in (open_book, private):  # Chen shows stats too, but is still playing
            await db.execute(text("UPDATE fz_profiles SET show_stats = true WHERE id = :p"), {"p": shows["player"]})
        await db.commit()
    challenge = await challenge_id(client, a)
    await play(client, app, open_book, challenge, correct=7)
    await play(client, app, hidden, challenge, correct=5)
    for who in (private, stranger, blocked):
        await nxt(client, who, challenge)

    await nxt(client, a, challenge)
    assert (await today(client, a)).json()["friends"] is None  # hidden until I finish
    await answer(client, a, challenge, 1, await key_at(app, challenge, 1))
    finished = await play_rest(client, app, a, challenge)
    shown = {friend["displayName"]: friend for friend in finished["friends"]}
    assert set(shown) == {"Bilal Khan", "Chen Li", "Farah Iqbal"}  # no stranger, no blocked member: no wider board
    assert (shown["Bilal Khan"]["status"], shown["Bilal Khan"]["correct"]) == ("completed", 7)
    assert shown["Bilal Khan"]["score"] >= 700 and shown["Bilal Khan"]["initials"] == "BK"
    for name, status in (("Chen Li", "in_progress"), ("Farah Iqbal", "completed")):  # no running count; no stats
        assert (shown[name]["status"], shown[name]["score"], shown[name]["correct"]) == (status, None, None)
    assert "NH-FZ" not in str(finished) and private["email"] not in str(finished)


async def play_rest(client, app, who: dict, challenge: str) -> dict:
    """Answer the open question's successors (2-10) correctly; the last response."""
    body = {}
    for position in range(2, 11):
        await nxt(client, who, challenge)
        body = (await answer(client, who, challenge, position, await key_at(app, challenge, position))).json()
    return body


# ------------------------------------------------------------------ isolation and access
async def test_a_qa_daily_earns_no_xp_and_no_learning_day(client, app, bank):  # noqa: F811
    account = await enrolled(client, app, "NH-FZ-0371")
    app.state.settings = make_settings(
        programs_mode="on", friend_zone_mode="on", friend_zone_qa_emails=account["email"]
    )
    joined = await client.post(f"{BASE}/me", json={"displayName": "QA one"}, headers=account["headers"])
    assert joined.status_code == 201 and joined.json()["qa"]
    challenge = await challenge_id(client, account)
    done = await play(client, app, account, challenge)
    assert done["attempt"]["status"] == "finished" and done["attempt"]["correct"] == 10
    assert await xp(app, account) == {} and await learning_days(app, account) == 0
    async with app.state.db.sessionmaker() as db:
        modes = set(await db.scalars(select(QbSet.mode).where(QbSet.request_id == uuid.UUID(challenge))))
        assert modes == {"fz_qa"}
        cohort = await db.scalar(
            select(FzDailyAttempt.cohort).where(FzDailyAttempt.challenge_id == uuid.UUID(challenge))
        )
        assert cohort == "qa"


async def test_only_members_with_the_program_open_see_a_challenge(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0381")
    challenge = await challenge_id(client, a)
    outsider = await enrolled(client, app, "NH-FZ-0382")  # enrolled, never joined
    other_program = await enrolled(client, app, "NH-FZ-0383", "cyber-cloud-security-ai")
    joined = await client.post(f"{BASE}/me", json={"displayName": "Other"}, headers=other_program["headers"])
    assert joined.status_code == 201, joined.text  # a member, of another program only
    for who in (outsider, other_program):
        assert (await today(client, who)).status_code == 404
        assert (await nxt(client, who, challenge)).status_code == 404
        assert (await answer(client, who, challenge, 1, "A")).status_code == 404
    assert (await today(client, a, "no-such-program")).status_code == 404
    assert (await nxt(client, a, str(uuid.uuid4()))).status_code == 404
    assert (await today(client, a)).headers["Cache-Control"] == "no-store"


async def test_the_end_to_end_seconds_override_sets_every_window(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0391")
    challenge = await challenge_id(client, a)  # made without the override
    app.state.settings = make_settings(programs_mode="on", friend_zone_mode="on", friend_zone_round_seconds=7)
    assert (await nxt(client, a, challenge)).json()["question"]["seconds"] == 7
    await served_ago(app, a, challenge, 1, 9.5)  # past 7 s and the allowance, well inside any stored window
    assert (await answer(client, a, challenge, 1, "A")).json()["error"]["code"] == "round_closed"
    await nxt(client, a, challenge)
    await served_ago(app, a, challenge, 2, 8.5)  # inside the allowance
    assert (await answer(client, a, challenge, 2, "A")).status_code == 200
    async with app.state.db.sessionmaker() as db:
        stored = await db.get(FzDailyChallenge, uuid.UUID(challenge))
        assert all(seconds >= scoring.MIN_SECONDS for seconds in stored.seconds)
        made = await daily.challenge_for(db, stored.program_id, date(2030, 1, 1), round_seconds=7)
        assert made.seconds == [7] * 10  # a challenge made with the override stores it
        await db.rollback()


async def unapprove(app, challenge: str, position: int) -> None:
    """Send the challenge's question at `position` back for review: no longer competitive (what an edit does)."""
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text(
                "UPDATE qb_questions SET review_status = 'needs_review' WHERE question_id = "
                "(SELECT question_ids[:p] FROM fz_daily_challenges WHERE id = :c)"
            ),
            {"p": position, "c": uuid.UUID(challenge)},
        )
        await db.commit()


async def test_a_void_is_recorded_when_it_happens_and_never_rejudged(client, app, bank):  # noqa: F811
    a = await member(client, app, "NH-FZ-0395")
    challenge = await challenge_id(client, a)
    await nxt(client, a, challenge)
    assert (await answer(client, a, challenge, 1, await key_at(app, challenge, 1))).status_code == 200
    await unapprove(app, challenge, 1)  # changed after it was answered and scored: the score stands
    await nxt(client, a, challenge)
    await unapprove(app, challenge, 2)  # changed between serve and answer: the answer is kept, scores nothing
    assert (await answer(client, a, challenge, 2, await key_at(app, challenge, 2))).status_code == 200
    await unapprove(app, challenge, 3)  # withdrawn before it is served: skipped
    assert (await nxt(client, a, challenge)).json()["question"]["position"] == 4
    for position in range(4, 11):
        await answer(client, a, challenge, position, await key_at(app, challenge, position))
        if position < 10:
            await nxt(client, a, challenge)
    done = (await today(client, a)).json()
    review = {item["position"]: item for item in done["review"]}
    assert (review[1]["voided"], review[1]["correct"]) == (False, True) and review[1]["points"] >= 100
    assert (review[2]["voided"], review[2]["correct"], review[2]["points"]) == (True, True, 0)
    assert (review[3]["voided"], review[3]["myChoice"], review[3]["points"]) == (True, None, 0)
    attempt = done["attempt"]
    assert (attempt["served"], attempt["answered"], attempt["correct"]) == (10, 8, 8)
    assert attempt["score"] == sum(item["points"] for item in done["review"])
    assert await xp(app, a) == {"daily": 25}  # all ten served; a void is never right
    async with app.state.db.sessionmaker() as db:
        stored = await db.get(FzDailyAttempt, (uuid.UUID(challenge), uuid.UUID(a["user"]["id"])))
        assert stored.voided == [2, 3]


def test_a_daily_counts_only_with_half_its_counted_questions_answered():
    """Phase 2 security review: a run that answers nothing (or only withdrawn questions) earns nothing."""
    from app.db.models import FzDailyAttempt  # noqa: PLC0415
    from app.friendzone.progress import completed  # noqa: PLC0415

    def run(served, answered, voided=()):
        return completed(FzDailyAttempt(served=served, answered=answered, voided=list(voided)))

    assert not run(10, 0) and not run(10, 4) and run(10, 5) and run(10, 10)
    assert not run(9, 9)  # closed before all ten were served
    assert run(10, 3, voided=[1, 2, 3, 4, 5, 6]) and not run(10, 0, voided=range(1, 11))
