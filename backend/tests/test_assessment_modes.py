"""Practice retries and mock exams (app/assessments/modes.py), the v1 action rule, and what the assistant is told
about them (tutor_summary). Pure: records built and answered by the app's own code."""

import copy
import json
import re

import pytest

from app.assessments import state
from scripts.eval_tutor_record import build_cases
from tests.test_assessment_kinds import KINDS, act, built

SCHOOL = re.compile(
    r"\b(students?|tutor\w*|homework|syllabus|ncert|cbse|icse|textbooks?|schools?|teachers?|class \d{1,2})\b", re.I
)


def exam(*kinds: str) -> dict:
    return built(*(KINDS[kind][0] for kind in kinds), mode="exam")


def reason(record: dict, question: int, action: str, value=None, revision=None) -> str:
    with pytest.raises(state.Refused) as refused:
        act(record, question, action, value, revision)
    return refused.value.reason


def test_save_clear_and_submit_are_refused_on_a_v1_record_and_on_practice():
    v1, practice = built(KINDS["mcq"][0]), built(KINDS["numeric"][0])
    assert v1["schemaVersion"] == 1 and not {"drafts", "attempts", "revision", "submittedAt"} & set(v1)
    for record in (v1, practice):
        assert [
            reason(record, 0, action, value) for action, value in (("save", 0), ("clear", None), ("submit", None))
        ] == ["not_exam"] * 3


def test_numeric_and_cli_practice_take_three_tries_then_show_the_answer():
    record = built(KINDS["numeric"][0], KINDS["cli"][0])
    untouched = copy.deepcopy(record)
    first, changed = act(record, 0, "answer", "60")
    assert changed and record == untouched  # respond never changes the record it is given
    shown = state.public(first, "m", "complete")
    assert shown["pending"] == {"question": 0, "tries": ["60"]} and (shown["answered"], shown["current"]) == (0, 0)
    assert shown["questions"][0]["accepted"] is None and shown["questions"][0]["maxTries"] == 3
    assert act(first, 0, "answer", "60") == (first, False)  # the same try again is a replay
    third, _ = act(act(first, 0, "answer", "61")[0], 0, "answer", "63")  # the third wrong try is final
    response = third["responses"][0]
    assert (response["status"], response["earned"], response["answer"], response["tries"]) == (
        "answered",
        0,
        "63",
        ["60", "61"],
    )
    shown = state.public(third, "m", "complete")
    assert shown["pending"] is None and shown["questions"][0]["accepted"] == ["62"] and third["attempts"][0] is None
    assert act(third, 0, "answer", "61") == (third, False)  # an earlier try, sent again after the reply was lost
    assert reason(third, 0, "answer", "62") == "answered"
    tried, _ = act(third, 1, "answer", "show ip route")
    shown_answer, _ = act(tried, 1, "skip")  # Show answer: 0 points, its tries kept
    assert {key: shown_answer["responses"][1][key] for key in ("status", "earned", "tries")} == {
        "status": "skipped",
        "earned": 0,
        "tries": ["show ip route"],
    }
    assert [first["revision"], third["revision"], tried["revision"], shown_answer["revision"]] == [1, 3, 4, 5]
    right, _ = act(built(KINDS["numeric"][0]), 0, "answer", "62")
    assert "tries" not in right["responses"][0] and right["responses"][0]["earned"] == 1


def test_a_mock_exam_saves_in_any_order_and_grades_only_on_submit():
    record = exam("numeric", "match", "short", "multi")
    key = record["questions"][1]["key"]
    saved = act(act(act(record, 2, "save", "broadcast domains")[0], 0, "save", "61")[0], 1, "save", [key[0], None])[0]
    shown = state.public(saved, "m", "complete")
    assert (shown["state"], shown["current"], shown["answered"], shown["submitted"], shown["earned"]) == (
        "active",
        None,
        0,
        False,
        0,
    )
    assert [q["draft"] for q in shown["questions"]] == ["61", [key[0], None], "broadcast domains", None]
    assert all(q["response"] is None and q["correctList"] is None and q["accepted"] is None for q in shown["questions"])
    changed, _ = act(saved, 0, "save", "62")
    assert act(changed, 0, "save", "62") == (changed, False)  # the same draft
    with pytest.raises(state.Invalid):
        act(changed, 0, "save", "12.")
    cleared, _ = act(changed, 2, "clear")
    assert act(cleared, 2, "clear") == (cleared, False)  # clearing an empty draft
    assert reason(cleared, 0, "answer", "62") == "exam" and reason(cleared, 0, "skip") == "exam"
    assert reason(cleared, 0, "submit", revision=cleared["revision"] - 1) == "stale"
    reloaded = json.loads(json.dumps(cleared))  # drafts and tries are lists: they survive JSONB unchanged
    submitted, _ = act(reloaded, 0, "submit", revision=reloaded["revision"])
    assert [(r["status"], r["earned"]) for r in submitted["responses"]] == [
        ("answered", 1),
        ("answered", 1),
        ("skipped", 0),
        ("skipped", 0),
    ]
    shown = state.public(submitted, "m", "complete")
    assert (shown["state"], shown["submitted"], shown["answerable"], shown["earned"], shown["possible"]) == (
        "completed",
        True,
        False,
        2,
        6,
    )
    assert shown["questions"][1]["correctList"] == key and shown["questions"][0]["accepted"] == ["62"]
    assert act(submitted, 0, "submit") == (submitted, False)  # submit after submit: unchanged, any revision
    assert act(submitted, 0, "save", "62") == (submitted, False)  # an exact replay of a save
    assert reason(submitted, 0, "save", "61") == "submitted" and reason(submitted, 1, "clear") == "submitted"
    assert reason(submitted, 0, "save", "12.") == "submitted"  # a malformed value too: the exam is closed (409)


def test_drafts_are_capped_and_a_draft_a_rule_no_longer_accepts_earns_zero():
    long = KINDS["long"][0]
    record = built(*({**long, "question": f"Explain part {n}."} for n in range(11)), mode="exam")
    for question in range(10):
        record, _ = act(record, question, "save", "x" * 6000)
    with pytest.raises(state.Invalid) as error:
        act(record, 10, "save", "x" * 6000)
    assert str(error.value) == "drafts_full"
    numeric = exam("numeric", "mcq")
    stale = {**numeric, "drafts": ["sixty-two", 0]}  # stored before a rule changed (drafts aren't hashed)
    submitted, _ = act(stale, 0, "submit")
    assert [(r["status"], r["earned"]) for r in submitted["responses"]] == [("skipped", 0), ("answered", 1)]


def test_an_exam_in_progress_puts_the_guard_first_and_tells_no_answer():
    saved, _ = act(exam("numeric", "cli"), 0, "save", "6262")
    summary = state.tutor_summary([saved])
    assert summary.split("\n")[1:3] == [state.SUMMARY_FRAME, state.EXAM_GUARD]
    assert state.USE_RECORD not in summary and "6262" not in summary and "show ip interface brief" not in summary
    assert "1 of 2 questions have a saved answer" in summary and state.EXAM_WAITING in summary
    v1 = built(KINDS["mcq"][0])
    alone = state.tutor_summary([v1])
    both = state.tutor_summary([v1, saved])  # an earlier exam still guards; the v1 text is unchanged
    assert both.startswith(alone.replace(state.SUMMARY_FRAME, f"{state.SUMMARY_FRAME}\n{state.EXAM_GUARD}"))
    assert both.endswith(
        '- Earlier mock exam "Net basics": IN PROGRESS, nothing graded until they submit (2 questions).'
    )
    submitted, _ = act(saved, 0, "submit")
    told = state.tutor_summary([submitted])
    assert state.EXAM_GUARD not in told and "FINISHED. Earned 0 of 2 points; 0 of 2 questions fully right" in told
    assert 'Q1 missed "Usable hosts in a /26?": gave "6262", right "62 hosts"' in told


def worst_case() -> dict:
    """A 30-question practice test at its longest, every question partly right."""
    points = [[f"point {p} " + "x" * 70] for p in range(10)]
    questions = [{"type": "long", "question": f"{n} " + "q" * 900, "keyPoints": points} for n in range(30)]
    record = built(*questions, title="t" * 200)
    for question in range(30):
        record, _ = act(record, question, "answer", "point 3 " + "x" * 70 + " and more " + "y" * 5900)
    return record


def test_the_summary_stays_within_its_budget_and_keeps_the_frame_and_the_guard():
    latest = worst_case()
    in_exam, _ = act(exam("numeric"), 0, "save", "1")
    summary = state.tutor_summary([latest, in_exam])
    lines = summary.split("\n")
    assert len(summary) <= state.SUMMARY_MAX and lines[:3] == [
        state.SUMMARY_HEADING,
        state.SUMMARY_FRAME,
        state.EXAM_GUARD,
    ]
    block = state._latest(latest)
    assert len(block) <= state.LATEST_MAX < len("\n".join(state._v2_lines(latest)))  # cut, on a line boundary
    assert lines[3] == block.split("\n")[0] and block in summary
    assert "- Points per question: Q1 1/10 pts" in block and state.USE_RECORD in block
    for case in build_cases():
        text = state.tutor_summary([case["record"]])
        assert len(text) <= state.SUMMARY_MAX and not SCHOOL.search(text), case["name"]
    assert not SCHOOL.search(summary)


def test_an_unreadable_record_is_left_out_of_the_summary():
    v1 = built(KINDS["mcq"][0])
    expected = state.tutor_summary([v1])
    for broken in ({"schemaVersion": 2, "mode": "exam"}, "not a record", {"schemaVersion": 2, "questions": None}):
        assert state.tutor_summary([broken, v1]) == expected
    assert state.tutor_summary([{"schemaVersion": 3}]) == ""
