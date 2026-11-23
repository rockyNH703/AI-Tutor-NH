"""NH programs: claims on the owner's invoices, the programs they open, each month's material, and a program chat's
scope (who may open what: app/programs/access.py). Logs never carry an invoice number."""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Conversation,
    InvoiceClaim,
    NhInvoice,
    NhprepLesson,
    Program,
    ProgramModule,
    ProgramModuleBook,
    ProgramModuleCourse,
    ProgramModuleDocument,
    QbBook,
    QbChapter,
    QbQuestion,
    QbTopic,
    User,
)
from app.programs.access import open_program_ids
from app.programs.prompt import ProgramContext
from app.rag.types import Curriculum

INVOICE_NUMBER = re.compile(r"^[A-Z0-9/_.-]{3,40}$")
MAX_LESSONS = 300
MAX_TOPICS = 300
SERVABLE = ("approved", "needs_review")


def normalize_invoice(raw: str) -> str | None:
    """The form invoice numbers are stored in: no spaces, upper case; anything else is no invoice."""
    number = "".join((raw or "").split()).upper()
    return number if INVOICE_NUMBER.fullmatch(number) else None


def invoice_fingerprint(number: str, secret: str) -> str:
    """What a log may say about an invoice number: 8 hex characters of its HMAC, never the number."""
    return hmac.new(secret.encode(), number.encode(), hashlib.sha256).hexdigest()[:8]


@dataclass(frozen=True)
class Claimed:
    status: str  # "claimed" (now) or "already" (by this account before)
    program: Program


async def claim(session: AsyncSession, user_id: uuid.UUID, raw: str) -> Claimed | None:
    """Claim an invoice for this account, or None (unknown, not active, expired, or another account's): one answer
    for all of those, so nobody learns which invoices exist. The invoice row is locked while it is claimed, and
    UNIQUE(invoice_id) makes a race between two accounts end with exactly one claim."""
    number = normalize_invoice(raw)
    if number is None:
        return None
    invoice = await session.scalar(
        select(NhInvoice)
        .where(
            NhInvoice.invoice_no == number,
            NhInvoice.status == "active",
            or_(NhInvoice.valid_until.is_(None), NhInvoice.valid_until > func.clock_timestamp()),
        )
        .with_for_update()
    )
    if invoice is None:
        return None
    program = await session.get(Program, invoice.program_id)
    holder = await session.scalar(select(InvoiceClaim.user_id).where(InvoiceClaim.invoice_id == invoice.id))
    if holder is None:
        inserted = await session.scalar(
            insert(InvoiceClaim)
            .values(user_id=user_id, invoice_id=invoice.id)
            .on_conflict_do_nothing(index_elements=["invoice_id"])
            .returning(InvoiceClaim.user_id)
        )
        if inserted is not None:
            return Claimed("claimed", program)
        holder = await session.scalar(select(InvoiceClaim.user_id).where(InvoiceClaim.invoice_id == invoice.id))
    return Claimed("already", program) if holder == user_id else None


async def open_programs(session: AsyncSession, user_id: uuid.UUID) -> list[Program]:
    return list(
        await session.scalars(
            select(Program).where(Program.id.in_(open_program_ids(user_id))).order_by(Program.position)
        )
    )


async def open_program(session: AsyncSession, user_id: uuid.UUID, slug: str) -> Program | None:
    return await session.scalar(select(Program).where(Program.slug == slug, Program.id.in_(open_program_ids(user_id))))


async def open_month(
    session: AsyncSession, user_id: uuid.UUID, slug: str, month: int
) -> tuple[Program, ProgramModule] | None:
    row = (
        await session.execute(
            select(Program, ProgramModule)
            .join(ProgramModule, ProgramModule.program_id == Program.id)
            .where(Program.slug == slug, ProgramModule.month == month, Program.id.in_(open_program_ids(user_id)))
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


@dataclass(frozen=True)
class Material:
    documents: int = 0
    lessons: int = 0
    questions: int = 0


async def months(
    session: AsyncSession, program_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[ProgramModule, Material]]]:
    """Every month of these programs with what is mapped to it: three grouped counts, never a query per month."""
    modules = list(
        await session.scalars(
            select(ProgramModule)
            .where(ProgramModule.program_id.in_(list(program_ids)))
            .order_by(ProgramModule.program_id, ProgramModule.month)
        )
    )
    ids = [module.id for module in modules]
    if not ids:
        return {}
    documents = dict(
        (
            await session.execute(
                select(ProgramModuleDocument.module_id, func.count())
                .where(ProgramModuleDocument.module_id.in_(ids))
                .group_by(ProgramModuleDocument.module_id)
            )
        ).all()
    )
    lessons = dict(
        (
            await session.execute(
                select(ProgramModuleCourse.module_id, func.count(NhprepLesson.id))
                .join(NhprepLesson, NhprepLesson.course_id == ProgramModuleCourse.course_id)
                .where(ProgramModuleCourse.module_id.in_(ids), NhprepLesson.removed_at.is_(None))
                .group_by(ProgramModuleCourse.module_id)
            )
        ).all()
    )
    questions = dict(
        (
            await session.execute(
                select(ProgramModuleBook.module_id, func.count())
                .join(QbChapter, QbChapter.book_id == ProgramModuleBook.book_id)
                .join(QbTopic, QbTopic.chapter_id == QbChapter.chapter_id)
                .join(QbQuestion, QbQuestion.topic_id == QbTopic.topic_id)
                .where(
                    ProgramModuleBook.module_id.in_(ids),
                    QbQuestion.is_active,
                    QbQuestion.review_status.in_(SERVABLE),
                )
                .group_by(ProgramModuleBook.module_id)
            )
        ).all()
    )
    found: dict[uuid.UUID, list[tuple[ProgramModule, Material]]] = {}
    for module in modules:
        material = Material(documents.get(module.id, 0), lessons.get(module.id, 0), questions.get(module.id, 0))
        found.setdefault(module.program_id, []).append((module, material))
    return found


async def month_detail(
    session: AsyncSession, module: ProgramModule
) -> tuple[list[uuid.UUID], list[tuple[str, str, str, str]], bool]:
    """The month's NHprep lessons (Videos & Links) and its servable question-bank topics (Quizzes)."""
    lesson_ids = list(
        await session.scalars(
            select(NhprepLesson.id)
            .join(ProgramModuleCourse, ProgramModuleCourse.course_id == NhprepLesson.course_id)
            .where(ProgramModuleCourse.module_id == module.id, NhprepLesson.removed_at.is_(None))
            .order_by(NhprepLesson.course_id, NhprepLesson.position, NhprepLesson.id)
            .limit(MAX_LESSONS)
        )
    )
    servable = (
        select(QbQuestion.topic_id)
        .where(QbQuestion.topic_id == QbTopic.topic_id, QbQuestion.is_active, QbQuestion.review_status.in_(SERVABLE))
        .exists()
    )
    topics = [
        (row[0], row[1], row[2], row[3])
        for row in (
            await session.execute(
                select(QbTopic.topic_id, QbTopic.title, QbChapter.title, QbBook.title)
                .join(QbChapter, QbChapter.chapter_id == QbTopic.chapter_id)
                .join(QbBook, QbBook.book_id == QbChapter.book_id)
                .join(ProgramModuleBook, ProgramModuleBook.book_id == QbBook.book_id)
                .where(ProgramModuleBook.module_id == module.id, servable)
                .order_by(QbBook.title, QbChapter.chapter_id, QbTopic.topic_id)
                .limit(MAX_TOPICS)
            )
        ).all()
    ]
    return lesson_ids, topics, bool(topics)


@dataclass(frozen=True)
class ProgramTurn:
    """A program chat's turn: what its tutor is told, and what it may search, quiz and show, all from the stored
    conversation and the owner's mapping (never from the request)."""

    context: ProgramContext
    curriculum: Curriculum
    books: tuple[str, ...]
    question: str = ""  # the student's message this turn (the tutor block's title check)

    @property
    def courses(self) -> tuple[uuid.UUID, ...]:
        return self.curriculum.courses

    def ref(self) -> dict:
        context = self.context
        return {
            "slug": context.slug,
            "title": context.title,
            "month": context.month,
            "month_title": context.month_title,
            "mode": context.mode,
        }


async def chat_turn(session: AsyncSession, conversation: Conversation) -> ProgramTurn:
    """The scope of a program chat: its month (or, for QnA, every month of its program)."""
    program = await session.get(Program, conversation.program_id)
    modules = list(
        await session.scalars(
            select(ProgramModule)
            .where(ProgramModule.program_id == conversation.program_id)
            .order_by(ProgramModule.month)
        )
    )
    module = next((item for item in modules if item.id == conversation.module_id), None)
    scope = [module.id] if module is not None else [item.id for item in modules]

    async def mapped(column, table) -> tuple:
        return tuple(
            await session.scalars(select(column).where(table.module_id.in_(scope)).distinct().order_by(column))
        )

    documents = await mapped(ProgramModuleDocument.document_id, ProgramModuleDocument)
    courses = await mapped(ProgramModuleCourse.course_id, ProgramModuleCourse)
    books = await mapped(ProgramModuleBook.book_id, ProgramModuleBook)
    context = ProgramContext(
        program_id=program.id,
        slug=program.slug,
        title=program.title,
        month=module.month if module is not None else None,
        month_title=module.title if module is not None else None,
        mode=conversation.mode or "tutor",
        months=tuple((item.month, item.title) for item in modules),
    )
    return ProgramTurn(context, Curriculum(documents=documents, courses=courses), books)


async def conversation_ref(session: AsyncSession, conversation: Conversation) -> dict | None:
    """How a conversation names its program and month to its reader (ConversationOut.program); None for other chats."""
    if conversation.program_id is None:
        return None
    program = await session.get(Program, conversation.program_id)
    module = await session.get(ProgramModule, conversation.module_id) if conversation.module_id else None
    return {
        "slug": program.slug,
        "title": program.title,
        "month": module.month if module is not None else None,
        "month_title": module.title if module is not None else None,
        "mode": conversation.mode or "tutor",
    }


async def dismiss_prompt(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(update(User).where(User.id == user_id).values(invoice_prompt_done_at=func.clock_timestamp()))
