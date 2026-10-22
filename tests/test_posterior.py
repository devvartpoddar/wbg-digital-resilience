"""The probability layer (src/posterior.py): the model's score combined with
how similar a sentence is to the sentences a person graded for the label.
Invented vectors and grades; no document text."""
import json
import os
import sys

import numpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import posterior  # noqa: E402

S = {"sharpness": 20.0, "centroid": True, "ridge": 1.0, "min_graded": 20}


def _graded(n=40, seed=0):
    """Graded yes sentences near (1, 0, 0), no sentences near (0, 1, 0); the
    model's score is uninformative (0.5 for all)."""
    rng = numpy.random.default_rng(seed)
    vecs, graded = {}, []
    for i in range(n):
        y = i % 2
        base = numpy.array([1.0, 0.0, 0.0]) if y else numpy.array([0.0, 1.0, 0.0])
        vecs[f"u{i}"] = base + 0.1 * rng.normal(size=3)
        graded.append((f"u{i}", y))
    return graded, {u: 0.5 for u, _ in graded}, vecs


def test_similar_to_graded_yes_scores_higher_at_the_same_model_score():
    graded, scores, vecs = _graded()
    spec = posterior.fit(graded, scores, vecs, "q1", "cra", S)
    vecs.update(new_yes=numpy.array([0.95, 0.05, 0.0]), new_no=numpy.array([0.05, 0.95, 0.0]))
    layer = posterior.Layer(spec, vecs.get)
    assert layer.probability("new_yes", 0.5) > 0.8 > 0.2 > layer.probability("new_no", 0.5)


def test_a_graded_sentence_is_scored_without_its_own_grade():
    graded, scores, vecs = _graded()
    spec = posterior.fit(graded, scores, vecs, "q1", "cra", S)
    layer = posterior.Layer(spec, vecs.get)
    # u0 is a graded "no" among nos: left out, its neighbours still say no
    assert layer.probability("u0", 0.5) < 0.5
    assert layer.ids["u0"] == 0


def test_too_few_graded_sentences_give_no_layer():
    graded, scores, vecs = _graded(n=10)
    assert posterior.fit(graded, scores, vecs, "q1", "cra", S) is None


def test_the_layer_applies_only_to_the_question_it_was_fitted_on(tmp_path, monkeypatch):
    graded, scores, vecs = _graded()
    spec = posterior.fit(graded, scores, vecs, "q1", "cra", S)
    path = str(tmp_path / "cra_posterior.json")
    posterior.write(path, spec)
    assert posterior.load(path, "q1", vecs.get) is not None
    assert posterior.load(path, "q2", vecs.get) is None          # a revised definition
    assert posterior.digest(posterior.read_spec(path, "q1")) == posterior.digest(spec)
    assert posterior.read_spec(path, "q2") is None
    p = tmp_path / "panel.csv"
    p.write_text("setting,value\nposterior.on,false\n")
    monkeypatch.setenv("WBG_PANEL", str(p))
    assert posterior.load(path, "q1", vecs.get) is None          # switched off on the panel
    assert json.load(open(path))["n_yes"] == 20


def test_a_sentence_with_no_vector_keeps_the_models_score():
    graded, scores, vecs = _graded()
    layer = posterior.Layer(posterior.fit(graded, scores, vecs, "q1", "cra", S), vecs.get)
    assert layer.probability("not_embedded", 0.37) == 0.37


def test_the_suggested_floor_keeps_the_most_recall_at_the_target():
    probs = [0.95, 0.9, 0.8, 0.7, 0.6, 0.4, 0.3]
    ys = [1, 1, 1, 0, 1, 0, 0]
    t, prec, rec = posterior.suggest_floor(probs, ys, 0.8)
    assert t == 0.6 and prec == 0.8 and rec == 1.0
    assert posterior.suggest_floor([0.9, 0.8], [0, 0], 0.8)[1] == 0.0   # none reaches: the most precise
