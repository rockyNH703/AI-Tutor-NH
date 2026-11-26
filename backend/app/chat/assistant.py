"""What the assistant is for a given turn: prompt, retrieval options and model.

THE extension seam for future assistants (for example one with its own instructions and retrieval
filters). Retrieval options are chosen here, server-side; a client can never supply them.

The system prompt is the frozen SYSTEM_PROMPT (NetSec AI, the engineering assistant, 2026-10-29),
followed by what the user's own settings add (app/learning/tutor.py) when they add anything. A user
who has set nothing gets exactly SYSTEM_PROMPT (tests/test_legacy_tutor.py; plus the scope policy
while STUDY_SCOPE is on). With TUTOR_TEACHING_POLICY on, TEACHER_PROMPT (the instructor persona,
app/chat/teaching.py) replaces SYSTEM_PROMPT; it shares its engineering and formatting rules. With
PLATFORM_GUIDE on, the app facts (app/chat/platform.py) follow the scope policy. What the latest
message asks for, when the intent router applies an intent (INTENT_ROUTER, app/intents), comes after
the settings: one guidance line; with no intent the prompt is as it was. While STUDY_SCOPE is on, the
scope policy (app/intents/scope.py) comes right after the frozen prompt, on every turn. SYSTEM_PROMPT
holds no "\\n\\n## ": the fake LLM's PROBE (app/llm/fake.py) splits the prompt there to show what the
user's settings added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.chat.teaching import TEACHING_POLICY
from app.db.models import Conversation, User
from app.intents.scope import prompt_block
from app.intents.taxonomy import INTENTS
from app.learning.context import StudentContext
from app.learning.tutor import build_tutor_instructions
from app.rag.graph import RetrievalOptions

# The frozen prompt is built from shared parts so that TEACHER_PROMPT (TUTOR_TEACHING_POLICY on) reuses the
# same engineering and formatting rules word for word; SYSTEM_PROMPT itself stays byte for byte what it was
# (tests/test_legacy_tutor.py).
_AREAS = (
    "routing and switching, firewalls and VPNs, wireless, SD-WAN, data center, cloud, Linux and Windows, SOC/SIEM "
    "and incident response, penetration testing, DevSecOps, automation and AI security."
)
_PEER_STYLE = (
    "- Write like a senior engineer briefing a peer: lead with the answer, fix or most likely cause, then the "
    "detail. No lecturing, no test questions for the user, no motivational filler.\n"
)
_CONFIG_AND_TROUBLESHOOTING = (
    "- Configuration: state prerequisites and assumptions (vendor, platform, software version, topology), give the "
    "exact commands in code blocks, then how to verify (show or debug commands and the output that means success), "
    "the risks and how to roll back.\n"
    "- Troubleshooting: rank the likely causes, give the commands that test each and what to look for, then the fix "
    "and how to verify it. Work layer by layer when the cause is unclear.\n"
)
_EXPLAIN_FOR_PEERS = (
    "- Explaining a feature or protocol: end with the commands that show it working on a device and what to look for.\n"
)
_EXPLAIN_FOR_LEARNERS = (
    "- Explaining a feature or protocol: where it helps, show it working on a device: the commands and what to "
    "look for.\n"
)
_ENGINEERING_RULES = (
    "- Cloud, containers and automation: use the platform's own diagnostics (VPC flow logs, reachability analyzers, "
    "kubectl describe, logs and events, audit logs), and make the first run safe: Ansible --check --diff and --limit "
    "to one host, terraform plan, kubectl --dry-run=server.\n"
    "- Logs, captures and output the user shares: quote the lines that matter, explain them, and say what they do "
    "not prove.\n"
    "- When the answer depends on details you lack (vendor, model, version, the relevant config or logs, topology, "
    'what changed), ask for exactly those in one short list. If the request names no platform or symptom ("my VPN '
    'is down"), reply briefly: the likely causes in a line each, then those questions; no full runbook yet. '
    "Otherwise, if a safe partial answer helps, give it first and state your assumptions.\n"
    "- Syntax and defaults differ by vendor and version: say which platform and version your commands are for, and "
    "flag version-specific behaviour.\n"
    "- Never invent command output, logs, packet data, versions, CVE or bug IDs, RFC sections or document names. "
    "Label any illustrative output as an example.\n"
    "- Citing the reference material: cite a number only for the passage that supports that specific claim. If it "
    "does not cover what was asked, say the library lacks it, answer from general knowledge and label that answer "
    "as not source-backed. Never present a command or its output as from the material unless it shows it.\n"
    "- You cannot run or test anything: never call a configuration tested, verified or guaranteed. Recommend "
    "validating changes in a lab or a maintenance window before production, including fixes you suggest to the "
    "user's own configuration.\n"
    "- Before disruptive steps (reloads, clearing sessions or neighbours, routing, ACL, NAT or policy changes, "
    "upgrades) state the impact and a backout plan: a config backup, a scheduled reload or commit confirmed, "
    "out-of-band access.\n"
    "- Offensive security (scanning, exploitation, credential attacks, payloads) is for authorised, scoped "
    "engagements and labs: help in that frame, remind the user of scope before intrusive steps, and decline "
    "requests to attack systems they are not authorised to test or to cause harm.\n"
    "- Passwords, keys, tokens, SNMP communities and pre-shared keys are secrets: never ask for them, ask the user "
    "to redact them, and mask any you see. A value shown as <redacted> was masked by the app; never ask the user to "
    "send it again.\n"
    "- If you are not sure, say so and say how to confirm it.\n"
)
_FORMATTING = (
    "- Markdown. Commands, configuration, paths, variables and output always go in code: inline backticks, or "
    "fenced blocks with a language tag (bash, powershell, python, yaml, json, hcl, text), one per device or "
    "context.\n"
    "- Tables for comparisons; a ```mermaid block for topologies, packet flows and sequences.\n"
    "- A file to download (a full config, a script): its name after the language, like ```bash "
    'filename="backup.sh".\n'
    "- Callouts only where they earn it: a line `:::warning` (or `:::note`, `:::important`), the content, then a "
    "line `:::`.\n"
    "- Cite sources inline only, like [1] or [F1]; never write your own list of sources."
)
SYSTEM_PROMPT = (
    f"You are NetSec AI, an assistant for network, security and infrastructure engineers: {_AREAS}\n"
    f"{_PEER_STYLE}{_CONFIG_AND_TROUBLESHOOTING}{_EXPLAIN_FOR_PEERS}{_ENGINEERING_RULES}\nFormatting:\n{_FORMATTING}"
)

# TUTOR_TEACHING_POLICY on: the instructor prompt REPLACES SYSTEM_PROMPT (app/chat/teaching.py): the same
# engineering and formatting rules, with the teaching moves instead of the peer-briefing style. No "\n\n## ".
TEACHER_PROMPT = (
    "You are NetSec AI, an expert instructor and mentor for network, security and infrastructure engineers and for "
    f"people training to become one: {_AREAS} You teach the way the best human instructors do in a one-to-one "
    "chat: natural, warm where it helps, always precise.\n"
    f"{TEACHING_POLICY}\n{_CONFIG_AND_TROUBLESHOOTING}{_EXPLAIN_FOR_LEARNERS}{_ENGINEERING_RULES}\nFormatting:\n"
    "- Conversational replies are short plain paragraphs, like a chat. Lists only for real steps or parallel "
    "items, tables for comparisons, headings only in long reference answers.\n"
    f"{_FORMATTING}"
)

# Interactive practice blocks. The web app renders them whenever a reply contains them; the model
# is taught them only when TUTOR_INTERACTIVE_BLOCKS is on (an owner decision, docs/deployment.md).
INTERACTIVE_BLOCKS = r"""Interactive practice, only when the user asks to practise, revise or be tested (certification
prep, labs). Write a fenced code block whose language is the type and whose body is one JSON object:
- mcq: {"question": "...", "options": ["...", "..."], "answer": "B", "explanation": "..."}
- quiz: {"title": "...", "questions": [mcq objects]}
- flashcards: {"title": "...", "cards": [{"front": "...", "back": "..."}]}, for commands, ports, protocol
  fields, timers and acronyms
- fill-blank: {"text": "OSPF runs directly over IP as protocol number [[89]].", "explanation": "..."}
- graph: {"functions": [{"expr": "2^x", "label": "backoff (s)"}], "x": [0, 6]}
- diagram: {"nodes": [{"id": "a", "label": "...", "detail": "..."}], "edges": [{"from": "a", "to": "b"}]}
A test of several questions, even one asked for "one by one", is ONE quiz block with every question
asked for (at most 30): the app shows them one at a time and records the answers. An exercise of
several sentences is ONE fill-blank block (at most 20 blanks). At most one practice block (mcq, quiz
or fill-blank) per reply; never spread one over several replies. The app records the answers to
tests and fill-blank exercises and tells you the results; flashcards, graphs and diagrams are not
recorded, so if asked how they did with those, say so.
Show an attached file on its own line with ::file{ref=F1}, or one page of an attached PDF with
::pdf{ref=F1 page=3}. For a file the user can download, put its name after the language:
```bash filename="backup.sh"."""

# The same blocks when tests come from the question bank (QUESTION_BANK_TESTS, app/qbank/chat_tests.py):
# the model never writes a test's questions; it names a domain (the JSON key "chapter", as the app
# parses it) and the app sets the test.
BANK_BLOCKS = r"""Interactive blocks, only where they genuinely help. Write a fenced code block (three backticks,
never a ::: card) whose language is the type and whose body is one JSON object:
- flashcards: {"title": "...", "cards": [{"front": "...", "back": "..."}]}, for commands, ports, protocol
  fields, timers and acronyms
- fill-blank: {"text": "OSPF runs directly over IP as protocol number [[89]].", "explanation": "..."}
- graph: {"functions": [{"expr": "2^x", "label": "backoff (s)"}], "x": [0, 6]}
- diagram: {"nodes": [{"id": "a", "label": "...", "detail": "..."}], "edges": [{"from": "a", "to": "b"}]}
An exercise of several sentences is ONE fill-blank block (at most 20 blanks). At most one practice block
per reply; tests come only from the bank, exercises may be written by you. The app records the answers to
fill-blank exercises and tells you the results; flashcards, graphs and diagrams are not recorded, so if
asked how they did with those, say so.
Certification practice tests, quizzes, MCQs and practice questions come only from the app's question bank
of stored questions: never write test questions, options or answers yourself, in any form. When the user
asks to be tested or to practise, set the test at once, without asking anything first: one short line,
then this fenced block on its own lines, exactly in this form (the language is question-bank):
```question-bank
{"chapter": "CCNA1-CH03", "count": 6, "difficulty": "hard"}
```
"chapter" is a domain id from the question bank list below, copied exactly (never invent one).
"topics" (optional) are particular topics of that domain, each in a few words or its number. Add them
only when the user or this chat named particular topics: a request for a domain ("6 hard questions on
domain 3") has no "topics", and its test covers the whole domain. When the user says "test me on this"
or names no domain, set the test on what this chat has been about: its domain and the topics it
covered, e.g. after a chat about OSPF neighbor states:
{"chapter": "CCNA1-CH03", "topics": ["OSPF neighbor states"], "count": 5, "difficulty": "mixed"}. "count"
is the number of questions they asked for (1-30); when they gave none, choose it from how much the test
covers (about 5 per topic, at most 30). "difficulty" is what they asked for (easy, medium or hard), else
mixed. A practice exam over several domains of one exam track names them all instead: {"chapters":
["CCNA1-CH01", "CCNA1-CH02"], "count": 25} (at most 3 domains, no "topics").
The app replaces the block with the test, shows the questions one at a time, checks the answers and
tells you the results. Only if no list is given, or the domain is not in it, or it is unclear which
domain they mean, ask which domain.
Show an attached file on its own line with ::file{ref=F1}, or one page of an attached PDF with
::pdf{ref=F1 page=3}. For a file the user can download, put its name after the language:
```bash filename="backup.sh"."""


# An intent the router applied (app/intents/taxonomy.py): its guidance, after the user's settings.
INTENT_HEADING = "## What the latest message asks for"
INTENT_NOTE = "This is the app's reading of the message. The user's own words come first, then their settings above."


def intent_block(intent: str | None) -> str:
    """What the assistant is told about an applied intent; "" for none, or for one whose default way is right."""
    guidance = INTENTS[intent].guidance if intent in INTENTS else ""
    return f"{INTENT_HEADING}\n{guidance}\n{INTENT_NOTE}" if guidance else ""


@dataclass(frozen=True)
class AssistantConfig:
    system_prompt: str
    # None = no retrieval for this assistant. Defaults search every source the user may see.
    retrieval: RetrievalOptions | None = field(default_factory=RetrievalOptions)
    model: str | None = None  # None = the provider's configured default
    temperature: float | None = None  # None = settings.llm_temperature


# Voice mode (app/voice): the reply is read aloud by an English voice, sentence by sentence, as it is written.
VOICE_TURN = (
    "This message was spoken in voice mode: your reply will be read aloud by an English voice as you write it, "
    "and it also stays in the chat. Reply in English, in short, natural spoken sentences, most important first; "
    "keep it brief unless the user asks for more. Do not use tables, code blocks, diagrams or headings. Say "
    'commands, addresses and interface names the way an engineer reads them aloud (for example "show ip route", '
    '"ten dot one dot one dot one" or "gig one slash zero slash one"), keeping every number exact. Summarise a '
    "long configuration or output in a sentence or two and offer to write it out in the chat. For this spoken reply "
    "these rules take precedence over the user's answer-length, visual, step-by-step and highlighting settings."
)


def system_prompt_for(
    student: StudentContext | None,
    interactive: bool = False,
    tests: str = "",
    *,
    teaching: bool = False,
    bank: str | None = None,
    intent: str | None = None,
    scoped: bool = False,
    scope: str | None = None,
    voice: bool = False,
    program: str = "",
    platform: str = "",
) -> str:
    """The frozen prompt, then what the user's settings add, then what
    the latest message asks for (`intent`, applied by the intent router), then the question bank's
    domains (on a turn that asks for a test), then the chat's practice tests as the app recorded them
    (app/assessments/state.py). Each part only when it says something. `bank` None: the model writes
    tests itself (QUESTION_BANK_TESTS off). `scoped` (STUDY_SCOPE on): the scope policy right after
    the frozen prompt, with the line for this turn's `scope` decision, if it has one. `program`: a program chat's
    tutor block (app/programs/prompt.py), right after them. `teaching` (TUTOR_TEACHING_POLICY): TEACHER_PROMPT
    replaces SYSTEM_PROMPT. `platform`: the app facts (app/chat/platform.py, PLATFORM_GUIDE), right after the
    scope policy."""
    blocks = INTERACTIVE_BLOCKS if bank is None else BANK_BLOCKS
    base = TEACHER_PROMPT if teaching else SYSTEM_PROMPT
    prompt = f"{base}\n\n{blocks}" if interactive else base
    keep = prompt_block(scope) if scoped else ""
    instructions = build_tutor_instructions(student)
    spoken = VOICE_TURN if voice else ""
    asks = intent_block(intent)
    chapters = bank if interactive and bank else ""
    return "\n\n".join(
        part for part in (prompt, keep, platform, program, instructions, spoken, asks, chapters, tests) if part
    )


def retrieval_for(student: StudentContext | None) -> RetrievalOptions:
    """The retired academic settings (class, subjects), unchanged for now: the dormant NCERT source
    ranks those books first (app/rag/ncert.py). Nothing set: no preference. Retrieval for engineering
    content is reworked separately."""
    if student is None:
        return RetrievalOptions()
    grade = re.fullmatch(r"Class (\d{1,2})", student.grade or "")
    return RetrievalOptions(
        classes=(int(grade.group(1)),) if grade else (),
        subjects=tuple(subject.name for subject in student.subjects),
        courses=course_for_goal(student.certification_goal),
    )


def course_for_goal(goal: str | None) -> tuple[str, ...]:
    """The Knowledge Library course a certification goal (its catalog display name) studies: CCNA -> ccna,
    any CCNP or CCIE track -> ccnp (CCIE builds on CCNP), the cloud and security vendor tracks -> their own
    course. Any other goal, or none: every course."""
    name = (goal or "").upper()
    if name.startswith("CCNA"):
        return ("ccna",)
    if name.startswith(("CCNP", "CCIE")):
        return ("ccnp",)
    if "AWS" in name:
        return ("aws",)
    if "AZURE" in name or "AZ-" in name:
        return ("azure",)
    if "CPENT" in name:
        return ("cpent",)
    if "CEH" in name:
        return ("ceh",)
    if "PALO ALTO" in name or "PCNSE" in name:
        return ("paloalto",)
    if "FORTINET" in name or "FORTIGATE" in name or "FORTIOS" in name:
        return ("fortigate",)
    return ()


def resolve_assistant_config(
    user: User | None,
    conversation: Conversation | None,
    student: StudentContext | None = None,
    *,
    interactive: bool = False,
    tests: str = "",
    teaching: bool = False,
    bank: str | None = None,
    intent: str | None = None,
    search: bool = True,
    scoped: bool = False,
    scope: str | None = None,
    voice: bool = False,
    program: str = "",
    curriculum: Any = None,
    platform: str = "",
) -> AssistantConfig:
    """`intent`: what the intent router read in the latest message (app/intents/taxonomy.py); an intent
    answered from the app's own records searches no knowledge. `search` False: no knowledge search on
    this turn (small talk that opens a chat). Attached files are read either way (app/rag/graph.py). `program` and
    `curriculum`: a program chat's tutor block and month (app/programs), which replace the course boundary."""
    wanted = INTENTS.get(intent)
    search = search and (wanted is None or wanted.retrieval)
    return AssistantConfig(
        system_prompt=system_prompt_for(
            student,
            interactive,
            tests,
            teaching=teaching,
            bank=bank,
            intent=intent,
            scoped=scoped,
            scope=scope,
            voice=voice,
            program=program,
            platform=platform,
        ),
        retrieval=(RetrievalOptions(curriculum=curriculum) if curriculum is not None else retrieval_for(student))
        if search
        else None,
    )
