"""Practice exams: a program month's certification questions as one timed paper (session 62 plan, Task 1C).

No new engine: the questions are picked by the bank's own picker (bank_tests.pick_batch, one track = one Pool), held
in a `test` practice set served whole at the start, and graded by the ledger's trigger (bank_tests.answer_keys /
chat_tests.answer) at the submit, each answer followed by record_practice, as Friend Zone's completion does.

- Access: the month must be open to the caller to start; every later read or write re-checks that the exam's program
  is still open (open_program_ids), unless the exam is already submitted (its owner may always read the result).
- Time: the database clock. `ends_at` = start + minutes; saves are taken until the cutoff (ends_at + ALLOWANCE). The
  first read or write at or after the cutoff submits the saved answers, stamped at ends_at (no timer process).
- Concurrency: every read and write locks the exam row; saves are compare-and-set on `rev`; a submit grades once,
  under the lock, and a second (or concurrent) submit returns the stored result and writes nothing.
- Secrecy: keys, explanations and difficulty appear only in the result, once submitted.
Logs (the router's) carry ids and counts only, never an answer or a key."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    NhInvoice,
    PracticeExam,
    Program,
    ProgramModuleBook,
    QbBook,
    QbChapter,
    QbQuestion,
    QbSet,
    QbSetItem,
    QbTopic,
)
from app.errors import Conflict, NotFound, ValidationFailed
from app.exams.schemas import (
    MAX_QUESTIONS,
    Available,
    Durations,
    ExamOptionsOut,
    ExamOut,
    ExamStartRequest,
    OptionOut,
    PracticeExamResponseItem,
    PracticeExamSubmitRequest,
    QuestionOut,
    QuizOptionOut,
    ResponseOut,
    ResultOut,
    Rules,
)
from app.programs import service as programs
from app.programs.access import open_program_ids
from app.programs.schemas import ProgramRef
from app.progress.learning import record_practice
from app.qbank import bank_tests, chat_tests
from app.qbank.selection import SERVABLE, Pool

ALLOWANCE = timedelta(seconds=2)  # the network allowance after the deadline, as Friend Zone's (D8)
LENGTHS = [5, 10, 15, 20, 25, 30]
SECONDS_PER_QUESTION = 60
MULTIPLIERS = [1, 1.5, 2]
MAX_MINUTES = 60
LEVELS = bank_tests.LEVELS


async def clock(db: AsyncSession) -> datetime:
    return await db.scalar(select(func.clock_timestamp()))


def cutoff(exam: PracticeExam) -> datetime:
    return exam.ends_at + ALLOWANCE


def _conflict(message: str, code: str, current: dict | None = None) -> Conflict:
    error = Conflict(message, code=code)
    error.current = current  # the error envelope's `current` (app/errors.py)
    return error


def stale(exam: PracticeExam) -> Conflict:
    return _conflict("These answers were changed elsewhere. Reload and try again.", "stale", {"rev": exam.rev})


def closed() -> Conflict:
    return _conflict("This test is over: answers can no longer be changed.", "closed")


# ------------------------------------------------------------------ setup
async def exam_options(db: AsyncSession, user_id: uuid.UUID, slug: str, month: int) -> ExamOptionsOut | None:
    """What a test can be made of in this month: its quizzes (in the month's order) with the servable questions per
    difficulty, from one GROUP BY. None when the month is not open to the caller. Every NHprep-derived question is
    counted: an account this month is open to is an enrolled student of its program (access.bank_scope)."""
    found = await programs.open_month(db, user_id, slug, month)
    if found is None:
        return None
    program, module = found
    _, topics, _ = await programs.month_detail(db, module)
    rows = await db.execute(
        select(QbQuestion.topic_id, QbQuestion.difficulty, func.count())
        .join(ProgramModuleBook, ProgramModuleBook.book_id == QbQuestion.book_id)
        .where(ProgramModuleBook.module_id == module.id, QbQuestion.is_active, QbQuestion.review_status.in_(SERVABLE))
        .group_by(QbQuestion.topic_id, QbQuestion.difficulty)
    )
    counts: dict[str, dict[str, int]] = {}
    for topic_id, difficulty, count in rows.all():
        counts.setdefault(topic_id, dict.fromkeys(LEVELS, 0))[difficulty] = count
    quizzes = []
    for topic_id, title, chapter, book in topics:
        levels = counts.get(topic_id, dict.fromkeys(LEVELS, 0))
        available = Available(**levels, total=sum(levels.values()))
        quizzes.append(QuizOptionOut(topic_id=topic_id, title=title, chapter=chapter, book=book, available=available))
    return ExamOptionsOut(
        program=ProgramRef(slug=program.slug, title=program.title),
        month=module.month,
        quizzes=quizzes,
        lengths=LENGTHS,
        max_questions=MAX_QUESTIONS,
        durations=Durations(
            seconds_per_question=SECONDS_PER_QUESTION, multipliers=MULTIPLIERS, max_minutes=MAX_MINUTES
        ),
        rules=Rules(),
    )


async def _by_request(db: AsyncSession, user_id: uuid.UUID, request_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(
        select(PracticeExam.id).where(PracticeExam.user_id == user_id, PracticeExam.request_id == request_id)
    )


async def start(db: AsyncSession, user_id: uuid.UUID, body: ExamStartRequest) -> tuple[uuid.UUID, bool]:
    """(exam id, created now). The same request id again is the same exam. 404 unless the month is open to the
    caller; 422 `unknown_quiz` for a quiz that is not one of the month's, `one_track` for quizzes of two tracks; 409
    `not_enough_questions` when the pool has fewer than `length` (nothing is written). Never commits."""
    found = await programs.open_month(db, user_id, body.program, body.month)
    if found is None:
        raise NotFound()
    program, module = found
    existing = await _by_request(db, user_id, body.request_id)
    if existing is not None:
        return existing, False
    topic_ids = tuple(dict.fromkeys(body.topic_ids))
    tracks = (
        await db.execute(
            select(QbTopic.topic_id, QbBook.book_id, QbBook.language, QbBook.class_level, QbBook.subject_code)
            .join(QbChapter, QbChapter.chapter_id == QbTopic.chapter_id)
            .join(QbBook, QbBook.book_id == QbChapter.book_id)
            .join(ProgramModuleBook, ProgramModuleBook.book_id == QbBook.book_id)
            .where(ProgramModuleBook.module_id == module.id, QbTopic.topic_id.in_(topic_ids))
        )
    ).all()
    if len(tracks) != len(topic_ids):
        raise ValidationFailed("A chosen quiz is not in this month.", code="unknown_quiz")
    if len({row.book_id for row in tracks}) > 1:
        raise ValidationFailed("A test takes quizzes of one track only.", code="one_track")
    book = tracks[0]
    pool = Pool(book.language, book.class_level, book.subject_code, topic_ids, entitled=True)
    level = None if body.difficulty == "mixed" else body.difficulty
    picked = await bank_tests.pick_batch(db, user_id, pool, str(body.request_id), body.length, level)
    if len(picked) < body.length:  # pick_batch stops only when the pool has nothing left
        raise _conflict(
            "There are not enough questions for this test.",
            "not_enough_questions",
            {"available": len(picked), "needed": body.length},
        )
    filters = {"practiceExam": True, "program": program.slug, "month": module.month, "bookId": book.book_id}
    set_id = await db.scalar(
        insert(QbSet)
        .values(
            user_id=user_id,
            request_id=body.request_id,
            mode="test",
            filters={**filters, "topicIds": list(topic_ids), "difficulty": body.difficulty},
            requested=body.length,
            served=body.length,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "request_id"])
        .returning(QbSet.id)
    )
    if set_id is None:  # a concurrent start with this request id won (or the id named another set of theirs)
        await db.rollback()
        existing = await _by_request(db, user_id, body.request_id)
        if existing is not None:
            return existing, False
        raise _conflict("This request id was already used.", "request_reused")
    await db.execute(
        insert(QbSetItem),
        [
            {
                "set_id": set_id,
                "position": n,
                "user_id": user_id,
                "question_id": row.question_id,
                "topic_id": row.topic_id,
            }
            for n, row in enumerate(picked, 1)
        ],
    )
    now = await clock(db)
    exam_id = await db.scalar(
        insert(PracticeExam)
        .values(
            user_id=user_id,
            request_id=body.request_id,
            set_id=set_id,
            program_id=program.id,
            month=module.month,
            topic_ids=list(topic_ids),
            difficulty=body.difficulty,
            length=body.length,
            minutes=body.minutes,
            paper=[row.question_id for row in picked],
            started_at=now,
            ends_at=now + timedelta(minutes=body.minutes),
        )
        .returning(PracticeExam.id)
    )
    return exam_id, True


# ------------------------------------------------------------------ loading (every route after the start)
async def _still_open(db: AsyncSession, user_id: uuid.UUID, program_id: uuid.UUID) -> bool:
    return bool(await db.scalar(select(open_program_ids(user_id).where(NhInvoice.program_id == program_id).exists())))


async def load(db: AsyncSession, user_id: uuid.UUID, exam_id: uuid.UUID) -> PracticeExam:
    """The caller's exam, its row locked and brought up to the clock: past the cutoff it is submitted now (the
    saved answers, at the deadline). 404 for another account's, and for an unsubmitted one whose program is no
    longer open to the caller. The caller commits (also before raising, to keep an auto-submit)."""
    exam = await db.scalar(
        select(PracticeExam)
        .where(PracticeExam.id == exam_id, PracticeExam.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if exam is None:
        raise NotFound()
    if exam.submitted_at is None:
        if not await _still_open(db, user_id, exam.program_id):
            raise NotFound()
        if await clock(db) >= cutoff(exam):
            await finish(db, exam, auto=True)
    return exam


async def _paper(db: AsyncSession, exam: PracticeExam) -> list[tuple[QbQuestion, str, str]]:
    """The exam's questions in order, each with its quiz (QbTopic) and topic (QbChapter) titles."""
    rows = await db.execute(
        select(QbQuestion, QbTopic.title, QbChapter.title)
        .join(QbTopic, QbTopic.topic_id == QbQuestion.topic_id)
        .join(QbChapter, QbChapter.chapter_id == QbQuestion.chapter_id)
        .where(QbQuestion.question_id.in_(exam.paper))
    )
    found = {question.question_id: (question, quiz, topic) for question, quiz, topic in rows.all()}
    return [found[question_id] for question_id in exam.paper]


def _letters(question: QbQuestion) -> str:
    return bank_tests.LETTERS[: len(bank_tests.options_of(question))]


def _merged(
    exam: PracticeExam, items: list[PracticeExamResponseItem], paper: list[tuple[QbQuestion, str, str]]
) -> list[dict]:
    """The drafts with these items applied (each replaces its question's state), ordered by index. 422 for a
    question not in the paper, a letter not among its options, a repeated letter, or two on a single answer."""
    drafts = {draft["index"]: draft for draft in exam.responses}
    for item in items:
        if item.index >= len(paper):
            raise ValidationFailed("There is no such question in this test.")
        question = paper[item.index][0]
        multi = question.question_format == "multi_select"
        letters = _letters(question)
        if (
            len(set(item.keys)) != len(item.keys)
            or any(key not in letters for key in item.keys)
            or (not multi and len(item.keys) > 1)
        ):
            raise ValidationFailed("That answer does not fit this question.")
        drafts[item.index] = {
            "index": item.index,
            "keys": sorted(item.keys),
            "marked": item.marked,
            "visited": item.visited,
        }
    return [drafts[index] for index in sorted(drafts)]


# ------------------------------------------------------------------ saving and submitting
async def save(db: AsyncSession, exam: PracticeExam, base_rev: int, items: list[PracticeExamResponseItem]) -> None:
    """Apply drafts when `base_rev` is the exam's revision: 409 `closed` once submitted (load submits past the
    cutoff), 409 `stale` with the current revision otherwise. Never commits."""
    if exam.submitted_at is not None:
        raise closed()
    if base_rev != exam.rev:
        raise stale(exam)
    exam.responses = _merged(exam, items, await _paper(db, exam))
    exam.rev += 1
    await db.flush()


async def submit(db: AsyncSession, exam: PracticeExam, body: PracticeExamSubmitRequest) -> list[int]:
    """Submit with any unsent items as final values; returns the items not applied (late). An exam already
    submitted (or submitted by load, past the cutoff) applies nothing and grades nothing again. Never commits."""
    sent = [item.index for item in body.items]
    if exam.submitted_at is not None:
        return sent
    if body.base_rev is not None and body.base_rev != exam.rev:
        raise stale(exam)
    if body.items:
        exam.responses = _merged(exam, body.items, await _paper(db, exam))
    await finish(db, exam, auto=body.auto)
    return []


async def finish(db: AsyncSession, exam: PracticeExam, *, auto: bool) -> None:
    """Grade once (the caller holds the row lock and has seen submitted_at empty): each saved answer into the
    practice set, ONE row at a time and each followed by record_practice (it counts the day's answers); a question
    whose content changed since it was served is void (not graded, not counted). Then the result, and the set is
    closed. An auto-submit is stamped at the deadline; an explicit one at the clock, never after the deadline."""
    now = await clock(db)
    paper = await _paper(db, exam)
    served = dict(
        (await db.execute(select(QbSetItem.position, QbSetItem.served_hash).where(QbSetItem.set_id == exam.set_id)))
        .tuples()
        .all()
    )
    answers = {draft["index"]: draft["keys"] for draft in exam.responses if draft["keys"]}
    void = set()
    for index, (question, _, _) in enumerate(paper):
        position = index + 1
        if served[position] is not None and served[position] != question.content_hash:
            void.add(index)
            continue
        keys = answers.get(index)
        if not keys:
            continue
        if question.correct_keys is None:  # the A-D shape takes one letter
            graded = await chat_tests.answer(db, exam.user_id, exam.set_id, position, keys[0])
        else:
            graded = await bank_tests.answer_keys(db, exam.user_id, exam.set_id, position, keys)
        if graded is not None:
            await record_practice(db, exam.user_id, now, graded[0])
    marks = dict(
        (await db.execute(select(QbSetItem.position, QbSetItem.is_correct).where(QbSetItem.set_id == exam.set_id)))
        .tuples()
        .all()
    )
    exam.submitted_at = exam.ends_at if auto else min(now, exam.ends_at)
    exam.auto_submitted = auto
    exam.result = _result(exam, paper, answers, marks, void)
    exam.rev += 1
    await bank_tests.close(db, exam.user_id, exam.set_id)
    await db.flush()


def _result(
    exam: PracticeExam,
    paper: list[tuple[QbQuestion, str, str]],
    answers: dict[int, list[str]],
    marks: dict[int, bool | None],
    void: set[int],
) -> dict[str, Any]:
    review, topics = [], {}
    for index, (question, _, topic) in enumerate(paper):
        keys = answers.get(index, [])
        if index in void:
            review.append({"index": index, "keys": keys, "correct_keys": [], "explanation": "", "outcome": "void"})
            continue
        right = marks.get(index + 1)
        outcome = "unanswered" if right is None else "correct" if right else "incorrect"
        row = topics.setdefault(topic, {"topic": topic, "correct": 0, "total": 0})
        row["total"] += 1
        row["correct"] += outcome == "correct"
        correct_keys = sorted(bank_tests.keys_of(question))
        review.append(
            {
                "index": index,
                "keys": keys,
                "correct_keys": correct_keys,
                "explanation": question.explanation,
                "outcome": outcome,
            }
        )
    count = {name: sum(entry["outcome"] == name for entry in review) for name in ("correct", "incorrect", "unanswered")}
    total = len(paper) - len(void)
    return {
        "score": count["correct"],  # 1 mark each, no negative marking
        "total": total,
        "percentage": round(100 * count["correct"] / total) if total else 0,
        **count,
        "auto_submitted": exam.auto_submitted,
        "submitted_at": exam.submitted_at.isoformat(),
        "by_topic": list(topics.values()),
        "review": review,
    }


# ------------------------------------------------------------------ the view
async def _title(db: AsyncSession, exam: PracticeExam) -> str:
    """The topic (chapter) when every quiz is from one, else "N quizzes"."""
    chapters = (
        await db.execute(
            select(QbChapter.chapter_id, QbChapter.title)
            .join(QbTopic, QbTopic.chapter_id == QbChapter.chapter_id)
            .where(QbTopic.topic_id.in_(exam.topic_ids))
            .distinct()
        )
    ).all()
    return chapters[0].title if len(chapters) == 1 else f"{len(exam.topic_ids)} quizzes"


async def view(db: AsyncSession, exam: PracticeExam) -> ExamOut:
    """The exam as its owner sees it. Questions carry their text and options only (never a key, an explanation or a
    difficulty); the review comes with the result, once submitted."""
    program = await db.get(Program, exam.program_id)
    questions = [
        QuestionOut(
            index=index,
            stem=question.stem,
            options=[
                OptionOut(key=key, text=text)
                for key, text in zip(_letters(question), bank_tests.options_of(question), strict=True)
            ],
            multi=question.question_format == "multi_select",
            quiz=quiz,
            topic=topic,
        )
        for index, (question, quiz, topic) in enumerate(await _paper(db, exam))
    ]
    return ExamOut(
        exam_id=exam.id,
        status="submitted" if exam.submitted_at is not None else "in_progress",
        program=ProgramRef(slug=program.slug, title=program.title),
        month=exam.month,
        title=await _title(db, exam),
        difficulty=exam.difficulty,
        length=exam.length,
        minutes=exam.minutes,
        started_at=exam.started_at,
        ends_at=exam.ends_at,
        cutoff_at=cutoff(exam),
        server_time=await clock(db),
        rev=exam.rev,
        rules=Rules(),
        questions=questions,
        responses=[ResponseOut(**draft) for draft in exam.responses],
        result=ResultOut.model_validate(exam.result) if exam.result is not None else None,
    )
