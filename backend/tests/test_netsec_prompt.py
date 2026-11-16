"""NetSec AI's model-facing text (2026-10-29): an engineering assistant, with nothing left of the school tutor.

The frozen prompt is the owner's text (tests/test_legacy_tutor.py holds it byte for byte); this checks
its key clauses, and that no text the model reads still speaks of students, tutors or schoolwork.
"""

import re

import pytest

from app.assessments.state import NOT_ANSWERED, SUMMARY_FRAME, USE_RECORD
from app.chat.assistant import BANK_BLOCKS, INTENT_NOTE, INTERACTIVE_BLOCKS, SYSTEM_PROMPT, VOICE_TURN
from app.chat.teaching import TEACHING_POLICY
from app.intents import scope
from app.intents.taxonomy import GROUP_QUESTION, GROUPS, INTENT_QUESTION, INTENTS
from app.memory.extraction import _SYSTEM_PROMPT as EXTRACTION_PROMPT
from app.qbank import chat_tests
from app.rag.context import CONTEXT_HEADER, FILES_HEADER, MEMORY_HEADER

SCHOOL = re.compile(
    r"\b(students?|tutor\w*|homework|syllabus|ncert|cbse|icse|textbooks?|schools?|teachers?|class \d{1,2})\b",
    re.IGNORECASE,
)

FIXED_TEXTS = {
    "SYSTEM_PROMPT": SYSTEM_PROMPT,
    "VOICE_TURN": VOICE_TURN,
    "INTERACTIVE_BLOCKS": INTERACTIVE_BLOCKS,
    "BANK_BLOCKS": BANK_BLOCKS,
    "INTENT_NOTE": INTENT_NOTE,
    "TEACHING_POLICY": TEACHING_POLICY,
    "CONTEXT_HEADER": CONTEXT_HEADER,
    "MEMORY_HEADER": MEMORY_HEADER,
    "FILES_HEADER": FILES_HEADER,
    "EXTRACTION_PROMPT": EXTRACTION_PROMPT,
    "SCOPE_POLICY": scope.POLICY,
    "SCOPE_QUESTION": scope.QUESTION["question"],
    "SCOPE_REDIRECT": scope.COPY["english"],
    "SUMMARY_FRAME": SUMMARY_FRAME,
    "USE_RECORD": USE_RECORD,
    "NOT_ANSWERED": NOT_ANSWERED,
    "NOTE_UNKNOWN": chat_tests.NOTE_UNKNOWN,
    "NOTE_MODEL": chat_tests.NOTE_MODEL,
    "GROUP_QUESTION": GROUP_QUESTION,
    "INTENT_QUESTION": INTENT_QUESTION,
    **{f"scope option {name}": text for name, text in scope.OPTIONS.items()},
    **{f"scope line {name}": text for name, text in scope.INSTRUCTIONS.items()},
    **{f"group {name}": text for name, text in GROUPS.items()},
    **{f"{name} option": intent.option for name, intent in INTENTS.items()},
    **{f"{name} guidance": intent.guidance for name, intent in INTENTS.items()},
}


def test_the_frozen_prompt_is_an_engineering_assistants():
    for clause in (
        "You are NetSec AI, an assistant for network, security and infrastructure engineers",
        "lead with the answer, fix or most likely cause",
        "how to verify (show or debug commands and the output that means success), the risks and how to roll back",
        "Troubleshooting: rank the likely causes",
        "say which platform and version your commands are for",
        "Never invent command output, logs, packet data, versions, CVE or bug IDs, RFC sections or document names.",
        "never call a configuration tested, verified or guaranteed",
        "state the impact and a backout plan",
        "for authorised, scoped engagements and labs",
        "A value shown as <redacted> was masked by the app; never ask the user to send it again.",
        "Cite sources inline only, like [1] or [F1]; never write your own list of sources.",
    ):
        assert clause in SYSTEM_PROMPT, clause
    # The fake LLM's PROBE splits the prompt at the first "\n\n## ": what follows is what settings added.
    assert "\n\n## " not in SYSTEM_PROMPT
    assert "\n\n## " not in BANK_BLOCKS and "\n\n## " not in INTERACTIVE_BLOCKS


def test_the_frozen_prompt_demands_citation_honesty():
    """The blind-evaluation failures (2026-11-06): unsupported citations, no missing-topic admission,
    invented CLI. Each must be answered by an explicit prompt clause."""
    for clause in (
        "cite a number only for the passage that supports that specific claim",
        "say the library lacks it",
        "label that answer as not source-backed",
        "Never present a command or its output as from the material unless it shows it",
    ):
        assert clause in SYSTEM_PROMPT, clause


@pytest.mark.parametrize("name", sorted(FIXED_TEXTS))
def test_no_text_the_model_reads_speaks_of_school(name):
    found = SCHOOL.search(FIXED_TEXTS[name])
    assert found is None, f"{name}: {found and found.group()!r}"


def test_the_bank_blocks_keep_the_keys_the_app_parses():
    # The question bank's request is parsed by app/qbank/chat_tests.py: "chapter" (a domain id),
    # "chapters", "topics", "count" and "difficulty"; the examples are ids it accepts.
    for key in ('"chapter"', '"chapters"', '"topics"', '"count"', '"difficulty"', "```question-bank"):
        assert key in BANK_BLOCKS
    examples = re.findall(r"\"(?:chapter|chapters)\": \[?\"([^\"]+)\"", BANK_BLOCKS)
    assert examples and all(chat_tests.CHAPTER_ID.fullmatch(example) for example in examples)
    assert "say commands, addresses and interface names" in VOICE_TURN.lower()
