"""Who may open which NH program (owner decisions D1, D2): this account's claims on the owner's invoice rows, read on
every request, under PROGRAMS_MODE. The one rule for every program route, program chat and conversation read; the web
app never decides it. Editing an invoice row (status, valid_until, program) changes access on the next request."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.students import is_nh_student
from app.db.models import (
    Conversation,
    InvoiceClaim,
    NhInvoice,
    Program,
    ProgramModule,
    ProgramModuleBook,
    ProgramModuleCourse,
    ProgramModuleDocument,
)
from app.settings import Settings


def programs_allow(user: Any, settings: Settings) -> bool:
    """PROGRAMS_MODE for this account (off nobody, qa the listed accounts, on everyone with a verified address)."""
    return bool(getattr(user, "email_verified", False)) and settings.programs_allow(getattr(user, "email", "") or "")


def open_program_ids(user_id: uuid.UUID):
    """The programs this account's claimed invoices open right now: active, and not past valid_until."""
    return (
        select(NhInvoice.program_id)
        .join(InvoiceClaim, InvoiceClaim.invoice_id == NhInvoice.id)
        .where(
            InvoiceClaim.user_id == user_id,
            NhInvoice.status == "active",
            or_(NhInvoice.valid_until.is_(None), NhInvoice.valid_until > func.clock_timestamp()),
        )
    )


def conversation_visible(user_id: uuid.UUID):
    """A conversation its owner may read or continue: an ordinary chat, or a program chat while its program is open."""
    return or_(Conversation.program_id.is_(None), Conversation.program_id.in_(open_program_ids(user_id)))


async def bank_scope(session: AsyncSession, user: Any, settings: Settings) -> tuple[bool, tuple[str, ...] | None]:
    """Who gets NHprep-derived bank questions outside a program chat (owner, 2026-11-24: Quiz me for every enrolled
    student): a NetworkersHome address every track; an account whose invoices open a program that program's tracks;
    anyone else none. (entitled, books): books None means every track."""
    if is_nh_student(user, settings):
        return True, None
    books = await enrolled_books(session, user, settings)
    return bool(books), books or None


async def enrolled_books(session: AsyncSession, user: Any, settings: Settings) -> tuple[str, ...]:
    """The bank tracks mapped to the programs this account's invoices open now; none while PROGRAMS_MODE shuts it
    out. Non-empty means an enrolled student: the web app's Quiz me button (owner, 2026-11-24: invoice holders only)."""
    if not programs_allow(user, settings):
        return ()
    return tuple(
        await session.scalars(
            select(ProgramModuleBook.book_id)
            .join(ProgramModule, ProgramModule.id == ProgramModuleBook.module_id)
            .where(ProgramModule.program_id.in_(open_program_ids(user.id)))
            .distinct()
            .order_by(ProgramModuleBook.book_id)
        )
    )


MONTHS_PER_DOCUMENT = 3
MONTH_ROWS_MAX = 5_000  # mapping rows read per turn; the owner maps a few hundred documents per program


async def enrolled_months(
    session: AsyncSession, user: Any, settings: Settings, program_id: uuid.UUID | None = None
) -> dict[str, str]:
    """Knowledge Library document id -> the months of this account's open programs it is mapped to, at most
    MONTHS_PER_DOCUMENT in program order ("Month 1: <title> | Month 2: <title>"), for the context block labels
    (app/rag/context.py). `program_id` keeps one program (a program's Q&A chat); otherwise every open program, each
    month prefixed with its program's title when more than one is open. {} while PROGRAMS_MODE shuts the account
    out, and for a program that is not open to it."""
    if not programs_allow(user, settings):
        return {}
    open_ids = open_program_ids(user.id)
    if program_id is None:
        count = await session.scalar(select(func.count(func.distinct(Program.id))).where(Program.id.in_(open_ids)))
        prefixed = (count or 0) > 1
    else:
        prefixed = False
    query = (
        select(ProgramModuleDocument.document_id, Program.title, ProgramModule.month, ProgramModule.title)
        .join(ProgramModule, ProgramModule.id == ProgramModuleDocument.module_id)
        .join(Program, Program.id == ProgramModule.program_id)
        .where(Program.id.in_(open_ids))
        .order_by(ProgramModuleDocument.document_id, Program.position, Program.id, ProgramModule.month)
        .limit(MONTH_ROWS_MAX)
    )
    if program_id is not None:
        query = query.where(Program.id == program_id)
    months: dict[str, list[str]] = {}
    for document_id, program, month, title in await session.execute(query):
        named = months.setdefault(str(document_id), [])
        if len(named) < MONTHS_PER_DOCUMENT:
            named.append(f"{program}, Month {month}: {title}" if prefixed else f"Month {month}: {title}")
    return {document_id: " | ".join(named) for document_id, named in months.items()}


def open_course_ids(user_id: uuid.UUID):
    """The NHprep courses mapped to any month of the programs this account's invoices open (Videos & Links)."""
    return (
        select(ProgramModuleCourse.course_id)
        .join(ProgramModule, ProgramModule.id == ProgramModuleCourse.module_id)
        .where(ProgramModule.program_id.in_(open_program_ids(user_id)))
    )
