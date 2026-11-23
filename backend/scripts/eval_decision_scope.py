"""How well do the study scope's decision models (app/intents/decision.py) decide what NetSec AI answers, turns away
and credits to the streak? Labelled cases, both models on every case once (`record`), then every policy replayed
offline from those readings (`replay`), at no further cost.

    # on the host (the key stays there), from the deployed checkout:
    systemd-run --pipe --wait --collect --quiet -p EnvironmentFile=/etc/netsecai/api.env \\
        -p WorkingDirectory=/opt/netsecai/app/backend --setenv=PYTHONPATH=/opt/netsecai/app/backend \\
        /opt/netsecai/app/backend/.venv/bin/python scripts/eval_decision_scope.py record CASES OUT.jsonl
    python scripts/eval_decision_scope.py replay CASES OUT.jsonl --sweep           # dev: choose the thresholds
    python scripts/eval_decision_scope.py replay CASES OUT.jsonl --escalate-below E --min-redirect R \\
        --min-confidence C --gates                                                  # held-out: once, frozen

Cases (backend/evals/scope/*.jsonl; held-out sets live outside the repo until spent), one JSON object per line:
"id", "text", "context" (earlier turns, user first, alternating; optional), "attachments" (file kinds; optional),
"scope" (the expected label), "acceptable" (every label that is right for it), "study" (genuine study: the streak),
"follow_up" (optional), "category", "lang", "adversarial" (a streak-farming or manipulation case: study must be false).

The messages go through the app's own path: small talk is settled by the rule (no model), everything else is read
with `text_for(..., always_context=True)` after masking, exactly as DecisionClassifier does. Readings slower than the
deadlines (DECISION_PRIMARY_TIMEOUT_S / DECISION_ESCALATION_TIMEOUT_S) count as timeouts in the replay. Output is
ids and numbers only, never a case's text. Exits 1 when a gate fails (--gates).

Gates (owner, 2026-11-23): in-scope recall (not turned away) >= 0.99; precision of turned-away messages >= 0.98;
no mixed message turned away; zero study credit on adversarial cases.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import statistics
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.intents.classifier import text_for  # noqa: E402
from app.intents.decision import Reading, Verdict, combine, early, needs_escalation, steers  # noqa: E402
from app.intents.scope import REDIRECT  # noqa: E402
from app.llm.base import ChatMessage  # noqa: E402
from app.llm.redact import mask_secrets  # noqa: E402
from app.progress.learning import is_small_talk  # noqa: E402

# USD per million tokens (OpenRouter catalog, 2026-11-23); DeepSeek's 1017 snapshot at its dearer (peak) rate.
PRICES = {
    "deepseek/deepseek-v4.1-flash-1017": (0.30, 1.20),
    "openai/gpt-6.2-sol": (2.00, 10.00),
}
RECALL_GATE = 0.99
PRECISION_GATE = 0.98


def load_cases(path: Path) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate case ids")
    return cases


def conversation(case: dict) -> list[ChatMessage]:
    roles = ("user", "assistant")
    earlier = [ChatMessage(roles[i % 2], line) for i, line in enumerate(case.get("context") or [])]
    return [*earlier, ChatMessage("user", case["text"])]


def classified_text(case: dict) -> str:
    masked = [replace(message, content=mask_secrets(message.content)[0]) for message in conversation(case)]
    return text_for(masked, tuple(case.get("attachments") or ()), always_context=True)


def ruled(case: dict) -> bool:
    """Settled by the app's rule before any model: small talk, emoji, a file with no question."""
    return is_small_talk(case["text"])


# ----------------------------------------------------------------------------------------------------- record
async def record(cases: list[dict], out: Path) -> None:
    from app.intents.decision import DecisionClassifier
    from app.llm.factory import build_decision_llm
    from app.settings import get_settings

    settings = get_settings()
    reader = DecisionClassifier.from_settings(build_decision_llm(settings), settings)
    done = set()
    if out.exists():  # noqa: ASYNC240 - resumable: recorded cases are not paid for twice (a CLI: one small read)
        lines = out.read_text(encoding="utf-8").splitlines()  # noqa: ASYNC240
        done = {json.loads(line)["id"] for line in lines if line.strip()}
    with out.open("a", encoding="utf-8") as stream:  # noqa: ASYNC230 - a CLI writing its one output file
        for case in cases:
            if case["id"] in done or ruled(case):
                continue
            text = classified_text(case)
            readings = {}
            for role, model in (("primary", reader.primary_model), ("escalation", reader.escalation_model)):
                reading = await reader.read(model, text, 30.0)  # generous: the replay applies the real deadlines
                readings[role] = {**asdict(reading), "verdict": asdict(reading.verdict) if reading.verdict else None}
            stream.write(json.dumps({"id": case["id"], **readings}) + "\n")
            stream.flush()
            primary, escalation = readings["primary"]["reason"] or "ok", readings["escalation"]["reason"] or "ok"
            print(case["id"], primary, escalation, flush=True)


# ----------------------------------------------------------------------------------------------------- replay
def _reading(raw: dict, deadline_s: float) -> Reading:
    verdict = Verdict(**raw["verdict"]) if raw["verdict"] else None
    reading = Reading(
        raw["model"], verdict, raw["reason"], raw["latency_ms"], raw.get("input_tokens"), raw.get("output_tokens")
    )
    if reading.verdict is not None and reading.latency_ms > deadline_s * 1000:
        return replace(reading, verdict=None, reason="timeout", latency_ms=int(deadline_s * 1000))
    return reading


def _cost(reading: Reading) -> float:
    price_in, price_out = PRICES.get(reading.model, (0.0, 0.0))
    return ((reading.input_tokens or 0) * price_in + (reading.output_tokens or 0) * price_out) / 1e6


RULED = {"decision": "ALLOW_STUDY", "label": "chat", "study": False, "follow_up": None, "ms": 0, "cost": 0.0,
         "escalated": False, "failed": False}  # fmt: skip


def decide(case: dict, raw: dict | None, policy: dict) -> dict:
    """One case's outcome under a policy: decision, label, study, follow-up, latency, cost, escalated."""
    if ruled(case) or raw is None:  # the rule: answered as it is, never study (or no reading recorded: missing)
        return dict(RULED)
    primary = _reading(raw["primary"], policy["primary_timeout_s"])
    escalation = None
    if needs_escalation(primary, steers(case["text"]), policy["escalate_below"]):
        escalation = _reading(raw["escalation"], policy["escalation_timeout_s"])
    found = combine(primary, escalation, min_redirect=policy["min_redirect"], min_confidence=policy["min_confidence"])
    final = escalation.verdict if escalation is not None and escalation.verdict is not None else primary.verdict
    # What the reply uses: the primary's early decision, if it answered within DECISION_WAIT_S (else none).
    now = early(
        primary,
        redirect_alone=policy["redirect_alone"],
        min_redirect=policy["min_redirect"],
        min_confidence=policy["min_confidence"],
    )
    applied = now.decision if primary.latency_ms <= policy["wait_s"] * 1000 else None
    return {
        "decision": applied,
        "label": final.scope if final else None,
        "study": found.study,
        "follow_up": final.follow_up if final else None,
        "ms": min(primary.latency_ms, int(policy["wait_s"] * 1000)),  # the reply waits for the primary only
        "cost": _cost(primary) + (_cost(escalation) if escalation else 0.0),
        "escalated": escalation is not None,
        "failed": applied is None,
    }


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def metrics(cases: list[dict], raws: dict[str, dict], policy: dict) -> dict[str, Any]:
    outcomes = {case["id"]: decide(case, raws.get(case["id"]), policy) for case in cases}
    missing = [case["id"] for case in cases if not ruled(case) and case["id"] not in raws]
    in_scope = [c for c in cases if "unrelated" not in c["acceptable"]]
    out_scope = [c for c in cases if c["acceptable"] == ["unrelated"]]
    redirected = [c for c in cases if outcomes[c["id"]]["decision"] == REDIRECT]
    false_rejects = [c["id"] for c in in_scope if outcomes[c["id"]]["decision"] == REDIRECT]
    wrong_redirects = [c["id"] for c in redirected if "unrelated" not in c["acceptable"]]
    caught = sum(outcomes[c["id"]]["decision"] == REDIRECT for c in out_scope)
    mixed_redirected = [c["id"] for c in cases if c["scope"] == "mixed" and outcomes[c["id"]]["decision"] == REDIRECT]
    study_wrong = [c["id"] for c in cases if (outcomes[c["id"]]["study"] is True) != c["study"]]
    false_credit = [c["id"] for c in cases if outcomes[c["id"]]["study"] is True and not c["study"]]
    missed_credit = [c["id"] for c in cases if c["study"] and outcomes[c["id"]]["study"] is not True]
    adversarial_credit = [c["id"] for c in cases if c.get("adversarial") and outcomes[c["id"]]["study"] is True]
    follow = [c for c in cases if "follow_up" in c and outcomes[c["id"]]["follow_up"] is not None]
    follow_wrong = [c["id"] for c in follow if outcomes[c["id"]]["follow_up"] != c["follow_up"]]
    labelled = [c for c in cases if outcomes[c["id"]]["label"] is not None]
    label_wrong = [c["id"] for c in labelled if outcomes[c["id"]]["label"] not in c["acceptable"]]
    read = [o for o in outcomes.values() if o["ms"]]
    times = [o["ms"] for o in read]
    return {
        "cases": len(cases),
        "read_by_models": len(read),
        "missing_readings": missing,
        "in_scope_recall": _ratio(len(in_scope) - len(false_rejects), len(in_scope)),
        "in_scope_cases": len(in_scope),
        "false_rejections": false_rejects,
        "redirect_precision": _ratio(len(redirected) - len(wrong_redirects), len(redirected)),
        "redirects": len(redirected),
        "wrong_redirects": wrong_redirects,
        "out_of_scope_recall": _ratio(caught, len(out_scope)),
        "out_of_scope_cases": len(out_scope),
        "mixed_redirected": mixed_redirected,
        "label_accuracy": _ratio(len(labelled) - len(label_wrong), len(labelled)),
        "label_wrong": label_wrong,
        "study_accuracy": _ratio(len(cases) - len(study_wrong), len(cases)),
        "false_study_credit": false_credit,
        "missed_study_credit": missed_credit,
        "adversarial_cases": sum(bool(c.get("adversarial")) for c in cases),
        "adversarial_false_credit": adversarial_credit,
        "follow_up_accuracy": _ratio(len(follow) - len(follow_wrong), len(follow)),
        "follow_up_wrong": follow_wrong,
        "escalation_rate": _ratio(sum(o["escalated"] for o in read), len(read)),
        "failed": [cid for cid, o in outcomes.items() if o["failed"]],
        "latency_ms": {
            "p50": percentile(times, 0.5),
            "p95": percentile(times, 0.95),
            "max": max(times, default=None),
            "mean": round(statistics.fmean(times), 1) if times else None,
        },
        "cost_per_1000_messages_usd": round(1000 * sum(o["cost"] for o in read) / len(read), 4) if read else None,
        "policy": policy,
    }


def gates(report: dict) -> list[str]:
    failed = []
    if report["missing_readings"]:
        failed.append("missing_readings")
    if (report["in_scope_recall"] or 0) < RECALL_GATE:
        failed.append("in_scope_recall")
    if report["redirects"] and (report["redirect_precision"] or 0) < PRECISION_GATE:
        failed.append("redirect_precision")
    if report["mixed_redirected"]:
        failed.append("mixed_redirected")
    if report["adversarial_false_credit"]:
        failed.append("adversarial_false_credit")
    return failed


def sweep(cases: list[dict], raws: dict[str, dict], base: dict) -> None:
    rows = []
    for escalate, redirect, confidence in itertools.product(
        (0.7, 0.8, 0.85, 0.9, 0.95, 1.01), (0.5, 0.6, 0.7, 0.8, 0.9), (0.5, 0.6, 0.7)
    ):
        policy = {**base, "escalate_below": escalate, "min_redirect": redirect, "min_confidence": confidence}
        report = metrics(cases, raws, policy)
        rows.append((gates(report), report))
    for failed, report in sorted(rows, key=lambda row: (len(row[0]), -(row[1]["study_accuracy"] or 0))):
        policy = report["policy"]
        summary = {
            "escalate_below": policy["escalate_below"],
            "min_redirect": policy["min_redirect"],
            "min_confidence": policy["min_confidence"],
            "gates_failed": failed,
            "recall": report["in_scope_recall"],
            "precision": report["redirect_precision"],
            "oos_recall": report["out_of_scope_recall"],
            "study_acc": report["study_accuracy"],
            "adv_credit": len(report["adversarial_false_credit"]),
            "escalation": report["escalation_rate"],
            "p95_ms": report["latency_ms"]["p95"],
            "cost_1000": report["cost_per_1000_messages_usd"],
        }
        print(json.dumps(summary))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("record", "replay"))
    parser.add_argument("cases", type=Path)
    parser.add_argument("readings", type=Path)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--gates", action="store_true", help="exit 1 when a gate fails")
    parser.add_argument("--escalate-below", type=float)
    parser.add_argument("--min-redirect", type=float)
    parser.add_argument("--min-confidence", type=float)
    parser.add_argument("--primary-timeout-s", type=float)
    parser.add_argument("--escalation-timeout-s", type=float)
    args = parser.parse_args()
    cases = load_cases(args.cases)
    if args.mode == "record":
        asyncio.run(record(cases, args.readings))
        return
    from app.settings import Settings

    defaults = Settings(_env_file=None)

    def pick(value: float | None, default: float) -> float:
        return default if value is None else value

    base = {
        "escalate_below": pick(args.escalate_below, defaults.decision_escalate_below),
        "min_redirect": pick(args.min_redirect, defaults.scope_min_redirect),
        "min_confidence": pick(args.min_confidence, defaults.scope_min_confidence),
        "primary_timeout_s": pick(args.primary_timeout_s, defaults.decision_primary_timeout_s),
        "escalation_timeout_s": pick(args.escalation_timeout_s, defaults.decision_escalation_timeout_s),
        "redirect_alone": defaults.decision_redirect_alone,
        "wait_s": defaults.decision_wait_s,
    }
    raws = {}
    for line in args.readings.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            raws[entry["id"]] = entry
    if args.sweep:
        sweep(cases, raws, base)
        return
    report = metrics(cases, raws, base)
    failed = gates(report)
    print(json.dumps({**report, "gates_failed": failed}, indent=1))
    if args.gates and failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
