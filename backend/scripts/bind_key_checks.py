"""Bind the bank's AI blind checks to the exact question content they checked (owner decision U1, option ii).

    python scripts/bind_key_checks.py <qbank_root> --verification-commit <sha> [--apply]

An authored question's key passed a three-way AI blind check when it was written; the record sits in its content
file, but nothing tied it to the text it checked. This writes qb_key_checks (question_id, content_hash) only where
all of these hold, so Friend Zone (FRIEND_ZONE_KEY_POLICY=human_or_ai) can rely on it:

1. the question is authored, active, and its record passes the importer's own check (notes and closed-book agree,
   one defensible answer, who and when);
2. git proves its content file is unchanged since the verification commit: the file's last commit is that commit,
   and the working tree holds no change to it;
3. the database row's content_hash equals the hash of the file's text (the imported text is the checked text).

A question edited later gets a new content_hash, so its old binding stops counting at once; it needs a new check.
Dry run until --apply. Output: counts only. Run on the host like import_questions.py (systemd-run with api.env), from
the deployed checkout so git can prove the history.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess  # nosec B404: git, with fixed arguments, on the operator's own checkout
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import QbKeyCheck, QbQuestion  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.qbank import netsec  # noqa: E402
from app.qbank.validate import read_bank  # noqa: E402
from app.settings import Settings  # noqa: E402

METHOD = "ai_blind_3way"
COMMIT = re.compile(r"[0-9a-f]{7,40}")


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603  nosec B603: fixed git arguments, no shell
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def resolve(root: Path, commit: str) -> str:
    """The full SHA of the verification commit, given as hex (never as an option or a ref name)."""
    if not COMMIT.fullmatch(commit):
        raise SystemExit("--verification-commit must be a commit hash (7 to 40 hex characters)")
    return _git(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")


def _clean(top: Path, relative: str) -> bool:
    """Tracked by git, and the working tree and index hold no change to it (per file: no output parsing)."""
    tracked = subprocess.run(  # noqa: S603  nosec B603
        ["git", "-C", str(top), "ls-files", "--error-unmatch", "--", relative],  # noqa: S607
        capture_output=True,
        check=False,
    )
    unchanged = subprocess.run(  # noqa: S603  nosec B603
        ["git", "-C", str(top), "diff", "--quiet", "HEAD", "--", relative],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return tracked.returncode == 0 and unchanged.returncode == 0


def unchanged_files(root: Path, full: str) -> set[str]:
    """The content files (repo-relative) whose last commit is `full` and that the working tree leaves as they are."""
    top = Path(_git(root, "rev-parse", "--show-toplevel"))
    files = set()
    for path in sorted(root.glob("content/*/*/questions.jsonl")):
        relative = path.resolve().relative_to(top.resolve()).as_posix()
        if _clean(top, relative) and _git(top, "log", "-1", "--format=%H", "--", relative) == full:
            files.add(relative)
    return files


def candidates(root: Path, commit: str) -> tuple[dict[str, str], Counter]:
    """question_id -> the file's content hash, for every question that may be bound; and why the others may not."""
    bank = read_bank(root)
    if bank.errors:
        raise SystemExit(f"the bank has {len(bank.errors)} error(s): run the importer's check first")
    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    proven = unchanged_files(root, resolve(root, commit))
    hashes = {row["question_id"]: row["content_hash"] for row in bank.rows}
    found, skipped = {}, Counter()
    for question in bank.questions:
        question_id = question.get("question_id")
        where = question["_where"].rsplit(":", 1)[0]
        relative = (root.resolve() / "content" / where).relative_to(top).as_posix()
        if question.get("key_origin") != "authored" or not question.get("active", True):
            skipped["not authored or inactive"] += 1
        elif not netsec.verified(question.get("verification")):
            skipped["no passing blind check"] += 1
        elif relative not in proven:
            skipped["file changed since the verification commit"] += 1
        elif question_id not in hashes:
            skipped["not importable"] += 1
        else:
            found[question_id] = hashes[question_id]
    return found, skipped


async def bind(db, found: dict[str, str], commit: str, apply: bool) -> Counter:
    counts = Counter()
    current = dict((await db.execute(select(QbQuestion.question_id, QbQuestion.content_hash))).all())
    rows = []
    for question_id, file_hash in sorted(found.items()):
        if current.get(question_id) != file_hash:
            counts["database text differs from the file"] += 1
            continue
        rows.append(
            {
                "question_id": question_id,
                "content_hash": file_hash,
                "method": METHOD,
                "single_defensible": True,
                "checked_by": f"bank-verification@{commit[:12]}",
            }
        )
    counts["bindable"] = len(rows)
    if apply and rows:
        for start in range(0, len(rows), 500):
            result = await db.execute(insert(QbKeyCheck).values(rows[start : start + 500]).on_conflict_do_nothing())
            counts["bound now"] += result.rowcount
        await db.commit()
    return counts


async def _run(args: argparse.Namespace) -> int:
    full = resolve(args.root, args.verification_commit)
    found, skipped = candidates(args.root, full)
    database = Database(Settings())
    try:
        async with database.sessionmaker() as db:
            counts = await bind(db, found, full, args.apply)
    finally:
        await database.dispose()
    for reason, count in sorted({**skipped, **counts}.items()):
        print(f"{reason}: {count}")
    print("applied" if args.apply else "dry run: add --apply to write the bindings")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="the qbank/ folder of the deployed checkout")
    parser.add_argument("--verification-commit", required=True, help="the commit that added the blind checks")
    parser.add_argument("--apply", action="store_true")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
