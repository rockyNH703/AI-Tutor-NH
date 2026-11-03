"""Guarded operator command for Knowledge Library administrator capability."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.models import KnowledgeAdminGrant, User
from app.db.session import Database
from app.settings import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("grant", "revoke", "show"))
    parser.add_argument("email", help="Existing verified account email")
    parser.add_argument("--reason", help="Required audit reason for grant/revoke")
    parser.add_argument("--yes", action="store_true", help="Confirm the mutation non-interactively")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings)
    try:
        async with database.sessionmaker() as db:
            user = await db.scalar(select(User).where(func.lower(User.email) == args.email.strip().lower()))
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
    finally:
        await database.dispose()


def main() -> int:
    args = _parser().parse_args()
    if args.action != "show" and not args.yes:
        answer = input(f"{args.action} Knowledge Library admin for {args.email}? Type YES: ")
        if answer != "YES":
            print("Cancelled.")
            return 1
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
