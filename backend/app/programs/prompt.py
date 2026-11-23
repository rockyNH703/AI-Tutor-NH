"""What a program chat's tutor is told: the program, its month, its months and the grounding rules (owner decision D6,
tightened by the second deep pass: a question that belongs to another month gets a pointer, never an answer). Built
from the database's titles only, never from a request."""

from __future__ import annotations

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


def tutor_block(context: ProgramContext) -> str:
    months = "\n".join(f"- Month {number}: {title}" for number, title in context.months)
    if context.month is not None:
        uncovered = f"This isn't covered in your Month {context.month} material."
        lines = [
            f"You are the NetworkersHome (NH) tutor for the program {context.title}, "
            f"Month {context.month}: {context.month_title}.",
            "Teach this month's topics to an enrolled NH student: explain step by step, give examples where they help, "
            "and keep the conversation in mind for follow-up questions.",
            "The reference material with each message is this month's NH material and nothing else. Ground your "
            "answers in it and cite it.",
            f'When the reference material does not cover the question, first say: "{uncovered}" Then give a short '
            f'general explanation under the heading "{GENERAL}".',
            "When the question belongs to another month of this program, do not answer it, not even briefly: say "
            'which month covers it (for example "That is covered in Month 3: ...") and suggest opening that month. '
            "The program's months:",
            months,
            "Never present general knowledge as NH material, and never say the NH material covers something the "
            "reference material does not show.",
        ]
    else:
        lines = [
            f"You are the NetworkersHome (NH) tutor for the program {context.title}: questions on any of its months.",
            "The reference material with each message is this program's NH material. Ground your answers in it and "
            "cite it.",
            f'When the reference material does not cover the question, first say: "{PROGRAM_UNCOVERED}" Then give a '
            f'short general explanation under the heading "{GENERAL}".',
            "The program's months, so you can say which month a topic belongs to:",
            months,
            "Never present general knowledge as NH material.",
        ]
    if context.mode == "interview":
        lines.append(
            "This chat is a mock interview for this month: you are the interviewer. Ask one interview question at a "
            "time, preferring the NH interview questions in the reference material; wait for the answer; then give "
            "brief feedback (what was right, what was missing, a model answer) and ask the next question. If the "
            "student has not answered a question yet, start with the first one."
        )
    return "\n".join(lines)
