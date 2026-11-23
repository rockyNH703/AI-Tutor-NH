"""Who may open which NH program (owner decisions D1, D2): this account's claims on the owner's invoice rows, read on
every request, under PROGRAMS_MODE. The one rule for every program route, program chat and conversation read; the web
app never decides it. Editing an invoice row (status, valid_until, program) changes access on the next request."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select

from app.db.models import Conversation, InvoiceClaim, NhInvoice, ProgramModule, ProgramModuleCourse
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


def open_course_ids(user_id: uuid.UUID):
    """The NHprep courses mapped to any month of the programs this account's invoices open (Videos & Links)."""
    return (
        select(ProgramModuleCourse.course_id)
        .join(ProgramModule, ProgramModule.id == ProgramModuleCourse.module_id)
        .where(ProgramModule.program_id.in_(open_program_ids(user_id)))
    )
