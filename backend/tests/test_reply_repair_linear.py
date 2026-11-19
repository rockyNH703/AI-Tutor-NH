"""The reply repair before practice tests are read (app/qbank/chat_tests.py) and the test reader
(app/assessments/state.py) on hostile model text.

Each part below was quadratic, or raised, on a reply the model can write:
- a ::fill{...} directive with a long name, or many opening lines and no closing quote (`_directive_exercises`);
- many `:::a` card lines with no closing line, or sharing one (`_card_exercises`);
- a run of blank lines in a fill-blank block that is not JSON (`_KEY_LINE`: `^\\s*` crossed them);
- JSON nested deeper than Python recurses (RecursionError, not ValueError, escaped every parse).

The old code is kept here to prove the new code gives the same output, in linear time.
"""

import asyncio
import json
import random
import re
import time
import uuid

import pytest

from app.assessments import state
from app.qbank import chat_tests
from app.qbank.chat_tests import (
    _ATTRIBUTE,
    _BARE_EXERCISE,
    _BLANK,
    _BOLD_BLANK,
    _CARD_END,
    _CARD_START,
    _KEY_LINE,
    _card_exercises,
    _directive_exercises,
    _exercise,
    _fenced,
    _parse,
)

BUDGET_S = 0.5
DEEP = 100_000

OLD_DIRECTIVE = re.compile(r"^[ \t]*::[\w-]*(?:fill|blank)[\w-]*\{(?P<attrs>.*?\")\s*\}[ \t]*$", re.I | re.M | re.S)
OLD_KEY_LINE = re.compile(r"^\s*\"?(text|explanation)\"?\s*:\s*(.+?)\s*,?\s*$", re.M)


def old_directive_exercises(text: str) -> str:
    def block(match: re.Match) -> str:
        fields = {name: value.replace('\\"', '"') for name, value in _ATTRIBUTE.findall(match["attrs"])}
        value = {key: fields[key] for key in ("text", "explanation") if key in fields}
        return f"```fill-blank\n{json.dumps(value, ensure_ascii=False)}\n```" if _exercise(value) else match[0]

    return OLD_DIRECTIVE.sub(block, text)


def old_card_exercises(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        opened = _CARD_START.match(lines[index])
        end = next((at for at in range(index + 1, len(lines)) if _CARD_END.match(lines[at])), None) if opened else None
        body = "\n".join(lines[index + 1 : end]).strip() if opened and end is not None else ""
        if not _BLANK.search(body) or "```" in body or _BARE_EXERCISE.search(body):
            out.append(lines[index])
            index += 1
            continue
        title = (opened["title"] or "").strip()
        exercise = json.dumps({"text": _BOLD_BLANK.sub(r"\1", body)}, ensure_ascii=False)
        out.append((f"**{title}**\n\n" if title else "") + f"```fill-blank\n{exercise}\n```")
        index = end + 1
    return "\n".join(out)


DIRECTIVE_LINES = [
    '::fill-blank{text="OSPF elects a [[DR]]." explanation="On broadcast links."}',
    '::fill{text="A /24 has [[256]] addresses."}',
    "  ::BLANKS{",
    '::fill-blank{text="two',
    'lines [[here]]"}',
    '"}',
    '" }  ',
    "}",
    "::fillx",
    "::steps{a=1}",
    'text="[[x]]"',
    "",
    "prose with [[x]]",
]
CARD_LINES = [
    ":::steps[Try it]",
    ":::fill-blank",
    ":::a",
    ":::",
    "  :::  ",
    "The [[answer]] goes here.",
    "**[[bold]]** blank",
    "```",
    '{"text": "a [[b]]"}',
    "{",
    '"text":',
    "",
    "plain line",
]
KEY_LINES = [
    "text: The [[answer]]",
    ' "text": "a [[b]]",',
    '"explanation": "why"',
    "explanation:",
    "",
    "   ",
    "\t",
    "\r",
    "\x0b",
    "\x0c",
    "text",
    ": x",
    ", ",
    "  text :  y  ,",
]


def _lines(rng: random.Random, parts: list[str], most: int) -> str:
    return "\n".join(rng.choice(parts) for _ in range(rng.randint(0, most)))


def test_directives_repair_as_before():
    rng = random.Random(20261119)
    repaired = 0
    for _ in range(5_000):
        text = _lines(rng, DIRECTIVE_LINES, 10)
        expected = old_directive_exercises(text)
        assert _directive_exercises(text) == expected, repr(text)
        repaired += expected != text
    assert repaired > 500  # the comparison covers real directives, not only text left alone


def test_cards_repair_as_before():
    rng = random.Random(20261120)
    repaired = 0
    for _ in range(20_000):
        text = _lines(rng, CARD_LINES, 12)
        expected = old_card_exercises(text)
        assert _card_exercises(text) == expected, repr(text)
        repaired += expected != text
    assert repaired > 1_000


def test_key_lines_read_as_before():
    rng = random.Random(20261121)
    found = 0
    for _ in range(5_000):
        body = _lines(rng, KEY_LINES, 10)
        expected = OLD_KEY_LINE.findall(body)
        assert _KEY_LINE.findall(body) == expected, repr(body)
        found += bool(expected)
    assert found > 1_000


@pytest.mark.parametrize(
    ("repair", "text"),
    [
        (_directive_exercises, "::fill{\n" * 4_000),
        (_directive_exercises, '::fill{"\n' * 4_000),
        (_directive_exercises, "::" + "fillx" * 8_000 + "\nx"),
        (_card_exercises, ":::a\n" * 4_000 + "[[x]]"),
        (_card_exercises, ":::a[t]\n" * 3_000 + "[[x]] ```\n:::\n"),
        (_card_exercises, ":::a\n" * 3_000 + '[[x]] {"text": 1}\n:::\n'),
        (lambda body: _KEY_LINE.findall(body), "\n" * 10_000),
        (lambda body: _KEY_LINE.findall(body), " \n" * 6_000),
        (_fenced, "```fill-blank\n" + "\n" * 8_000 + "```\n[[x]]"),
    ],
    ids=[
        "directive-openings",
        "directive-quoted-openings",
        "directive-long-name",
        "cards-unclosed",
        "cards-sharing-a-close-with-a-fence",
        "cards-sharing-a-close-with-json",
        "key-lines-blank",
        "key-lines-spaces",
        "fill-blank-block-of-blank-lines",
    ],
)
def test_the_repair_is_linear(repair, text):
    start = time.perf_counter()
    repair(text)
    assert time.perf_counter() - start < BUDGET_S


def test_a_card_and_a_directive_still_become_exercises():
    reply = ':::steps[Routing]\nOSPF elects a **[[DR]]**.\n:::\n\n::fill-blank{text="A /24 has [[256]] addresses."}'
    repaired = _card_exercises(_directive_exercises(reply))
    assert '**Routing**\n\n```fill-blank\n{"text": "OSPF elects a [[DR]]."}\n```' in repaired
    assert '```fill-blank\n{"text": "A /24 has [[256]] addresses."}\n```' in repaired


@pytest.mark.parametrize(
    "reply",
    [
        "```fill-blank\n" + "[" * DEEP + "\n```\n[[x]]",
        '{"text": ' + "[" * DEEP + " [[x]]",
        "```json\n" + "[" * DEEP + "\n```\n[[x]]",
    ],
    ids=["fill-blank-block", "bare-exercise", "other-block"],
)
def test_deep_json_in_a_reply_is_left_as_it_is(reply):
    assert _fenced(reply) == reply


def test_deep_json_is_no_question_bank_request():
    assert _parse("[" * DEEP) is None


def test_deep_json_after_an_inline_request_is_left_as_it_is():
    reply = "question-bank" + '{"a": ' * DEEP
    content, test = asyncio.run(chat_tests.fill(None, reply, "seed", uuid.uuid4(), uuid.uuid4()))  # type: ignore[arg-type]
    assert (content, test) == (reply, None)


@pytest.mark.parametrize("language", ["quiz", "mcq", "fill-blank"])
def test_a_deep_test_block_is_invalid_json_not_an_error(language):
    records, problem = state.build(f"```{language}\n" + "[" * DEEP + "\n```", None)
    assert not records
    assert problem == "invalid_json"


def test_the_model_view_reads_past_a_deep_test_block():
    deep = '```quiz\n{"questions": ' + "[" * DEEP + "\n```"
    assert state.for_model(deep) == deep
