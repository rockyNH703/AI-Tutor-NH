"""Guarded operator command for the Knowledge Library: administrators, imports and revision publication.

    python scripts/knowledge_admin.py grant|revoke|show <email> [--reason ...] [--yes]
    python scripts/knowledge_admin.py source <name> --as <admin email> [--yes]
    python scripts/knowledge_admin.py batch <source-id> --as <admin email> [--files N] [--bytes N] [--yes]
    python scripts/knowledge_admin.py revisions [--status needs_review|ready_to_publish|...]
    python scripts/knowledge_admin.py approve|publish|reject <revision-id> --as <admin email> [--reason ...] [--yes]

Run on the host with the API's settings (systemd-run -p EnvironmentFile=/etc/netsecai/api.env, as
docs/knowledge-library-readiness.md shows). `source` creates a private (owner_only) source owned by the
administrator; `batch` issues a 12-hour import token for it, printed once, for import_windows_folder.py.
`approve` accepts a revision that needs review (for example OCR was used), `publish` copies a ready revision
into the searchable index in one transaction (app/knowledge/publication.py), `reject` cancels it. Only an
active Knowledge Library administrator can act; prints ids and counts, never book text.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (  # noqa: E402
    DocumentRevision,
    KnowledgeAdminGrant,
    KnowledgeImportBatch,
    KnowledgeSource,
    User,
)
from app.db.session import Database  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.knowledge.access import is_knowledge_admin  # noqa: E402
from app.knowledge.publication import publish_revision  # noqa: E402
from app.knowledge.router import BATCH_TTL, MAX_BATCH_BYTES, MAX_BATCH_FILES, _hash_secret  # noqa: E402
from app.settings import Settings  # noqa: E402

GRANT_ACTIONS = ("grant", "revoke", "show")
IMPORT_ACTIONS = ("source", "batch")
REVISION_ACTIONS = ("approve", "publish", "reject")
MUTATIONS = ("grant", "revoke", *IMPORT_ACTIONS, *REVISION_ACTIONS)
PAST = {"approve": "approved", "reject": "rejected"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=(*GRANT_ACTIONS, *IMPORT_ACTIONS, "revisions", *REVISION_ACTIONS))
    parser.add_argument("target", nargs="?", help="account email, source name, source id or revision id")
    parser.add_argument("--as", dest="actor", help="the administrator's email (all but grant/revoke/show)")
    parser.add_argument("--files", type=int, help="expected number of PDFs in the batch (batch)")
    parser.add_argument("--bytes", type=int, help="expected total size of the batch in bytes (batch)")
    parser.add_argument("--status", help="only revisions in this status (revisions)")
    parser.add_argument("--reason", help="Required audit reason for grant/revoke/reject")
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
    actor_email = getpass.getuser()[:200]
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


async def _admin(db, args: argparse.Namespace) -> User | None:
    actor = await _user(db, args.actor or "")
    if actor is None or not await is_knowledge_admin(db, actor.id):
        print("--as must name an active Knowledge Library administrator.", file=sys.stderr)
        return None
    return actor


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
        source = KnowledgeSource(name=name, owner_user_id=actor.id)
        db.add(source)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            print("A source with that name already exists.", file=sys.stderr)
            return 1
        print(f"created source {source.id} (owner_only)")
        return 0
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
    if source is None or source.owner_user_id != actor.id:
        print("No such source owned by that administrator.", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    batch = KnowledgeImportBatch(
        owner_user_id=actor.id,
        issued_by_user_id=actor.id,
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
    statement = select(DocumentRevision).order_by(DocumentRevision.created_at)
    if args.status:
        statement = statement.where(DocumentRevision.status == args.status)
    for revision in await db.scalars(statement):
        report = revision.quality_report or {}
        print(
            f"{revision.id} {revision.status} document={revision.document_id} "
            f"pages={report.get('pageCount')} chunks={report.get('chunks')} "
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
    if args.action == "publish":
        try:
            chunks = await publish_revision(db, revision_id, actor.id)
        except AppError as error:
            await db.rollback()
            print(f"Not published: {error.code}.", file=sys.stderr)
            return 1
        await db.commit()
        print(f"published revision {revision_id}: {chunks} chunks")
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
    await db.commit()
    print(f"{PAST[args.action]} revision {revision_id}: {revision.status}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings)
    try:
        async with database.sessionmaker() as db:
            if args.action in GRANT_ACTIONS:
                if not args.target:
                    print("An account email is required.", file=sys.stderr)
                    return 2
                return await _grants(db, args)
            if args.action in IMPORT_ACTIONS:
                return await _imports(db, args)
            if args.action == "revisions":
                return await _list_revisions(db, args)
            return await _revision(db, args)
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
