"""Seeds the Friend Zone e2e database (e2e/friendzone/global-setup.ts runs it before every run, with the backend venv
and the backend snapshot as the working directory). Idempotent:

1. the synthetic certification bank (backend tests/test_qbank_certification.py `netsec_bank`), its single-answer
   questions approved by a person for their current content (FRIEND_ZONE_KEY_POLICY=human_only), and book `ccna`
   mapped to month 1 of networking-ai, exactly as tests/test_friend_zone_battles.py's `bank` fixture does: at least
   30 of them competitive (app/friendzone/eligibility.py, human_only) in that month, for a 30-question exam battle;
2. a second, small synthetic book (`fzsm`: the same builder with 3 questions a topic, renamed), whose 8 single-answer
   questions are approved and mapped to month 1 of cyber-cloud-security-ai: fewer than a daily challenge's ten, so
   that program shows "No daily challenge today";
3. the per-address search counts of earlier runs forgotten: every run searches from 127.0.0.1, and 200 a day per
   address (R-S1) would otherwise stop the suite on its third run of a day. Per-account limits are untouched.

Refuses anything but a loopback `*_test` database."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.getcwd())  # the snapshot: `app` and `tests`

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.db.models import QbQuestion  # noqa: E402
from app.friendzone.eligibility import competitive  # noqa: E402
from app.qbank.importer import import_bank  # noqa: E402
from tests.test_qbank_certification import netsec_bank  # noqa: E402

PROGRAM = "networking-ai"
MONTH = 1
SMALL_PROGRAM = "cyber-cloud-security-ai"
SMALL_BOOK = "fzsm"
LONGEST_EXAM = 30  # an exam battle has 10, 20 or 30 questions, all from one month


def exam_bank(folder: Path) -> Path:
    """The test bank builder's output with 15 questions a topic: 40 competitive (9 a topic gives only 24, too few for a
    30-question exam; tests/test_friend_zone_battles.py `load_bank`)."""
    return netsec_bank(folder, per_topic=15)


def small_bank(folder: Path) -> Path:
    """The test bank builder's output with 3 questions a topic, renamed from book `ccna` to `fzsm` throughout."""
    source = netsec_bank(folder / "source", per_topic=3)
    target = folder / "small"
    for path in source.rglob("*"):
        if path.is_file():
            relative = str(path.relative_to(source)).replace("ccna", SMALL_BOOK)
            renamed = target / relative
            renamed.parent.mkdir(parents=True, exist_ok=True)
            text_ = path.read_text(encoding="utf-8").replace("CCNA", SMALL_BOOK.upper()).replace("ccna", SMALL_BOOK)
            renamed.write_text(text_, encoding="utf-8")
    return target


async def approve_and_map(db, book: str, slug: str, month: int) -> int:
    """Approve the book's single-answer questions for their current content, map the book to the program's month, and
    return how many approved single-answer questions that month now has."""
    await db.execute(
        text(
            "UPDATE qb_questions SET review_status = 'approved', reviewed_by = 'trainer', "
            "reviewed_at = now(), reviewed_hash = content_hash "
            "WHERE book_id = :book AND question_format = 'mcq' AND reviewed_hash IS DISTINCT FROM content_hash"
        ),
        {"book": book},
    )
    await db.execute(
        text(
            "INSERT INTO program_module_books (module_id, book_id) "
            "SELECT m.id, :book FROM program_modules m JOIN programs p ON p.id = m.program_id "
            "WHERE p.slug = :slug AND m.month = :month ON CONFLICT DO NOTHING"
        ),
        {"book": book, "slug": slug, "month": month},
    )
    return await db.scalar(
        text(
            "SELECT count(*) FROM qb_questions q JOIN program_module_books b ON b.book_id = q.book_id "
            "JOIN program_modules m ON m.id = b.module_id JOIN programs p ON p.id = m.program_id "
            "WHERE p.slug = :slug AND q.question_format = 'mcq' AND q.reviewed_hash = q.content_hash"
        ),
        {"slug": slug},
    )


async def competitive_in_month(db, slug: str, month: int) -> int:
    """How many questions mapped to the program's month a battle may use (the API's own predicate, human_only)."""
    books = (
        await db.scalars(
            text(
                "SELECT b.book_id FROM program_module_books b JOIN program_modules m ON m.id = b.module_id "
                "JOIN programs p ON p.id = m.program_id WHERE p.slug = :slug AND m.month = :month"
            ),
            {"slug": slug, "month": month},
        )
    ).all()
    query = select(func.count()).select_from(QbQuestion).where(QbQuestion.book_id.in_(books), competitive("human_only"))
    return await db.scalar(query)


def safe(url: str) -> str:
    parsed = urlparse(url)
    name = parsed.path.lstrip("/")
    if parsed.hostname not in ("127.0.0.1", "localhost") or not re.fullmatch(r"[a-z0-9_]+_test", name):
        raise SystemExit(f"refusing to seed {parsed.hostname}/{name}: only a loopback *_test database")
    return url


async def main() -> None:
    engine = create_async_engine(safe(os.environ["DATABASE_URL"]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for build in (exam_bank, small_bank):
            async with sessions() as db:
                with tempfile.TemporaryDirectory() as folder:
                    report = await import_bank(db, build(Path(folder)), apply=True)
                if not report.applied:
                    raise SystemExit(f"bank import failed ({build.__name__}): {report.errors}")
        async with sessions() as db:
            approved = await approve_and_map(db, "ccna", PROGRAM, MONTH)
            small = await approve_and_map(db, SMALL_BOOK, SMALL_PROGRAM, MONTH)
            await db.execute(text("DELETE FROM auth_attempts WHERE scope = 'fz_search_ip'"))
            await db.commit()
            usable = await competitive_in_month(db, PROGRAM, MONTH)
        print(
            f"seeded: {approved} approved single-answer questions on {PROGRAM} "
            f"({usable} competitive in month {MONTH}), {small} on {SMALL_PROGRAM}"
        )
        if usable < LONGEST_EXAM:
            raise SystemExit(f"{PROGRAM} month {MONTH} needs {LONGEST_EXAM} competitive questions, has {usable}")
        if small != 8:
            raise SystemExit(f"{SMALL_PROGRAM} must have fewer than a daily's 10 questions (8), has {small}")
    finally:
        await engine.dispose()


asyncio.run(main())
