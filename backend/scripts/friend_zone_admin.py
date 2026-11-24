"""Operator command for NH Friend Zone (app/friendzone): moderation and the QA cohort (plan sections 6 and 7).

    python scripts/friend_zone_admin.py reports [--status open|reviewed|actioned|dismissed]
    python scripts/friend_zone_admin.py report <report_id>
    python scripts/friend_zone_admin.py resolve <report_id> --outcome reviewed|actioned|dismissed --note TEXT [--apply]
    python scripts/friend_zone_admin.py suspend <player_id> --reason TEXT [--apply]
    python scripts/friend_zone_admin.py unsuspend <player_id> [--apply]
    python scripts/friend_zone_admin.py qa-report
    python scripts/friend_zone_admin.py qa-cleanup [--apply]
    python scripts/friend_zone_admin.py qa-verify
    python scripts/friend_zone_admin.py retention [--apply]

Run on the host with the API's settings (systemd-run -p EnvironmentFile=/etc/netsecai/api.env, like
program_admin.py). Everything that changes data is a dry run until --apply, and every applied moderation step writes
fz_moderation_actions. Output: ids, counts and states. A report's text and the members' Friend Zone names are shown
only by `report`; nothing here prints an email or an invoice number.

- reports / report: the queue (oldest first) and one report in full.
- resolve: move a report on (open -> reviewed -> actioned or dismissed), with a note.
- suspend: hide a member everywhere at once; their pending requests end and their open battles are void (no winner,
  no stats). unsuspend: visible again (friendships were kept; voided battles stay void).
- qa-report (read-only): QA cohort rows per table. qa-cleanup: delete them all, and the QA answer sets, in one
  transaction (owner-authorised). qa-verify (read-only): exit 1 unless every count is 0 and no live member's bell
  points at a battle that is not live.
- retention: the owner-approved retention (CD20, DPDP review): closed friend requests and battle invitations that
  never started go 30 days after they closed (a request left pending, 30 days after it expired); resolved reports
  180 days after resolution; moderation audit rows after one year. Everything else stays until the account goes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select, update

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (  # noqa: E402
    FZ_BATTLE_OPEN,
    FZ_REPORT_STATUSES,
    FzBattle,
    FzBattlePlayer,
    FzBlock,
    FzFriendRequest,
    FzFriendship,
    FzModerationAction,
    FzProfile,
    FzReport,
    QbSet,
    UserNotification,
)
from app.db.session import Database  # noqa: E402
from app.friendzone import battles, relations  # noqa: E402
from app.settings import Settings  # noqa: E402

QA_TABLES = (
    ("fz_profiles", FzProfile),
    ("fz_friend_requests", FzFriendRequest),
    ("fz_friendships", FzFriendship),
    ("fz_blocks", FzBlock),
    ("fz_reports", FzReport),
    ("fz_battles", FzBattle),
    ("fz_battle_players", FzBattlePlayer),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--actor", default="owner", help="who acts, for the audit (default: owner)")
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("reports")
    listing.add_argument("--status", choices=FZ_REPORT_STATUSES)
    one = actions.add_parser("report")
    one.add_argument("report_id", type=uuid.UUID)
    resolve = actions.add_parser("resolve")
    resolve.add_argument("report_id", type=uuid.UUID)
    resolve.add_argument("--outcome", required=True, choices=("reviewed", "actioned", "dismissed"))
    resolve.add_argument("--note", required=True)
    resolve.add_argument("--apply", action="store_true")
    suspend = actions.add_parser("suspend")
    suspend.add_argument("player_id", type=uuid.UUID)
    suspend.add_argument("--reason", required=True)
    suspend.add_argument("--apply", action="store_true")
    unsuspend = actions.add_parser("unsuspend")
    unsuspend.add_argument("player_id", type=uuid.UUID)
    unsuspend.add_argument("--apply", action="store_true")
    actions.add_parser("qa-report")
    cleanup = actions.add_parser("qa-cleanup")
    cleanup.add_argument("--apply", action="store_true")
    actions.add_parser("qa-verify")
    retention = actions.add_parser("retention")
    retention.add_argument("--apply", action="store_true")
    return parser


def _audit(db, action: str, actor: str, *, report_id=None, target=None, note: str | None = None) -> None:
    db.add(
        FzModerationAction(
            report_id=report_id, target_user_id=target, action=action, actor=actor[:40], note=(note or "")[:500] or None
        )
    )


# ------------------------------------------------------------------ reports
async def reports(db, status: str | None) -> int:
    query = select(FzReport).order_by(FzReport.created_at)
    if status:
        query = query.where(FzReport.status == status)
    rows = (await db.scalars(query.limit(500))).all()
    for row in rows:
        print(f"{row.id}  {row.created_at:%Y-%m-%d %H:%M}  {row.status:<9} {row.reason:<19} cohort={row.cohort}")
    print(f"{len(rows)} report(s)")
    return 0


async def report(db, report_id: uuid.UUID) -> int:
    row = await db.get(FzReport, report_id)
    if row is None:
        print("no such report")
        return 1
    members = select(FzProfile).where(FzProfile.user_id.in_([row.reporter_id, row.reported_id]))
    names = {profile.user_id: profile for profile in await db.scalars(members)}
    for label, user_id in (("reporter", row.reporter_id), ("reported", row.reported_id)):
        profile = names.get(user_id)
        suspended = " (suspended)" if profile and profile.suspended_at else ""
        print(f"{label}: player {profile.id if profile else '-'} {profile.display_name if profile else ''}{suspended}")
    print(f"reason: {row.reason}  status: {row.status}  cohort: {row.cohort}  battle: {row.battle_id or '-'}")
    print(f"created: {row.created_at:%Y-%m-%d %H:%M} UTC")
    print(f"details: {row.details or '(none)'}")
    actions = await db.scalars(
        select(FzModerationAction).where(FzModerationAction.report_id == row.id).order_by(FzModerationAction.created_at)
    )
    for action in actions:
        print(f"  {action.created_at:%Y-%m-%d %H:%M} {action.action} by {action.actor}: {action.note or ''}")
    return 0


async def resolve(db, args: argparse.Namespace) -> int:
    row = await db.scalar(select(FzReport).where(FzReport.id == args.report_id).with_for_update())
    if row is None:
        print("no such report")
        return 1
    if row.status in ("actioned", "dismissed"):
        print(f"already {row.status}: nothing to do")
        return 1
    print(f"report {row.id}: {row.status} -> {args.outcome}")
    if not args.apply:
        print("dry run: add --apply to record it")
        return 0
    row.status, row.reviewed_at = args.outcome, func.now()
    action = {"reviewed": "review", "actioned": "resolve", "dismissed": "dismiss"}[args.outcome]
    _audit(db, action, args.actor, report_id=row.id, target=row.reported_id, note=args.note)
    await db.commit()
    print("recorded")
    return 0


# ------------------------------------------------------------------ suspension
async def _member(db, player_id: uuid.UUID) -> FzProfile | None:
    return await db.scalar(select(FzProfile).where(FzProfile.id == player_id).with_for_update())


def _pending_of(user_id: uuid.UUID):
    return (
        FzFriendRequest.status == "pending",
        or_(FzFriendRequest.sender_id == user_id, FzFriendRequest.recipient_id == user_id),
    )


async def suspend(db, args: argparse.Namespace) -> int:
    profile = await _member(db, args.player_id)
    if profile is None:
        print("no such member")
        return 1
    if profile.suspended_at is not None:
        print("already suspended")
        return 1
    user_id = profile.user_id
    open_ids = (
        await db.scalars(
            select(FzBattle.id)
            .join(FzBattlePlayer, FzBattlePlayer.battle_id == FzBattle.id)
            .where(FzBattlePlayer.user_id == user_id, FzBattle.status.in_(FZ_BATTLE_OPEN))
        )
    ).all()
    pending = await db.scalar(select(func.count()).select_from(FzFriendRequest).where(*_pending_of(user_id)))
    print(f"suspend player {profile.id}: {pending} pending request(s) end, {len(open_ids)} open battle(s) end")
    if not args.apply:
        print("dry run: add --apply to suspend")
        return 0
    # Hidden at once (every read checks suspended_at), then cleaned up under the same locks the API takes: the pair
    # lock per counterparty, then the member's live lock and each battle row (plan section 9, lock order).
    profile.suspended_at = func.now()
    _audit(db, "suspend", args.actor, target=user_id, note=args.reason)
    await db.commit()
    others = await db.scalars(
        select(FzFriendRequest.sender_id)
        .where(*_pending_of(user_id), FzFriendRequest.sender_id != user_id)
        .union(
            select(FzFriendRequest.recipient_id).where(*_pending_of(user_id), FzFriendRequest.recipient_id != user_id)
        )
    )
    for other in list(others):
        await relations.lock_pair(db, user_id, other)
        await db.execute(
            update(FzFriendRequest)
            .where(
                *_pending_of(user_id), or_(FzFriendRequest.sender_id == other, FzFriendRequest.recipient_id == other)
            )
            .values(status="cancelled", responded_at=func.now())
        )
        await db.commit()
    await battles.lock_live(db, user_id)
    open_ids = (
        await db.scalars(
            select(FzBattle.id)
            .join(FzBattlePlayer, FzBattlePlayer.battle_id == FzBattle.id)
            .where(FzBattlePlayer.user_id == user_id, FzBattle.status.in_(FZ_BATTLE_OPEN))
        )
    ).all()
    for battle_id in open_ids:
        battle = await db.scalar(
            select(FzBattle).where(FzBattle.id == battle_id).with_for_update().execution_options(populate_existing=True)
        )
        players = (await db.scalars(select(FzBattlePlayer).where(FzBattlePlayer.battle_id == battle_id))).all()
        found = battles.Loaded(battle, {row.role: row for row in players}, "challenger")
        if battle.status == "active":
            await battles.end_battle(db, found, "completed", "suspended", "void")
        elif battle.status in FZ_BATTLE_OPEN:
            await battles.end_battle(db, found, "cancelled", "suspended")
    await db.commit()
    print("suspended")
    return 0


async def unsuspend(db, args: argparse.Namespace) -> int:
    profile = await _member(db, args.player_id)
    if profile is None or profile.suspended_at is None:
        print("no such suspended member")
        return 1
    print(f"unsuspend player {profile.id}")
    if not args.apply:
        print("dry run: add --apply to unsuspend")
        return 0
    profile.suspended_at = None
    _audit(db, "unsuspend", args.actor, target=profile.user_id)
    await db.commit()
    print("unsuspended")
    return 0


# ------------------------------------------------------------------ the QA cohort (section 6)
async def qa_counts(db) -> dict[str, int]:
    counts = {}
    for name, table in QA_TABLES:
        counts[name] = await db.scalar(select(func.count()).select_from(table).where(table.cohort == "qa"))
    counts["qb_sets (fz_qa)"] = await db.scalar(select(func.count()).select_from(QbSet).where(QbSet.mode == "fz_qa"))
    return counts


async def qa_report(db) -> int:
    for name, count in (await qa_counts(db)).items():
        print(f"{name:<22} {count}")
    return 0


async def qa_cleanup(db, apply: bool, actor: str) -> int:
    counts = await qa_counts(db)
    for name, count in counts.items():
        print(f"{name:<22} {count}")
    if not apply:
        print("dry run: add --apply to delete every QA row above")
        return 0
    # Order: reports point at battles; battles own their players and rounds; players point at the QA sets; profiles
    # own the rest (requests, friendships, blocks). Live rows can never reference QA ones (composite cohort keys).
    await db.execute(delete(FzReport).where(FzReport.cohort == "qa"))
    await db.execute(delete(FzBattle).where(FzBattle.cohort == "qa"))
    await db.execute(delete(QbSet).where(QbSet.mode == "fz_qa"))
    await db.execute(delete(FzProfile).where(FzProfile.cohort == "qa"))
    _audit(db, "qa_cleanup", actor, note=f"deleted {sum(counts.values())} rows")
    await db.commit()
    print("deleted")
    return 0


async def qa_verify(db) -> int:
    counts = await qa_counts(db)
    live = {str(battle_id) for battle_id in await db.scalars(select(FzBattle.id).where(FzBattle.cohort == "live"))}
    links = await db.scalars(
        select(UserNotification.link).where(
            UserNotification.user_id.in_(select(FzProfile.user_id).where(FzProfile.cohort == "live")),
            UserNotification.kind.in_(("battle_invite", "battle_accepted", "battle_finished")),
        )
    )
    stray = sum(1 for link in links if (battle := (link or "").rpartition("battle=")[2]) and battle not in live)
    for name, count in counts.items():
        print(f"{name:<22} {count}")
    print(f"live bell links to a battle that is not live: {stray}")
    clean = not any(counts.values()) and stray == 0
    print("CLEAN" if clean else "NOT CLEAN")
    return 0 if clean else 1


RETENTION = {"requests": timedelta(days=30), "invites": timedelta(days=30), "reports": timedelta(days=180)}
AUDIT_RETENTION = timedelta(days=365)


def _retention_targets(now):
    """What retention removes, as (name, table, condition)."""
    return (
        (
            "friend requests",
            FzFriendRequest,
            or_(
                and_(FzFriendRequest.status != "pending", FzFriendRequest.responded_at < now - RETENTION["requests"]),
                and_(FzFriendRequest.status == "pending", FzFriendRequest.created_at < now - 2 * RETENTION["requests"]),
            ),
        ),
        (
            "battle invitations that never started",
            FzBattle,
            and_(
                FzBattle.status.in_(("declined", "expired", "cancelled")),
                FzBattle.question_ids.is_(None),
                FzBattle.ended_at < now - RETENTION["invites"],
                ~select(FzReport.id).where(FzReport.battle_id == FzBattle.id).exists(),
            ),
        ),
        (
            "resolved reports",
            FzReport,
            and_(FzReport.status.in_(("actioned", "dismissed")), FzReport.reviewed_at < now - RETENTION["reports"]),
        ),
        ("moderation audit rows", FzModerationAction, FzModerationAction.created_at < now - AUDIT_RETENTION),
    )


async def retention(db, apply: bool) -> int:
    now = await db.scalar(select(func.now()))
    targets = _retention_targets(now)
    for name, table, condition in targets:
        print(f"{name:<38} {await db.scalar(select(func.count()).select_from(table).where(condition))}")
    if not apply:
        print("dry run: add --apply to delete them")
        return 0
    for _, table, condition in targets:
        await db.execute(delete(table).where(condition))
    await db.commit()
    print("deleted")
    return 0


async def run(db, args: argparse.Namespace) -> int:
    if args.action == "reports":
        return await reports(db, args.status)
    if args.action == "report":
        return await report(db, args.report_id)
    if args.action == "resolve":
        return await resolve(db, args)
    if args.action == "suspend":
        return await suspend(db, args)
    if args.action == "unsuspend":
        return await unsuspend(db, args)
    if args.action == "qa-report":
        return await qa_report(db)
    if args.action == "qa-cleanup":
        return await qa_cleanup(db, args.apply, args.actor)
    if args.action == "retention":
        return await retention(db, args.apply)
    return await qa_verify(db)


async def _run(args: argparse.Namespace) -> int:
    database = Database(Settings())
    try:
        async with database.sessionmaker() as db:
            return await run(db, args)
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
