"""Battle timing and scoring (owner-approved R-T1, R-B1, R-B3). Pure functions: the engine stores their results, so a
battle's totals can always be recomputed from its rounds."""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_SECONDS, MAX_SECONDS = 20, 45  # R-T1: clamp(10 + words / 3, 20, 45)
REVEAL_SECONDS = 3
COUNTDOWN_SECONDS = 3
BASE_POINTS, SPEED_POINTS = 100, 50  # R-B1: 100 + 50 x time left / T for a correct answer
FORFEIT_AFTER_MISSED = 3  # R-B1: three missed rounds in a row forfeit the battle


def round_seconds(words: int) -> int:
    """A round's window for a question of `words` words: the same for both players."""
    return min(MAX_SECONDS, max(MIN_SECONDS, 10 + math.ceil(words / 3)))


def points(correct: bool, ms_left: int, seconds: int) -> int:
    """A correct answer earns 100, plus up to 50 for the time left; a wrong or missing one earns nothing."""
    if not correct:
        return 0
    left = min(max(ms_left, 0), seconds * 1000)
    return BASE_POINTS + (SPEED_POINTS * left) // (seconds * 1000)


@dataclass(frozen=True)
class Tally:
    score: int
    correct: int
    answer_ms: int  # total time taken over correct and wrong answers


def winner(a: Tally, b: Tally) -> int | None:
    """0 when `a` wins, 1 when `b` wins, None for a draw: points, then correct answers, then less time taken."""
    for left, right in ((a.score, b.score), (a.correct, b.correct), (b.answer_ms, a.answer_ms)):
        if left != right:
            return 0 if left > right else 1
    return None
