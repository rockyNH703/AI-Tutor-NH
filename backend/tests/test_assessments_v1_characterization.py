"""Characterization (green first, must stay green): practice records stored before schema v2 keep their
hash, their public() shape and their tutor summary byte for byte. tests/fixtures/assessments_v1.json was
written by the engine as it was before v2 (no code change). A stored record whose fingerprint drifted would
be refused as assessment_corrupt; a summary that drifted would undo what eval_tutor_record measured."""

import json
from pathlib import Path

from app.assessments import state as assessments

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "assessments_v1.json").read_text(encoding="utf-8"))
AT = FIXTURE["at"]
MSG = FIXTURE["messageId"]
RECORDS = FIXTURE["records"]
# Keys a later schema may add to public() for every record or question; always null on a v1 record.
NEW_RECORD_KEYS: set[str] = set()
NEW_QUESTION_KEYS: set[str] = set()


def step(record, question, action, value=None):
    record, changed = assessments.respond(
        record, status="complete", client_hash=record["hash"], question=question, action=action, value=value, at=AT
    )
    assert changed
    return record


def rebuilt() -> dict:
    [quiz], err = assessments.build(FIXTURE["content"]["quiz"], "Quiz me with 3 MCQs on cells")
    assert err is None
    quiz = step(step(quiz, 0, "answer", 0), 1, "skip")
    [exercise], err = assessments.build(FIXTURE["content"]["exercise"], "Give me a fill in the blanks")
    assert err is None
    exercise = step(exercise, 0, "skip")
    raw = FIXTURE["bankRaw"]
    [bank], err = assessments.build(FIXTURE["content"]["bank"], None)
    assert err is None
    bank |= {"total": 5, "adaptive": True}
    source = RECORDS["bank"]["stored"]["source"]
    bank["source"] = {**source, "questionIds": source["questionIds"][:1]}
    bank = assessments.extend(step(bank, 0, "answer", 1), raw["next"])
    bank["source"] = source
    return {"quiz": quiz, "exercise": exercise, "bank": bank}


def test_building_the_same_blocks_gives_the_same_records_and_hashes():
    for name, record in rebuilt().items():
        assert record == RECORDS[name]["stored"], name
        assert assessments.fingerprint(record) == RECORDS[name]["hash"], name


def test_stored_v1_records_replay_and_take_their_next_answer():
    for name in RECORDS:
        record = RECORDS[name]["stored"]
        last = record["responses"][-1]
        replay, changed = assessments.respond(
            record,
            status="complete",
            client_hash=record["hash"],
            question=last["question"],
            action="answer" if last["status"] == "answered" else "skip",
            value=last["value"],
            at=AT,
        )
        assert (replay, changed) == (record, False), name
    quiz = step(RECORDS["quiz"]["stored"], 2, "answer", 0)
    assert assessments.state_of(quiz) == "completed" and quiz["hash"] == RECORDS["quiz"]["hash"]
    bank = step(RECORDS["bank"]["stored"], 1, "answer", 0)
    assert [r["earned"] for r in bank["responses"]] == [1, 1]


def test_public_shape_of_a_v1_record_only_gains_listed_null_keys():
    for name in RECORDS:
        shown = assessments.public(RECORDS[name]["stored"], MSG, "complete")
        expected = RECORDS[name]["public"]
        assert set(shown) - set(expected) == NEW_RECORD_KEYS, name
        assert all(shown[key] is None for key in NEW_RECORD_KEYS), name
        assert {key: shown[key] for key in expected} == expected, name
        for got, want in zip(shown["questions"], expected["questions"], strict=True):
            assert set(got) - set(want) == NEW_QUESTION_KEYS, name
            assert all(got[key] is None for key in NEW_QUESTION_KEYS), name
            assert {key: got[key] for key in want} == want, name


def test_tutor_summary_of_v1_records_is_byte_identical():
    newer = {"quiz": "exercise", "exercise": "quiz", "bank": "exercise"}
    for name in RECORDS:
        record = RECORDS[name]["stored"]
        assert assessments.tutor_summary([record]) == RECORDS[name]["summaryLatest"], name
        other = RECORDS[newer[name]]["stored"]
        assert assessments.tutor_summary([other, record]) == RECORDS[name]["summaryEarlier"], name
