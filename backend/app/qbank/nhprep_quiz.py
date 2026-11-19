"""NHprep's quiz questions into the certification bank's files, from session 3's export v1 (a bundle of
export.jsonl + manifest.json; the contract is frozen in netsec-handoffs/nhprep.md, "Export v1").

What NHprep doesn't give per question is supplied offline, by qbank/nhprep/quiz-map/<category>.json:
  {"category": "ccna", "book_id": "ccna",
   "questions": {"<NHprep id>": {"topic_id": "CCNA-D3-T04", "difficulty": "medium",
                                 "competency"?, "concept_tag"?, "explanation"? (only when NHprep has none),
                                 "dispute"? (why the key is doubted: kept inactive)}}}

Rules:
- NHprep's text, options (in its order) and key are kept as served; key_origin "nhprep".
- A question with no map entry (or no explanation anywhere) is not written: it is listed, and the run fails its
  count gate until every exported question is mapped.
- A key that disagrees with itself, or a format other than one-of-N, is an error, never a guess.
- The same question twice (same stem and options) is written once.
- A question NHprep no longer serves is retired (inactive with a reason) only when that category's coverage
  says every question was seen; otherwise it is left as it was.
- A review is kept only while the question's content is unchanged.
Other lines in the content files (authored questions, other categories) are kept in place.
No database, no model, no network; the files are checked by validate.read_bank afterwards.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.qbank import netsec
from app.qbank.validate import fingerprint, read_bank

RECORD_TYPES = ("course", "lesson", "quiz_question", "quiz_coverage")
DIFFICULTIES = ("easy", "medium", "hard")
RETIRED = "no longer in NHprep's quiz (its category was fully seen)"
_REF = re.compile(r"^nhprep:quiz/([^/]+)/")


class ExportError(ValueError):
    """The bundle is not a complete, unaltered export v1."""


@dataclass
class Report:
    exported: int = 0
    written: int = 0
    retired: int = 0
    unmapped: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    coverage: dict[str, dict] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.unmapped


def question_id(category: str, nhprep_id: Any) -> str:
    """The bank's permanent id for an NHprep question."""
    return "NHQ-" + re.sub(r"[^A-Z0-9]+", "-", f"{category}-{nhprep_id}".upper()).strip("-")


def read_export(folder: Path) -> tuple[list[dict], dict[str, dict]]:
    """The quiz questions and per-category coverage of a complete export whose bytes match its manifest."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        data = (folder / "export.jsonl").read_bytes()
    except (OSError, ValueError) as error:
        raise ExportError(f"unreadable export bundle ({error})") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("complete") is not True:
        raise ExportError("the export is not a complete schema 1 bundle")
    if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
        raise ExportError("export.jsonl does not match its manifest's sha256")
    questions, coverage = [], {}
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise ExportError(f"export.jsonl:{number}: unreadable JSON ({error})") from error
        if not isinstance(record, dict) or record.get("schema") != 1 or record.get("type") not in RECORD_TYPES:
            raise ExportError(f"export.jsonl:{number}: not an export v1 record")
        if record["type"] == "quiz_question":
            questions.append(record)
        elif record["type"] == "quiz_coverage":
            coverage[str(record.get("category"))] = record
    return questions, coverage


def _topics(root: Path) -> dict[str, dict[str, tuple[str, set[str]]]]:
    """book_id -> topic_id -> (chapter_id, concept tags), from the manifests."""
    books: dict[str, dict[str, tuple[str, set[str]]]] = {}
    for path in root.glob("manifest/*/*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        topics = books.setdefault(data["book"]["book_id"], {})
        for chapter in data.get("chapters", []):
            for topic in chapter.get("topics", []):
                tags = {concept["concept_tag"] for concept in topic.get("concepts", [])}
                topics[topic["topic_id"]] = (chapter["chapter_id"], tags)
    return books


def _read_map(root: Path, category: str) -> tuple[str | None, dict[str, dict]]:
    path = root / "nhprep" / "quiz-map" / f"{category}.json"
    if not path.exists():
        return None, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("category") != category or not isinstance(data.get("questions"), dict):
        raise ExportError(f"{path.name}: needs this category and a questions object")
    return data.get("book_id"), data["questions"]


def _record(question: dict, entry: dict, place: tuple[str, set[str]], qid: str) -> dict | str:
    """The bank line for one NHprep question, or why it can't be one."""
    options, index = question.get("options"), question.get("correctIndex")
    if question.get("format") != "single":
        return f"{qid}: format {question.get('format')!r} is not one-of-N (export v1 serves only 'single')"
    if not (isinstance(options, list) and isinstance(index, int) and 0 <= index < min(len(options), 8)):
        return f"{qid}: options or correctIndex out of range"
    if question.get("correctOption") != options[index]:
        return f"{qid}: correctOption is not the option at correctIndex"
    chapter_id, tags = place
    concept = entry.get("concept_tag", "core")
    if concept not in tags:
        return f"{qid}: concept {concept!r} is not one of {entry['topic_id']}'s"
    record = {
        "question_id": qid,
        "chapter_id": chapter_id,
        "topic_id": entry["topic_id"],
        "concept_tag": concept,
        "difficulty": entry["difficulty"],
        "format": "mcq",
        "competency": entry.get("competency", "knowledge_understanding"),
        "stem": question.get("question"),
        "options": list(options),
        "correct": [netsec.LETTERS[index]],
        "explanation": (question.get("explanation") or "").strip() or entry.get("explanation"),
        "marks": 1,
        "source": {"kind": "nhprep_quiz", "ref": f"nhprep:quiz/{question['category']}/{question['id']}"},
        "lesson_ref": None,
        "key_origin": "nhprep",
    }
    if entry.get("dispute"):
        record |= {"active": False, "inactive_reason": f"key disputed: {entry['dispute']}"[:300]}
    return record


def _ready(question: dict, entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("topic_id"), str)
        and entry.get("difficulty") in DIFFICULTIES
        and bool((question.get("explanation") or "").strip() or entry.get("explanation"))
    )


def _category(line: dict) -> str | None:
    source = line.get("source") or {}
    found = _REF.match(source.get("ref") or "") if source.get("kind") == "nhprep_quiz" else None
    return found.group(1) if found else None


def _same(old: dict, new: dict) -> bool:
    return all(old.get(name) == new.get(name) for name in netsec.FIELDS)


def _print(line: dict) -> str:
    return fingerprint("english", line["stem"], line["options"])


def _rewrite(
    root: Path, book_id: str, fresh: dict[str, dict], exported: set[str], complete: set[str], report: Report
) -> None:
    """Rewrite one book's content files: other lines in place, then this export's categories by question id."""
    categories = {_category(line) for line in fresh.values()} | complete
    files = sorted((root / "content" / book_id).glob("*/questions.jsonl"))
    chapters: dict[str, list[dict]] = {path.parent.name: [] for path in files}
    old: dict[str, dict] = {}
    for path in files:
        for text in path.read_text(encoding="utf-8").splitlines():
            if text.strip():
                line = json.loads(text)
                if _category(line) in categories:
                    old[line["question_id"]] = line
                else:
                    chapters[path.parent.name].append(line)
    printed = {_print(line): line["question_id"] for lines in chapters.values() for line in lines}
    for qid in sorted(set(fresh) | set(old)):
        line, previous = fresh.get(qid), old.get(qid)
        if line is None:  # only in the files: still served (unmapped this run), or gone from NHprep
            line = previous
            if qid not in exported and _category(line) in complete and line.get("active", True):
                line = line | {"active": False, "inactive_reason": RETIRED}
                report.retired += 1
        elif previous and "review" in previous and _same(previous, line):
            line = line | {"review": previous["review"]}
        twin = printed.setdefault(_print(line), qid)
        if twin != qid:
            report.duplicates.append(f"{qid} = {twin}")
            continue
        report.written += qid in fresh
        chapters.setdefault(line["chapter_id"], []).append(line)
    for chapter, lines in chapters.items():
        folder = root / "content" / book_id / chapter
        folder.mkdir(parents=True, exist_ok=True)
        body = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
        (folder / "questions.jsonl").write_text(body, encoding="utf-8")


def convert(export: Path, root: Path) -> Report:
    """Write an export's quiz questions into the bank's files under root (qbank/), then check every file."""
    questions, coverage = read_export(export)
    report = Report(coverage={c: {k: v.get(k) for k in ("attempts", "seen", "reported")} for c, v in coverage.items()})
    complete = {c for c, v in coverage.items() if isinstance(v.get("seen"), int) and v.get("seen") == v.get("reported")}
    books, maps = _topics(root), {}
    fresh: dict[str, dict[str, dict]] = {}  # book_id -> question_id -> line
    exported: set[str] = set()
    for question in questions:
        category = str(question.get("category"))
        if category not in maps:
            maps[category] = _read_map(root, category)
        book_id, entries = maps[category]
        qid = question_id(category, question.get("id"))
        if qid in exported:
            report.duplicates.append(f"{qid} exported twice")
            continue
        exported.add(qid)
        entry = entries.get(str(question.get("id")))
        if book_id is None or not _ready(question, entry):
            report.unmapped.append(qid)
            continue
        place = books.get(book_id, {}).get(entry["topic_id"])
        line = _record(question, entry, place, qid) if place else f"{qid}: {entry['topic_id']} is not a {book_id} topic"
        if isinstance(line, str):
            report.errors.append(line)
            continue
        fresh.setdefault(book_id, {})[qid] = line
    report.exported = len(exported)
    for book_id in sorted({b for b, _ in maps.values() if b} | set(fresh)):
        _rewrite(root, book_id, fresh.get(book_id, {}), exported, complete, report)
    report.errors += read_bank(root).errors
    return report
