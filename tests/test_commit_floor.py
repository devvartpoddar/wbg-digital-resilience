"""A measure's floor (control panel, floor.<set> else floor.default): a unit
counts as committed (or planned) when its probability - the probability
layer's, else the model's score for the grades the panel counts (2 and 3) -
reaches the floor; with no floor, when its most likely grade is one of them."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import labels  # noqa: E402
import measure  # noqa: E402
import panel  # noqa: E402


def _u(grade, p2, p3, sent="true", **kw):
    return {"sent": sent, "grade": str(grade), "p2": f"{p2:.3f}", "p3": f"{p3:.3f}", **kw}


def test_without_a_floor_the_most_likely_grade_decides():
    assert measure.is_committed(_u(3, 0.1, 0.41), 3)
    assert measure.is_committed(_u(2, 0.45, 0.1), 3)          # grade 2 counts too
    assert not measure.is_committed(_u(1, 0.3, 0.3), 3)
    assert not measure.is_committed(_u(3, 0.0, 0.9, sent="false"), 3)


def test_a_floor_reads_grades_two_and_three_together():
    assert measure.is_committed(_u(1, 0.3, 0.25), 3, 0.5)      # 0.55 >= 0.5
    assert not measure.is_committed(_u(3, 0.0, 0.45), 3, 0.5)  # 0.45 < 0.5


def test_the_layers_probability_wins_over_the_score():
    assert not measure.is_committed(_u(3, 0.4, 0.5, p_committed_calibrated="0.30"), 3, 0.5)
    assert measure.is_committed(_u(0, 0.0, 0.1, p_committed_calibrated="0.80"), 3, 0.5)


def test_a_row_that_says_so_is_believed():
    assert measure.is_committed(_u(0, 0.0, 0.0, committed="true"), 3, 0.9)
    assert not measure.is_committed(_u(3, 0.0, 0.99, committed="false"), 3)


def test_the_floor_is_read_from_the_control_panel(tmp_path, monkeypatch):
    p = tmp_path / "panel.csv"
    p.write_text("setting,value\nfloor.default,0.5\nfloor.cra,0.7\nfloor.ews,\n")
    monkeypatch.setenv("WBG_PANEL", str(p))
    assert labels.commit_floor("cra") == 0.7
    assert labels.commit_floor("ews") == 0.5                   # blank = the default
    p.write_text("setting,value\nfloor.default,\n")
    os.utime(p, (1, 1))
    assert labels.commit_floor("ews") is None


def test_panel_values_are_typed():
    assert panel._parse("0.25") == 0.25 and panel._parse("6") == 6
    assert panel._parse("true") is True and panel._parse("") is None
    assert panel._parse("unit|paragraph") == "unit|paragraph"
