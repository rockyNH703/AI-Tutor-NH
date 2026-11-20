"""NHprep's quiz questions into the certification bank's files (app/qbank/nhprep_quiz.py), from session 3's export v1
(C:\\Users\\test\\netsec-handoffs\\nhprep.md, "Export v1"). Pure files: no database, no model, no network.
Every export here is synthetic."""

import hashlib
import json
from pathlib import Path

import pytest

from app.qbank.nhprep_quiz import ExportError, convert, question_id
from app.qbank.validate import read_bank
from tests.test_qbank_certification import netsec_bank


def _quiz(n: int, *, category: str = "ccna", **extra) -> dict:
    options = [f"Option {n}-{i} for the router" for i in range(4)]
    record = {
        "type": "quiz_question",
        "schema": 1,
        "category": category,
        "categoryName": category.upper(),
        "id": f"q{n:03d}",
        "format": "single",
        "question": f"Which option {n} is right for the OSPF router question number {n}?",
        "options": options,
        "correctOption": options[n % 4],
        "correctIndex": n % 4,
        "explanation": f"Option {n}-{n % 4} is right.",
        "difficulty": None,
        "lessonExternalId": None,
    }
    return record | extra


def export(folder: Path, questions: list[dict], *, seen: int | None = None, reported: int | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "course", "schema": 1, "slug": "ccna", "title": "CCNA"}, *questions]
    lines.append(
        {
            "type": "quiz_coverage",
            "schema": 1,
            "category": "ccna",
            "attempts": 3,
            "seen": len(questions) if seen is None else seen,
            "reported": len(questions) if reported is None else reported,
        }
    )
    data = ("\n".join(json.dumps(line) for line in lines) + "\n").encode("utf-8")
    (folder / "export.jsonl").write_bytes(data)
    manifest = {"schema": 1, "complete": True, "sha256": hashlib.sha256(data).hexdigest(), "counts": {}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def quiz_map(root: Path, entries: dict[str, dict]) -> None:
    folder = root / "nhprep" / "quiz-map"
    folder.mkdir(parents=True, exist_ok=True)
    data = {"category": "ccna", "book_id": "ccna", "questions": entries}
    (folder / "ccna.json").write_text(json.dumps(data), encoding="utf-8")


def mapped(*numbers: int, **extra) -> dict[str, dict]:
    return {f"q{n:03d}": {"topic_id": "CCNA-D3-T01", "difficulty": "medium"} | extra for n in numbers}


def lines(root: Path) -> list[dict]:
    found = []
    for path in sorted((root / "content" / "ccna").glob("*/questions.jsonl")):
        found += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return found


def nhprep(root: Path) -> dict[str, dict]:
    return {line["question_id"]: line for line in lines(root) if line["source"]["kind"] == "nhprep_quiz"}


@pytest.fixture
def root(tmp_path):
    return netsec_bank(tmp_path / "qbank", per_topic=1)


def test_every_mapped_quiz_question_becomes_a_valid_bank_question_with_its_own_text_and_key(root, tmp_path):
    questions = [_quiz(n) for n in range(1, 6)]
    quiz_map(root, mapped(1, 2, 3, 4, 5))
    authored = lines(root)

    report = convert(export(tmp_path / "run", questions), root)

    assert report.ok and report.exported == 5 and report.written == 5, report
    assert read_bank(root).errors == []
    first = nhprep(root)[question_id("ccna", "q001")]
    assert first["stem"] == questions[0]["question"] and first["options"] == questions[0]["options"]
    assert first["correct"] == ["B"] and first["format"] == "mcq" and first["key_origin"] == "nhprep"
    assert first["source"] == {"kind": "nhprep_quiz", "ref": "nhprep:quiz/ccna/q001"}
    assert first["chapter_id"] == "CCNA-D3" and first["difficulty"] == "medium"
    assert [line for line in lines(root) if line["source"]["kind"] != "nhprep_quiz"] == authored


def test_an_unmapped_question_fails_the_count_gate_and_is_not_written(root, tmp_path):
    quiz_map(root, mapped(1))
    report = convert(export(tmp_path / "run", [_quiz(1), _quiz(2)]), root)
    assert not report.ok and report.unmapped == [question_id("ccna", "q002")]
    assert set(nhprep(root)) == {question_id("ccna", "q001")}


def test_a_missing_explanation_needs_one_from_the_map(root, tmp_path):
    quiz_map(root, mapped(1))
    report = convert(export(tmp_path / "run", [_quiz(1, explanation="")]), root)
    assert report.unmapped == [question_id("ccna", "q001")]
    quiz_map(root, mapped(1, explanation="Written offline: option 1-1 is the right one."))
    assert convert(export(tmp_path / "run2", [_quiz(1, explanation="")]), root).ok


@pytest.mark.parametrize("broken, message", [({"complete": False}, "complete"), ({"sha256": "0" * 64}, "sha256")])
def test_an_incomplete_or_altered_export_is_refused(root, tmp_path, broken, message):
    folder = export(tmp_path / "run", [_quiz(1)])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8")) | broken
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExportError, match=message):
        convert(folder, root)


def test_an_inconsistent_key_or_another_format_is_an_error_not_a_guess(root, tmp_path):
    quiz_map(root, mapped(1, 2))
    report = convert(export(tmp_path / "run", [_quiz(1, correctOption="nope"), _quiz(2, format="multi")]), root)
    assert not report.ok and len(report.errors) == 2 and nhprep(root) == {}


def test_a_disputed_key_is_kept_inactive_with_its_reason(root, tmp_path):
    quiz_map(root, mapped(1, dispute="the notes say area 0 is optional"))
    report = convert(export(tmp_path / "run", [_quiz(1)]), root)
    assert report.ok and read_bank(root).errors == []
    line = nhprep(root)[question_id("ccna", "q001")]
    assert line["active"] is False and "area 0" in line["inactive_reason"]


def test_a_question_gone_from_nhprep_is_retired_only_when_the_category_was_fully_seen(root, tmp_path):
    quiz_map(root, mapped(1, 2))
    convert(export(tmp_path / "run1", [_quiz(1), _quiz(2)]), root)

    partial = convert(export(tmp_path / "run2", [_quiz(1)], seen=1, reported=2), root)
    assert partial.retired == 0 and nhprep(root)[question_id("ccna", "q002")].get("active", True) is True

    complete = convert(export(tmp_path / "run3", [_quiz(1)]), root)
    gone = nhprep(root)[question_id("ccna", "q002")]
    assert complete.retired == 1 and gone["active"] is False and "no longer" in gone["inactive_reason"]
    assert read_bank(root).errors == []


def test_a_review_is_kept_while_the_question_is_unchanged_and_dropped_when_nhprep_changes_it(root, tmp_path):
    quiz_map(root, mapped(1))
    convert(export(tmp_path / "run1", [_quiz(1)]), root)
    path = root / "content" / "ccna" / "CCNA-D3" / "questions.jsonl"
    reviewed = [
        line | {"review": {"status": "rejected", "note": "trainer"}}
        if line["source"]["kind"] == "nhprep_quiz"
        else line
        for line in lines(root)
        if line["chapter_id"] == "CCNA-D3"
    ]
    path.write_text("\n".join(json.dumps(line) for line in reviewed) + "\n", encoding="utf-8")

    convert(export(tmp_path / "run2", [_quiz(1)]), root)
    assert nhprep(root)[question_id("ccna", "q001")]["review"]["status"] == "rejected"

    convert(export(tmp_path / "run3", [_quiz(1, question="A reworded OSPF question one?")]), root)
    assert "review" not in nhprep(root)[question_id("ccna", "q001")]


def test_the_same_question_twice_is_written_once(root, tmp_path):
    quiz_map(root, mapped(1, 2))
    first = _quiz(1)
    twin = _quiz(2, question=first["question"], options=first["options"], correctOption=first["options"][1])
    report = convert(export(tmp_path / "run", [first, twin | {"correctIndex": 1}]), root)
    assert report.ok and report.written == 1 and len(report.duplicates) == 1
    assert read_bank(root).errors == []


def test_a_rerun_changes_nothing(root, tmp_path):
    quiz_map(root, mapped(1, 2, 3))
    folder = export(tmp_path / "run", [_quiz(1), _quiz(2), _quiz(3)])
    convert(folder, root)
    before = {path: path.read_bytes() for path in root.rglob("questions.jsonl")}
    convert(folder, root)
    assert {path: path.read_bytes() for path in root.rglob("questions.jsonl")} == before


def _manifest(folder: Path, **changes) -> None:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8")) | changes
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_the_real_exporters_per_part_complete_is_accepted_only_when_the_run_finished(root, tmp_path):
    # session 3's exporter writes complete: {lessons, quiz}; quiz false = some categories not fully seen
    quiz_map(root, mapped(1))
    folder = export(tmp_path / "run", [_quiz(1)])
    _manifest(folder, complete={"lessons": True, "quiz": False})
    assert convert(folder, root).ok
    _manifest(folder, complete={"lessons": True})
    with pytest.raises(ExportError, match="complete"):
        convert(folder, root)


def test_a_category_seen_in_several_runs_counts_its_fullest_coverage(root, tmp_path):
    quiz_map(root, mapped(1, 2))
    convert(export(tmp_path / "run1", [_quiz(1), _quiz(2)]), root)
    folder = export(tmp_path / "run2", [_quiz(1)])  # its own coverage line: seen 1 of 1, complete
    lines = (folder / "export.jsonl").read_text(encoding="utf-8").splitlines()
    early = {"type": "quiz_coverage", "schema": 1, "category": "ccna", "attempts": 1, "seen": 1, "reported": 2}
    data = ("\n".join([*lines[:-1], lines[-1], json.dumps(early)]) + "\n").encode("utf-8")
    (folder / "export.jsonl").write_bytes(data)
    _manifest(folder, sha256=hashlib.sha256(data).hexdigest())
    # the later, smaller line must not hide the full one (and the full one says 1 of 1: q002 is gone)
    assert convert(folder, root).retired == 1


def test_code_options_that_differ_only_in_symbols_are_different_options(root, tmp_path):
    # NHprep's Ansible/Terraform/Python items turn on syntax: "{% for %}" vs "{{ for }}", quoted vs unquoted
    # The database's own rule (qb_options_valid): lower(btrim(option)) distinct; newlines and inner spaces count.
    quiz_map(root, mapped(1, 2, 3))
    code = ["{% for x in y %}", "{{ for x in y }}", "{% loop x in y %}", "{{ loop x in y }}"]
    yaml = ["r1:\n  host: 10.0.0.1", "r1: host: 10.0.0.1", "r1: { host: 10.0.0.1 }", "r1: host = 10.0.0.1"]
    same = ["Same answer", "same answer ", "Other", "Another"]  # NHprep's own data repeats an option
    questions = [
        _quiz(1, options=code, correctOption=code[1]),
        _quiz(2, options=yaml, correctOption=yaml[2]),
        _quiz(3, options=same, correctOption=same[3]),
    ]
    report = convert(export(tmp_path / "run", questions), root)
    assert report.ok and report.written == 2 and read_bank(root).errors == []
    assert len(report.unsupported) == 1 and question_id("ccna", "q003") in report.unsupported[0]


def test_question_ids_are_permanent_bank_ids():
    assert question_id("ccna", "cl9x-Ab_12") == "NHQ-CCNA-CL9X-AB-12"
