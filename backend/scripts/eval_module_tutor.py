"""Month tutors on the real model: do they stay inside their month (NH programs, app/programs)?

Each case asks one question in one month of a program, the way a program chat's turn does: the month's tutor block
(app/programs/prompt.py) and its curriculum scope (the documents and NHprep courses mapped to the month), through the
production graph (app.main.build_rag). Three kinds of case:

- `in`: a question the month covers. Gate: every cited source is the month's own (a mapped document, or an NHprep
  lesson of a mapped course), 100%.
- `other`: a question another month covers. Gate: the reply points to that month and does not teach it, >= 90%.
- `uncovered`: a question no month covers. Gate: the reply says it isn't covered and labels the general
  explanation, >= 90%.

On the production host, after the mapping is loaded (nothing is written anywhere but stdout):

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/netsecai/api.env \\
        -p WorkingDirectory=/opt/netsecai/app/backend \\
        /opt/netsecai/app/backend/.venv/bin/python scripts/eval_module_tutor.py

It uses the production key's quota (one model call at a time). It prints case ids and numbers only, never the reply,
unless --show-failures (then at most 200 characters of each failing reply). Exits 1 when a gate fails, 2 on a setup
error.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

P1, P2 = "cyber-cloud-security-ai", "network-security-ai"
# (case id, program slug, month, kind, question, the month an `other` question belongs to)
CASES: list[tuple[str, str, int, str, str, int | None]] = [
    ("p2m1-in-1", P2, 1, "in", "How does a switch build its MAC address table?", None),
    ("p2m1-in-2", P2, 1, "in", "Explain how OSPF elects a DR and BDR.", None),
    ("p2m1-in-3", P2, 1, "in", "What is the difference between a standard and an extended ACL?", None),
    ("p2m1-other", P2, 1, "other", "How do I write a Palo Alto security policy using App-ID?", 5),
    ("p2m1-unc", P2, 1, "uncovered", "How do I write an F5 BIG-IP iRule?", None),
    ("p2m2-in-1", P2, 2, "in", "Explain how VXLAN encapsulates frames in an enterprise fabric.", None),
    ("p2m2-in-2", P2, 2, "in", "How does HSRP provide first-hop redundancy?", None),
    ("p2m2-in-3", P2, 2, "in", "What does Cisco DNA Center do in SD-Access?", None),
    ("p2m2-other", P2, 2, "other", "How do I configure VDOMs on a FortiGate?", 6),
    ("p2m2-unc", P2, 2, "uncovered", "How do I set up Check Point ClusterXL?", None),
    ("p2m4-in-1", P2, 4, "in", "What are the roles of vManage, vSmart and vBond?", None),
    ("p2m4-in-2", P2, 4, "in", "How does OMP distribute routes in Cisco SD-WAN?", None),
    ("p2m4-in-3", P2, 4, "in", "What is a TLOC in Cisco SD-WAN?", None),
    ("p2m4-other", P2, 4, "other", "How do Palo Alto zone protection profiles work?", 5),
    ("p2m4-unc", P2, 4, "uncovered", "How do I configure Cisco ESA mail flow policies?", None),
    ("p2m5-in-1", P2, 5, "in", "What is App-ID on a Palo Alto firewall?", None),
    ("p2m5-in-2", P2, 5, "in", "How does a Palo Alto firewall process a packet (the flow logic)?", None),
    ("p2m5-in-3", P2, 5, "in", "How do I configure a Palo Alto NAT policy?", None),
    ("p2m5-other", P2, 5, "other", "How do I build a device template in Cisco vManage?", 4),
    ("p2m5-unc", P2, 5, "uncovered", "How do I use commit confirmed on Juniper Junos?", None),
    ("p2m6-in-1", P2, 6, "in", "How do I create a firewall policy on a FortiGate?", None),
    ("p2m6-in-2", P2, 6, "in", "How does FortiGate HA (FGCP) work?", None),
    ("p2m6-in-3", P2, 6, "in", "How do I configure an IPsec site-to-site VPN on FortiOS?", None),
    ("p2m6-other", P2, 6, "other", "How does OMP advertise routes in Cisco SD-WAN?", 4),
    ("p2m6-unc", P2, 6, "uncovered", "How does Cisco ISE posture assessment work?", None),
    ("p1m4-in-1", P1, 4, "in", "What is the difference between a security group and a NACL in AWS?", None),
    ("p1m4-in-2", P1, 4, "in", "How does an AWS VPC route table decide where traffic goes?", None),
    ("p1m4-in-3", P1, 4, "in", "How do I create a security policy on a FortiGate?", None),
    ("p1m4-other", P1, 4, "other", "How do Kubernetes pod security admission levels work?", 6),
    ("p1m4-unc", P1, 4, "uncovered", "How do I configure an Infoblox DNS view?", None),
]

GATES = {"in": 1.0, "other": 0.9, "uncovered": 0.9}
UNCOVERED = re.compile(r"isn['’]t covered in your Month \d+ material", re.IGNORECASE)
GENERAL = re.compile(r"general explanation", re.IGNORECASE)
POINTER_MAX_CHARS = 900  # a pointer, not a lesson


def check(
    kind: str, reply: str, sources: list[dict], documents: set[str], courses: set[str], other: int | None
) -> bool:
    """True when the reply meets the case's rule."""
    if kind == "in":
        return bool(reply.strip()) and all(
            (source.get("source") == "NHprep" and str(source.get("sourceId")) in courses)
            or str(source.get("documentId")) in documents
            for source in sources
        )
    if kind == "other":
        return bool(re.search(rf"\bMonth {other}\b", reply)) and len(reply) <= POINTER_MAX_CHARS
    return bool(UNCOVERED.search(reply)) and bool(GENERAL.search(reply))


async def _run(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.chat.assistant import resolve_assistant_config
    from app.db.models import Conversation, Program, ProgramModule
    from app.db.session import Database
    from app.llm.base import ChatMessage, GenerationOptions
    from app.llm.factory import build_llm
    from app.main import build_rag
    from app.programs.prompt import tutor_block
    from app.programs.service import chat_turn
    from app.rag.embeddings import build_embeddings
    from app.settings import get_settings

    settings = get_settings()
    db = Database(settings)
    _, graph = build_rag(settings, db, build_llm(settings), build_embeddings(settings))
    results: dict[str, list[bool]] = {kind: [] for kind in GATES}
    try:
        for case_id, slug, month, kind, question, other in CASES:
            async with db.sessionmaker() as session:
                program = await session.scalar(select(Program).where(Program.slug == slug))
                module = program and await session.scalar(
                    select(ProgramModule).where(ProgramModule.program_id == program.id, ProgramModule.month == month)
                )
                if module is None:
                    print(f"setup error: {slug} month {month} not found", file=sys.stderr)
                    return 2
                # Never added to the session: only its program, month and mode are read.
                turn = await chat_turn(session, Conversation(program_id=program.id, module_id=module.id, mode="tutor"))
            config = resolve_assistant_config(
                None, None, None, program=tutor_block(turn.context, question), curriculum=turn.curriculum
            )
            documents = {str(item) for item in turn.curriculum.documents}
            courses = {str(item) for item in turn.curriculum.courses}
            for run in range(args.runs):
                state = {
                    "user_id": None,
                    "conversation": [ChatMessage("user", question)],
                    "system_prompt": config.system_prompt,
                    "retrieval": config.retrieval,
                    "generation": GenerationOptions(
                        temperature=settings.llm_temperature, max_output_tokens=settings.llm_max_output_tokens
                    ),
                    "files": [],
                }
                pieces, sources = [], []
                async for event in graph.astream(state, stream_mode="custom"):
                    if event.get("event") == "token":
                        pieces.append(event["text"])
                    elif event.get("event") == "retrieval":
                        sources = event["summary"].get("sources") or []
                reply = "".join(pieces)
                ok = check(kind, reply, sources, documents, courses, other)
                results[kind].append(ok)
                print(f"{case_id} run {run + 1}: {'pass' if ok else 'FAIL'} (sources {len(sources)})")
                if not ok and args.show_failures:
                    print("   ", reply[:200].replace("\n", " "))
    finally:
        await db.engine.dispose()
    failed = False
    for kind, gate in GATES.items():
        outcomes = results[kind]
        rate = sum(outcomes) / len(outcomes) if outcomes else 0.0
        failed |= rate < gate
        verdict = "pass" if rate >= gate else "FAIL"
        print(f"{kind}: {sum(outcomes)}/{len(outcomes)} = {rate:.0%} (gate {gate:.0%}) {verdict}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--show-failures", action="store_true")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
