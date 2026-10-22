"""The tuning report (src/tune.py) on invented vectors and grades."""
import os
import sys

import numpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import tune  # noqa: E402


def _block(seed, n=40):
    rng = numpy.random.default_rng(seed)
    rows, score, vecs, probs = [], {}, {}, {}
    for i in range(n):
        y = i % 2
        u = f"s{seed}u{i}"
        base = numpy.array([1.0, 0.0, 0.0]) if y else numpy.array([0.0, 1.0, 0.0])
        vecs[u] = base + 0.2 * rng.normal(size=3)
        score[u] = 0.5
        probs[u] = [0.25, 0.25, 0.25, 0.25]
        rows.append((u, y, 1.0 + (i % 3), frozenset({f"P{i % 6}"})))
    return rows, score, vecs, "q", probs


def test_weighted_auc():
    assert tune.wauc([0.9, 0.1], [1, 0], [1, 1]) == 1.0
    assert tune.wauc([0.5, 0.5], [1, 0], [1, 1]) == 0.5
    assert tune.wauc([0.9], [1], [1]) is None


def test_the_grid_ranks_every_setting_and_marks_none_it_did_not_run(monkeypatch):
    monkeypatch.setattr(tune, "GRID", {"sharpness": [5.0, 20.0], "near_copy": ["off"],
                                       "ridge": [1.0], "centroid": [True], "pool": [False, True]})
    lines = tune.layer_grid({"a": _block(1), "b": _block(2)}, log=lambda *_: None)
    table = [l for l in lines if l.startswith("| ") and l[2].isdigit()]
    assert len(table) == 4
    # the layer reads the vectors, so it beats the uninformative score (log 2 = 0.693)
    best = float(table[0].split("|")[7])
    assert best < 0.6
    assert any("score alone: log loss 0.693" in l for l in lines)


def test_norm_matches_panel_values():
    assert tune._norm(20) == tune._norm(20.0) and tune._norm("Off") == tune._norm("off")
    assert tune._norm(True) == "true"
