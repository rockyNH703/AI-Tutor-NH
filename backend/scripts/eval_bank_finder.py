"""Measure the bank turn's request parser and topic finder on the frozen query set (evals/qbank_requests/).

    python scripts/eval_bank_finder.py --database-url postgresql+asyncpg://.../netsec_qbank_eval_test \
        [--root ../qbank] [--split dev | --file <cases.jsonl>] [--import] [--show 20]

`--file`: a case file outside the repository (a held-out set run once by session 1; it is not read while tuning).

The database must be a *_test one: with --import, the bank's manifests are imported into it first (questions
too, if any). No model is called. Topics are matched by title against each case's set of acceptable titles.

Reported:
- route accuracy;
- false triggers: an ordinary or NHprep message read as a quiz request;
- for bank cases:
  - hit: at least one found topic is acceptable;
  - precision: the share of found topics that are acceptable;
  - status counts;
- latency p50/p95 of parse + find (the database part of the bank turn).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.qbank import finder  # noqa: E402
from app.qbank.importer import import_bank  # noqa: E402
from app.qbank.request import parse  # noqa: E402


def _route(expected: str) -> str:
    return expected if expected in ("bank", "next") else "normal"


async def run(args: argparse.Namespace) -> int:
    if not args.database_url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        print("refused: the database name must end in _test", file=sys.stderr)
        return 2
    engine = create_async_engine(args.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    if args.do_import:
        async with sessions() as session:
            report = await import_bank(session, Path(args.root), apply=True)
        if report.errors:
            print("import refused:", *report.errors[:10], sep="\n  ")
            return 1
    split = Path(args.file) if args.file else ROOT / "evals" / "qbank_requests" / f"{args.split}.jsonl"
    cases = [json.loads(line) for line in split.read_text(encoding="utf-8").splitlines() if line.strip()]
    stats: dict[str, Counter] = defaultdict(Counter)
    misses: list[tuple] = []
    timings: list[float] = []
    async with sessions() as session:
        await finder.find(session, "warm up", need_questions=False)
        books = set(await session.scalars(text("SELECT book_id FROM qb_books WHERE curriculum = 'netsec'")))
        for case in cases:
            expect, category = case["expect"], case["category"]
            started = time.perf_counter()
            request = parse(case["query"])
            route = request.route if request else "normal"
            found = None
            if request is not None and request.route == "bank":
                topic, loose = request.topic, request.lesson_named
                if request.refers_to_chat and case.get("context"):
                    topic, loose = case["context"][-1], True
                found = await finder.find(
                    session, topic, track=request.track, goal=args.goal, need_questions=False, loose=loose
                )
            timings.append((time.perf_counter() - started) * 1000)
            counter = stats[category]
            counter["cases"] += 1
            counter["route_ok"] += route == _route(expect["route"])
            if _route(expect["route"]) == "normal" and route != "normal":
                counter["false_trigger"] += 1
                misses.append((category, case["query"], "false trigger", route))
            if expect["route"] != "bank" or route != "bank" or found is None:
                if expect["route"] == "bank" and route != "bank":
                    misses.append((category, case["query"], "not routed", route))
                continue
            wanted = set(expect["topics"])
            titles = [t.title for t in found.topics]
            counter["status_" + found.status] += 1
            if not wanted and expect["track"] is not None and expect["track"] not in books:
                ok = not titles  # a track the bank has no questions for: the right answer is "none yet"
                counter["hit"] += ok
                counter["precision_sum_x1000"] += 1000 if ok else 0
            elif not wanted:  # a whole track: any found topic of the right track
                ok = bool(titles) and (expect["track"] is None or found.book_id == expect["track"])
                counter["hit"] += ok
                counter["precision_sum_x1000"] += 1000 if ok else 0
            else:
                hit = bool(wanted & set(titles))
                counter["hit"] += hit
                counter["precision_sum_x1000"] += (
                    round(1000 * sum(t in wanted for t in titles) / len(titles)) if titles else 0
                )
                if not hit or any(t not in wanted for t in titles):
                    misses.append((category, case["query"], found.status, titles[:4], sorted(wanted)[:3]))
            counter["bank_cases"] += 1
    await engine.dispose()
    total = Counter()
    for counter in stats.values():
        total.update(counter)
    print(f"split={args.file or args.split} cases={total['cases']}")
    for category in sorted(stats):
        c = stats[category]
        line = f"  {category:16} cases={c['cases']:3} route={c['route_ok'] / c['cases']:.3f}"
        if c["bank_cases"]:
            precision = c["precision_sum_x1000"] / 1000 / c["bank_cases"]
            line += f" hit={c['hit'] / c['bank_cases']:.3f} precision={precision:.3f}"
        if c["false_trigger"]:
            line += f" false_triggers={c['false_trigger']}"
        print(line)
    negatives = sum(stats[c]["cases"] for c in stats if c.startswith(("neg", "cross")))
    print(f"route accuracy {total['route_ok'] / total['cases']:.4f}")
    print(f"false triggers {total['false_trigger']} of {negatives} negatives/cross")
    if total["bank_cases"]:
        precision = total["precision_sum_x1000"] / 1000 / total["bank_cases"]
        print(f"bank hit {total['hit'] / total['bank_cases']:.4f}  precision {precision:.4f}")
    ordered = sorted(timings)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    print(f"latency ms p50 {statistics.median(ordered):.1f}  p95 {p95:.1f}  max {ordered[-1]:.1f}")
    for miss in misses[: args.show]:
        print("  MISS", miss)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--root", default=str(ROOT.parent / "qbank"))
    parser.add_argument("--split", default="dev", choices=("dev", "test"))
    parser.add_argument("--file", help="a case file in the same line format, instead of a split")
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--show", type=int, default=25)
    parser.add_argument("--goal", help="the student's certification goal (a book id, e.g. ccna), as the chat passes it")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
