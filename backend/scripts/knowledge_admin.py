"""Guarded operator command for the Knowledge Library: administrators, the shared library, imports, publication.

    python scripts/knowledge_admin.py grant|revoke|show <email> [--reason ...] [--yes]
    python scripts/knowledge_admin.py source <name> --as <admin email> [--yes]
    python scripts/knowledge_admin.py source <name> --operator --reason <rights basis> [--course <slug>] [--yes]
    python scripts/knowledge_admin.py batch <source-id> --as <admin email> [--files N] [--bytes N] [--yes]
    python scripts/knowledge_admin.py import <source-id> --operator --pdf <path> [--pdf <path> ...] [--yes]
    python scripts/knowledge_admin.py revisions --as <admin email>|--operator [--status needs_review|...]
    python scripts/knowledge_admin.py approve|publish|reject <revision-id> --as <admin>|--operator [--reason ...]
    python scripts/knowledge_admin.py course <slug> --name <name> [--level N] [--builds-on <slug>] --operator
    python scripts/knowledge_admin.py assign <source-id> --course <slug|none> --as <admin email>|--operator [--yes]
    python scripts/knowledge_admin.py egress <source-id> --kind embed|extract --reason <basis> [--withdraw] --as|--op
    python scripts/knowledge_admin.py duplicates [--status pending|all] --as <admin email>|--operator
    python scripts/knowledge_admin.py decide <review-id> --decision reuse|keep_separate|reject --as|--operator [--yes]
    python scripts/knowledge_admin.py delete-source <source-id> --reason ... --as <admin email>|--operator [--yes]

Run on the host with the API's settings (systemd-run -p EnvironmentFile=/etc/netsecai/api.env, as
docs/knowledge-library-readiness.md shows). Every command acts as one of two principals:

- `--as <admin email>`: an active Knowledge Library administrator, on the private (owner_only) sources they own.
  `source` creates one; `batch` issues a 12-hour import token for it, printed once, for import_windows_folder.py.
- `--operator`: the host operator (root on the NetSec host, recorded by name, like administrator grants), on the
  shared library: global sources that belong to no user and that every NetSec AI user may search. `source`
  creates one with its distribution-rights basis (required) and course; `import` stores PDFs from host paths
  with the import API's checks (safe PDF name, size, %PDF signature, capacity, one copy per source). The operator
  never reaches a user's private source; an administrator never reaches the shared library.

`approve` accepts a revision that needs review (for example OCR was used), `publish` makes a ready revision's
passages live in its source in one transaction (app/knowledge/passages.py; held for review if another copy of
the book appeared meanwhile), `reject` cancels it. `course` and `assign` set the CCNA/CCNP boundaries, `egress`
records the approval for book text to reach the embedding (or extraction) provider: nothing is queued without
it. `duplicates` and `decide` review the copies, editions and volumes found across the library (a pending
review blocks publication; reuse and reject keep the copy unpublished). `delete-source` removes a source and
purges the text no other book carries. Prints ids and counts, never book text.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import os
import secrets
import stat
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (  # noqa: E402
    KNOWLEDGE_BLOB_BYTES,
    DocumentFile,
    DocumentFileBlob,
    DocumentRevision,
    KnowledgeAdminGrant,
    KnowledgeCourse,
    KnowledgeDuplicateReview,
    KnowledgeImportBatch,
    KnowledgeSource,
    User,
)
from app.db.session import Database  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.files.service import check_capacity  # noqa: E402
from app.knowledge.access import is_knowledge_admin  # noqa: E402
from app.knowledge.passages import publish, purge_orphans  # noqa: E402
from app.knowledge.router import (  # noqa: E402
    BATCH_TTL,
    MAX_BATCH_BYTES,
    MAX_BATCH_FILES,
    MAX_IMPORT_FILE_BYTES,
    _filename,
    _hash_secret,
)
from app.settings import Settings  # noqa: E402

GRANT_ACTIONS = ("grant", "revoke", "show")
IMPORT_ACTIONS = ("source", "batch")
REVISION_ACTIONS = ("approve", "publish", "reject")
LIBRARY_ACTIONS = ("course", "assign", "egress", "duplicates", "decide", "delete-source", "import")
MUTATIONS = ("grant", "revoke", *IMPORT_ACTIONS, *REVISION_ACTIONS, *(a for a in LIBRARY_ACTIONS if a != "duplicates"))
DECISIONS = ("reuse", "keep_separate", "reject")
PAST = {"approve": "approved", "reject": "rejected"}


@dataclass(frozen=True)
class Actor:
    """Who acts: an administrator (on the private sources they own) or the host operator (on the shared library)."""

    user: User | None
    operator: str | None = None

    @property
    def id(self) -> uuid.UUID | None:
        return self.user.id if self.user is not None else None

    @property
    def by(self) -> str:
        return f"user {self.user.id}" if self.user is not None else f"operator {self.operator}"

    def note(self, text: str) -> str:
        """An audit basis, with the operator's name when the operator acted (no user id records it)."""
        return text if self.user is not None else f"{text} (operator {self.operator})"

    def recorded(self, report: dict | None, action: str, reason: str | None = None) -> dict:
        """The revision's report with this decision appended: who (by name for the operator), what, when, why."""
        entry = {"action": action, "by": self.by, "at": datetime.now(UTC).isoformat()}
        if reason:
            entry["reason"] = reason.strip()[:300]
        report = dict(report or {})
        return {**report, "operatorActions": [*report.get("operatorActions", []), entry]}


def _operator_name() -> str:
    """The host account running the command, from the OS (not from USER/LOGNAME, which a caller can set)."""
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name[:200]
    except (ImportError, KeyError, AttributeError):  # not a POSIX host (a developer's Windows PC)
        return getpass.getuser()[:200]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "action", choices=(*GRANT_ACTIONS, *IMPORT_ACTIONS, "revisions", *REVISION_ACTIONS, *LIBRARY_ACTIONS)
    )
    parser.add_argument("target", nargs="?", help="email, source name or id, revision id, course or review id")
    parser.add_argument("--as", dest="actor", help="the administrator's email (all but grant/revoke/show)")
    parser.add_argument("--operator", action="store_true", help="act as the host operator on the shared library")
    parser.add_argument("--pdf", action="append", help="a PDF on this host to import (import; repeatable)")
    parser.add_argument("--sha256", action="append", help="the expected SHA-256 of each --pdf, in order (import)")
    parser.add_argument("--files", type=int, help="expected number of PDFs in the batch (batch)")
    parser.add_argument("--bytes", type=int, help="expected total size of the batch in bytes (batch)")
    parser.add_argument("--status", help="only revisions in this status (revisions); pending|all (duplicates)")
    parser.add_argument("--name", help="the course name (course)")
    parser.add_argument("--level", type=int, default=1, help="the course level, 1-9 (course)")
    parser.add_argument("--builds-on", dest="builds_on", help="the course this one assumes, e.g. ccna (course)")
    parser.add_argument("--course", help="a course slug, or none for general material (assign; operator source)")
    parser.add_argument("--kind", choices=("embed", "extract"), help="which provider egress (egress)")
    parser.add_argument("--withdraw", action="store_true", help="withdraw the egress approval (egress)")
    parser.add_argument("--decision", choices=DECISIONS, help="the review decision (decide)")
    parser.add_argument("--reason", help="Required audit reason for grant/revoke/reject; the rights basis (source)")
    parser.add_argument("--yes", action="store_true", help="Confirm the mutation non-interactively")
    return parser


async def _user(db, email: str) -> User | None:
    return await db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


async def _grants(db, args: argparse.Namespace) -> int:
    user = await _user(db, args.target)
    if user is None:
        print("No matching user.", file=sys.stderr)
        return 2
    grant = await db.get(KnowledgeAdminGrant, user.id)
    if args.action == "show":
        active = bool(
            grant is not None
            and grant.revoked_at is None
            and (grant.expires_at is None or grant.expires_at > datetime.now(UTC))
        )
        print(f"knowledge_admin={str(active).lower()}")
        return 0
    if args.action == "grant" and not user.email_verified:
        print("The account's email address is not verified.", file=sys.stderr)
        return 2
    if not args.reason or not args.reason.strip():
        print("--reason is required for mutations.", file=sys.stderr)
        return 2
    actor_email = _operator_name()
    if args.action == "grant":
        if grant is None:
            db.add(
                KnowledgeAdminGrant(
                    user_id=user.id,
                    granted_by_user_id=None,
                    granted_by_operator=actor_email,
                    reason=args.reason.strip(),
                )
            )
        else:
            grant.reason = args.reason.strip()
            grant.granted_by_user_id = None
            grant.granted_by_operator = actor_email
            grant.granted_at = datetime.now(UTC)
            grant.revoked_at = None
            grant.revoked_by_user_id = None
            grant.revoke_reason = None
    else:
        if grant is None or grant.revoked_at is not None:
            print("Capability is not active.", file=sys.stderr)
            return 2
        grant.revoked_at = datetime.now(UTC)
        grant.revoked_by_user_id = None
        grant.revoke_reason = f"{args.reason.strip()} (operator {actor_email})"
    await db.commit()
    print(f"{args.action}ed Knowledge Library admin for {user.email}")
    return 0


async def _admin(db, args: argparse.Namespace) -> Actor | None:
    if args.operator:
        if args.actor:
            print(
                "Act either as an administrator (--as) or as the host operator (--operator), not both.", file=sys.stderr
            )
            return None
        return Actor(None, _operator_name())
    actor = await _user(db, args.actor or "")
    if actor is None or not await is_knowledge_admin(db, actor.id):
        print("--as must name an active Knowledge Library administrator (or use --operator).", file=sys.stderr)
        return None
    return Actor(actor)


def _shared(source: KnowledgeSource) -> bool:
    """The shared library: global sources that belong to no user."""
    return source.owner_user_id is None and source.visibility == "global"


def _reaches(actor: Actor, source: KnowledgeSource) -> bool:
    return _shared(source) if actor.user is None else source.owner_user_id == actor.user.id


async def _course_or_none(db, value: str | None) -> tuple[bool, str | None]:
    wanted = (value or "").strip().lower()
    if not wanted or wanted == "none":
        return True, None
    return await db.get(KnowledgeCourse, wanted) is not None, wanted


async def _imports(db, args: argparse.Namespace) -> int:
    """`source` and `batch`: what POST /v1/knowledge/imports needs, which the web app has no screen for."""
    actor = await _admin(db, args)
    if actor is None:
        return 2
    if args.action == "source":
        name = (args.target or "").strip()
        if not 1 <= len(name) <= 120:
            print("A source name of 1-120 characters is required.", file=sys.stderr)
            return 2
        if actor.user is not None:
            source = KnowledgeSource(name=name, owner_user_id=actor.user.id)
        else:
            basis = (args.reason or "").strip()
            if not basis:
                print("--reason (the distribution-rights basis for every NetSec AI user) is required.", file=sys.stderr)
                return 2
            known, course = await _course_or_none(db, args.course)
            if not known:
                print("No such course (create it with `course`).", file=sys.stderr)
                return 2
            source = KnowledgeSource(
                name=name,
                owner_user_id=None,
                visibility="global",
                rights_status="approved",
                rights_basis=actor.note(basis[:440]),
                rights_approved_at=datetime.now(UTC),
                rights_approved_by_user_id=None,
                course=course,
            )
        db.add(source)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            print("A source with that name already exists.", file=sys.stderr)
            return 1
        kind = (
            "owner_only" if actor.user is not None else f"global, shared library, course={source.course or 'general'}"
        )
        print(f"created source {source.id} ({kind})")
        return 0
    if actor.user is None:
        print(
            "The shared library imports from the host: `import <source-id> --operator --pdf <path>`.", file=sys.stderr
        )
        return 2
    try:
        source_id = uuid.UUID(args.target or "")
    except ValueError:
        print("A source id is required.", file=sys.stderr)
        return 2
    if args.files is not None and not 1 <= args.files <= MAX_BATCH_FILES:
        print(f"--files must be 1-{MAX_BATCH_FILES}.", file=sys.stderr)
        return 2
    if args.bytes is not None and not 1 <= args.bytes <= MAX_BATCH_BYTES:
        print(f"--bytes must be 1-{MAX_BATCH_BYTES}.", file=sys.stderr)
        return 2
    source = await db.get(KnowledgeSource, source_id)
    if source is None or source.owner_user_id != actor.user.id:
        print("No such source owned by that administrator.", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    batch = KnowledgeImportBatch(
        owner_user_id=actor.user.id,
        issued_by_user_id=actor.user.id,
        source_id=source.id,
        secret_hash=_hash_secret(token),
        expected_file_count=args.files,
        expected_total_bytes=args.bytes,
        expires_at=datetime.now(UTC) + BATCH_TTL,
    )
    db.add(batch)
    await db.commit()
    print(f"batch {batch.id} expires {batch.expires_at.isoformat()}")
    print(f"token (shown once; keep it out of files and shell history): {token}")
    return 0


async def _list_revisions(db, args: argparse.Namespace) -> int:
    """The revisions of the sources the acting principal administers (theirs, or the shared library)."""
    actor = await _admin(db, args)
    if actor is None:
        return 2
    statement = (
        select(DocumentRevision)
        .where(DocumentRevision.source_id.in_(await _owned_ids(db, actor)))
        .order_by(DocumentRevision.created_at)
    )
    if args.status:
        statement = statement.where(DocumentRevision.status == args.status)
    for revision in await db.scalars(statement):
        report = revision.quality_report or {}
        print(
            f"{revision.id} {revision.status} source={revision.source_id} document={revision.document_id} "
            f"pages={report.get('pageCount')} chunks={report.get('chunks')} passages={report.get('passages')} "
            f"warnings={','.join(report.get('warnings') or []) or '-'} error={revision.error_code or '-'}"
        )
    return 0


async def _revision(db, args: argparse.Namespace) -> int:
    try:
        revision_id = uuid.UUID(args.target or "")
    except ValueError:
        print("A revision id is required.", file=sys.stderr)
        return 2
    actor = await _admin(db, args)
    if actor is None:
        return 2
    # Each principal acts only on its own books: an administrator on the sources they own, the operator on the
    # shared library. A revision anywhere else is indistinguishable from one that does not exist.
    source_id = await db.scalar(select(DocumentRevision.source_id).where(DocumentRevision.id == revision_id))
    source = await db.get(KnowledgeSource, source_id) if source_id is not None else None
    if source is None or not _reaches(actor, source):
        where = "in the shared library" if actor.user is None else "in a source that administrator owns"
        print(f"No such revision {where}.", file=sys.stderr)
        return 2
    if args.action == "publish":
        try:
            chunks = await publish(db, revision_id, actor.id)
        except AppError as error:
            await db.rollback()
            print(f"Not published: {error.code}.", file=sys.stderr)
            return 1
        if chunks:
            published = await db.get(DocumentRevision, revision_id)
            published.quality_report = actor.recorded(published.quality_report, "publish")
        await db.commit()
        if chunks == 0:
            print(
                f"Held for duplicate review: revision {revision_id} needs review (see `duplicates`).", file=sys.stderr
            )
            return 1
        print(f"published revision {revision_id}: {chunks} passages")
        return 0
    revision = await db.scalar(select(DocumentRevision).where(DocumentRevision.id == revision_id).with_for_update())
    if revision is None:
        print("No such revision.", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    if args.action == "approve":
        if revision.status != "needs_review":
            print(f"Only a revision that needs review can be approved (it is {revision.status}).", file=sys.stderr)
            return 1
        revision.status = "ready_to_publish"
    else:
        if revision.status in ("published", "retired", "cancelled"):
            print(f"A {revision.status} revision cannot be rejected.", file=sys.stderr)
            return 1
        if not args.reason or not args.reason.strip():
            print("--reason is required to reject.", file=sys.stderr)
            return 2
        revision.status = "cancelled"
        revision.error_code = "rejected_by_operator"
    revision.reviewed_by_user_id = actor.id
    revision.reviewed_at = revision.updated_at = now
    revision.quality_report = actor.recorded(revision.quality_report, args.action, args.reason)
    await db.commit()
    print(f"{PAST[args.action]} revision {revision_id}: {revision.status}")
    return 0


# ------------------------------------------------------------------ courses, egress, duplicates, deletion, import
async def _owned_ids(db, actor: Actor) -> set[uuid.UUID]:
    if actor.user is None:
        reach = KnowledgeSource.owner_user_id.is_(None) & (KnowledgeSource.visibility == "global")
    else:
        reach = KnowledgeSource.owner_user_id == actor.user.id
    return set(await db.scalars(select(KnowledgeSource.id).where(reach)))


async def _owned_source(db, args: argparse.Namespace, actor: Actor) -> KnowledgeSource | None:
    try:
        source = await db.get(KnowledgeSource, uuid.UUID(args.target or ""))
    except ValueError:
        source = None
    if source is None or not _reaches(actor, source):
        where = "in the shared library" if actor.user is None else "owned by that administrator"
        print(f"No such source {where}.", file=sys.stderr)
        return None
    return source


async def _course(db, args: argparse.Namespace, actor: Actor) -> int:
    if actor.user is not None:  # courses scope every user's retrieval: they belong to the shared library
        print("Courses are the shared library's boundaries: only the host operator (--operator) sets them.")
        return 2
    slug = (args.target or "").strip().lower()
    if not args.name or not args.name.strip() or not 1 <= args.level <= 9:
        print("--name and a --level of 1-9 are required.", file=sys.stderr)
        return 2
    course = await db.get(KnowledgeCourse, slug)
    if course is None:
        course = KnowledgeCourse(slug=slug, name=args.name.strip()[:120])
        db.add(course)
    course.name, course.level, course.builds_on = args.name.strip()[:120], args.level, args.builds_on or None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        print("Invalid course: a slug of a-z, 0-9 and _ (2-40), an existing --builds-on, not itself.", file=sys.stderr)
        return 1
    print(f"course {slug}: level {args.level} builds_on={args.builds_on or '-'}")
    return 0


async def _assign(db, args: argparse.Namespace, actor: Actor) -> int:
    source = await _owned_source(db, args, actor)
    if source is None:
        return 2
    if not (args.course or "").strip():
        print("--course <slug|none> is required.", file=sys.stderr)
        return 2
    known, course = await _course_or_none(db, args.course)
    if not known:
        print("No such course (create it with `course`).", file=sys.stderr)
        return 2
    source.course = course
    await db.commit()
    print(f"source {source.id} course={course or 'general'}")
    return 0


async def _egress(db, args: argparse.Namespace, actor: Actor) -> int:
    source = await _owned_source(db, args, actor)
    if source is None:
        return 2
    if args.kind is None or (not args.withdraw and not (args.reason or "").strip()):
        print("--kind embed|extract and --reason (the basis of the approval) are required.", file=sys.stderr)
        return 2
    if args.withdraw:
        values = (None, None, None)
    else:
        values = (datetime.now(UTC), actor.note(args.reason.strip()[:440]), actor.id)
    for column, value in zip(("approved_at", "basis", "approved_by_user_id"), values, strict=True):
        setattr(source, f"{args.kind}_egress_{column}", value)
    await db.commit()
    print(f"{args.kind} egress {'withdrawn' if args.withdraw else 'approved'} for source {source.id}")
    return 0


async def _duplicates(db, args: argparse.Namespace, actor: Actor) -> int:
    owned = await _owned_ids(db, actor)
    statement = (
        select(KnowledgeDuplicateReview, DocumentFile)
        .join(DocumentFile, DocumentFile.id == KnowledgeDuplicateReview.file_id)
        .where(DocumentFile.source_id.in_(owned))
        .order_by(KnowledgeDuplicateReview.created_at)
    )
    status = args.status or "pending"
    if status != "all":
        statement = statement.where(KnowledgeDuplicateReview.decision == status)
    for review, file in (await db.execute(statement)).all():
        candidate = await db.get(DocumentFile, review.candidate_file_id)
        # Another owner's copy is named by id only: its filename is theirs to see.
        other = candidate.filename if candidate is not None and candidate.source_id in owned else "another owner's file"
        print(
            f"{review.id} {review.classification} confidence={review.confidence} decision={review.decision} "
            f"file={file.filename} candidate={other} ({review.candidate_file_id}) "
            f"shared_pages={review.signals.get('sharedPages', '-')}"
        )
    return 0


async def _decide(db, args: argparse.Namespace, actor: Actor) -> int:
    try:
        review = await db.get(KnowledgeDuplicateReview, uuid.UUID(args.target or ""), with_for_update=True)
    except ValueError:
        review = None
    file = await db.get(DocumentFile, review.file_id) if review is not None else None
    if file is None or file.source_id not in await _owned_ids(db, actor):
        print("No such review of a book in a source this actor administers.", file=sys.stderr)
        return 2
    if args.decision is None:
        print("--decision reuse|keep_separate|reject is required.", file=sys.stderr)
        return 2
    review.decision, review.reviewed_by_user_id, review.reviewed_at = args.decision, actor.id, datetime.now(UTC)
    review.signals = {**(review.signals or {}), "decidedBy": actor.by}
    await db.commit()
    print(f"review {review.id}: {args.decision}")
    return 0


async def _delete_source(db, args: argparse.Namespace, actor: Actor) -> int:
    source = await _owned_source(db, args, actor)
    if source is None:
        return 2
    if not (args.reason or "").strip():
        print("--reason is required to delete a source.", file=sys.stderr)
        return 2
    source_id = source.id
    # Revisions first (their file reference is RESTRICT); occurrences, liveness, files and editions follow by
    # cascade; then the text no other book carries, with its vectors.
    await db.execute(delete(DocumentRevision).where(DocumentRevision.source_id == source_id))
    await db.execute(delete(KnowledgeSource).where(KnowledgeSource.id == source_id))
    purged = await purge_orphans(db)
    await db.commit()
    print(f"deleted source {source_id}: {purged} passages no other book carries were purged")
    return 0


def _read_pdf(path: Path) -> tuple[str, bytes] | str:
    """(safe name, bytes) of a PDF on this host, or why it is refused: the import API's own rules, read once
    from one open file whose size must not change while it is read (a copy still being written is refused)."""
    try:
        name = _filename(path.name)
    except AppError as error:
        return error.code
    try:
        with path.open("rb") as handle:
            details = os.fstat(handle.fileno())
            if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= MAX_IMPORT_FILE_BYTES:
                return "not_a_file_or_too_large"
            data = handle.read(MAX_IMPORT_FILE_BYTES + 1)
    except OSError:
        return "unreadable"
    if len(data) != details.st_size:
        return "changed_while_reading"
    return (name, data) if data.startswith(b"%PDF-") else "not_a_pdf"


async def _last_status(db, file_id: uuid.UUID) -> str | None:
    return await db.scalar(
        select(DocumentRevision.status)
        .where(DocumentRevision.file_id == file_id)
        .order_by(DocumentRevision.created_at.desc())
        .limit(1)
    )


async def _import(db, args: argparse.Namespace, actor: Actor) -> int:
    """The shared library's PDFs, from this host, one stored copy per source; the worker takes them from there."""
    if actor.user is not None:
        print("Only the host operator imports from host paths (--operator); administrators use the import API.")
        return 2
    source = await _owned_source(db, args, actor)
    if source is None:
        return 2
    if not args.pdf:
        print("--pdf <path> is required (repeatable).", file=sys.stderr)
        return 2
    expected = [value.strip().lower() for value in args.sha256 or []]
    if expected and len(expected) != len(args.pdf):
        print("Give one --sha256 per --pdf, in the same order (or none).", file=sys.stderr)
        return 2
    source_id = source.id  # a rollback below expires the ORM objects
    refused = 0
    for index, raw in enumerate(args.pdf):
        read = _read_pdf(Path(raw))
        if isinstance(read, str):
            print(f"refused {Path(raw).name}: {read}", file=sys.stderr)
            refused += 1
            continue
        name, data = read
        digest = hashlib.sha256(data).hexdigest()
        if expected and digest != expected[index]:
            print(f"refused {name}: sha256_mismatch (read {digest})", file=sys.stderr)
            refused += 1
            continue
        existing = await db.scalar(
            select(DocumentFile).where(DocumentFile.source_id == source_id, DocumentFile.sha256 == digest)
        )
        if existing is not None:
            last = await _last_status(db, existing.id)
            if last in ("failed", "cancelled"):
                print(f"refused {name}: already file {existing.id}, whose last revision is {last}", file=sys.stderr)
                refused += 1
            else:
                print(f"duplicate {name}: the same bytes are already file {existing.id} ({existing.filename})")
            continue
        try:
            await check_capacity(db)
        except AppError as error:
            print(f"refused {name}: {error.code}", file=sys.stderr)
            refused += 1
            continue
        file = DocumentFile(
            source_id=source_id,
            filename=name,
            size_bytes=len(data),
            received_bytes=len(data),
            sha256=digest,
            status="ready",
            completed_at=datetime.now(UTC),
        )
        try:
            db.add(file)
            await db.flush()
            for ordinal, start in enumerate(range(0, len(data), KNOWLEDGE_BLOB_BYTES)):
                chunk = data[start : start + KNOWLEDGE_BLOB_BYTES]
                db.add(DocumentFileBlob(file_id=file.id, ordinal=ordinal, data=chunk))
            await db.commit()
        except IntegrityError:  # another run stored the same bytes meanwhile: one copy per source holds
            await db.rollback()
            print(f"duplicate {name}: the same bytes were imported meanwhile")
            continue
        print(f"imported {file.id} {name} {len(data)} bytes sha256={digest}")
    return 1 if refused else 0


async def _library(db, args: argparse.Namespace) -> int:
    actor = await _admin(db, args)
    if actor is None:
        return 2
    handlers = {
        "course": _course,
        "assign": _assign,
        "egress": _egress,
        "duplicates": _duplicates,
        "decide": _decide,
        "delete-source": _delete_source,
        "import": _import,
    }
    return await handlers[args.action](db, args, actor)


async def _dispatch(db, args: argparse.Namespace) -> int:
    if args.action in GRANT_ACTIONS:
        if not args.target:
            print("An account email is required.", file=sys.stderr)
            return 2
        return await _grants(db, args)
    if args.action in IMPORT_ACTIONS:
        return await _imports(db, args)
    if args.action == "revisions":
        return await _list_revisions(db, args)
    if args.action in LIBRARY_ACTIONS:
        return await _library(db, args)
    return await _revision(db, args)


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings)
    try:
        async with database.sessionmaker() as db:
            return await _dispatch(db, args)
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action in MUTATIONS and not args.yes:
        answer = input(f"{args.action} {args.target}? Type YES: ")
        if answer != "YES":
            print("Cancelled.")
            return 1
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
