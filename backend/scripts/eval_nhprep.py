"""Evaluate the NHprep finder and cue parse on a labelled case file (plan rev 4 §10 G4), or time it (--latency).

    python scripts/eval_nhprep.py [--cases evals/nhprep/dev.jsonl] [--dense off|on] [--vectors vectors.jsonl]
                                  [--tuning tuning.json] [--timeout 1.0] [--json]
    python scripts/eval_nhprep.py --latency [--dense on] < messages.txt     # one typed message per line

Retrieval cases run end to end: intent.parse(message) -> search.answer(...). Metrics per category: R@1, R@5, MRR,
nDCG@5 (graded relevance by lesson external_id; a case the parse misses scores 0), multi-match top-1 and "all
equivalents in the top 6", confident false positives, trainer precision, intent accuracy and false triggers on
ordinary chat (bank first, as in the chat turn), per-leg p50/p95 latency. --dense off (default) runs the title and
lexical legs only; --dense on uses build_embeddings (masked; EMBEDDING_PROVIDER=fake gives keyword-hash vectors that
are NOT semantic); --vectors uses precomputed query vectors (nhprep_embed_offline.py). Output carries case ids,
counts and numbers only, never NHprep text or the messages. Uses DATABASE_URL (a *_test database or the host's own,
read-only: the finder only reads).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "evals" / "nhprep" / "dev.jsonl"
LESSONS = "SELECT l.id, l.external_id, l.title FROM nhprep_lessons l WHERE l.removed_at IS NULL"
ITEMS = (
    "SELECT l.external_id, i.kind, i.item_key, i.title "
    "FROM nhprep_lesson_items i JOIN nhprep_lessons l ON l.id = i.lesson_id"
)
TRAINERS = (
    "SELECT l.id, t.display_name FROM nhprep_lessons l JOIN nhprep_course_trainers ct ON ct.course_id = l.course_id "
    "JOIN nhprep_trainers t ON t.id = ct.trainer_id"
)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def spread(legs: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
    return {
        leg: {"p50": percentile(v, 0.5), "p95": percentile(v, 0.95), "n": len(v)} for leg, v in sorted(legs.items())
    }


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def ndcg(ranked: list[str], relevant: dict[str, int], k: int = 5) -> float:
    gains = [(2 ** relevant.get(lesson, 0) - 1) / math.log2(i + 2) for i, lesson in enumerate(ranked[:k])]
    ideal = [(2**g - 1) / math.log2(i + 2) for i, g in enumerate(sorted(relevant.values(), reverse=True)[:k])]
    return sum(gains) / sum(ideal) if ideal else 0.0


def score_case(ranked: list[str], relevant: dict[str, int]) -> dict[str, float]:
    first = next((i for i, lesson in enumerate(ranked) if relevant.get(lesson, 0) > 0), None)
    return {
        "r1": float(first == 0),
        "r5": float(first is not None and first < 5),
        "mrr": 0.0 if first is None else 1.0 / (first + 1),
        "ndcg5": ndcg(ranked, relevant),
    }


def _embedder(args: argparse.Namespace, settings: Any) -> Any:
    if args.vectors:
        from app.nhprep.vectors import PrecomputedEmbeddings, read_vectors

        with open(args.vectors, encoding="utf-8") as stream:
            return PrecomputedEmbeddings(*read_vectors(stream))
    if args.dense == "on":
        from app.rag.embeddings import build_embeddings

        return build_embeddings(settings)  # masked: secrets never reach the provider
    return None


def _message(case: dict, titles: dict[str, str], items: dict[tuple[str, str, str], str]) -> str | None:
    """Templates name NHprep titles by id ({title}, {chapter}, {note}); the case file never copies them."""
    message = case["message"]
    try:
        if "title_of" in case:
            message = message.replace("{title}", titles[case["title_of"]].lower())
        if "chapter_of" in case:
            message = message.replace(
                "{chapter}", items[(case["chapter_of"][0], "chapter", case["chapter_of"][1])].lower()
            )
        if "note_of" in case:
            message = message.replace("{note}", items[(case["note_of"][0], "note", case["note_of"][1])].lower())
    except KeyError:
        return None
    return message


def route(message: str) -> tuple[str, Any]:
    from app.nhprep import intent
    from app.qbank.request import parse as bank_parse

    if bank_parse(message) is not None:
        return "bank", None
    query = intent.parse(message)
    return ("nhprep", query) if query is not None else ("chat", None)


async def evaluate(args: argparse.Namespace, cases: list[dict]) -> dict[str, Any]:
    from sqlalchemy import text

    from app.db.session import Database
    from app.nhprep import search
    from app.settings import get_settings

    settings = get_settings()
    tuning = args.tuning_values or search.TUNING
    db = Database(settings)
    embeddings = _embedder(args, settings)
    try:
        async with db.sessionmaker() as session:
            lessons = (await session.execute(text(LESSONS))).all()
            items = {(str(e), k, key): t for e, k, key, t in (await session.execute(text(ITEMS))).all()}
            taught = defaultdict(set)
            for lesson, name in (await session.execute(text(TRAINERS))).all():
                taught[lesson].add(name)
        external = {row.id: str(row.external_id) for row in lessons}
        titles = {str(row.external_id): row.title for row in lessons}
        per: dict[str, list[dict[str, float]]] = defaultdict(list)
        legs: dict[str, list[float]] = defaultdict(list)
        out: dict[str, Any] = {"confident": 0, "confident_wrong": [], "trainer_returned": 0, "trainer_right": 0,
                               "multi_top1": [], "multi_all6": [], "missed_parse": [], "unresolved": [],
                               "status": defaultdict(int), "negative_status": defaultdict(int),
                               "intent_views_wrong": []}  # fmt: skip
        for case in (c for c in cases if c["kind"] == "retrieval"):
            message = _message(case, titles, items)
            case = {**case, "relevant": {k: v for k, v in case["relevant"].items() if k in titles}}  # imported only
            if message is None or (case["category"] != "negative" and not case["relevant"]):
                out["unresolved"].append(case["id"])
                continue
            kind, query = route(message)
            ranked: list[str] = []
            if query is None:
                if case["category"] != "negative":
                    out["missed_parse"].append(case["id"])
            else:
                if case.get("views") and list(query.views) != case["views"]:
                    out["intent_views_wrong"].append(case["id"])
                result = await search.answer(db, embeddings, query, args.timeout, tuning=tuning)
                out["status"][result.status] += 1
                if case["category"] == "negative":
                    out["negative_status"][result.status] += 1
                for leg, ms in result.trace.get("ms", {}).items():
                    legs[leg].append(ms)
                if result.primary is not None:
                    found = [result.primary, *result.alternatives]
                    ranked = [external[m.lesson_id] for m in found]
                    if case.get("trainer"):
                        out["trainer_returned"] += len(found)
                        out["trainer_right"] += sum(case["trainer"] in taught[m.lesson_id] for m in found)
                if result.status == "confident":
                    out["confident"] += 1
                    if case["relevant"].get(ranked[0], 0) == 0:
                        out["confident_wrong"].append(case["id"])
            if case["category"] != "negative":
                per[case["category"]].append(score_case(ranked, case["relevant"]))
            if case["category"] == "multi_match":
                equivalents = {k for k, v in case["relevant"].items() if v == 2}
                out["multi_top1"].append(bool(ranked) and ranked[0] in equivalents)
                out["multi_all6"].append(equivalents <= set(ranked[:6]))
    finally:
        await db.dispose()
    return summarise(cases, per, legs, out)


def summarise(cases: list[dict], per: dict, legs: dict, out: dict) -> dict[str, Any]:
    table = {}
    everything = [s for scores in per.values() for s in scores]
    for category, scores in sorted(per.items()) + [("ALL", everything)]:
        n = len(scores)
        mean = {
            m: round(statistics.fmean(s[m] for s in scores), 3) if n else None for m in ("r1", "r5", "mrr", "ndcg5")
        }
        table[category] = {"n": n, **mean, "r5_ci95": wilson(int(sum(s["r5"] for s in scores)), n)}
    intents = [c for c in cases if c["kind"] == "intent"]
    right, false_triggers, chats, wrong = 0, 0, 0, []
    for case in intents:
        kind, query = route(case["message"])
        ok = kind == case["expect"] and (
            kind != "nhprep" or not case.get("views") or list(query.views) == case["views"]
        )
        right += ok
        if not ok:
            wrong.append(case["id"])
        if case["expect"] == "chat":
            chats += 1
            false_triggers += kind == "nhprep"
    negatives = [c for c in cases if c.get("category") == "negative"]
    return {
        "metrics": table,
        "multi_match": {
            "top1_in_set": round(statistics.fmean(out["multi_top1"]), 3) if out["multi_top1"] else None,
            "all_equivalents_top6": round(statistics.fmean(out["multi_all6"]), 3) if out["multi_all6"] else None,
        },
        "confident": out["confident"],
        "confident_false_positives": len(out["confident_wrong"]),
        "confident_fp_rate": round(len(out["confident_wrong"]) / out["confident"], 3) if out["confident"] else None,
        "confident_fp_ids": out["confident_wrong"],
        "negatives": len(negatives),
        "trainer_precision": ratio(out["trainer_right"], out["trainer_returned"]),
        "trainer_returned": out["trainer_returned"],
        "intent": {"cases": len(intents), "accuracy": round(right / len(intents), 3) if intents else None,
                   "false_triggers": false_triggers, "chat_cases": chats,
                   "false_trigger_rate": round(false_triggers / chats, 3) if chats else None, "wrong_ids": wrong},
        "retrieval_parse_missed": out["missed_parse"],
        "retrieval_views_wrong": out["intent_views_wrong"],
        "unresolved_templates": out["unresolved"],
        "status": dict(out["status"]),
        "negative_status": dict(out["negative_status"]),
        "latency_ms": spread(legs),
    }  # fmt: skip


async def latency(args: argparse.Namespace, messages: list[str]) -> dict[str, Any]:
    from app.db.session import Database
    from app.nhprep import search
    from app.settings import get_settings

    settings = get_settings()
    db = Database(settings)
    embeddings = _embedder(args, settings)
    legs: dict[str, list[float]] = defaultdict(list)
    outcomes: dict[str, int] = defaultdict(int)
    try:
        for message in messages:
            kind, query = route(message)
            if query is None:
                outcomes[f"not_nhprep:{kind}"] += 1
                continue
            result = await search.answer(db, embeddings, query, args.timeout)
            outcomes[result.status] += 1
            for leg, ms in result.trace.get("ms", {}).items():
                legs[leg].append(ms)
    finally:
        await db.dispose()
    return {
        "messages": len(messages),
        "outcomes": dict(outcomes),
        "latency_ms": spread(legs),
    }  # fmt: skip


def print_table(report: dict[str, Any]) -> None:
    print(f"{'category':18} {'n':>4} {'R@1':>6} {'R@5':>6} {'MRR':>6} {'nDCG@5':>7}  R@5 95% CI")
    for category, row in report["metrics"].items():
        values = " ".join(f"{row[m]:>6.3f}" if row[m] is not None else f"{'-':>6}" for m in ("r1", "r5", "mrr"))
        nd = f"{row['ndcg5']:>7.3f}" if row["ndcg5"] is not None else f"{'-':>7}"
        print(f"{category:18} {row['n']:>4} {values} {nd}  {row['r5_ci95']}")
    rest = {k: v for k, v in report.items() if k != "metrics"}
    print(json.dumps(rest, indent=1, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--dense", choices=("off", "on"), default="off")
    parser.add_argument("--vectors", help="precomputed query vectors (nhprep-vectors/1)")
    parser.add_argument("--tuning", help="a tuning.json to try instead of app/nhprep/tuning.json")
    parser.add_argument("--timeout", type=float, default=1.0, help="NHPREP_FIND_TIMEOUT_S")
    parser.add_argument("--latency", action="store_true", help="time the finder on messages read from stdin")
    parser.add_argument("--json", action="store_true", help="print the report as one JSON object")
    args = parser.parse_args()
    if args.latency:
        messages = [line.strip() for line in sys.stdin if line.strip()]
        print(json.dumps(asyncio.run(latency(args, messages)), indent=1))
        return
    with open(args.cases, encoding="utf-8") as stream:
        cases = [json.loads(line) for line in stream if line.strip()]
    args.tuning_values = json.loads(Path(args.tuning).read_text(encoding="utf-8")) if args.tuning else None
    report = asyncio.run(evaluate(args, cases))
    print(json.dumps(report, default=str) if args.json else "", end="\n" if args.json else "")
    if not args.json:
        print_table(report)


if __name__ == "__main__":
    main()
