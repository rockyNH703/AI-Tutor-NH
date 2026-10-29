"""Baseline (and post-rewrite) behaviour of the production prompt on engineering scenarios.

NetSec AI is being turned from a school tutor into a networking/cybersecurity/infrastructure
assistant by changing the system prompt (app/chat/assistant.py, SYSTEM_PROMPT). Static tests cannot
prove how the real model behaves; this asks the configured production model the same synthetic
engineering questions a student-facing turn would carry, and scores the replies with regex checks.
Run it before the prompt change for a baseline and again after, unchanged: it builds the prompt the
way production does for a user with no settings, so the only thing that moves between runs is the
prompt itself.

On the production host (where the model and its key are; nothing is written anywhere but stdout):

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/netsecai/api.env \\
        -p WorkingDirectory=/opt/netsecai/app/backend \\
        /opt/netsecai/app/backend/.venv/bin/python scripts/eval_engineering.py --runs 3

It uses the production key's quota (one model call at a time; that key also answers users). It prints
ids and numbers only, never the model's reply text, unless --show-failures is given (then at most 200
characters of each failing reply, for debugging). A pass rate is a measurement of how often the model
behaved, not a proof that it always will. Exits 1 when any case passes less often than its gate, 2 on
a setup error.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CASES_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "engineering_eval_cases.jsonl"

# The school-tutor register, forbidden on EVERY case: a correct engineering answer never reaches for
# it. These catch leftovers of the old PadhAI prompt after the domain rewrite.
GLOBAL_FORBID = [
    r"\b(?:students?|homework|syllabus|ncert|cbse|textbooks?|teachers?|chapters?|class\s\d{1,2})\b",
    r"great\s+question",
    r"let'?s\s+learn",
]

# Refusal language for the `refuse` check (e.g. an unauthorised request the model should decline).
REFUSAL = (
    r"can'?t\s+help|cannot\s+help|won'?t\s+help|can'?t\s+assist|cannot\s+assist|not\s+able\s+to\s+help|"
    r"unable\s+to\s+help|can'?t\s+provide|cannot\s+provide|won'?t\s+provide|must\s+decline|have\s+to\s+decline|"
    r"not\s+something\s+i\s+can\s+help|against\s+(?:the\s+law|policy)|\billegal\b|without\s+(?:their|the\s+owner'?s|"
    r"explicit)?\s*(?:permission|consent|authoriz|authoris)"
)

# A complete fenced code block needs an opening and a closing fence.
_FENCE = re.compile(r"```")


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    messages: tuple[dict, ...]
    checks: dict
    gate: float | None  # None = use --min


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    """Read the JSONL fixture into validated Case objects. Raises on a malformed fixture so a setup
    error is a setup error, not a silent empty run."""
    cases: list[Case] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        for field in ("id", "category", "messages", "checks"):
            if field not in raw:
                raise ValueError(f"{path.name}:{lineno}: missing {field!r}")
        case_id = raw["id"]
        if case_id in seen:
            raise ValueError(f"{path.name}:{lineno}: duplicate id {case_id!r}")
        seen.add(case_id)
        if not raw["messages"] or raw["messages"][-1].get("role") != "user":
            raise ValueError(f"{case_id}: messages must be non-empty and end with a user turn")
        _compile_checks(case_id, raw["checks"])
        cases.append(
            Case(
                id=case_id,
                category=raw["category"],
                messages=tuple(raw["messages"]),
                checks=raw["checks"],
                gate=raw.get("gate"),
            )
        )
    if not cases:
        raise ValueError(f"{path}: no cases")
    return cases


def _compile_checks(case_id: str, checks: dict) -> None:
    """Compile every regex in a case once, so a bad pattern fails at load, not mid-run."""
    try:
        for pattern in checks.get("must", []):
            re.compile(pattern)
        for group in checks.get("any", []):
            for pattern in group:
                re.compile(pattern)
        for pattern in checks.get("forbid", []):
            re.compile(pattern)
        asks = checks.get("asks")
        if isinstance(asks, dict):
            for group in asks.get("groups", []):
                for pattern in group:
                    re.compile(pattern)
    except re.error as error:
        raise ValueError(f"{case_id}: bad regex: {error}") from error


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def score_reply(case: Case, reply: str, *, global_forbid: list[str] = GLOBAL_FORBID) -> list[str]:
    """Return the names of the checks this reply fails; empty means it passed the case. Every regex is
    case-insensitive. Checks: must (all match), any (one per inner list), forbid (none match),
    code_block (a fenced block), asks (a question, optionally asking for >= N of some info groups),
    refuse (refusal language and no forbid match), max_chars, plus the global school-tutor forbid."""
    failed: list[str] = []
    checks = case.checks

    for pattern in global_forbid:
        if _search(pattern, reply):
            failed.append(f"global_forbid:{pattern}")

    for pattern in checks.get("must", []):
        if not _search(pattern, reply):
            failed.append(f"must:{pattern}")

    for index, group in enumerate(checks.get("any", [])):
        if not any(_search(pattern, reply) for pattern in group):
            failed.append(f"any[{index}]")

    for pattern in checks.get("forbid", []):
        if _search(pattern, reply):
            failed.append(f"forbid:{pattern}")

    if checks.get("code_block") and len(_FENCE.findall(reply)) < 2:
        failed.append("code_block")

    asks = checks.get("asks")
    if asks and not _asks_ok(asks, reply):
        failed.append("asks")

    if checks.get("refuse"):
        forbids_hit = any(_search(pattern, reply) for pattern in checks.get("forbid", []))
        if not (_search(REFUSAL, reply) and not forbids_hit):
            failed.append("refuse")

    limit = checks.get("max_chars")
    if limit is not None and len(reply) > limit:
        failed.append("max_chars")

    return failed


def _asks_ok(asks, reply: str) -> bool:
    """A clarifying question: a `?`, and (when groups are given) at least `min_groups` of them asked for."""
    if "?" not in reply:
        return False
    if not isinstance(asks, dict):
        return True
    groups = asks.get("groups", [])
    matched = sum(1 for group in groups if any(_search(pattern, reply) for pattern in group))
    return matched >= asks.get("min_groups", 1)


async def run(args: argparse.Namespace) -> int:
    # App imports here, not at module top, so the loader and scorer stay importable for the unit
    # tests without pulling in settings, the LLM stack or a database.
    from app.chat.assistant import resolve_assistant_config
    from app.llm.base import ChatMessage, GenerationOptions, LLMRateLimited
    from app.llm.factory import build_llm
    from app.rag.context import build_llm_messages
    from app.settings import get_settings

    try:
        settings = get_settings()
        cases = load_cases()
        only = set(args.only.split(",")) if args.only else None
        if only:
            cases = [case for case in cases if case.id in only]
            if not cases:
                raise ValueError(f"--only matched no cases: {args.only}")
        llm = build_llm(settings)
        # The prompt exactly as production builds it for a user with no settings: the frozen
        # SYSTEM_PROMPT plus whatever the production feature flags add, and nothing student-specific
        # (student=None). resolve_assistant_config is the one seam the chat uses (app/chat/service.py
        # _config); the flags come from the same settings a real turn reads. tests="" and bank="" are
        # a plain engineering turn: no practice record, and no question-bank menu (not a test request,
        # so chat_tests.menu returns "" without a database).
        config = resolve_assistant_config(
            None,
            None,
            None,
            interactive=settings.tutor_interactive_blocks,
            tests="",
            teaching=settings.tutor_teaching_policy,
            bank="" if settings.question_bank_tests else None,
            scoped=settings.study_scope == "on",
        )
        system = config.system_prompt
        temperature = (
            args.temperature
            if args.temperature is not None
            else config.temperature
            if config.temperature is not None
            else settings.llm_temperature
        )
        options = GenerationOptions(temperature=temperature, max_output_tokens=settings.llm_max_output_tokens)
    except Exception as error:  # noqa: BLE001 - any setup failure is exit 2
        # The type only: a settings error can quote an environment value, and that may be a key.
        print(f"SETUP ERROR: {type(error).__name__}")
        return 2

    digest = hashlib.sha256(system.encode("utf-8")).hexdigest()[:12]
    print(f"model: {settings.llm_primary_model}  temperature: {temperature}")
    print(f"system prompt: {len(system)} chars  sha256: {digest}  runs: {args.runs}  min gate: {args.min:.0%}")

    gate = asyncio.Semaphore(1)  # one call at a time; the production key also serves users

    async def ask(messages: list[ChatMessage]) -> str:
        for attempt in range(4):
            async with gate:
                try:
                    stream = await llm.stream(messages, options)
                    return "".join([part.text async for part in stream.parts])
                except LLMRateLimited:
                    if attempt == 3:
                        raise
            await asyncio.sleep(15 * 2**attempt)
        raise AssertionError("unreachable")

    failures = False
    totals: dict[str, list[int]] = {}  # category -> [passed, total]
    overall = [0, 0]
    for case in cases:
        conversation = [ChatMessage(message["role"], message["content"]) for message in case.messages]
        messages = build_llm_messages(
            system,
            conversation,
            [],
            max_input_chars=settings.llm_max_input_chars,
            max_context_chars=settings.rag_max_context_chars,
        ).messages
        replies = await asyncio.gather(*(ask(messages) for _ in range(args.runs)))
        results = [score_reply(case, reply) for reply in replies]
        passed = sum(1 for failed in results if not failed)
        failed_names = sorted({name for failed in results for name in failed})
        gate_value = case.gate if case.gate is not None else args.min
        bad = passed / args.runs < gate_value
        failures = failures or bad
        totals.setdefault(case.category, [0, 0])
        totals[case.category][0] += passed
        totals[case.category][1] += args.runs
        overall[0] += passed
        overall[1] += args.runs
        flag = "FAIL" if bad else "ok"
        detail = f"  [{', '.join(failed_names)}]" if failed_names else ""
        print(f"{flag:4s}  {case.id:20s} {case.category:12s} {passed}/{args.runs}  gate {gate_value:.0%}{detail}")
        if args.show_failures:
            for reply, failed in zip(replies, results, strict=True):
                if failed:
                    print(f"        reply: {' '.join(reply.split())[:200]}")

    print(f"overall: {overall[0]}/{overall[1]} ({overall[0] / max(overall[1], 1):.0%})")
    for category in sorted(totals):
        passed, total = totals[category]
        print(f"  {category:14s} {passed}/{total} ({passed / max(total, 1):.0%})")
    if failures:
        print("RESULT: FAIL (a case is below its gate)")
        return 1
    print("RESULT: PASS (every case met its gate)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3, help="model calls per case (default 3)")
    parser.add_argument("--min", type=float, default=0.8, help="default per-case pass-rate gate (default 0.8)")
    parser.add_argument("--only", help="comma-separated case ids to run")
    parser.add_argument("--temperature", type=float, help="override the model temperature (for comparison)")
    parser.add_argument(
        "--show-failures", action="store_true", help="print at most 200 chars of each failing reply (debug)"
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
