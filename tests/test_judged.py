"""The full loop for the decisions after a measure (src/judged.py), on
invented vectors and answers."""
import json
import os
import sys

import numpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import judged  # noqa: E402

S = {"sharpness": 20.0, "centroid": True, "ridge": 1.0, "min_graded": 4, "min_each": 2,
     "near_copy": 2.0, "folds": 2, "pool": True}


def _block(seed, n=30):
    rng = numpy.random.default_rng(seed)
    rows, score, vecs = [], {}, {}
    for i in range(n):
        y = i % 2
        item = f"x{seed}-{i}"
        vecs[item] = (numpy.array([1.0, 0, 0]) if y else numpy.array([0, 1.0, 0])) + \
            0.1 * rng.normal(size=3)
        score[item] = 0.5
        rows.append((item, y, 1.0, frozenset({f"P{i % 5}"})))
    return rows, score, vecs


def test_a_kind_is_fitted_pooled_and_read_back_by_its_question(tmp_path, monkeypatch):
    monkeypatch.setattr(judged.posterior, "settings", lambda: dict(S))
    data = str(tmp_path)
    blocks = {"a": _block(1) + ("qa",), "b": _block(2) + ("qb",)}
    lines, oof = judged.fit_kind(data, "types", blocks)
    assert "pooled layer over 2 labels" in lines[0] and set(oof) == {"a", "b"}
    vec = {**blocks["a"][2], **blocks["b"][2], "new": numpy.array([0.95, 0.05, 0])}
    lays = judged.layers(data, "types", {"a": "qa", "b": "another wording"}, vec.get)
    assert set(lays) == {"a"}                     # b's question changed: no layer
    assert judged.calibrated(lays, "a", "new", 0.5) > 0.7
    assert judged.calibrated(lays, "b", "new", 0.5) == 0.5
    assert judged.digest(data, "types", {"a": "qa"}) != judged.digest(data, "types", {"a": "x"})


def test_too_few_grades_leave_the_models_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(judged.posterior, "settings", lambda: dict(S))
    rows, score, vecs = _block(1, n=2)
    lines, oof = judged.fit_kind(str(tmp_path), "pairs", {"drop:x": (rows, score, vecs, "q")})
    assert "too few" in lines[0] and oof == {}
    assert not os.path.exists(judged.layer_path(str(tmp_path), "pairs"))


def test_a_pair_vector_averages_the_sentence_and_the_package():
    v = judged.pair_vector([1.0, 0, 0], [0, 2.0, 0])
    assert numpy.allclose(v, [2 ** -0.5, 2 ** -0.5, 0])
    assert judged.pair_vector(None, [1, 0]) is None


def test_grades_are_merged_the_latest_winning(tmp_path):
    data = str(tmp_path)
    cols = judged.ASSET_LABEL_COLS
    judged.store(data, "package_assets", cols, [{"package_sha256": "s", "asset_id": "fiber",
                                                 "question_sha256": "q", "answer": "no"}],
                 ("package_sha256", "asset_id", "question_sha256"))
    judged.store(data, "package_assets", cols, [{"package_sha256": "s", "asset_id": "fiber",
                                                 "question_sha256": "q", "answer": "yes"}],
                 ("package_sha256", "asset_id", "question_sha256"))
    assert [r["answer"] for r in judged.read_labels(data, "package_assets")] == ["yes"]
