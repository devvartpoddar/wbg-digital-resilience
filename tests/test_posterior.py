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

S = {"sharpness": 20.0, "centroid": True, "ridge": 1.0, "min_graded": 20, "min_each": 5,
     "near_copy": 1.01, "folds": 5}


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



def test_a_sentence_with_no_vector_keeps_the_models_score():
    graded, scores, vecs = _graded()
    layer = posterior.Layer(posterior.fit(graded, scores, vecs, "q1", "cra", S), vecs.get)
    assert layer.probability("not_embedded", 0.37) == 0.37


def test_the_suggested_floor_keeps_the_most_recall_at_the_target():
    probs = [0.95, 0.9, 0.8, 0.7, 0.6, 0.4, 0.3]
    ys = [1, 1, 1, 0, 1, 0, 0]
    t, prec, rec = posterior.suggest_floor(probs, ys, 0.8)
    assert t == 0.6 and prec == 0.8 and rec == 1.0
    # Weighted: the false positive at 0.7 stands for four sentences, so 0.6
    # no longer reaches 0.8; 0.75 and 0.8 keep the same three, and the higher wins.
    t, prec, rec = posterior.suggest_floor(probs, ys, 0.8, ws=[1, 1, 1, 4, 1, 1, 1])
    assert t == 0.8 and prec == 1.0 and rec == 0.75
    assert posterior.suggest_floor([0.9, 0.8], [0, 0], 0.8)[1] == 0.0   # none reaches: the most precise


def test_a_near_copy_does_not_vote_but_a_sentence_of_the_same_project_does():
    graded, scores, vecs = _graded()
    graded = [(u, y, 1.0, "A" if y else "B") for u, y in graded]   # every yes in project A
    s = dict(S, near_copy=0.999)
    spec = posterior.fit(graded, scores, vecs, "q1", "cra", s)
    layer = posterior.Layer(spec, vecs.get)
    vecs["near_yes"] = numpy.array([0.95, 0.05, 0.0])
    # The yes of project A still vote for a new sentence of project A.
    assert layer.probability("near_yes", 0.5, "A") == layer.probability("near_yes", 0.5, "C") > 0.8
    # A near-copy of a graded yes is not informed by it.
    vecs["copy"] = vecs["u1"].copy()
    sims = layer.G @ posterior._unit(vecs["copy"])
    assert layer.ids["u1"] in layer.graded.drop(sims, 0.999)
    # Off (the default), only the sentence itself is left out.
    assert list(layer.graded.drop(sims, posterior._near("off"))) == []


def test_design_weights_move_the_fit_towards_the_heavier_rows():
    graded, scores, vecs = _graded()
    s1 = dict(S, fit_weight_power=1.0)       # fit weights follow the design weights
    light = posterior.fit([(u, y, 1.0) for u, y in graded], scores, vecs, "q", "cra", s1)
    heavy = posterior.fit([(u, y, 5.0 if y == 0 else 1.0) for u, y in graded], scores, vecs,
                          "q", "cra", s1)
    # More weight on the noes lowers the intercept: a lower base rate.
    assert heavy["coef"][0] < light["coef"][0]


def test_out_of_fold_scores_each_project_with_a_layer_fitted_without_it():
    graded, scores, vecs = _graded(n=60)
    graded = [(u, y, 1.0, f"P{i % 7}") for i, (u, y) in enumerate(graded)]
    oof = posterior.out_of_fold(graded, scores, vecs, S)
    assert set(oof) == {u for u, *_ in graded}
    yes = [oof[u] for u, y, *_ in graded if y]
    no = [oof[u] for u, y, *_ in graded if not y]
    assert min(yes) > max(no)
    folds = posterior.folds_of(posterior._rows(graded, scores, vecs, None), 5)
    by_project = {}
    for u, _, _, p in graded:
        by_project.setdefault(p, set()).add(folds[u])
    assert all(len(f) == 1 for f in by_project.values())   # a project sits in one fold


def test_the_gram_shortcut_gives_the_centroids_summed_directly():
    graded, scores, vecs = _graded()
    rows = posterior._rows(graded, scores, vecs, None)
    y = numpy.array([r[1] for r in rows], dtype="float64")
    w = numpy.linspace(0.5, 2.0, len(rows))
    G = numpy.array([posterior._unit(vecs[r[0]]) for r in rows])
    gr = posterior._Graded(G, y, w)
    v = posterior._unit(numpy.array([0.7, 0.3, 0.1]))
    s = G @ v
    drop = numpy.array([0, 3, 7])
    keep = numpy.ones(len(rows), dtype=bool)
    keep[drop] = False
    pos, neg = keep & (y > 0), keep & (y == 0)
    cp = posterior._unit((G[pos] * w[pos, None]).sum(axis=0)) @ v
    cn = posterior._unit((G[neg] * w[neg, None]).sum(axis=0)) @ v
    assert abs(gr.features(s, drop, 20.0, True)[1] - (cp - cn)) < 1e-9


def test_pooled_labels_share_slopes_and_keep_their_own_intercepts():
    a, sa, va = _graded(seed=1)
    b, sb, vb = _graded(seed=2)
    b = [(f"b{u}", y) for u, y in b] + [(f"bx{i}", 0) for i in range(20)]   # a lower base rate
    vb = {f"b{u}": v for u, v in vb.items()}
    rng = numpy.random.default_rng(3)
    vb.update({f"bx{i}": numpy.array([0.0, 1.0, 0.0]) + 0.1 * rng.normal(size=3) for i in range(20)})
    sb = {u: 0.5 for u, _ in b}
    specs = posterior.fit_pooled({"A": (a, sa, va, "qa"), "B": (b, sb, vb, "qb")}, S)
    assert set(specs) == {"A", "B"} and specs["A"]["coef"][1:] == specs["B"]["coef"][1:]
    assert specs["B"]["coef"][0] < specs["A"]["coef"][0]
    assert specs["A"]["question_sha256"] == "qa" and specs["A"]["pooled"] == ["A", "B"]
    oof = posterior.out_of_fold_all({"A": ([(u, y, 1.0, f"P{i % 7}") for i, (u, y) in enumerate(a)],
                                           sa, va, "qa")}, dict(S, pool=True))
    assert len(oof["A"]) == len(a)
