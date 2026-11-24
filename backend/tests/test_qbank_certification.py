"""The certification question bank (0026, app/qbank/netsec.py, finder.py, request.py, bank_tests.py, the bank turn in
app/chat/service.py): stored single- and multiple-answer questions, imported from qbank/ files, found from what a
student asks, served as a batch, and graded by the database, with no model call while a student chats.

Every bank here is synthetic; nothing calls a model."""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.access.students import is_nh_student
from app.db.models import NhInvoice, Program, ProgramModule, ProgramModuleBook
from app.qbank import bank_tests, finder
from app.qbank.importer import import_bank
from app.qbank.request import parse
from app.qbank.selection import Pool, pick_next
from app.qbank.validate import read_bank
from tests.conftest import make_settings, register, stream_chat

VERSION = {
    "code": "netsec-2026",
    "curriculum": "netsec",
    "academic_session": "2026_27",
    "title": "Synthetic certification bank",
    "source_url": "https://example.com/",
}
TOPICS = {
    "CCNA-D3": [
        ("OSPF neighbor states", ["ospf neighbors", "ospf adjacency"]),
        ("OSPF areas", ["ospf area", "backbone area"]),
    ],
    "CCNA-D2": [("VLANs", ["vlan", "vlans", "virtual lan"]), ("EtherChannel (LACP)", ["etherchannel", "lacp"])],
}
VOCABULARY = {"groups": [["ospf", "open shortest path first"], ["vlan", "virtual lan"]]}
CHECKED = {
    "notes": "agree",
    "closed_book": "agree",
    "single_defensible": True,
    "by": "verifier",
    "at": "2026-11-19T10:00:00+00:00",
}
STUDENT = "student-{}@networkershome.com"


def _question(topic_id: str, n: int, *, kind: str, source: str = "nhprep_lesson") -> dict:
    words = [f"w{topic_id.lower().replace('-', '')}{n:02d}{i}" for i in range(6)]
    multi = n % 3 == 0
    options = [f"o{topic_id.lower().replace('-', '')}{n:02d}{i}" for i in range(5 if multi else 4)]
    return {
        "question_id": f"{topic_id}-{n:03d}",
        "chapter_id": topic_id.rsplit("-", 1)[0],
        "topic_id": topic_id,
        "concept_tag": "core",
        "difficulty": ("easy", "medium", "hard")[n % 3],
        "format": "multi_select" if multi else "mcq",
        "competency": "application",
        "stem": " ".join(words) + (" (Choose two)" if multi else ""),
        "options": options,
        "correct": ["A", "C"] if multi else ["ABCD"[n % 4]],
        "explanation": "Because " + " ".join(words) + " explains exactly why this is the answer here.",
        "marks": 1,
        "source": {"kind": source, "ref": f"lesson-{topic_id}"},
        "lesson_ref": f"lesson-{topic_id}",
        "key_origin": kind,
        "verification": CHECKED,
    }


def netsec_bank(root: Path, *, per_topic: int = 9, source: str = "nhprep_lesson") -> Path:
    chapters, questions = [], {}
    for number, (chapter_id, topics) in enumerate(sorted(TOPICS.items()), 2):
        entries = []
        for t, (title, aliases) in enumerate(topics, 1):
            topic_id = f"{chapter_id}-T{t:02d}"
            entries.append(
                {
                    "topic_id": topic_id,
                    "topic_number": t,
                    "title": title,
                    "aliases": aliases,
                    "lessons": [{"ref": f"lesson-{topic_id}", "title": f"{title} explained", "course": "CCNA"}],
                    "concepts": [{"concept_tag": "core", "learning_objective": f"Understand {title}."}],
                }
            )
            kind = "authored"
            questions.setdefault(chapter_id, []).extend(
                _question(topic_id, n, kind=kind, source=source) for n in range(1, per_topic + 1)
            )
        chapters.append(
            {
                "chapter_id": chapter_id,
                "chapter_number": number,
                "title": f"Domain {number}",
                "max_questions": 5000,
                "topics": entries,
            }
        )
    manifest = {
        "syllabus_version": VERSION,
        "book": {
            "book_id": "ccna",
            "curriculum": "netsec",
            "class_level": 1,
            "subject_code": "routing_switching",
            "language": "english",
            "title": "CCNA",
            "edition": "200-301 v1.1",
        },
        "chapters": chapters,
    }
    folder = root / "manifest" / VERSION["code"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ccna.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "vocabulary").mkdir(exist_ok=True)
    (root / "vocabulary" / "netsec.json").write_text(json.dumps(VOCABULARY), encoding="utf-8")
    for chapter_id, records in questions.items():
        target = root / "content" / "ccna" / chapter_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "questions.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def settings():
    return make_settings(question_bank_tests=True, question_bank_turn="on")


@pytest.fixture
async def bank(app, tmp_path):
    async with app.state.db.sessionmaker() as db:
        report = await import_bank(db, netsec_bank(tmp_path), apply=True)
    assert report.applied, report.errors
    finder._vocabulary["at"] = 0.0  # the vocabulary cache reads this database's bank


# ------------------------------------------------------------------ files and schema
def test_the_validator_reads_a_netsec_bank_and_refuses_what_is_wrong(tmp_path):
    root = netsec_bank(tmp_path)
    bank = read_bank(root)
    assert bank.errors == []
    assert {row["question_format"] for row in bank.rows} == {"mcq", "multi_select"}
    assert all(row["option_a"] is None and row["options"] for row in bank.rows)
    topic = bank.topics["CCNA-D2-T01"]
    assert "virtual lan" in topic["aliases"] and topic["search_title"].startswith("VLANs")
    assert "OSPF neighbor states explained" in bank.topics["CCNA-D3-T01"]["search_doc"]

    path = root / "content" / "ccna" / "CCNA-D3" / "questions.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    lines[0] |= {"correct": ["A", "B"]}  # an mcq with two keys
    lines[1] |= {"options": ["same", "Same", "x", "y"]}
    lines[2] |= {"stem": lines[2]["stem"].replace("(Choose two)", "(Choose three)")}
    lines[3] |= {"verification": {**CHECKED, "closed_book": "disagree"}}  # served only when all three agree
    lines[4] |= {"stem": lines[4]["stem"] + " All of the above"}
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    errors = "\n".join(read_bank(root).errors)
    for expected in (
        "exactly one correct letter",
        "two options are the same",
        "says '(Choose three)' but there are 2",
        "three-way blind check",
        "all/none of the above",
    ):
        assert expected in errors, expected


async def test_the_database_keeps_one_of_two_shapes_and_valid_keys(app, bank):
    insert = text(
        "UPDATE qb_questions SET options = CAST(:options AS jsonb), correct_keys = CAST(:keys AS text[]), "
        "question_format = :format WHERE question_id = 'CCNA-D2-T01-001'"
    )
    for options, keys, form in (
        (["a", "b", "c"], ["D"], "mcq"),  # a key past the options
        (["a", "b", "c"], ["A", "B"], "mcq"),  # two keys on a single-answer question
        (["a", "b"], ["A", "B"], "multi_select"),  # every option correct
        (["a", "A", "c"], ["A"], "mcq"),  # duplicate options
    ):
        async with app.state.db.sessionmaker() as db:
            with pytest.raises(DBAPIError):
                await db.execute(insert, {"options": json.dumps(options), "keys": keys, "format": form})
    async with app.state.db.sessionmaker() as db:
        with pytest.raises(DBAPIError):  # both shapes at once
            await db.execute(text("UPDATE qb_questions SET option_a = 'x' WHERE question_id = 'CCNA-D2-T01-001'"))


async def test_the_database_grades_a_set_of_letters_and_refuses_the_wrong_shape(client, app, bank):
    user = (await register(client, app))["user"]
    async with app.state.db.sessionmaker() as db:
        rows = [
            await db.execute(text("SELECT * FROM qb_questions WHERE question_id = :q"), {"q": q})
            for q in ("CCNA-D2-T01-003", "CCNA-D2-T01-001")
        ]
        multi, single = (row.one() for row in rows)
        set_id = await bank_tests.open_set(
            db, uuid.UUID(user["id"]), uuid.uuid4(), bank_tests.Batch((multi, single), {"netsec": True})
        )
        right = await bank_tests.answer_keys(db, uuid.UUID(user["id"]), set_id, 1, ["C", "A"])
        assert right is not None and right[1] is True
        partly = await bank_tests.answer_keys(db, uuid.UUID(user["id"]), set_id, 2, ["B"])
        assert partly is not None and partly[1] is (single.correct_keys == ["B"])
        assert await bank_tests.answer_keys(db, uuid.UUID(user["id"]), set_id, 1, ["A", "C"]) is None  # final
        with pytest.raises(DBAPIError):  # and can't be reset
            async with db.begin_nested():
                await db.execute(text("UPDATE qb_set_items SET chosen_keys = NULL WHERE set_id = :s"), {"s": set_id})
        fresh = await bank_tests.open_set(
            db, uuid.UUID(user["id"]), uuid.uuid4(), bank_tests.Batch((multi,), {"netsec": True})
        )
        await db.commit()
    async with app.state.db.sessionmaker() as db:  # a new-shape question answered with one letter: refused
        with pytest.raises(DBAPIError):
            await db.execute(
                text("UPDATE qb_set_items SET chosen = 'A' WHERE set_id = :s AND position = 1"), {"s": fresh}
            )


# ------------------------------------------------------------------ who may see it
def test_a_student_is_a_verified_networkershome_address_and_nothing_like_it():
    settings = make_settings(nhprep_allowed_email_domains="networkershome.com")

    class Account:
        def __init__(self, email, verified=True):
            self.email, self.email_verified = email, verified

    assert is_nh_student(Account("a@NetworkersHome.com"), settings)
    assert not is_nh_student(Account("a@networkershome.com", verified=False), settings)
    assert not is_nh_student(Account("a@networkershome.com.evil.tld"), settings)
    assert not is_nh_student(Account("a@evil.tld@x.com"), settings)
    assert not is_nh_student(Account("a@networKershome.com"), settings)  # KELVIN SIGN folds to "k"
    assert not is_nh_student(Account("a@networkershome.com"), make_settings(nhprep_allowed_email_domains=""))


async def test_nhprep_questions_are_never_picked_for_someone_else(app, bank):
    pool = Pool("english", 1, "routing_switching", ("CCNA-D2-T01",), card=True)
    async with app.state.db.sessionmaker() as db:
        assert await pick_next(db, uuid.uuid4(), pool, "seed") is None
        found = await pick_next(db, uuid.uuid4(), Pool(**{**pool.__dict__, "entitled": True}), "seed")
        assert found is not None and found.options


# ------------------------------------------------------------------ finding topics
@pytest.mark.parametrize(
    ("asked", "status", "titles"),
    [
        ("quiz me on OSPF neighbor states", "confident", ["OSPF neighbor states"]),
        ("test me on ospf", "broad", ["OSPF areas", "OSPF neighbor states"]),
        ("ask me some questions on vlna", "confident", ["VLANs"]),
        ("give me 5 mcqs on etherchanel", "confident", ["EtherChannel (LACP)"]),
        ("quiz me on bgp", "none", []),
    ],
)
async def test_the_finder_reads_what_the_student_named(app, bank, asked, status, titles):
    request = parse(asked)
    async with app.state.db.sessionmaker() as db:
        found = await finder.find(db, request.topic, track=request.track, entitled=True)
    assert found.status == status
    assert sorted(topic.title for topic in found.topics) == titles


@pytest.mark.parametrize(
    "message",
    [
        "how do I test ACLs?",
        "what is a test lab?",
        "I have 2 questions about BGP",
        "best practice for stp",
        "what is the ccna exam fee",
        "explain ospf",
        "interview questions on bgp",
        "make flashcards for vlan",
    ],
)
def test_ordinary_messages_are_not_quiz_requests(message):
    assert parse(message) is None


# ------------------------------------------------------------------ the bank turn
async def _student(client, app) -> dict:
    return (await register(client, app, email=STUDENT.format(uuid.uuid4().hex[:8])))["headers"]


async def test_a_quiz_request_is_answered_from_the_bank_with_no_model_call(client, app, fake_llm, bank):
    headers = await _student(client, app)
    response, events = await stream_chat(client, headers, "give me 5 questions on ospf neighbor states")
    assert response.status_code == 200 and events[-1][0] == "done", response.text
    assert fake_llm.calls == []  # no model, no recall, no classifier
    message = events[-1][1]["message"]
    assert "```quiz" in message["content"] and '"correct' not in message["content"]
    test = message["metadata"]["assessments"][0]
    assert test["schemaVersion"] == 2 and test["total"] == 5
    assert all(
        q["correct"] is None and q["correctList"] is None and q["explanation"] is None for q in test["questions"]
    )
    assert {q["kind"] for q in test["questions"]} == {"mcq", "multi"}
    assert message["metadata"]["bank"]["status"] == "confident" and message["metadata"]["bank"]["next"] is True

    async with app.state.db.sessionmaker() as db:
        keys = (
            await db.execute(
                text(
                    "SELECT i.position, q.correct_keys FROM qb_sets s JOIN qb_set_items i ON i.set_id = s.id "
                    "JOIN qb_questions q ON q.question_id = i.question_id WHERE s.request_id = :m ORDER BY i.position"
                ),
                {"m": uuid.UUID(message["id"])},
            )
        ).all()
    for question, (_position, correct) in enumerate(keys):
        chosen = ["ABCDEFGH".index(letter) for letter in correct]
        kind = test["questions"][question]["kind"]
        body = {"position": 0, "hash": test["hash"], "question": question, "action": "answer"}
        body["value"] = chosen if kind == "multi" else chosen[0]
        reply = await client.post(f"/v1/messages/{message['id']}/assessment-responses", json=body, headers=headers)
        assert reply.status_code == 200, reply.text
        test = reply.json()
    assert test["state"] == "completed" and test["earned"] == 5
    async with app.state.db.sessionmaker() as db:
        graded = (
            await db.execute(
                text(
                    "SELECT bool_and(i.is_correct) AS all_right, count(i.chosen_keys) AS keyed, "
                    "bool_and(s.closed_at IS NOT NULL) AS closed FROM qb_sets s JOIN qb_set_items i ON i.set_id = s.id "
                    "WHERE s.request_id = :m"
                ),
                {"m": uuid.UUID(message["id"])},
            )
        ).one()
    assert graded.all_right and graded.keyed == 5 and graded.closed


async def test_next_five_continues_the_test_with_new_questions(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "quiz me on vlans")
    first = events[-1][1]["message"]
    conversation = events[-1][1]["conversation"]["id"]
    _, events = await stream_chat(client, headers, "next 5", conversation_id=conversation)
    second = events[-1][1]["message"]
    assert fake_llm.calls == []
    ids = lambda m: {q["text"] for q in m["metadata"]["assessments"][0]["questions"]}  # noqa: E731
    assert len(ids(second)) >= 4 and not ids(first) & ids(second)
    # 9 VLAN questions: the second batch takes the last 4, so no third is offered (session 1's e2e nit b)
    assert first["metadata"]["bank"]["next"] is True and second["metadata"]["bank"]["next"] is False
    assert '"more":false' in second["content"]
    # The card says "Choose N" itself; the stem shown doesn't repeat "(Choose two)" (nit a)
    assert not any("(Choose" in text for text in ids(first) | ids(second))


async def test_the_quiz_me_button_shows_only_where_the_bank_answers(client, app):
    # GET /v1/progress/bank: the web app's "Quiz me" button. Every chat turn still checks for itself.
    student = await _student(client, app)
    other = (await register(client, app))["headers"]  # example.com: NHprep's questions are for students only
    assert (await client.get("/v1/progress/bank", headers=student)).json() == {"enabled": True}
    assert (await client.get("/v1/progress/bank", headers=other)).json() == {"enabled": False}
    app.state.settings.question_bank_turn = "off"
    assert (await client.get("/v1/progress/bank", headers=student)).json() == {"enabled": False}
    app.state.settings.question_bank_turn = "on"
    app.state.settings.question_bank_tests = False
    assert (await client.get("/v1/progress/bank", headers=student)).json() == {"enabled": False}
    assert (await client.get("/v1/progress/bank")).status_code == 401


async def test_someone_who_is_not_a_student_gets_no_nhprep_questions(client, app, fake_llm, bank):
    headers = (await register(client, app))["headers"]  # example.com
    _, events = await stream_chat(client, headers, "quiz me on ospf neighbor states")
    message = events[-1][1]["message"]
    assert fake_llm.calls == [] and "assessments" not in message["metadata"]
    assert "No practice question bank is available" in message["content"]


async def _enrol(client, app, headers, number: str, books: list[str]) -> None:
    """An NH invoice for Full Stack Networking with AI, whose month 1 maps `books`, claimed by this account."""
    app.state.settings.programs_mode = "on"
    async with app.state.db.sessionmaker() as db:
        program = await db.scalar(select(Program.id).where(Program.slug == "networking-ai"))
        module = await db.scalar(
            select(ProgramModule.id).where(ProgramModule.program_id == program, ProgramModule.month == 1)
        )
        db.add_all(ProgramModuleBook(module_id=module, book_id=book) for book in books)
        db.add(NhInvoice(invoice_no=number, program_id=program))
        await db.commit()
    claimed = await client.post("/v1/programs/claims", json={"invoiceNumber": number}, headers=headers)
    assert claimed.status_code == 201, claimed.text


async def test_an_enrolled_student_gets_quiz_me_whatever_their_email_while_the_invoice_is_active(
    client, app, fake_llm, bank
):
    # Owner, 2026-11-24: Quiz me for every enrolled student, not only networkershome.com addresses.
    headers = (await register(client, app))["headers"]  # example.com
    assert (await client.get("/v1/progress/bank", headers=headers)).json() == {"enabled": False}
    await _enrol(client, app, headers, "NH-Q-0001", ["ccna"])
    assert (await client.get("/v1/progress/bank", headers=headers)).json() == {"enabled": True}

    _, events = await stream_chat(client, headers, "quiz me on vlans")  # the general chat, not a program chat
    message = events[-1][1]["message"]
    assert fake_llm.calls == [] and message["metadata"]["bank"]["source"] == "NHprep"
    test = message["metadata"]["assessments"][0]
    multi = test["questions"][0]["kind"] == "multi"
    body = {"position": 0, "hash": test["hash"], "question": 0, "action": "answer", "value": [0, 1] if multi else 0}
    reply = await client.post(f"/v1/messages/{message['id']}/assessment-responses", json=body, headers=headers)
    assert reply.status_code == 200, reply.text
    conversation = events[-1][1]["conversation"]["id"]
    shown = (await client.get(f"/v1/conversations/{conversation}/messages", headers=headers)).json()["items"][-1]
    assert shown["metadata"]["assessments"]

    async with app.state.db.sessionmaker() as db:  # the invoice revoked: no longer enrolled
        await db.execute(update(NhInvoice).where(NhInvoice.invoice_no == "NH-Q-0001").values(status="revoked"))
        await db.commit()
    assert (await client.get("/v1/progress/bank", headers=headers)).json() == {"enabled": False}
    hidden = (await client.get(f"/v1/conversations/{conversation}/messages", headers=headers)).json()["items"][-1]
    assert hidden["content"] == "This reply isn't available."


async def test_an_enrolled_student_is_quizzed_only_on_their_programs_tracks(client, app, fake_llm, bank):
    headers = (await register(client, app))["headers"]  # example.com
    await _enrol(client, app, headers, "NH-Q-0002", [])  # the program maps no track of this bank
    assert (await client.get("/v1/progress/bank", headers=headers)).json() == {"enabled": False}
    _, events = await stream_chat(client, headers, "quiz me on vlans")
    assert "assessments" not in events[-1][1]["message"]["metadata"]


async def test_ordinary_chat_and_a_switched_off_turn_still_reach_the_model(client, app, fake_llm, bank):
    headers = await _student(client, app)
    await stream_chat(client, headers, "how do I test ACLs?")
    assert len(fake_llm.calls) == 1
    app.state.settings.question_bank_turn = "off"
    await stream_chat(client, headers, "quiz me on vlans")
    assert len(fake_llm.calls) == 2


async def test_the_model_cannot_open_the_certification_bank(client, app, fake_llm, bank):
    headers = await _student(client, app)
    fake_llm.reply = lambda messages: 'Sure.\n\n```question-bank\n{"chapter": "CCNA-D3", "count": 5}\n```'
    app.state.settings.question_bank_turn = "off"
    _, events = await stream_chat(client, headers, "hello there")
    assert "assessments" not in events[-1][1]["message"]["metadata"]
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(text("SELECT count(*) FROM qb_sets")) == 0


async def test_an_nhprep_derived_test_is_hidden_from_a_reader_who_is_not_a_student(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "quiz me on vlans")
    conversation = events[-1][1]["conversation"]["id"]
    shown = (await client.get(f"/v1/conversations/{conversation}/messages", headers=headers)).json()["items"][-1]
    assert shown["metadata"]["assessments"] and shown["metadata"]["bank"]["source"] == "NHprep"
    app.state.settings.nhprep_allowed_email_domains = "example.org"  # the account is no longer a student
    hidden = (await client.get(f"/v1/conversations/{conversation}/messages", headers=headers)).json()["items"][-1]
    assert hidden["content"] == "This reply isn't available." and hidden["metadata"] == {}


async def test_a_weak_cue_without_a_topic_is_left_to_the_model(client, app, fake_llm, bank):
    headers = await _student(client, app)
    _, events = await stream_chat(client, headers, "practice questions on bgp")  # weak cue, no BGP topic
    assert len(fake_llm.calls) == 1 and "assessments" not in events[-1][1]["message"]["metadata"]
    _, events = await stream_chat(client, headers, "quiz me on bgp")  # asked in so many words: the bank says so
    assert len(fake_llm.calls) == 1 and "don't have stored practice questions" in events[-1][1]["message"]["content"]
