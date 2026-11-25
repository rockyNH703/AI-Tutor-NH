"""NH Friend Zone timing and scoring (R-T1, R-B1 for the daily; D1, D2, D7 for the exam battle): pure functions, no
database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import FzBattle, FzBattlePlayer
from app.friendzone import battles, progress
from app.friendzone.scoring import ExamTally, exam_marks, exam_winner, points, round_seconds


def test_the_round_window_scales_with_the_question_and_is_clamped():
    assert [round_seconds(words) for words in (0, 30, 31, 60, 105, 500)] == [20, 20, 21, 30, 45, 45]


def test_points_reward_a_correct_answer_and_its_speed_only():
    assert points(False, 30_000, 30) == 0
    assert points(True, 0, 30) == 100
    assert points(True, 15_000, 30) == 125
    assert points(True, 30_000, 30) == 150
    assert points(True, 99_000, 30) == 150 and points(True, -5, 30) == 100  # clamped to the window


def test_exam_marks_are_plus_four_minus_one_and_can_be_negative():
    assert exam_marks(0, 0) == 0
    assert exam_marks(10, 0) == 40
    assert exam_marks(7, 3) == 25
    assert exam_marks(0, 3) == -3


def test_the_exam_winner_is_decided_by_marks_then_fewer_incorrect_then_the_earlier_submission():
    assert exam_winner(ExamTally(12, 3, 500), ExamTally(11, 0, 10)) == 0  # marks first
    assert exam_winner(ExamTally(-3, 3, 10), ExamTally(0, 0, 600)) == 1  # nothing answered beats a negative score
    assert exam_winner(ExamTally(-5, 5, 10), ExamTally(-2, 2, 600)) == 1  # two negative totals
    assert exam_winner(ExamTally(8, 0, 600), ExamTally(8, 2, 10)) == 0  # equal marks: fewer incorrect
    assert exam_winner(ExamTally(8, 1, 300), ExamTally(8, 1, 299)) == 1  # then the earlier submission
    assert exam_winner(ExamTally(8, 1, 300), ExamTally(8, 1, 300)) is None  # then a draw


def _at(start: datetime, seconds: float) -> FzBattlePlayer:
    return FzBattlePlayer(role="challenger", submitted_at=start + timedelta(seconds=seconds))


def test_time_taken_is_whole_seconds_floored_from_the_start():
    start = datetime(2026, 11, 25, 10, 0, 0, tzinfo=UTC)
    battle = FzBattle(starts_at=start, ends_at=start + timedelta(seconds=600))
    # two submissions 0.3 s apart across a wall-clock second: different whole seconds, the earlier wins
    assert (battles.taken_seconds(battle, _at(start, 100.9)), battles.taken_seconds(battle, _at(start, 101.2))) == (
        100,
        101,
    )
    # 0.3 s apart inside one second: the same whole second, so a draw on time
    assert battles.taken_seconds(battle, _at(start, 100.1)) == battles.taken_seconds(battle, _at(start, 100.4))
    assert battles.taken_seconds(battle, _at(start, 600)) == 600  # an auto-submit: the whole exam
    assert battles.taken_seconds(battle, FzBattlePlayer(role="opponent")) is None


def test_perfect_score_needs_an_exam_of_10_20_or_30_all_right():
    right = FzBattlePlayer(role="challenger", correct=20)
    assert progress._perfect(FzBattle(format="exam", length=20), 20, right)
    assert not progress._perfect(FzBattle(format="exam", length=20), 19, right)  # a voided question
    ten = FzBattlePlayer(role="challenger", correct=10)
    assert progress._perfect(FzBattle(format="exam", length=10), 10, ten)
    assert not progress._perfect(FzBattle(format="rounds", length=10), 10, ten)  # a legacy round battle
    five = FzBattlePlayer(role="challenger", correct=5)
    assert not progress._perfect(FzBattle(format="rounds", length=5), 5, five)
