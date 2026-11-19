"""The ::: card opener `_fenced` removes along with a bare exercise (app/qbank/chat_tests.py `_CARD_OPEN`).

Its card name was `[\\w-]*(?:fill|blank)[\\w-]*`: when anything but the end followed a long name, the engine tried
every split of it, so `":::" + "fillx" * 2000 + "\\nx"` took about 2 s per reply (quadratic in the line, on model
text). The name is now taken whole: the same matches, in linear time.
"""

import random
import re
import time

import pytest

from app.qbank.chat_tests import _CARD_OPEN, _fenced

# The opener before the fix, kept to prove the new one finds exactly the same spans.
OLD_CARD_OPEN = re.compile(
    r"(?:^|\n)[ \t]*:{3,}[ \t]*[\w-]*(?:fill|blank)[\w-]*(?:\[[^\]\n]*\]|\{[^}\n]*\})*\s*$", re.I
)
BUDGET_S = 0.5
LONG_NAME = ":::" + "fillx" * 2000
EXERCISE = '{"text": "The link-state protocol that elects a DR is [[OSPF]].", "explanation": "OSPF elects one."}'

NAME_PARTS = ["fill", "FILL", "Blank", "blank", "x", "-", "_", "9", "é", "ill", "lan"]
TAIL_PARTS = ["[t]", "[", "]", "{a=1}", "{", "}", " ", "\t", "\n", "!", "x", "fill", ":::"]
NOISE = [":", ":::", " ", "\t", "\n", "fill", "blank", "x", "-", "[", "]", "{", "}", "!", "é"]


def _span(pattern: re.Pattern[str], text: str) -> tuple[int, int] | None:
    match = pattern.search(text)
    return match.span() if match else None


def _pick(rng: random.Random, parts: list[str], most: int) -> str:
    return "".join(rng.choice(parts) for _ in range(rng.randint(0, most)))


def _generated(rng: random.Random) -> str:
    """Prose, then (usually) a ::: line: colons, a name built around fill/blank, titles and attributes, a tail."""
    if rng.random() < 0.2:
        return _pick(rng, NOISE, 30)
    opener = rng.choice(["", " ", "\t "]) + ":" * rng.randint(2, 5) + rng.choice(["", " ", "\t"])
    return (
        _pick(rng, NOISE, 8) + rng.choice(["", "\n"]) + opener + _pick(rng, NAME_PARTS, 6) + _pick(rng, TAIL_PARTS, 5)
    )


@pytest.mark.parametrize(
    "text",
    [
        ":::fill-blank\n",
        "Try this one:\n:::fill-blank[Routing]{level=2}\n\n",
        "  ::: Blank_card[a][b]",
        ":::steps[Check]",
        ":::fill-blank[unclosed",
        ":::fill-blank x",
        "prose\n:::\tmyFILLer",
        "::fill-blank",
        "",
    ],
)
def test_known_openers_match_as_before(text):
    assert _span(_CARD_OPEN, text) == _span(OLD_CARD_OPEN, text)


def test_generated_text_matches_as_before():
    rng = random.Random(20261119)
    matched = 0
    for _ in range(20_000):
        text = _generated(rng)
        expected = _span(OLD_CARD_OPEN, text)
        assert _span(_CARD_OPEN, text) == expected, repr(text)
        matched += expected is not None
    assert matched > 1_000  # the comparison covers real openers, not only misses


@pytest.mark.parametrize(
    "text",
    [
        LONG_NAME + "\nx",
        LONG_NAME + " x",
        LONG_NAME + "!",
        ("\n:::" + "fill" * 10) * 2000 + "x",
        ":::fill" + "[a]{b}" * 3000 + "x",
        ":" * 20_000 + "fill x",
        ":::" + "-" * 20_000 + "!",
    ],
    ids=[
        "long-name-then-line",
        "long-name-then-word",
        "long-name-then-mark",
        "many-lines",
        "many-groups",
        "colons",
        "no-fill",
    ],
)
def test_the_search_is_linear(text):
    start = time.perf_counter()
    _CARD_OPEN.search(text)
    assert time.perf_counter() - start < BUDGET_S


def test_a_reply_after_a_long_card_name_is_repaired_in_time():
    reply = f"{LONG_NAME}\nThe exercise:\n{EXERCISE}"
    start = time.perf_counter()
    repaired = _fenced(reply)
    assert time.perf_counter() - start < BUDGET_S
    assert f"```fill-blank\n{EXERCISE}\n```" in repaired
