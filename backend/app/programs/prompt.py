"""What a program chat's tutor is told: the program, its month, its months and the grounding rules (owner decision D6,
tightened by the second deep pass: a question that belongs to another month gets a pointer, never an answer). Built
from the database's titles only, never from a request."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

GENERAL = "General explanation (not from your NH material)"
PROGRAM_UNCOVERED = "This isn't covered in your program material."


@dataclass(frozen=True)
class ProgramContext:
    program_id: uuid.UUID
    slug: str
    title: str
    month: int | None  # None: the program's QnA (every month)
    month_title: str | None
    mode: str  # tutor | interview
    months: tuple[tuple[int, str], ...]


# Words a month title shares with too many questions to name a month's topic (and stop words).
_BROAD = frozenset(
    "a an and the of with for in on to ai network networks networking fundamentals foundations security mastery web "
    "cloud computing operations firewall firewalls technologies next generation gen core advanced stack project "
    "projects final career launch placement preparation enterprise routing switching testing response incident "
    "ccnp ngfw".split()
)
# Spellings of one topic, folded to the word month titles use.
_SPELLINGS = (
    (r"palo\s*alto", "paloalto"),
    (r"sd[\s-]*wan", "sdwan"),
    (r"\bv(manage|smart|bond|edge)\b|\bomp\b", "sdwan"),  # Cisco SD-WAN components
    (r"wi[\s-]*fi", "wifi"),
    (r"zero[\s-]*trust", "zerotrust"),
    (r"\bk8s\b", "kubernetes"),
    (r"\bfortinet\b", "fortigate"),
    (r"\bpen[\s-]*test(ing|er)?\b|\bpentest(ing|er)?\b", "penetration"),
    (r"\bnse[\s-]*4\b", "nse"),
)


def _terms(text: str) -> set[str]:
    folded = text.lower()
    for pattern, word in _SPELLINGS:
        folded = re.sub(pattern, word, folded)
    return {word for word in re.findall(r"[a-z0-9]+", folded) if word not in _BROAD and not word.isdigit()}


def months_naming(context: ProgramContext, question: str) -> list[tuple[int, str]]:
    """Other months whose title names a specific term of the question (FortiGate, Kubernetes, SD-WAN, ENARSI...),
    unless this month's own title names it too. A fact for the tutor, so it never guesses which month covers what."""
    asked = _terms(question)
    if context.month is None or not asked or asked & _terms(context.month_title or ""):
        return []
    return [(number, title) for number, title in context.months if number != context.month and asked & _terms(title)]


# A question about the app itself is not a course question, so the ordered steps above do not apply to it.
APP_QUESTIONS = (
    "A question about the NetSec AI app or the user's account is not a course question: skip these steps and "
    "answer it from the app facts when they are given, else point them to Settings > Help & Support."
)


def tutor_block(context: ProgramContext, question: str = "") -> str:
    months = "\n".join(f"- Month {number}: {title}" for number, title in context.months)
    if context.month is not None:
        uncovered = f"This isn't covered in your Month {context.month} material."
        named = months_naming(context, question)
        pointer = (
            "The app's title check: this question names the topic of "
            + "; ".join(f"Month {number}: {title}" for number, title in named)
            + ". So step 3 does not apply to this question: answer it from the reference material only if that "
            "material is about this exact topic; otherwise use step 2 (a pointer, no explanation)."
            if named
            else "The app's title check: no other month's title names this question's topic."
        )
        lines = [
            f"You are the NetworkersHome (NH) tutor for the program {context.title}, "
            f"Month {context.month}: {context.month_title}.",
            "Teach this month's topics to an enrolled NH student: explain step by step, give examples where they help, "
            "and keep the conversation in mind for follow-up questions.",
            "The reference material with each message is this month's NH material and nothing else. Ground your "
            "answers in it and cite it.",
            "Decide every question in this order, and stop at the first step that applies:",
            "1. The reference material covers it: answer from the material and cite it.",
            "2. The app's title check below names another month: do not answer it, not even briefly: say which month "
            'covers it (for example "That is covered in Month 3: ...") and suggest opening that month.',
            f'3. Otherwise: first say "{uncovered}" Then give a short general explanation under the heading '
            f'"{GENERAL}". Never say another month covers it.',
            pointer,
            "The program's months:",
            months,
            "Never present general knowledge as NH material, and never say the NH material covers something the "
            "reference material does not show.",
            APP_QUESTIONS,
        ]
    else:
        lines = [
            f"You are the NetworkersHome (NH) tutor for the program {context.title}: questions on any of its months.",
            "The reference material with each message is this program's NH material. Ground your answers in it and "
            "cite it.",
            # The same ordered decision as a month's tutor: without "stop", a reply answered from the material and
            # then said it was not covered (production audit, 2026-11-24).
            "Decide every question in this order, and stop at the first step that applies:",
            "1. The reference material covers it: answer from the material and cite it; never add a general section "
            "or say that anything is not covered.",
            f'2. Otherwise: first say "{PROGRAM_UNCOVERED}" Then give a short general explanation under the heading '
            f'"{GENERAL}".',
            "The program's months, so you can say which month a topic belongs to:",
            months,
            "Never present general knowledge as NH material.",
            APP_QUESTIONS,
        ]
    if context.mode == "interview":
        lines.append(
            "This chat is a mock interview for this month: you are the interviewer. Ask one interview question at a "
            "time, preferring the NH interview questions in the reference material; wait for the answer; then give "
            "brief feedback (what was right, what was missing, a model answer) and ask the next question. If the "
            "student has not answered a question yet, start with the first one."
        )
    return "\n".join(lines)
