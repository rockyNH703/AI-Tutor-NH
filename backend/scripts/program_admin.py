"""Operator command for NH programs (app/programs): the owner's invoices, the content mapping, claims, an inventory.

    python scripts/program_admin.py inventory
    python scripts/program_admin.py mapping <mapping.json> [--apply]
    python scripts/program_admin.py invoices <invoices.csv> [--apply]
    python scripts/program_admin.py invoice <number> [--status S] [--program SLUG] [--valid-until ISO|none] [--apply]
    python scripts/program_admin.py dummy-invoices [--apply]
    python scripts/program_admin.py release <number> [--apply]

Run on the host with the API's settings (systemd-run -p EnvironmentFile=/etc/netsecai/api.env, like
knowledge_admin.py). Everything is a dry run until --apply. Output: counts, ids and titles, never an invoice number,
except the dummy invoices this command creates for testing (printed once, to the operator).

- inventory (read-only): every Knowledge Library document of the shared library (source, title, passages, embedded),
  every question-bank track (servable questions) and NHprep course (lessons, search chunks), each with the months
  it is mapped to (slug:month).
- mapping: backend/curriculum/mapping.json. Its sets name library documents (source + title), question-bank tracks
  (book id) and NHprep courses (slug); its months list sets. Refuses an unknown item and any document that is not
  in an approved, ownerless global source. Replaces each listed month's mapping in one transaction; months the file
  does not list are left as they are.
- invoices: a CSV with invoice_no, program (slug), status (default active), valid_until (ISO date: valid through that
  day, UTC; or empty). Upserts by number; never deletes. Prints counts and the line numbers of rejected rows.
- invoice: change one invoice row (the owner's record): status, program, valid_until.
- dummy-invoices: one unguessable NHTEST-... invoice per program, for headed tests (owner decision D8).
- release: remove the claim on one invoice (only on the owner's instruction), so it can be entered again.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import secrets
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (  # noqa: E402
    INVOICE_STATUSES,
    Document,
    DocumentRevision,
    InvoiceClaim,
    KnowledgePassageEmbedding,
    KnowledgePassageOccurrence,
    KnowledgeSource,
    NhInvoice,
    NhprepCourse,
    NhprepLesson,
    NhprepSearchChunk,
    Program,
    ProgramModule,
    ProgramModuleBook,
    ProgramModuleCourse,
    ProgramModuleDocument,
    QbBook,
    QbChapter,
    QbQuestion,
    QbTopic,
)
from app.db.session import Database  # noqa: E402
from app.programs.service import normalize_invoice  # noqa: E402
from app.settings import Settings  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("inventory")
    mapping = actions.add_parser("mapping")
    mapping.add_argument("file", type=Path)
    mapping.add_argument("--apply", action="store_true")
    invoices = actions.add_parser("invoices")
    invoices.add_argument("file", type=Path)
    invoices.add_argument("--apply", action="store_true")
    invoice = actions.add_parser("invoice")
    invoice.add_argument("number")
    invoice.add_argument("--status", choices=INVOICE_STATUSES)
    invoice.add_argument("--program")
    invoice.add_argument("--valid-until")
    invoice.add_argument("--apply", action="store_true")
    dummy = actions.add_parser("dummy-invoices")
    dummy.add_argument("--apply", action="store_true")
    release = actions.add_parser("release")
    release.add_argument("number")
    release.add_argument("--apply", action="store_true")
    return parser


def _until(value: str | None) -> datetime | None:
    """An ISO date means valid through that day (UTC); a datetime is taken as given; empty or "none" is no end."""
    if value is None or not value.strip() or value.strip().lower() == "none":
        return None
    text = value.strip()
    if len(text) == 10:
        return datetime.combine(date.fromisoformat(text) + timedelta(days=1), time.min, tzinfo=UTC)
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def _modules(db) -> dict[tuple[str, int], object]:
    rows = await db.execute(
        select(Program.slug, ProgramModule).join(ProgramModule, ProgramModule.program_id == Program.id)
    )
    return {(slug, module.month): module for slug, module in rows.all()}


# ---------------------------------------------------------------- inventory
async def inventory(db) -> int:
    where = defaultdict(list)
    for table, column in (
        (ProgramModuleDocument, ProgramModuleDocument.document_id),
        (ProgramModuleBook, ProgramModuleBook.book_id),
        (ProgramModuleCourse, ProgramModuleCourse.course_id),
    ):
        rows = await db.execute(
            select(column, Program.slug, ProgramModule.month)
            .join(ProgramModule, ProgramModule.id == table.module_id)
            .join(Program, Program.id == ProgramModule.program_id)
        )
        for item, slug, month in rows.all():
            where[str(item)].append(f"{slug}:{month}")
    published = (
        select(func.count(func.distinct(KnowledgePassageOccurrence.passage_id)))
        .join(DocumentRevision, DocumentRevision.id == KnowledgePassageOccurrence.revision_id)
        .where(KnowledgePassageOccurrence.document_id == Document.id, DocumentRevision.status == "published")
        .scalar_subquery()
    )
    embedded = (
        select(func.count(func.distinct(KnowledgePassageEmbedding.passage_id)))
        .join(
            KnowledgePassageOccurrence,
            KnowledgePassageOccurrence.passage_id == KnowledgePassageEmbedding.passage_id,
        )
        .where(KnowledgePassageOccurrence.document_id == Document.id)
        .scalar_subquery()
    )
    documents = await db.execute(
        select(KnowledgeSource.name, Document.id, Document.title, published, embedded)
        .join(KnowledgeSource, KnowledgeSource.id == Document.source_id)
        .where(KnowledgeSource.owner_user_id.is_(None))
        .order_by(KnowledgeSource.name, Document.title)
    )
    for source, document_id, title, passages, vectors in documents.all():
        line = {"source": source, "title": title, "passages": passages, "embedded": vectors}
        print(json.dumps({"document": line, "months": where.get(str(document_id), [])}, ensure_ascii=False))
    servable = (
        select(func.count())
        .select_from(QbQuestion)
        .join(QbTopic, QbTopic.topic_id == QbQuestion.topic_id)
        .join(QbChapter, QbChapter.chapter_id == QbTopic.chapter_id)
        .where(
            QbChapter.book_id == QbBook.book_id,
            QbQuestion.is_active,
            QbQuestion.review_status.in_(("approved", "needs_review")),
        )
        .scalar_subquery()
    )
    for book_id, title, questions in (await db.execute(select(QbBook.book_id, QbBook.title, servable))).all():
        line = {"book": book_id, "title": title, "questions": questions, "months": where.get(book_id, [])}
        print(json.dumps(line, ensure_ascii=False))
    lessons = (
        select(func.count())
        .select_from(NhprepLesson)
        .where(NhprepLesson.course_id == NhprepCourse.id, NhprepLesson.removed_at.is_(None))
        .scalar_subquery()
    )
    chunks = (
        select(func.count())
        .select_from(NhprepSearchChunk)
        .where(NhprepSearchChunk.course_id == NhprepCourse.id)
        .scalar_subquery()
    )
    courses = await db.execute(
        select(NhprepCourse.id, NhprepCourse.slug, NhprepCourse.title, lessons, chunks)
        .where(NhprepCourse.removed_at.is_(None))
        .order_by(NhprepCourse.title)
    )
    for course_id, slug, title, count, search in courses.all():
        line = {"course": slug, "title": title, "lessons": count, "chunks": search}
        print(json.dumps({**line, "months": where.get(str(course_id), [])}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- mapping
async def mapping(db, path: Path, apply: bool) -> int:
    spec = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))  # no blocking read in the event loop
    sets, months = spec["sets"], spec["months"]
    modules = await _modules(db)
    documents = {
        (source, title): (document_id, visibility, rights, owner)
        for document_id, title, source, visibility, rights, owner in (
            await db.execute(
                select(
                    Document.id,
                    Document.title,
                    KnowledgeSource.name,
                    KnowledgeSource.visibility,
                    KnowledgeSource.rights_status,
                    KnowledgeSource.owner_user_id,
                ).join(KnowledgeSource, KnowledgeSource.id == Document.source_id)
            )
        ).all()
    }
    books = set(await db.scalars(select(QbBook.book_id)))
    courses = dict(
        (await db.execute(select(NhprepCourse.slug, NhprepCourse.id).where(NhprepCourse.removed_at.is_(None)))).all()
    )
    problems: list[str] = []
    resolved: dict[str, dict[str, set]] = {}
    for name, content in sets.items():
        found: dict[str, set] = {"documents": set(), "books": set(), "courses": set()}
        for item in content.get("documents", []):
            row = documents.get((item["source"], item["title"]))
            if row is None:
                problems.append(f"set {name}: no document {item['source']!r} / {item['title']!r}")
            elif row[1] != "global" or row[2] != "approved" or row[3] is not None:
                problems.append(f"set {name}: {item['title']!r} is not in an approved shared (global) source")
            else:
                found["documents"].add(row[0])
        for book in content.get("books", []):
            if book in books:
                found["books"].add(book)
            else:
                problems.append(f"set {name}: no question-bank track {book!r}")
        for slug in content.get("courses", []):
            if slug in courses:
                found["courses"].add(courses[slug])
            else:
                problems.append(f"set {name}: no NHprep course {slug!r}")
        resolved[name] = found
    plan: dict[object, dict[str, set]] = {}
    for slug, by_month in months.items():
        for month, names in by_month.items():
            module = modules.get((slug, int(month)))
            if module is None:
                problems.append(f"{slug} month {month}: no such month")
                continue
            union: dict[str, set] = {"documents": set(), "books": set(), "courses": set()}
            for name in names:
                if name not in resolved:
                    problems.append(f"{slug} month {month}: no set {name!r}")
                    continue
                for key in union:
                    union[key] |= resolved[name][key]
            plan[module] = union
            print(
                f"{slug}:{month} {module.title}: {len(union['documents'])} documents, "
                f"{len(union['books'])} tracks, {len(union['courses'])} courses"
            )
    if problems:
        for problem in problems:
            print("REFUSED:", problem)
        return 1
    if not apply:
        print(f"dry run: {len(plan)} months would be replaced (--apply to write)")
        return 0
    for module, union in plan.items():
        for table in (ProgramModuleDocument, ProgramModuleBook, ProgramModuleCourse):
            await db.execute(delete(table).where(table.module_id == module.id))
        db.add_all(ProgramModuleDocument(module_id=module.id, document_id=item) for item in union["documents"])
        db.add_all(ProgramModuleBook(module_id=module.id, book_id=item) for item in union["books"])
        db.add_all(ProgramModuleCourse(module_id=module.id, course_id=item) for item in union["courses"])
    await db.commit()
    print(f"applied: {len(plan)} months replaced")
    return 0


# ---------------------------------------------------------------- invoices
async def invoices(db, path: Path, apply: bool) -> int:
    programs = dict((await db.execute(select(Program.slug, Program.id))).all())
    rows, rejected = [], []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for line, record in enumerate(csv.DictReader(handle), start=2):
            number = normalize_invoice(record.get("invoice_no") or "")
            program = programs.get((record.get("program") or "").strip())
            status = (record.get("status") or "active").strip().lower()
            try:
                until = _until(record.get("valid_until"))
            except ValueError:
                until, status = None, "invalid"
            if number is None or program is None or status not in INVOICE_STATUSES:
                rejected.append(line)
                continue
            rows.append({"invoice_no": number, "program_id": program, "status": status, "valid_until": until})
    print(f"{len(rows)} invoices read, {len(rejected)} rejected" + (f" (lines {rejected})" if rejected else ""))
    if rejected or not apply:
        print("dry run: nothing written" if not rejected else "nothing written: fix the rejected lines first")
        return 1 if rejected else 0
    for row in rows:
        statement = insert(NhInvoice).values(**row)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["invoice_no"],
                set_={
                    "program_id": statement.excluded.program_id,
                    "status": statement.excluded.status,
                    "valid_until": statement.excluded.valid_until,
                    "updated_at": func.clock_timestamp(),
                },
            )
        )
    await db.commit()
    print(f"applied: {len(rows)} invoices upserted")
    return 0


async def invoice(db, args: argparse.Namespace) -> int:
    number = normalize_invoice(args.number)
    values: dict = {}
    if args.status:
        values["status"] = args.status
    if args.program:
        program = await db.scalar(select(Program.id).where(Program.slug == args.program))
        if program is None:
            print("REFUSED: no such program")
            return 1
        values["program_id"] = program
    if args.valid_until is not None:
        values["valid_until"] = _until(args.valid_until)
    if number is None or not values:
        print("REFUSED: give a valid invoice number and at least one change")
        return 1
    found = await db.scalar(select(func.count()).select_from(NhInvoice).where(NhInvoice.invoice_no == number))
    print(f"{found} invoice found; changes: {sorted(values)}")
    if not apply_or_dry(args.apply) or not found:
        return 0 if found else 1
    await db.execute(
        update(NhInvoice).where(NhInvoice.invoice_no == number).values(**values, updated_at=func.clock_timestamp())
    )
    await db.commit()
    print("applied: 1 invoice changed")
    return 0


def apply_or_dry(apply: bool) -> bool:
    if not apply:
        print("dry run: nothing written (--apply to write)")
    return apply


async def dummy_invoices(db, apply: bool) -> int:
    programs = (await db.execute(select(Program.slug, Program.id).order_by(Program.position))).all()
    numbers = [(slug, program, f"NHTEST-{secrets.token_hex(8).upper()}") for slug, program in programs]
    if not apply_or_dry(apply):
        print(f"{len(numbers)} dummy invoices would be created, one per program")
        return 0
    for _, program, number in numbers:
        db.add(NhInvoice(invoice_no=number, program_id=program))
    await db.commit()
    for slug, _, number in numbers:
        print(f"{slug} {number}")
    return 0


async def release(db, number_text: str, apply: bool) -> int:
    number = normalize_invoice(number_text)
    target = select(NhInvoice.id).where(NhInvoice.invoice_no == number).scalar_subquery()
    found = await db.scalar(select(func.count()).select_from(InvoiceClaim).where(InvoiceClaim.invoice_id == target))
    print(f"{found} claim found")
    if not found or not apply_or_dry(apply):
        return 0
    await db.execute(delete(InvoiceClaim).where(InvoiceClaim.invoice_id == target))
    await db.commit()
    print("applied: claim released")
    return 0


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings)
    try:
        async with database.sessionmaker() as db:
            if args.action == "inventory":
                return await inventory(db)
            if args.action == "mapping":
                return await mapping(db, args.file, args.apply)
            if args.action == "invoices":
                return await invoices(db, args.file, args.apply)
            if args.action == "invoice":
                return await invoice(db, args)
            if args.action == "dummy-invoices":
                return await dummy_invoices(db, args.apply)
            return await release(db, args.number, args.apply)
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
