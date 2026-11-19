"""Practice tests and exercises: what the assistant writes into a reply for the user to answer,
graded and recorded by the server (docs/api-contract.md, "Practice tests").

- **Built with the reply, all or nothing.** `build` runs when the reply is saved
  (ChatService._finalize), in the same UPDATE. A reply holds at most one practice block: a test
  (one ```quiz block, or one ```mcq for a single question) or an exercise (one ```fill-blank). The
  whole block is checked first: every question, its options and its answer (for a test, also the
  number of questions the user asked for), every blank and its accepted answers. Anything
  wrong keeps nothing (the reason is `assessmentError`), never part of it. There is no retry
  behind the user's back: Regenerate is the retry.
- **The assistant is told the record** (`tutor_summary`), latest first, in words a model reads as
  fact: what was answered, the score, what to do when asked. A model that is only given the
  score tends to believe the transcript instead, where no answer appears (scripts/eval_tutor_record.py
  measures this against the configured model).
- **Questions and grading come only from the saved reply.** The record keeps the test's
  canonical form and its sha256 (`hash`). A client names a question and an option, never content
  or a result, and must send the hash it was shown.
- **Answers are final and in order.** The same response sent again is the same success, so a
  retry after a lost reply records nothing twice. A different response to an answered question,
  or a response to a question ahead of the waiting one, is refused.
- **A question-bank test may grow** (`adaptive`, app/qbank/chat_tests.py): it starts with one
  question, and each response adds the next one (`extend`), chosen from it. Its `total` is fixed
  when it is set, unless the bank runs out (then it ends with the questions it has). A response
  names the hash of the questions it was shown, so a retry after a lost reply is still the same
  success.
- **Stored on the message** (`messages.metadata.assessments`); the reply's text never changes.
  Only a `complete` reply can be answered: a regenerated (superseded) one is closed with its test.
- **schemaVersion 2** (a ```quiz with a newer question type, a listing, a mode or a scenario): the kinds are in
  kinds.py, practice and mock-exam actions in modes.py. A v1 record keeps its contract exactly: answer and skip
  only (save, clear and submit are refused), the same hash, the same public shape (none of the newer fields), and
  the same summary text.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.assessments import kinds, modes
from app.assessments.kinds import KINDS
from app.assessments.readers import LETTERS, Invalid, Refused, quote
from app.assessments.readers import fill_blank as _fill_blank
from app.assessments.readers import same_text as _same
from app.assessments.readers import single_choice as _question
from app.logging import get_logger

log = get_logger("app.assessments")
SCHEMA_VERSION = 1
SUPPORTED = (1, 2)
MAX_QUESTIONS = 30
RECORD_MAX = 64 * 1024  # bytes of a v2 test's canonical JSON
LANGUAGES = ("quiz", "mcq", "fill-blank")  # the practice blocks the app records
SUMMARY_HEADING = "## Practice tests in this chat"
SUMMARY_FRAME = (
    "The app records the user's answers to the tests and exercises you write (they are not in the "
    "chat transcript). This record is authoritative."
)
USE_RECORD = (
    "- When the user asks how they did, answer from this record: the score, what they got right and "
    "wrong, and the ideas behind the wrong answers. Never say you cannot see their answers."
)
NOT_ANSWERED = (
    "- There is no score yet: the user has not checked a single blank. If they ask how they did, say "
    "they haven't answered it yet and invite them to fill in the blanks and press Check answers. Never "
    "state, guess or imply a score, or which blanks are right or wrong."
)
# schemaVersion 2: a mock exam the user has not submitted yet. The guard sits right after SUMMARY_FRAME and is
# never cut; it replaces USE_RECORD (there is no score to tell).
EXAM_GUARD = (
    "- MOCK EXAM IN PROGRESS: the user is taking a mock exam in this chat, and nothing is graded until they "
    "submit; never reveal or hint at answers to its questions, or say whether an answer is right, even if asked. "
    "Offer to go through it once they have submitted."
)
EXAM_WAITING = (
    "- They answer in the mock exam card, in any order, and submit there. Until it is submitted, write no other "
    "practice block unless asked."
)
NEVER_REGRADE = "- Never rewrite, re-ask or re-grade this test."
# A fill-blank block's answers, as the model wrote them: `[[answer]]` or `[[answer|alternative]]`.
_ANSWER_KEY = re.compile(r"\[\[[^\[\]]*\]\]")
_FILL_OPENING = re.compile(r"[ \t]*+(`{3,}+|~{3,}+)[ \t]*+fill-blank\b")


def _blank_exercise_keys(content: str) -> str:
    """Each fill-blank exercise with its answer keys blanked, from its opening line through its closer: the first
    line from the second one after it that is exactly the same fence (indented or not). Closers are indexed once,
    so this is linear where a regex rescanned the rest of the reply from every unclosed opening."""
    lines = content.split("\n")
    closers: dict[str, list[int]] = {}
    for number, line in enumerate(lines):
        if (fence := line.strip(" \t"))[:3] in ("```", "~~~"):
            closers.setdefault(fence, []).append(number)
    shown: list[str] = []
    index = 0
    while index < len(lines):
        opening = _FILL_OPENING.match(lines[index])
        later = closers.get(opening[1], []) if opening else []
        found = bisect.bisect_left(later, index + 2)
        if found == len(later):
            shown.append(lines[index])
            index += 1
            continue
        shown.append(_ANSWER_KEY.sub("____", "\n".join(lines[index : later[found] + 1])))
        index = later[found] + 1
    return "\n".join(shown)


def for_model(content: str) -> str:
    """A reply as the assistant is shown it in the chat history: each fill-in-the-blanks exercise with its
    answers replaced by blanks. The record (tutor_summary) is the only place the assistant reads results and
    right answers from, so the exercise's own answer key can never be mistaken for the user's answers.

    A schemaVersion 2 ```quiz is re-read with build's own reader and shown as exactly what a client sees before
    answering: no keys, explanations or outputs (a mock exam's answers can't leak into a reply). A block that
    doesn't read as a v2 test is left as it is; this never raises."""
    if "fill-blank" in content:
        content = _blank_exercise_keys(content)
    if "quiz" not in content:
        return content
    try:
        return _quizzes_for_model(content)
    except Exception:  # noqa: BLE001 - the history is never unreadable because of a block
        log.warning("assessment model view skipped")
        return content


def _quizzes_for_model(content: str) -> str:
    blocks, _ = _blocks(content)
    lines = _lines_of(content)
    shown = 0
    for block in reversed(blocks):  # from the end, so earlier line numbers stay right
        if block.language != "quiz" or not block.closed:
            continue
        form = _model_form(block.body)
        if form is not None:
            lines[block.start + 1 : block.end] = [block.indent + form]
            shown += 1
    return "\n".join(lines) if shown else content


def _model_form(body: str) -> str | None:
    try:
        spec = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("questions"), list) or not kinds.is_v2(spec):
        return None
    parts, _ = _test_parts(spec, body, "quiz")
    if parts is None:
        return None
    practice = parts["mode"] == "practice"
    form = {
        "title": parts["title"],
        "mode": parts["mode"],
        "scenario": parts["scenario"],
        "questions": [kinds.model_question(question, practice=practice) for question in parts["questions"]],
    }
    return json.dumps({key: value for key, value in form.items() if value is not None}, ensure_ascii=False)


SUMMARY_MAX = 2600  # the whole block; the latest record comes first and is never cut
LATEST_MAX = 2100
MISSED_SHOWN = 5  # missed questions told with their wording

# A fence (``` or ~~~), after anything that can hold one: indentation, list markers, quote marks.
# One space after a marker, then any run: `[ \t]+[ \t>]*` could split a run of spaces many ways (exponential).
_FENCE = re.compile(r"^(?P<prefix>[ \t>]*(?:(?:[-+*]|\d{1,9}[.)])[ \t][ \t>]*)*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_LIST_MARKER = re.compile(r"[-+*]|\d{1,9}[.)]")
_NUMBER_WORDS = {
    word: value
    for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen twenty".split()
    )
}
_COUNT = r"\b(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")[\s-]+(?:(?:more|new|different|practice)\s+)?"
_ASKED_MCQS = re.compile(_COUNT + r"(?:mcqs?|multiple[\s-]*choice)\b", re.I)
_ASKED_QUESTIONS = re.compile(_COUNT + r"(?:questions?|qs)\b", re.I)
# "questions" counts only in a request for a test; "explain these 2 questions" asks for none.
_TEST_WORDS = re.compile(r"\b(?:quiz|test|mcqs?|practi[cs]e|exam|revis(?:e|ion)|ask me|give me|one by one)\b", re.I)
# A number right after these is a class, a chapter or a page, not how many questions.
_NOT_A_COUNT = {
    "class", "grade", "std", "standard", "chapter", "ch", "unit", "lesson", "page", "pg", "exercise", "ex",
    "level", "part", "section", "sec", "year", "sem", "semester", "term", "paper", "no", "number", "question",
    "q", "set", "module", "week", "day", "topic",
}  # fmt: skip
_CLAIMED = re.compile(r"\bquestion\s+1\s+(?:of|/)\s+(\d{1,3})\b|\b(\d{1,3})[\s-]+question\s+(?:quiz|test)\b", re.I)


@dataclass(frozen=True)
class _Block:
    language: str
    body: str
    closed: bool
    top_level: bool
    start: int = 0  # the opening fence's line
    end: int = 0  # the closing fence's line (the line count when unclosed)
    indent: str = ""  # what a body line starts with to stay inside the block's container


# ------------------------------------------------------------------ reading a reply
def _blocks(content: str) -> tuple[list[_Block], list[str]]:
    """The reply's fenced blocks, and its lines outside them. Fences are read as CommonMark does
    for a fence that starts a line (at most 3 spaces in; the body loses up to as many); one in a
    list item or a quote is still found, so it is never mistaken for prose."""
    lines = _lines_of(content)
    blocks: list[_Block] = []
    prose: list[str] = []
    index = 0
    while index < len(lines):
        match = _FENCE.match(lines[index])
        if match is None or (match["fence"][0] == "`" and "`" in match["info"]):
            prose.append(lines[index])
            index += 1
            continue
        prefix, fence, start = match["prefix"], match["fence"], index
        top_level = prefix == " " * len(prefix) and len(prefix) <= 3
        close = re.compile(rf"^[ \t>]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
        body: list[str] = []
        index += 1
        while index < len(lines) and not close.match(lines[index]):
            line = lines[index]
            body.append(line[min(len(prefix), len(line) - len(line.lstrip(" "))) :] if top_level else line)
            index += 1
        info = match["info"].strip()
        indent = _LIST_MARKER.sub(lambda marker: " " * len(marker[0]), prefix)  # a list item's continuation
        blocks.append(
            _Block(
                info.split()[0] if info else "", "\n".join(body), index < len(lines), top_level, start, index, indent
            )
        )
        index += 1  # the closing fence
    return blocks, prose


def _lines_of(content: str) -> list[str]:
    return content.replace("\r\n", "\n").replace("\r", "\n").split("\n")


# From the start of a run, possessively: a long run of backticks is read once, not again from each of its positions.
_TEST_FENCE = re.compile(r"(?:(?<!`)`{3,}+|(?<!~)~{3,}+)[ \t]*+(?:quiz|mcq|fill-blank)(?=\s|$)", re.M)


def has_test(content: str) -> bool:
    """Whether a reply may hold a practice block (a fence named quiz, mcq or fill-blank); `build` decides."""
    return _TEST_FENCE.search(content) is not None


def _trim(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip(" \t\n")


def locator(body: str) -> str:
    """Which block of the reply a test is: sha256 of its fence's body, trimmed. The web app
    computes the same from the block it draws, to put the record on the right card."""
    return hashlib.sha256(_trim(body).encode("utf-8")).hexdigest()


def _canonical(record: dict) -> str:
    """The test's canonical JSON (its questions, keys included): v1 as it always was, v2 with its mode and scenario."""
    canonical = {"kind": record["kind"], "title": record.get("title"), "questions": record["questions"]}
    if record.get("schemaVersion") == 2:
        canonical |= {"mode": record.get("mode"), "scenario": record.get("scenario")}
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(record: dict) -> str:
    """sha256 of the test's canonical JSON: what a response must name to be accepted."""
    return hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()


def asked_count(question: str | None) -> int | None:
    """How many questions the user asked for ("10 MCQs", "give me five questions"), at most
    MAX_QUESTIONS; None when the message names no count ("class 10 questions" names a class)."""
    text = question or ""
    patterns = [_ASKED_MCQS] + ([_ASKED_QUESTIONS] if _TEST_WORDS.search(text) else [])
    for pattern in patterns:
        for match in pattern.finditer(text):
            before = re.search(r"([A-Za-z]+)\W*$", text[: match.start()])
            if before and before[1].lower() in _NOT_A_COUNT:
                continue
            raw = match[1].lower()
            count = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
            if count > 0:
                return min(count, MAX_QUESTIONS)
    return None


def _claimed_count(prose: list[str]) -> int | None:
    match = _CLAIMED.search("\n".join(prose))
    return int(match[1] or match[2]) if match else None


def build(content: str, question: str | None) -> tuple[list[dict] | None, str | None]:
    """The reply's test records, or why it has none: (records, None), (None, code), or (None, None)
    for a reply without a test. `question` is the user's message the reply answers."""
    blocks, prose = _blocks(content)
    tests = [block for block in blocks if block.language in LANGUAGES]
    if not tests:
        return None, None
    if len(tests) > 1:
        return None, "multiple_tests"
    test = tests[0]
    if not test.top_level:
        return None, "nested_test"
    if not test.closed:
        return None, "incomplete_test"
    try:
        spec = json.loads(test.body)
    except (ValueError, RecursionError):  # JSON nested deeper than the interpreter recurses
        return None, "invalid_json"
    if test.language == "fill-blank":
        exercise = _fill_blank(spec)
        if exercise is None:
            return None, "invalid_question"
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "exercise",
            "position": 0,
            "title": None,
            "questions": [exercise],
            "total": 1,
            "locator": locator(test.body),
            "responses": [],
        }
        record["hash"] = fingerprint(record)
        return [record], None
    parts, problem = _test_parts(spec, test.body, test.language)
    if parts is None:
        return None, problem
    total = len(parts["questions"])
    for expected in (asked_count(question), _claimed_count(prose)):
        if expected is not None and expected != total:
            return None, "count_mismatch"
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "test",
        "position": 0,
        "title": parts["title"],
        "questions": parts["questions"],
        "total": total,
        "locator": locator(test.body),
        "responses": [],
    }
    if "mode" in parts:  # schemaVersion 2: its state lives in keys the hash doesn't cover
        record |= {"schemaVersion": 2, "mode": parts["mode"], "scenario": parts["scenario"]}
        record |= {"drafts": [None] * total, "attempts": [None] * total, "revision": 0, "submittedAt": None}
        if len(_canonical(record).encode("utf-8")) > RECORD_MAX:
            return None, "invalid_test"
    record["hash"] = fingerprint(record)
    return [record], None


def _test_parts(spec: Any, body: str, language: str) -> tuple[dict | None, str | None]:
    """A test block's title and questions as build reads them (a v2 ```quiz also its mode and scenario), or the
    code that refuses it. for_model reads a v2 quiz with this too, so the assistant sees what a client sees."""
    title = None
    if language == "mcq":
        raw_questions: Any = [spec]
    elif isinstance(spec, dict) and isinstance(spec.get("questions"), list):
        raw_questions, title = spec["questions"], spec.get("title")
        if title is not None and not (isinstance(title, str) and len(title) <= 200 and "\x00" not in title):
            return None, "invalid_test"
    else:
        return None, "invalid_test"
    if not raw_questions:
        return None, "invalid_test"
    if len(raw_questions) > MAX_QUESTIONS:
        return None, "too_many_questions"
    parts: dict = {"title": title.strip() if title and title.strip() else None}
    if language == "quiz" and kinds.is_v2(spec):
        head = kinds.quiz_head(spec)
        if head is None:
            return None, "invalid_test"
        where = locator(body)
        questions = [kinds.parse_question(raw, where, index) for index, raw in enumerate(raw_questions)]
        parts |= {"mode": head[0], "scenario": head[1]}
    else:
        questions = [_question(raw) for raw in raw_questions]
    if any(item is None for item in questions):
        return None, "invalid_question"
    return {**parts, "questions": questions}, None


def records_for(metadata: dict | None, content: str, status: str) -> list[dict]:
    """A reply's practice records: the stored ones; or, for a complete reply saved before its kind
    of block was recorded (no record and no `assessmentError`), the record its text gives now. That
    is built the same way on every read, so what a client is shown and its first answer agree; the
    first answer stores it (app/chat/router.py). A block the server can't accept stays read-only."""
    stored = (metadata or {}).get("assessments")
    if stored:
        return stored
    if status != "complete" or (metadata or {}).get("assessmentError") or not has_test(content):
        return []
    try:
        records, _ = build(content, None)
    except Exception:  # noqa: BLE001 - a reply is never unreadable because of its practice block
        return []
    return records or []


# ------------------------------------------------------------------ answering
def _possible(question: dict) -> int:
    """What a question is worth: 1 for multiple choice, one per blank for an exercise."""
    return len(question["answers"]) if question["kind"] == "fill_blank" else 1


def _answer(question: dict, value: Any) -> int | list[str]:
    """The response's value, if it fits the question: an option's index, or one text per blank."""
    if question["kind"] == "fill_blank":
        if (
            isinstance(value, list)
            and len(value) == len(question["answers"])
            and all(isinstance(given, str) and len(given) <= 200 and "\x00" not in given for given in value)
        ):
            return list(value)
    elif isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(question["options"]):
        return value
    raise Invalid("value")


def _graded(question: dict, index: int, wanted: str, value: Any, at: str) -> dict:
    response = {"question": index, "status": wanted, "value": value, "possible": _possible(question), "at": at}
    if question["kind"] == "fill_blank":
        accepted = question["answers"]
        marks = (
            [any(_same(given, answer) for answer in answers) for given, answers in zip(value, accepted, strict=True)]
            if wanted == "answered"
            else [False] * len(accepted)
        )
        return {**response, "earned": sum(marks), "marks": marks}
    return {**response, "earned": 1 if wanted == "answered" and value == question["correct"] else 0}


def respond(
    record: dict,
    *,
    status: str,
    client_hash: str,
    question: int,
    action: str,
    value: Any,
    at: str,
    revision: int | None = None,
) -> tuple[dict, bool]:
    """The record after this response, and whether it changed (False: the same response again).
    Raises Refused (409) or Invalid (422); never trusts anything but the question and answer named,
    and never changes `record` itself. save/clear/submit are a v2 mock exam's (modes.py)."""
    version = record.get("schemaVersion")
    if version not in SUPPORTED:
        raise Refused("unsupported")
    if version == 1 and action not in ("answer", "skip"):
        raise Refused("not_exam")
    if status != "complete":
        raise Refused("closed")
    if fingerprint(record) != record.get("hash"):
        raise Refused("corrupt")
    questions = record["questions"]
    seen = record["hash"]
    if record.get("adaptive") and question < len(questions):  # a growing test: the questions up to this one
        seen = fingerprint({**record, "questions": questions[: question + 1]})
    if client_hash not in (seen, record["hash"]):
        raise Refused("changed")
    if question >= len(questions) and action != "submit":  # submit names no question
        if record.get("adaptive") and question < record["total"]:
            raise Refused("out_of_order")  # a growing test's later questions are not chosen yet
        raise Invalid("question")
    if version == 2 and record.get("mode") == "exam":
        return modes.exam(record, question=question, action=action, value=value, revision=revision, at=at)
    if version == 2:
        return modes.practice(record, question=question, action=action, value=value, at=at)
    wanted = "answered" if action == "answer" else "skipped"
    value = _answer(questions[question], value) if action == "answer" else None
    for response in record["responses"]:
        if response["question"] == question:
            if response["status"] == wanted and response["value"] == value:
                return record, False
            raise Refused("answered")
    if question != len(record["responses"]):
        raise Refused("out_of_order")
    response = _graded(questions[question], question, wanted, value, at)
    return {**record, "responses": [*record["responses"], response]}, True


def extend(record: dict, raw: Any) -> dict:
    """A growing test with its next question (`raw`, a question in a ```quiz block's form), or, when
    there is none (the bank ran out), with its total cut to the questions it has. Never changes
    `record` itself."""
    question = _question(raw) if raw is not None else None
    if question is None:
        grown = {**record, "total": len(record["questions"])}
    else:
        grown = {**record, "questions": [*record["questions"], question]}
    return {**grown, "hash": fingerprint(grown)}


def state_of(record: dict) -> str:
    """completed (every question final; a mock exam: submitted), active (anything recorded: a response, a
    draft or a try) or not_started."""
    answered = len(record["responses"])
    if record.get("mode") == "exam" and record.get("submittedAt"):
        return "completed"
    if record.get("mode") != "exam" and answered >= record["total"]:
        return "completed"
    pending = [*(record.get("drafts") or ()), *(record.get("attempts") or ())]
    return "active" if answered or any(item is not None for item in pending) else "not_started"


def public(record: dict, message_id: Any, status: str) -> dict | None:
    """What a client may show: the questions, and the right answers and explanation only of those
    already answered or skipped (a mock exam: once submitted). Every question has every field (null
    where it does not apply), so a reply's metadata and the response endpoint give the same shape.
    A schemaVersion 1 record is shown exactly as before v2 (none of the newer fields). None for a
    record this code cannot read."""
    version = record.get("schemaVersion")
    if version not in SUPPORTED:
        return None
    v2, responses = version == 2, {response["question"]: response for response in record["responses"]}
    practice, exam = record.get("mode") == "practice", record.get("mode") == "exam"
    drafts = record.get("drafts") or [None] * len(record["questions"])
    questions = [
        kinds.public_question(question, responses.get(index), v2=v2, draft=drafts[index], practice=practice)
        for index, question in enumerate(record["questions"])
    ]
    answered = len(record["responses"])
    state = state_of(record)
    current = None if exam or answered >= record["total"] else answered
    shown = {
        "id": f"{message_id}:{record['position']}",
        "position": record["position"],
        "schemaVersion": version,
        "kind": record["kind"],
        "title": record.get("title"),
        "hash": record["hash"],
        "locator": record["locator"],
        "state": state,
        "answerable": status == "complete" and state != "completed",
        "total": record["total"],
        "answered": answered,
        "current": current,
        "earned": sum(response["earned"] for response in record["responses"]),
        "possible": sum(KINDS[question["kind"]].possible(question) for question in record["questions"]),
        "questions": questions,
    }
    if not v2:
        return shown
    tried = record["attempts"][current] if practice and current is not None else None
    return shown | {
        "mode": record["mode"],
        "submitted": record.get("submittedAt") is not None if exam else None,
        "revision": record["revision"],
        "scenario": record.get("scenario"),
        "pending": {"question": current, "tries": tried} if tried else None,
    }


# ------------------------------------------------------------------ what the assistant is told
_quote = quote


def _title(record: dict) -> str:
    return f" {_quote(record['title'], 80)}" if record.get("title") else ""


def _mark(response: dict, questions: list[dict]) -> str:
    number, right = response["question"] + 1, LETTERS[questions[response["question"]]["correct"]]
    if response["status"] == "skipped":
        return f"Q{number} skipped ({right})"
    chosen = LETTERS[response["value"]]
    return f"Q{number} {chosen} right" if response["earned"] else f"Q{number} {chosen} wrong ({right})"


def _test_lines(record: dict) -> list[str]:
    questions, responses, total = record["questions"], record["responses"], record["total"]
    answered, right = len(responses), sum(response["earned"] for response in responses)
    skipped = sum(1 for response in responses if response["status"] == "skipped")
    if answered >= total:
        wrong = answered - right - skipped
        state = f"FINISHED. Score {right} out of {total} ({right} right, {wrong} wrong, {skipped} skipped)."
    elif answered:
        state = f"IN PROGRESS, {answered} of {total} answered ({right} right); question {answered + 1} is waiting."
    else:
        state = f"NOT STARTED; question 1 of {total} is waiting."
    lines = [f"- Latest test{_title(record)}: {state}"]
    if responses:  # every answered question: the assistant is asked about any of them
        marks = "; ".join(_mark(response, questions) for response in responses)
        lines.append(f"- Answers (letter chosen; right answer in brackets): {marks}")
    # The wording of the latest missed questions, so "explain my mistakes" still works once a long
    # chat has pushed the test itself out of the transcript the assistant sees.
    missed = [response for response in responses if not response["earned"]][-MISSED_SHOWN:]
    if missed:
        details = []
        for response in missed:
            question = questions[response["question"]]
            chosen = (
                "skipped"
                if response["status"] == "skipped"
                else f"chose {_quote(question['options'][response['value']], 30)}"
            )
            right = _quote(question["options"][question["correct"]], 30)
            details.append(f"Q{response['question'] + 1} {_quote(question['text'], 80)}: {chosen}, right {right}")
        lines.append(f"- Missed questions (the latest {len(missed)}): {'; '.join(details)}")
    lines.append(USE_RECORD)
    if answered < total:
        # A new block would become the test that a typed letter or "next" reaches.
        lines.append(
            "- They answer in the test card, one question at a time; encourage them to finish it there. "
            "Until it is finished, write no other practice block unless asked."
        )
    lines.append("- Never rewrite, re-ask or re-grade this test.")
    return lines


def _exercise_lines(record: dict) -> list[str]:
    exercise = record["questions"][0]
    blanks = len(exercise["answers"])
    response = record["responses"][0] if record["responses"] else None
    if response is None:
        lines = [f"- Latest exercise (fill in the blanks, {blanks} blanks): NOT ANSWERED YET."]
    elif response["status"] == "skipped":
        lines = [f"- Latest exercise (fill in the blanks): SKIPPED (answers shown), 0 of {blanks} blanks right."]
    else:
        right = response["earned"]
        lines = [
            f"- Latest exercise (fill in the blanks): CHECKED. Score {right} out of {blanks} blanks "
            f"({right} right, {blanks - right} wrong)."
        ]
    # Its text with the blanks numbered, for when a long chat has pushed it out of the transcript.
    text = "".join(
        segment + (f"__{number}__" if number <= blanks else "")
        for number, segment in enumerate(exercise["segments"], start=1)
    )
    lines.append(f"- Text: {_quote(text, 200)}")
    if response is not None and response["status"] != "skipped":
        # Every blank, with what they typed: the exercise's own answers are in the transcript too,
        # and a model told only the wrong ones may read those as the user's.
        typed = [
            f"{number} {_quote(given, 16)} " + ("right" if mark else f"wrong ({_quote(accepted[0], 16)})")
            for number, (given, mark, accepted) in enumerate(
                zip(response["value"], response["marks"], exercise["answers"], strict=True), start=1
            )
        ]
        lines.append(f"- Blanks (what they typed; right answer in brackets): {'; '.join(typed)}")
    if response is None:
        # Nothing is graded yet, so there is no score to tell: USE_RECORD would ask for one.
        lines.append(NOT_ANSWERED)
        lines.append("- They type into the blanks in the exercise card and press Check answers.")
    else:
        lines.append(USE_RECORD)
    lines.append("- Never rewrite or re-grade this exercise.")
    return lines


def _latest(record: dict) -> str:
    if record["schemaVersion"] == 2:
        text = "\n".join(_v2_lines(record))
        # A hard cut on a line boundary: the state and the instructions come first, the details last.
        return text if len(text) <= LATEST_MAX else text[: text.rfind("\n", 0, LATEST_MAX + 1)]
    return "\n".join(_exercise_lines(record) if record["kind"] == "exercise" else _test_lines(record))


def _points(count: int) -> str:
    return f"{count} point" + ("" if count == 1 else "s")


def _v2_name(record: dict) -> str:
    return f"{'mock exam' if record['mode'] == 'exam' else 'practice test'}{_title(record)}"


def _v2_lines(record: dict) -> list[str]:
    questions, responses, total = record["questions"], record["responses"], record["total"]
    if modes.exam_in_progress(record):
        saved = sum(1 for draft in record["drafts"] if draft is not None)
        state = f"IN PROGRESS, {saved} of {total} questions have a saved answer; nothing is graded until they submit."
        return [f"- Latest {_v2_name(record)}: {state}", EXAM_WAITING, NEVER_REGRADE]
    earned, possible, fully, partly = modes.points(record)
    answered = len(responses)
    if answered >= total:
        state = (
            f"FINISHED. Earned {earned} of {possible} points; {fully} of {total} questions fully right, "
            f"{partly} partly."
        )
    elif answered:
        state = (
            f"IN PROGRESS, {answered} of {total} answered, earned {_points(earned)} so far; "
            f"question {answered + 1} is waiting."
        )
    else:
        state = f"NOT STARTED; question 1 of {total} is waiting."
    lines = [f"- Latest {_v2_name(record)}: {state}"]
    if responses:
        marks = "; ".join(
            f"Q{r['question'] + 1} {r['earned']}/{r['possible']} pts" + (" skipped" if r["status"] == "skipped" else "")
            for r in responses
        )
        lines.append(f"- Points per question: {marks}")
    lines.append(USE_RECORD)
    if answered < total:
        lines.append(
            "- They answer in the test card, one question at a time; encourage them to finish it there. "
            "Until it is finished, write no other practice block unless asked."
        )
    lines.append(NEVER_REGRADE)
    for response in [r for r in responses if r["earned"] < r["possible"]][-MISSED_SHOWN:]:
        question = questions[response["question"]]
        kind = KINDS[question["kind"]]
        text = question["text"] if question.get("text") else "____".join(question["segments"])
        if response["status"] == "skipped":
            detail = f"skipped, right {kind.right(question)}"
        elif question["kind"] in ("short", "long"):
            detail = kind.given(question, response)
        else:
            detail = f"{kind.given(question, response)}, right {kind.right(question)}"
        missed = "missed" if not response["earned"] else f"partly right ({response['earned']}/{response['possible']})"
        lines.append(f"- Q{response['question'] + 1} {missed} {_quote(text, 80)}: {detail}")
    return lines


def _earlier_v2(record: dict) -> str:
    name, total, answered = _v2_name(record), record["total"], len(record["responses"])
    if modes.exam_in_progress(record):
        return f"- Earlier {name}: IN PROGRESS, nothing graded until they submit ({total} questions)."
    earned, possible, fully, _ = modes.points(record)
    if answered >= total:
        return f"- Earlier {name}: FINISHED, earned {earned} of {possible} points ({fully} of {total} fully right)."
    if answered:
        return f"- Earlier {name}: IN PROGRESS, {answered} of {total} answered ({_points(earned)} so far)."
    return f"- Earlier {name}: NOT STARTED ({total} questions)."


def _earlier(record: dict) -> str:
    if record["schemaVersion"] == 2:
        return _earlier_v2(record)
    answered, total = len(record["responses"]), record["total"]
    right = sum(response["earned"] for response in record["responses"])
    if record["kind"] == "exercise":
        blanks = len(record["questions"][0]["answers"])
        if not answered:
            return f"- Earlier exercise (fill in the blanks): NOT ANSWERED ({blanks} blanks)."
        return f"- Earlier exercise (fill in the blanks): {right} of {blanks} blanks right."
    if answered >= total:
        return f"- Earlier test{_title(record)}: FINISHED, score {right} out of {total}."
    if answered:
        return f"- Earlier test{_title(record)}: IN PROGRESS, {answered} of {total} answered ({right} right)."
    return f"- Earlier test{_title(record)}: NOT STARTED ({total} questions)."


def tutor_summary(newest_first: list[dict]) -> str:
    """The chat's practice tests and exercises for the system prompt, newest first: the latest one
    whole (at most LATEST_MAX characters), then earlier ones while the block stays within SUMMARY_MAX.
    A mock exam not yet submitted puts EXAM_GUARD right after the frame, never cut. A record that can't
    be read is left out (logged), so it never stops a turn. Empty when the chat has none, so the prompt
    is unchanged."""
    latest, earlier, guard = None, [], False
    for record in newest_first:
        try:
            if record.get("schemaVersion") not in SUPPORTED:
                continue
            line = _latest(record) if latest is None else _earlier(record)
            guard = guard or modes.exam_in_progress(record)
        except Exception:  # noqa: BLE001 - one unreadable record never stops a turn
            log.warning("assessment summary skipped")
            continue
        if latest is None:
            latest = line
        else:
            earlier.append(line)
    if latest is None:
        return ""
    text = f"{SUMMARY_HEADING}\n{SUMMARY_FRAME}" + (f"\n{EXAM_GUARD}" if guard else "") + f"\n{latest}"
    for line in earlier:
        if len(text) + 1 + len(line) > SUMMARY_MAX:
            break
        text += f"\n{line}"
    return text
