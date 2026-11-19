"""Spotting a practice fence (has_test) and blanking a fill-blank exercise's keys (for_model) read a reply in
linear time, and give exactly what the regexes they replaced gave: those were quadratic on a long run of
backticks (20,000: 17 s, on every save and records_for read) and on many unclosed fill-blank openings (56,000
characters: 6.2 s, on every history message of every turn)."""

import random
import re
import time

import pytest

from app.assessments import state

# The replaced regexes, verbatim: the reference on inputs they finish quickly.
OLD_TEST_FENCE = re.compile(r"(?:`{3,}|~{3,})[ \t]*(?:quiz|mcq|fill-blank)(?=\s|$)", re.M)
OLD_FILL_FENCE = re.compile(r"(^|\n)([ \t]*)(`{3,}|~{3,})[ \t]*fill-blank\b[^\n]*\n.*?\n[ \t]*\3[ \t]*(?=\n|$)", re.S)


def old_for_model(content: str) -> str:
    """for_model as it was, with the regex."""
    if "fill-blank" in content:
        content = OLD_FILL_FENCE.sub(lambda match: state._ANSWER_KEY.sub("____", match.group(0)), content)
    if "quiz" not in content:
        return content
    try:
        return state._quizzes_for_model(content)
    except Exception:
        return content


INFOS = ("fill-blank", "fill-blank x", "fill-blank [[k]]", "fill-blanks", "fill-blank_x", "fill-blank-x", "quiz", "")
BODIES = ("", "text", '{"text": "a [[b|c]] d [[e]]"}', "[[a", "b]]", "[[x]] [[", "]] [[y]]", "x ```quiz", "~~~mcq")
ATOMS = ("`", "```", "~", "~~~", " ", "\t", "\n", "\r\n", "quiz", "mcq", "fill-blank", "[[a]]", "[[", "]]")


def _fence(rng: random.Random) -> str:
    return rng.choice(("```", "```", "```", "~~~", "````", "~~~~~"))


def _line(rng: random.Random) -> str:
    shape = rng.randrange(4)
    if shape == 3:  # a whole exercise, as a model writes one
        fence = _fence(rng)
        return f"{fence}fill-blank\n{BODIES[2]}\n{rng.choice(('', '  '))}{fence}"
    if shape == 0:  # an opening
        return rng.choice(("", " ", "  ", "\t")) + _fence(rng) + rng.choice(("", " ", "\t")) + rng.choice(INFOS)
    if shape == 1:  # a closer, or nearly one
        return rng.choice(("", " ", "   ", "\t ")) + _fence(rng) + rng.choice(("", " ", "\t", "\r", "`", " x"))
    return rng.choice(BODIES)


def _replies(count: int) -> list[str]:
    rng = random.Random(20261118)
    lines = ["\n".join(_line(rng) for _ in range(rng.randint(1, 14))) for _ in range(count)]
    atoms = ["".join(rng.choice(ATOMS) for _ in range(rng.randint(0, 40))) for _ in range(count)]
    return lines + atoms


FIXED = [
    "```fill-blank\n```\n[[a]]\n```",  # the line right after the opening is never its closer
    "```fill-blank\n\n```\n[[b]]",
    "  ~~~ fill-blank [[k]]\n[[x|y]]\n   ~~~  \nafter [[z]]",
    "```fill-blank\r\n[[a]]\r\n```\r\n",  # a CR LF closer never closed it
    "```fill-blank\n[[a\nb]]\n```",  # a key over two lines
    "````fill-blank\n[[a]]\n```\n````\n```fill-blank\n[[b]]\n```",
    "`````quiz",
    "x ``quiz",
    "``~~~mcq\n",
    "~~~~~~~fill-blank\t",
]


def test_spotting_and_blanking_give_what_the_regexes_gave_on_every_reply():
    replies = FIXED + _replies(2500)
    blanked = 0
    for reply in replies:
        assert state.has_test(reply) is (OLD_TEST_FENCE.search(reply) is not None), repr(reply)
        expected = old_for_model(reply)
        assert state.for_model(reply) == expected, repr(reply)
        blanked += expected != reply
    assert sum(state.has_test(reply) for reply in replies) > 1000 and blanked > 300  # both paths were exercised


# A long run (with no practice word, or one that never ends the word) and thousands of unclosed openings.
LONG_RUNS = {
    "backticks": "`" * 20000,
    "tildes": "~" * 20000,
    "backticks-then-word": "A test:\n" + "`" * 20000 + "quizzes",
}
UNCLOSED = {
    "same-fence": "```fill-blank\n" * 4000,
    "indented-tildes": "  ~~~ fill-blank [[k]]\n" * 3000,
    "every-length": "".join("`" * length + "fill-blank\n" for length in range(3, 600)),
}


@pytest.mark.parametrize("reply", LONG_RUNS.values(), ids=LONG_RUNS)
def test_spotting_a_test_fence_takes_linear_time(reply):
    started = time.perf_counter()
    assert not state.has_test(reply) and state.records_for(None, reply, "complete") == []
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize("reply", UNCLOSED.values(), ids=UNCLOSED)
def test_blanking_exercise_keys_takes_linear_time(reply):
    started = time.perf_counter()
    assert state.for_model(reply) == reply  # nothing closed, so nothing blanked
    assert time.perf_counter() - started < 0.5
