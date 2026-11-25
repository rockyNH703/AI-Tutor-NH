"""Battle timing and scoring (owner-approved R-T1, R-B1; exam battle D1, D2, D8). Pure functions: the engine stores
their results, so a battle's totals can always be recomputed from its rows.

`round_seconds` and `points` are the daily challenge's (and a legacy round battle's, for its history); an exam battle
is marked +4 / -1 / 0 (`exam_marks`) and decided by `exam_winner`."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

MIN_SECONDS, MAX_SECONDS = 20, 45  # R-T1: clamp(10 + words / 3, 20, 45)
COUNTDOWN_SECONDS = 3
BASE_POINTS, SPEED_POINTS = 100, 50  # R-B1: 100 + 50 x time left / T for a correct answer
ALLOWANCE = timedelta(seconds=2)  # G6 / D8: network delay allowed after the time shown is up (daily and exam)
PER_QUESTION_SECONDS = 60  # D4: an exam's deadline is 60 s per question
CORRECT_MARKS, INCORRECT_MARKS = 4, -1  # D1


def round_seconds(words: int) -> int:
    """A round's window for a question of `words` words: the same for both players."""
    return min(MAX_SECONDS, max(MIN_SECONDS, 10 + math.ceil(words / 3)))


def points(correct: bool, ms_left: int, seconds: int) -> int:
    """A correct answer earns 100, plus up to 50 for the time left; a wrong or missing one earns nothing."""
    if not correct:
        return 0
    left = min(max(ms_left, 0), seconds * 1000)
    return BASE_POINTS + (SPEED_POINTS * left) // (seconds * 1000)


def exam_marks(correct: int, incorrect: int) -> int:
    """D1: +4 per correct answer, -1 per incorrect one, 0 unanswered (it can be negative)."""
    return CORRECT_MARKS * correct + INCORRECT_MARKS * incorrect


@dataclass(frozen=True)
class ExamTally:
    marks: int
    incorrect: int
    seconds: int  # whole seconds from the start to the final submission (an auto-submit: the whole exam)


def exam_winner(a: ExamTally, b: ExamTally) -> int | None:
    """D2: 0 when `a` wins, 1 when `b` wins, None for a draw: marks, then fewer incorrect, then the earlier final
    submission in whole seconds."""
    for left, right in ((a.marks, b.marks), (b.incorrect, a.incorrect), (b.seconds, a.seconds)):
        if left != right:
            return 0 if left > right else 1
    return None
