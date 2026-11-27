"""Practice exams (app/exams; session 62 plan, Task 1C; netsec-handoffs/exams-contract.md): a program month's
certification questions as one timed paper, outside the chat. The options count only what can be served; a start is
idempotent; the paper never carries keys while it runs; saves are compare-and-set; the database clock decides the
deadline (cutoff = endsAt + 2 s) and a read after it submits what was saved; a submit grades every answer into the
question-bank ledger exactly once, with one practice record each.

Time is moved by shifting the stored deadline (`shift`), never by sleeping."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from datetime import datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import select, text

from app.db.models import ProgramModuleBook, QbQuestion, QbSet, QbSetItem
from app.exams import service
from app.qbank.importer import import_bank
from tests.conftest import ROOT, TEST_DATABASE_URL, _asyncpg_dsn, make_settings, register
from tests.test_friend_zone_battles import load_bank
from tests.test_programs import PROGRAM, TITLE, change, enrolled, module_id
from tests.test_qbank_certification import netsec_bank

EXAMS = "/v1/practice-exams"
OPTIONS = f"/v1/programs/{PROGRAM}/months/1/exam-options"
QUESTION_FIELDS = {"index", "stem", "options", "multi", "quiz", "topic"}


@pytest.fixture
def settings():
    return make_settings(programs_mode="on")


@pytest.fixture
async def student(client, app, tmp_path) -> dict:
    """An enrolled student of PROGRAM whose month 1 maps the synthetic CCNA track (4 quizzes x 9 questions)."""
    await load_bank(app, tmp_path)
    return await enrolled(client, app, "NH-EX-0001")


def start_body(**extra) -> dict:
    body = {
        "requestId": str(uuid.uuid4()),
        "program": PROGRAM,
        "month": 1,
        "topicIds": ["CCNA-D2-T01"],
        "difficulty": "mixed",
        "length": 5,
        "minutes": 5,
    }
    return {**body, **extra}


async def begin(client, who: dict, **extra) -> str:
    created = await client.post(EXAMS, json=start_body(**extra), headers=who["headers"])
    assert created.status_code == 201, created.text
    return created.json()["examId"]


async def read(client, who: dict, exam_id: str) -> dict:
    found = await client.get(f"{EXAMS}/{exam_id}", headers=who["headers"])
    assert found.status_code == 200, found.text
    return found.json()


async def save(client, who: dict, exam_id: str, base_rev: int, *items: dict):
    body = {"baseRev": base_rev, "items": list(items)}
    return await client.put(f"{EXAMS}/{exam_id}/responses", json=body, headers=who["headers"])


async def submit(client, who: dict, exam_id: str, *items: dict, base_rev: int | None = None, auto: bool = False):
    body = {"baseRev": base_rev, "items": list(items), "auto": auto}
    return await client.post(f"{EXAMS}/{exam_id}/submit", json=body, headers=who["headers"])


def item(index: int, keys: list[str], marked: bool = False) -> dict:
    return {"index": index, "keys": keys, "marked": marked, "visited": True}


async def shift(app, exam_id: str, seconds: float) -> None:
    """Put the exam's deadline `seconds` from the database's now (negative: already past)."""
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text(
                "UPDATE practice_exams SET ends_at = clock_timestamp() + make_interval(secs => :s), "
                "started_at = least(started_at, clock_timestamp() + make_interval(secs => :s)) WHERE id = :id"
            ),
            {"s": seconds, "id": uuid.UUID(exam_id)},
        )
        await db.commit()


async def keys_of(app, exam_id: str) -> list[list[str]]:
    """The paper's correct letters, in order (read from the bank, never from the API)."""
    async with app.state.db.sessionmaker() as db:
        rows = await db.execute(
            text(
                "SELECT coalesce(q.correct_keys, ARRAY[q.correct_option::text]) AS k FROM practice_exams e "
                "CROSS JOIN LATERAL unnest(e.paper) WITH ORDINALITY AS p(question_id, n) "
                "JOIN qb_questions q ON q.question_id = p.question_id WHERE e.id = :id ORDER BY p.n"
            ),
            {"id": uuid.UUID(exam_id)},
        )
        return [sorted(row.k) for row in rows]


def wrong(keys: list[str]) -> list[str]:
    return ["B"] if keys != ["B"] else ["A"]


async def ledger(app, exam_id: str) -> list[QbSetItem]:
    async with app.state.db.sessionmaker() as db:
        set_id = await db.scalar(text("SELECT set_id FROM practice_exams WHERE id = :id"), {"id": uuid.UUID(exam_id)})
        return list(await db.scalars(select(QbSetItem).where(QbSetItem.set_id == set_id).order_by(QbSetItem.position)))


@pytest.fixture
def practice_calls(monkeypatch) -> list[uuid.UUID]:
    """Every record_practice call the exam makes (it still runs: the streak logic is unchanged)."""
    calls: list[uuid.UUID] = []
    real = service.record_practice

    async def counted(session, user_id, now, answered_at):
        calls.append(user_id)
        return await real(session, user_id, now, answered_at)

    monkeypatch.setattr(service, "record_practice", counted)
    return calls


# ---------------------------------------------------------------- the options
async def test_the_options_count_exactly_the_servable_questions_per_quiz_and_difficulty(client, app, student):
    async with app.state.db.sessionmaker() as db:  # one question inactive, one draft: neither may be offered
        await db.execute(text("UPDATE qb_questions SET is_active = false WHERE question_id = 'CCNA-D2-T01-001'"))
        await db.execute(text("UPDATE qb_questions SET review_status = 'draft' WHERE question_id = 'CCNA-D2-T01-002'"))
        await db.commit()
        rows = await db.execute(
            text(
                "SELECT topic_id, difficulty, count(*) AS n FROM qb_questions WHERE book_id = 'ccna' AND is_active "
                "AND review_status IN ('approved', 'needs_review') GROUP BY topic_id, difficulty"
            )
        )
        expected: dict[str, dict[str, int]] = {}
        for row in rows:
            expected.setdefault(row.topic_id, {"easy": 0, "medium": 0, "hard": 0})[row.difficulty] = row.n

    found = await client.get(OPTIONS, headers=student["headers"])
    assert found.status_code == 200, found.text
    options = found.json()
    month = (await client.get(f"/v1/programs/{PROGRAM}/months/1", headers=student["headers"])).json()
    assert [quiz["topicId"] for quiz in options["quizzes"]] == [topic["topicId"] for topic in month["topics"]]
    for quiz in options["quizzes"]:
        counts = expected[quiz["topicId"]]
        assert quiz["available"] == {**counts, "total": sum(counts.values())}, quiz["topicId"]
        assert quiz["book"] == "CCNA" and quiz["chapter"].startswith("Domain ") and quiz["title"]
    assert options["program"] == {"slug": PROGRAM, "title": TITLE} and options["month"] == 1
    assert options["lengths"] == [5, 10, 15, 20, 25, 30] and options["maxQuestions"] == 30
    assert options["durations"] == {"secondsPerQuestion": 60, "multipliers": [1, 1.5, 2], "maxMinutes": 60}
    assert options["rules"] == {"marksPerQuestion": 1, "negativeMarking": 0}
    outsider = await register(client, app)
    assert (await client.get(OPTIONS, headers=outsider["headers"])).status_code == 404


# ---------------------------------------------------------------- starting
async def test_a_start_is_idempotent_on_its_request_id(client, app, student):
    body = start_body()
    first = await client.post(EXAMS, json=body, headers=student["headers"])
    again = await client.post(EXAMS, json=body, headers=student["headers"])
    assert first.status_code == 201 and again.status_code == 200, (first.text, again.text)
    assert first.json() == again.json() and set(first.json()) == {"examId"}
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(text("SELECT count(*) FROM practice_exams")) == 1
        sets = (await db.execute(select(QbSet.mode, QbSet.requested, QbSet.served, QbSet.closed_at))).all()
        assert [(s.mode, s.requested, s.served, s.closed_at) for s in sets] == [("test", 5, 5, None)]
    assert len(await ledger(app, first.json()["examId"])) == 5
    # Concurrent starts with one request id: one exam.
    body = start_body()
    results = await asyncio.gather(*(client.post(EXAMS, json=body, headers=student["headers"]) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [200, 201]
    assert results[0].json() == results[1].json()


async def test_too_few_questions_is_a_409_with_the_real_count_and_writes_nothing(client, app, student):
    # CCNA-D2-T01 has 3 hard questions
    short = await client.post(EXAMS, json=start_body(difficulty="hard", length=5), headers=student["headers"])
    assert short.status_code == 409, short.text
    error = short.json()["error"]
    assert error["code"] == "not_enough_questions" and error["current"] == {"available": 3, "needed": 5}
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(text("SELECT count(*) FROM practice_exams")) == 0
        assert await db.scalar(text("SELECT count(*) FROM qb_sets")) == 0


async def second_track(app, tmp_path) -> None:
    """A second track (CCNP: the synthetic bank renamed) mapped to the same month."""
    root = netsec_bank(tmp_path / "second")
    for path in root.rglob("*"):
        if path.is_file():
            body = path.read_text(encoding="utf-8").replace("CCNA", "CCNP").replace("ccna", "ccnp")
            path.write_text(body, encoding="utf-8")
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if "ccna" in path.name.lower():
            path.rename(path.with_name(path.name.replace("ccna", "ccnp").replace("CCNA", "CCNP")))
    async with app.state.db.sessionmaker() as db:
        report = await import_bank(db, root, apply=True)
        assert report.applied, report.errors
        db.add(ProgramModuleBook(module_id=await module_id(app, PROGRAM, 1), book_id="ccnp"))
        await db.commit()


async def test_an_exam_takes_quizzes_of_one_track_of_the_month_only(client, app, student, tmp_path):
    await second_track(app, tmp_path)
    headers = student["headers"]
    both = await client.post(EXAMS, json=start_body(topicIds=["CCNA-D2-T01", "CCNP-D2-T01"]), headers=headers)
    assert both.status_code == 422 and both.json()["error"]["code"] == "one_track", both.text
    unknown = await client.post(EXAMS, json=start_body(topicIds=["CCNA-D2-T01", "NOPE-T01"]), headers=headers)
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unknown_quiz"
    async with app.state.db.sessionmaker() as db:  # a track that is not this month's
        await db.execute(text("DELETE FROM program_module_books WHERE book_id = 'ccnp'"))
        await db.commit()
    other = await client.post(EXAMS, json=start_body(topicIds=["CCNP-D2-T01"]), headers=headers)
    assert other.status_code == 422 and other.json()["error"]["code"] == "unknown_quiz"
    for wrong_body in (start_body(userId="x"), start_body(length=31), start_body(minutes=0), start_body(topicIds=[])):
        refused = await client.post(EXAMS, json=wrong_body, headers=headers)
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "validation_error"
    month_two = await client.post(EXAMS, json=start_body(month=2), headers=headers)  # nothing of ccna in month 2
    assert month_two.status_code == 422 and month_two.json()["error"]["code"] == "unknown_quiz"
    outsider = await register(client, app)
    assert (await client.post(EXAMS, json=start_body(), headers=outsider["headers"])).status_code == 404


# ---------------------------------------------------------------- the paper while it runs
async def test_the_running_paper_has_no_keys_explanations_or_difficulty(client, app, student):
    exam_id = await begin(client, student, topicIds=["CCNA-D2-T01", "CCNA-D2-T02"], length=10, minutes=10)
    found = await client.get(f"{EXAMS}/{exam_id}", headers=student["headers"])
    paper = found.json()
    assert paper["status"] == "in_progress" and paper["result"] is None and paper["rev"] == 0
    assert paper["difficulty"] == "mixed" and paper["length"] == 10 and paper["minutes"] == 10
    assert paper["program"] == {"slug": PROGRAM, "title": TITLE} and paper["month"] == 1
    assert paper["title"] == "Domain 2"  # both quizzes are in one topic (chapter)
    assert paper["rules"] == {"marksPerQuestion": 1, "negativeMarking": 0}
    started, ends, cutoff = (datetime.fromisoformat(paper[k]) for k in ("startedAt", "endsAt", "cutoffAt"))
    assert ends - started == timedelta(minutes=10) and cutoff - ends == timedelta(seconds=2)
    assert datetime.fromisoformat(paper["serverTime"]) < ends
    assert [q["index"] for q in paper["questions"]] == list(range(10)) and paper["responses"] == []
    for question in paper["questions"]:
        assert set(question) == QUESTION_FIELDS
        assert [option["key"] for option in question["options"]] == list("ABCDE"[: len(question["options"])])
        assert question["topic"] == "Domain 2" and question["quiz"]
    async with app.state.db.sessionmaker() as db:
        explanations = list(await db.scalars(select(QbQuestion.explanation).where(QbQuestion.book_id == "ccna")))
    body = found.text
    assert not any(explanation in body for explanation in explanations)
    for word in ("correct", "explanation", 'difficulty":"easy', 'difficulty":"medium', 'difficulty":"hard'):
        assert word not in body.lower().replace('"difficulty":"mixed"', ""), word
    assert found.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------- saving
async def test_saves_are_compare_and_set_and_checked_against_the_question(client, app, student):
    exam_id = await begin(client, student)
    paper = await read(client, student, exam_id)
    single = next(q["index"] for q in paper["questions"] if not q["multi"] and len(q["options"]) == 4)
    saved = await save(client, student, exam_id, 0, item(single, ["B"], marked=True), item(4, []))
    assert saved.status_code == 200, saved.text
    assert saved.json()["rev"] == 1 and datetime.fromisoformat(saved.json()["serverTime"])
    stale = await save(client, student, exam_id, 0, item(single, ["C"]))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale"
    assert stale.json()["error"]["current"] == {"rev": 1}
    for bad in (item(single, ["E"]), item(single, ["A", "B"]), item(5, ["A"]), item(single, ["A", "A"])):
        refused = await save(client, student, exam_id, 1, bad)
        assert refused.status_code == 422, (bad, refused.text)
    responses = (await read(client, student, exam_id))["responses"]  # ordered by index
    assert responses == sorted([item(single, ["B"], marked=True), item(4, [])], key=lambda r: r["index"])
    cleared = await save(client, student, exam_id, 1, item(single, []))
    assert cleared.json()["rev"] == 2
    assert next(r for r in (await read(client, student, exam_id))["responses"] if r["index"] == single)["keys"] == []


async def test_a_save_after_the_cutoff_is_409_closed_and_submits_what_was_saved(client, app, student):
    exam_id = await begin(client, student)
    keys = await keys_of(app, exam_id)
    assert (await save(client, student, exam_id, 0, item(0, keys[0]))).status_code == 200
    await shift(app, exam_id, -2.5)  # past the deadline and its 2 s allowance
    late = await save(client, student, exam_id, 1, item(1, keys[1]))
    assert late.status_code == 409 and late.json()["error"]["code"] == "closed", late.text
    done = await read(client, student, exam_id)
    assert done["status"] == "submitted" and done["result"]["autoSubmitted"] is True
    assert done["result"]["submittedAt"] == done["endsAt"]
    assert done["result"]["correct"] == 1 and done["result"]["unanswered"] == 4  # the late answer never counted
    again = await save(client, student, exam_id, done["rev"], item(2, keys[2]))
    assert again.status_code == 409 and again.json()["error"]["code"] == "closed"


async def test_within_the_allowance_a_save_is_still_taken(client, app, student):
    exam_id = await begin(client, student)
    await shift(app, exam_id, -0.5)  # past endsAt, before the cutoff
    assert (await save(client, student, exam_id, 0, item(0, ["A"]))).status_code == 200


async def test_a_read_after_the_cutoff_submits_the_saved_answers_at_the_deadline(client, app, student, practice_calls):
    exam_id = await begin(client, student)
    keys = await keys_of(app, exam_id)
    await save(client, student, exam_id, 0, item(0, keys[0]), item(1, wrong(keys[1])))
    await shift(app, exam_id, -3)
    done = await read(client, student, exam_id)
    assert done["status"] == "submitted"
    result = done["result"]
    assert (result["autoSubmitted"], result["submittedAt"]) == (True, done["endsAt"])
    assert (result["correct"], result["incorrect"], result["unanswered"]) == (1, 1, 3)
    assert len(practice_calls) == 2
    assert (await read(client, student, exam_id))["result"] == result and len(practice_calls) == 2


# ---------------------------------------------------------------- submitting
async def test_a_second_submit_returns_the_stored_result_and_writes_nothing(client, app, student, practice_calls):
    exam_id = await begin(client, student)
    keys = await keys_of(app, exam_id)
    await save(client, student, exam_id, 0, item(0, keys[0]))
    first = await submit(client, student, exam_id, item(1, keys[1]), item(2, wrong(keys[2])))
    assert first.status_code == 200, first.text
    done = first.json()
    assert done["status"] == "submitted" and done["lateItems"] == [] and done["result"]["autoSubmitted"] is False
    assert (done["result"]["correct"], done["result"]["incorrect"], done["result"]["unanswered"]) == (2, 1, 2)
    assert datetime.fromisoformat(done["result"]["submittedAt"]) <= datetime.fromisoformat(done["endsAt"])
    rows = await ledger(app, exam_id)
    assert sum(row.answered_at is not None for row in rows) == 3 and len(practice_calls) == 3

    again = await submit(client, student, exam_id, item(3, keys[3]), item(4, keys[4]))
    assert again.status_code == 200
    assert again.json()["result"] == done["result"] and again.json()["lateItems"] == [3, 4]
    assert [r.answered_at for r in await ledger(app, exam_id)] == [r.answered_at for r in rows]
    assert len(practice_calls) == 3
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(select(QbSet.closed_at)) is not None


async def test_two_submits_at_once_grade_once(client, app, student, practice_calls):
    exam_id = await begin(client, student)
    keys = await keys_of(app, exam_id)
    items = [item(n, keys[n]) for n in range(3)]
    results = await asyncio.gather(*(submit(client, student, exam_id, *items) for _ in range(2)))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert results[0].json()["result"] == results[1].json()["result"]
    assert results[0].json()["result"]["correct"] == 3
    assert sum(row.answered_at is not None for row in await ledger(app, exam_id)) == 3
    assert len(practice_calls) == 3


async def test_a_submit_after_the_cutoff_lists_its_items_as_late(client, app, student):
    exam_id = await begin(client, student)
    keys = await keys_of(app, exam_id)
    await save(client, student, exam_id, 0, item(0, keys[0]))
    await shift(app, exam_id, -3)
    late = await submit(client, student, exam_id, item(1, keys[1]), auto=True)
    assert late.status_code == 200, late.text
    assert late.json()["lateItems"] == [1] and late.json()["result"]["correct"] == 1
    assert late.json()["result"]["autoSubmitted"] is True


async def test_a_stale_submit_is_409_and_a_client_auto_submit_is_stamped_at_the_deadline(client, app, student):
    exam_id = await begin(client, student)
    stale = await submit(client, student, exam_id, base_rev=4)
    assert stale.status_code == 409 and stale.json()["error"]["current"] == {"rev": 0}
    done = await submit(client, student, exam_id, base_rev=0, auto=True)
    assert done.status_code == 200
    assert done.json()["result"]["autoSubmitted"] is True
    assert done.json()["result"]["submittedAt"] == done.json()["endsAt"]


# ---------------------------------------------------------------- grading and results
async def test_a_multi_select_answer_is_right_only_with_exactly_the_key_set(client, app, student):
    exam_id = await begin(client, student, difficulty="easy", length=3)  # the bank's easy questions are multi
    paper = await read(client, student, exam_id)
    assert all(question["multi"] for question in paper["questions"])
    keys = await keys_of(app, exam_id)
    assert all(len(k) == 2 for k in keys)
    letters = [option["key"] for option in paper["questions"][2]["options"]]
    superset = sorted({*keys[2], next(letter for letter in letters if letter not in keys[2])})
    done = (await submit(client, student, exam_id, item(0, keys[0]), item(1, keys[1][:1]), item(2, superset))).json()
    outcomes = [entry["outcome"] for entry in done["result"]["review"]]
    assert outcomes == ["correct", "incorrect", "incorrect"]
    assert [entry["correctKeys"] for entry in done["result"]["review"]] == keys


async def test_results_count_score_percentage_topics_and_review(client, app, student):
    exam_id = await begin(client, student, topicIds=["CCNA-D2-T01", "CCNA-D3-T01"], length=10, minutes=10)
    paper = await read(client, student, exam_id)
    assert paper["title"] == "2 quizzes"
    keys = await keys_of(app, exam_id)
    answers = [item(0, keys[0]), item(1, keys[1]), item(2, keys[2]), item(3, wrong(keys[3]))]
    done = (await submit(client, student, exam_id, *answers)).json()
    result = done["result"]
    assert {k: result[k] for k in ("score", "total", "percentage", "correct", "incorrect", "unanswered")} == {
        "score": 3,
        "total": 10,
        "percentage": 30,
        "correct": 3,
        "incorrect": 1,
        "unanswered": 6,
    }
    topics = {question["topic"] for question in paper["questions"]}
    assert {row["topic"] for row in result["byTopic"]} == topics
    assert sum(row["total"] for row in result["byTopic"]) == 10 and sum(r["correct"] for r in result["byTopic"]) == 3
    for row in result["byTopic"]:
        assert row["total"] == sum(q["topic"] == row["topic"] for q in paper["questions"])
    review = result["review"]
    assert [entry["index"] for entry in review] == list(range(10))
    assert [entry["outcome"] for entry in review[:5]] == ["correct", "correct", "correct", "incorrect", "unanswered"]
    assert review[3]["keys"] == wrong(keys[3]) and review[4]["keys"] == []
    assert all(entry["explanation"].startswith("Because ") for entry in review)
    assert done["questions"] == paper["questions"]  # the same paper, now with its review


async def test_a_question_whose_content_changed_since_it_was_served_is_void(client, app, student, practice_calls):
    exam_id = await begin(client, student)
    keys = await keys_of(app, exam_id)
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text(
                "UPDATE qb_questions SET content_hash = md5(content_hash) || md5(question_id) "
                "WHERE question_id = (SELECT paper[1] FROM practice_exams WHERE id = :id)"
            ),
            {"id": uuid.UUID(exam_id)},
        )
        await db.commit()
    done = (await submit(client, student, exam_id, item(0, keys[0]), item(1, keys[1]))).json()["result"]
    assert done["review"][0]["outcome"] == "void" and done["review"][0]["correctKeys"] == []
    assert (done["total"], done["correct"], done["unanswered"]) == (4, 1, 3)
    assert len(practice_calls) == 1


# ---------------------------------------------------------------- who may see it
async def test_another_account_gets_the_404_of_a_missing_exam(client, app, student):
    exam_id = await begin(client, student)
    other = await enrolled(client, app, "NH-EX-0002")
    for method, url, body in (
        ("GET", f"{EXAMS}/{exam_id}", None),
        ("PUT", f"{EXAMS}/{exam_id}/responses", {"baseRev": 0, "items": [item(0, ["A"])]}),
        ("POST", f"{EXAMS}/{exam_id}/submit", {"baseRev": None, "items": [], "auto": False}),
        ("GET", f"{EXAMS}/{uuid.uuid4()}", None),
    ):
        refused = await client.request(method, url, json=body, headers=other["headers"])
        assert refused.status_code == 404 and refused.json()["error"]["code"] == "not_found", (method, url)
    assert (await read(client, student, exam_id))["status"] == "in_progress"


async def test_revoking_the_invoice_ends_a_running_exam_but_a_submitted_one_stays_readable(client, app, student):
    done_id = await begin(client, student)
    assert (await submit(client, student, done_id)).status_code == 200
    running_id = await begin(client, student)
    await change(app, "NH-EX-0001", status="revoked")
    for method, url, body in (
        ("GET", f"{EXAMS}/{running_id}", None),
        ("PUT", f"{EXAMS}/{running_id}/responses", {"baseRev": 0, "items": [item(0, ["A"])]}),
        ("POST", f"{EXAMS}/{running_id}/submit", {"baseRev": None, "items": [], "auto": False}),
    ):
        assert (await client.request(method, url, json=body, headers=student["headers"])).status_code == 404
    kept = await read(client, student, done_id)
    assert kept["status"] == "submitted" and kept["result"]["total"] == 5
    await change(app, "NH-EX-0001", status="active")
    assert (await read(client, student, running_id))["status"] == "in_progress"


# ---------------------------------------------------------------- the migration
MIGRATION_DB = "aichat_exams_migration_test"
MIGRATION_URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{MIGRATION_DB}"


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosemgrep: this repo's own alembic, on a *_test database
        [sys.executable, "-m", "alembic", "-x", f"url={MIGRATION_URL}", *args], cwd=ROOT, capture_output=True, text=True
    )


def _sql(sql: str, *, database: str = MIGRATION_DB):
    async def go():
        dsn = _asyncpg_dsn(MIGRATION_URL).rsplit("/", 1)[0] + f"/{database}"
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()

    result: dict = {}
    import threading

    worker = threading.Thread(target=lambda: result.setdefault("rows", asyncio.run(go())))
    worker.start()
    worker.join()
    return result.get("rows")


def test_migration_0034_is_additive_and_its_downgrade_refuses_while_exams_exist():
    _sql(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)', database="postgres")
    _sql(f'CREATE DATABASE "{MIGRATION_DB}"', database="postgres")
    try:
        assert _alembic("upgrade", "0034_practice_exams").returncode == 0
        user = _sql(
            "INSERT INTO users (email, password_hash, display_name) VALUES ('m@example.com', 'x', 'M') RETURNING id"
        )
        user_id = user[0]["id"]
        set_row = _sql(
            f"INSERT INTO qb_sets (user_id, request_id, mode, filters, requested, served) "
            f"VALUES ('{user_id}', gen_random_uuid(), 'test', '{{}}', 1, 0) RETURNING id"
        )
        _sql(
            "INSERT INTO practice_exams (user_id, request_id, set_id, program_id, month, topic_ids, difficulty, "
            "length, minutes, paper, ends_at) "
            f"SELECT '{user_id}', gen_random_uuid(), '{set_row[0]['id']}', id, 1, ARRAY['T'], 'mixed', 1, 1, "
            "ARRAY['Q'], clock_timestamp() FROM programs LIMIT 1"
        )
        refused = _alembic("downgrade", "0033_chat_pins_archive_shares")
        assert refused.returncode != 0 and "0034 downgrade refused" in refused.stderr
        _sql("DELETE FROM practice_exams")
        assert _alembic("downgrade", "0033_chat_pins_archive_shares").returncode == 0
        assert _sql("SELECT to_regclass('public.practice_exams') AS t")[0]["t"] is None
        assert _alembic("upgrade", "head").returncode == 0
    finally:
        _sql(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)', database="postgres")
