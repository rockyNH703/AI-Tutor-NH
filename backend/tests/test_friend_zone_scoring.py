"""NH Friend Zone battle timing and scoring (R-T1, R-B1): pure functions, no database."""

from __future__ import annotations

from app.friendzone.scoring import Tally, points, round_seconds, winner


def test_the_round_window_scales_with_the_question_and_is_clamped():
    assert [round_seconds(words) for words in (0, 30, 31, 60, 105, 500)] == [20, 20, 21, 30, 45, 45]


def test_points_reward_a_correct_answer_and_its_speed_only():
    assert points(False, 30_000, 30) == 0
    assert points(True, 0, 30) == 100
    assert points(True, 15_000, 30) == 125
    assert points(True, 30_000, 30) == 150
    assert points(True, 99_000, 30) == 150 and points(True, -5, 30) == 100  # clamped to the window


def test_the_winner_is_decided_by_points_then_correct_answers_then_time():
    assert winner(Tally(300, 2, 9000), Tally(250, 3, 1000)) == 0
    assert winner(Tally(300, 2, 9000), Tally(300, 3, 9000)) == 1
    assert winner(Tally(300, 3, 8000), Tally(300, 3, 9000)) == 0
    assert winner(Tally(300, 3, 9000), Tally(300, 3, 9000)) is None
