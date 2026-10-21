"""A measure's commit floor (analysis/labels/<set>.csv, commit_floor): a unit
counts as committed at P(top grade) >= the floor, else at the top grade."""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import labels  # noqa: E402
import measure  # noqa: E402


def _u(grade, p3, sent="true", **kw):
    return {"sent": sent, "grade": str(grade), "p3": f"{p3:.3f}", **kw}


def test_without_a_floor_the_most_likely_grade_decides():
    assert measure.is_committed(_u(3, 0.41), 3)
    assert not measure.is_committed(_u(2, 0.45), 3)
    assert not measure.is_committed(_u(3, 0.9, sent="false"), 3)


def test_a_floor_counts_by_probability_either_way():
    assert measure.is_committed(_u(2, 0.35), 3, 0.3)       # below the top grade, above the floor
    assert not measure.is_committed(_u(3, 0.55), 3, 0.6)   # top grade, under the floor


def test_a_row_that_says_so_is_believed():
    assert measure.is_committed(_u(0, 0.0, committed="true"), 3, 0.9)
    assert not measure.is_committed(_u(3, 0.99, committed="false"), 3)


def test_the_floor_is_read_from_the_measure_file(tmp_path, monkeypatch):
    d = tmp_path / "labels"
    d.mkdir()
    for name, floor in (("cra", "0.4"), ("ews", "")):
        with open(d / f"{name}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["measure_id", "measure_name", "definition",
                                               "active", "commit_floor"])
            w.writeheader()
            w.writerow({"measure_id": name, "measure_name": name, "definition": "d",
                        "active": "true", "commit_floor": floor})
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(d),))
    assert labels.commit_floor("cra") == 0.4
    assert labels.commit_floor("ews") is None
