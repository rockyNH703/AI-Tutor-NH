"""Precision and recall of the concept graph's deterministic layer on the hand-labelled gold set.

    python scripts/eval_concepts.py [--gold tests/fixtures/concepts_gold.jsonl] [--json]

The gold set is synthetic (CC0) textbook-style sentences, written independently of the patterns, each labelled
with every fact it states in the closed predicate set; a third of them state nothing the patterns may claim
(negatives). A fact counts only with the same subject, predicate and object (or attribute and value). Exit 1 when
precision falls below the plan's gate (0.95); recall is reported, not gated (precision over recall, by design).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge.concepts import facts  # noqa: E402

GOLD = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "concepts_gold.jsonl"
PRECISION_GATE = 0.95


def _key(subject: str, predicate: str, third: str | None, value: str | None) -> tuple[str, str, str, str]:
    return subject, predicate, str(third or ""), str(value or "").casefold()


def evaluate(path: Path = GOLD) -> dict:
    tp = fp = fn = 0
    by_predicate: Counter[str] = Counter()
    errors, false_positives = [], []
    sentences = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for case in sentences:
        gold = {_key(*fact) for fact in case["facts"]}
        found = {_key(f.subject, f.predicate, f.object or f.attribute, f.value): f for f in facts(case["text"])}
        for key in found.keys() & gold:
            tp += 1
            by_predicate[f"{key[1]}:tp"] += 1
        for key in found.keys() - gold:
            fp += 1
            by_predicate[f"{key[1]}:fp"] += 1
            false_positives.append({"id": case["id"], "fact": list(key)})
        for key in gold - found.keys():
            fn += 1
            by_predicate[f"{key[1]}:fn"] += 1
        for fact in found.values():  # every claim is backed by a verbatim span of the sentence
            span = case["text"][fact.start : fact.end]
            if not span or (fact.value is not None and fact.value.casefold() not in span.casefold()):
                errors.append({"id": case["id"], "badEvidence": span})
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "sentences": len(sentences),
        "negativeSentences": sum(1 for case in sentences if not case["facts"]),
        "goldFacts": tp + fn,
        "extracted": tp + fp,
        "truePositives": tp,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "byPredicate": dict(sorted(by_predicate.items())),
        "falsePositives": false_positives,
        "evidenceErrors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    report = evaluate(args.gold)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"{report['sentences']} sentences ({report['negativeSentences']} negative), {report['goldFacts']} gold "
            f"facts: precision {report['precision']:.3f}, recall {report['recall']:.3f}, "
            f"{len(report['falsePositives'])} false positives, {len(report['evidenceErrors'])} evidence errors"
        )
    return 0 if report["precision"] >= PRECISION_GATE and not report["evidenceErrors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
