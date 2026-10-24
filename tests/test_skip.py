"""The skip rule (src/skip.py): the highest gate that loses at most 1 per
cent of the graded yes. Invented numbers."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import skip  # noqa: E402

S = {"max_loss": 0.01, "min_positives": 10}


def test_the_gate_sits_at_the_lowest_yes_when_none_may_be_lost():
    pos = [(0.40 + i / 100, False, 1.0) for i in range(30)]
    assert skip.fit(pos, S) == (0.40, 0.0, 30)


def test_a_light_yes_below_may_be_lost_within_one_per_cent():
    pos = [(0.20, False, 0.5)] + [(0.50 + i / 100, False, 1.0) for i in range(99)]
    gate, lost, n = skip.fit(pos, S)
    assert gate == 0.50 and 0 < lost <= 0.01


def test_a_yes_a_pattern_matches_does_not_hold_the_gate_down():
    pos = [(0.05, True, 1.0)] + [(0.45 + i / 100, False, 1.0) for i in range(20)]
    assert skip.fit(pos, S)[0] == 0.45


def test_too_few_yes_or_none_exposed_leave_the_panel_gate():
    assert skip.fit([(0.5, False, 1.0)] * 5, S) is None
    assert skip.fit([(0.5, True, 1.0)] * 30, S) is None
