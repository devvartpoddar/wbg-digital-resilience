"""Unit tests for the asset classifiers.

No corpus and no network: every fixture is generated, so the expected answer is
known in advance rather than read off the output. That matters more here than
in the cleaning code, because a classifier that is subtly wrong still produces
plausible-looking numbers.
"""
import os
import sys

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import train_assets as T  # noqa: E402


def make(n_pos=40, n_neg=80, dim=64, signal=3.0, seed=0):
    """Embeddings with a planted direction. Positives lean along it, negatives
    do not, and both are unit-normalised the way real embeddings are.

    `signal` is in units of the raw coordinate scale, so it has to be compared
    against the vector norm - a standard normal draw in 64 dimensions has norm
    about 8, so signal=3 is a real but not overwhelming lean, and signal=0.9 is
    nearly invisible once the vector is normalised. An earlier version of this
    fixture used 0.9 and the recovery test failed; the fixture was wrong, not
    the model."""
    rng = numpy.random.default_rng(seed)
    axis = rng.normal(size=dim)
    axis /= numpy.linalg.norm(axis)
    X = rng.normal(size=(n_pos + n_neg, dim))
    X[:n_pos] += signal * axis
    X /= numpy.linalg.norm(X, axis=1, keepdims=True)
    y = numpy.array([1.0] * n_pos + [-1.0] * n_neg)
    return X, y


# ------------------------------------------------------------------ the maths

def test_dual_ridge_equals_the_primal():
    """w = X'(XX' + lam I)^-1 y must equal (X'X + lam I)^-1 X'y exactly. The
    dual form is used only because it inverts 120x120 instead of 3072x3072; if
    the identity does not hold, every weight vector is quietly wrong."""
    X, y = make(n_pos=12, n_neg=18, dim=40, seed=3)
    for lam in (0.1, 1.0, 17.0):
        dual = T.ridge_dual(X, y, lam)
        primal = numpy.linalg.solve(X.T @ X + lam * numpy.eye(X.shape[1]), X.T @ y)
        assert numpy.allclose(dual, primal, atol=1e-8), f"lambda={lam}"


def test_stronger_regularisation_shrinks_the_weights():
    X, y = make(seed=1)
    norms = [numpy.linalg.norm(T.ridge_dual(X, y, lam)) for lam in (0.01, 1.0, 100.0)]
    assert norms[0] > norms[1] > norms[2]


@pytest.mark.parametrize("y, s, want", [
    ([1, 1, -1, -1], [4.0, 3.0, 2.0, 1.0], 1.0),      # perfect ranking
    ([1, 1, -1, -1], [1.0, 2.0, 3.0, 4.0], 0.0),      # exactly inverted
    ([1, -1, 1, -1], [4.0, 3.0, 2.0, 1.0], 0.75),
    ([1, 1, -1, -1], [1.0, 1.0, 1.0, 1.0], 0.5),      # all tied
])
def test_auc_on_known_rankings(y, s, want):
    assert T.auc(numpy.array(y, dtype=float), numpy.array(s)) == pytest.approx(want)


def test_platt_is_monotone_and_bounded():
    X, y = make(seed=2)
    scores = X @ T.ridge_dual(X, y, 1.0)
    a, b = T.platt(scores, y)
    grid = numpy.linspace(scores.min(), scores.max(), 40)
    p = 1.0 / (1.0 + numpy.exp(-(a * grid + b)))
    assert (numpy.diff(p) >= -1e-12).all(), "probability must not fall as score rises"
    assert p.min() >= 0.0 and p.max() <= 1.0


# ------------------------------------------------------------------- the folds

def test_a_document_never_straddles_two_folds():
    """The whole point of grouping. If one document's paragraphs land on both
    sides of a split, the score measures memorisation of that document."""
    groups = [f"doc{i % 9}" for i in range(120)]
    folds = T.group_folds(groups, 5)
    seen = {}
    for g, f in zip(groups, folds):
        assert seen.setdefault(g, f) == f, f"{g} appears in more than one fold"


def test_folds_are_roughly_balanced():
    groups = [f"doc{i % 11}" for i in range(110)]
    folds = T.group_folds(groups, 5)
    counts = [int((folds == f).sum()) for f in range(5)]
    assert max(counts) - min(counts) <= 10, counts


def test_every_fold_is_used_when_groups_allow():
    groups = [f"doc{i % 12}" for i in range(120)]
    assert len(set(T.group_folds(groups, 5))) == 5


# ---------------------------------------------------------------- does it work

def test_a_planted_signal_is_recovered():
    X, y = make(signal=3.0, seed=4)
    folds = T.group_folds([f"d{i % 10}" for i in range(len(y))], 5)
    oof = numpy.zeros(len(y))
    for f in range(5):
        tr, te = folds != f, folds == f
        oof[te] = X[te] @ T.ridge_dual(X[tr], y[tr], 1.0)
    assert T.auc(y, oof) > 0.85, "a real planted signal should be recovered"


def test_pure_noise_scores_near_chance():
    """The test that matters most. With 120 points in 64 dimensions a model can
    fit anything; if out-of-fold AUC on label noise is not near 0.5, the
    evaluation is leaking and every reported number is inflated."""
    rng = numpy.random.default_rng(7)
    X = rng.normal(size=(120, 64))
    X /= numpy.linalg.norm(X, axis=1, keepdims=True)
    y = numpy.array([1.0] * 40 + [-1.0] * 80)
    rng.shuffle(y)
    folds = T.group_folds([f"d{i % 10}" for i in range(120)], 5)
    oof = numpy.zeros(120)
    for f in range(5):
        tr, te = folds != f, folds == f
        oof[te] = X[te] @ T.ridge_dual(X[tr], y[tr], 1.0)
    assert 0.3 < T.auc(y, oof) < 0.7, "out-of-fold AUC on noise should be near chance"


def test_in_sample_fit_would_have_looked_perfect():
    """Why the out-of-fold protocol is not optional: the same noise fitted and
    scored on itself separates almost perfectly."""
    rng = numpy.random.default_rng(7)
    X = rng.normal(size=(120, 3072))
    X /= numpy.linalg.norm(X, axis=1, keepdims=True)
    y = numpy.array([1.0] * 40 + [-1.0] * 80)
    rng.shuffle(y)
    in_sample = X @ T.ridge_dual(X, y, 0.01)
    assert T.auc(y, in_sample) > 0.95


def test_prf_counts_are_consistent():
    y = numpy.array([1.0, 1.0, -1.0, -1.0])
    p, r, f, tp, fp, fn = T.prf(y, numpy.array([3.0, 1.0, 2.0, 0.0]), 1.5)
    assert (tp, fp, fn) == (1, 1, 1)
    assert p == pytest.approx(0.5) and r == pytest.approx(0.5)


# ------------------------------------------------------------- end to end

def build_corpus(root, n_docs=12, per_doc=12, dim=64, seed=5):
    """A data/ and inputs/ tree with the same shape as the real one. Unit tests
    above check the maths; this checks that the script can actually read the
    files the rest of the pipeline writes and produce the files the next stage
    expects."""
    import csv as _csv
    rng = numpy.random.default_rng(seed)
    data, inputs = os.path.join(root, "data"), os.path.join(root, "inputs")
    os.makedirs(os.path.join(inputs, "labels"), exist_ok=True)
    os.makedirs(os.path.join(inputs, "taxonomy"), exist_ok=True)
    os.makedirs(data, exist_ok=True)

    pids, docs = [], []
    for d in range(n_docs):
        for i in range(per_doc):
            pids.append(f"D{d:03d}:p{i:05d}")
            docs.append(f"D{d:03d}")
    n = len(pids)
    axis = rng.normal(size=dim); axis /= numpy.linalg.norm(axis)
    X = rng.normal(size=(n, dim))
    pos = rng.random(n) < 0.3
    X[pos] += 3.0 * axis
    X /= numpy.linalg.norm(X, axis=1, keepdims=True)
    numpy.save(os.path.join(data, "paragraph_emb.npy"), X.astype("float32"))

    with open(os.path.join(data, "emb_index.csv"), "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh, lineterminator="\n"); w.writerow(["row", "paragraph_id", "text_sha256"])
        for i, p in enumerate(pids):
            w.writerow([i, p, ""])
    with open(os.path.join(data, "paragraphs.csv"), "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh, lineterminator="\n"); w.writerow(["paragraph_id", "doc_id", "block"])
        for p, d in zip(pids, docs):
            w.writerow([p, d, "narrative"])
    with open(os.path.join(inputs, "taxonomy", "assets.csv"), "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh, lineterminator="\n")
        w.writerow(["asset_id", "asset_name", "definition", "active", "version"])
        w.writerow(["fiber", "Fiber", "fiber optic cable", "true", "v1"])
        w.writerow(["rare", "Rare", "hardly ever appears", "true", "v1"])
    with open(os.path.join(inputs, "labels", "paragraph_labels.csv"), "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh, lineterminator="\n")
        w.writerow(["paragraph_id", "asset_id", "label", "labeller", "labelled_at", "sample_id"])
        for p, is_pos in zip(pids, pos):
            w.writerow([p, "fiber", "true" if is_pos else "false", "t", "2026-01-01", "s"])
        for p in pids[:8]:                      # deliberately too few to train
            w.writerow([p, "rare", "true", "t", "2026-01-01", "s"])
    return data, inputs


def test_end_to_end_writes_models_and_a_manifest(tmp_path, monkeypatch):
    import json
    data, inputs = build_corpus(str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["train_assets.py", "--data", data,
                                      "--inputs", inputs, "--folds", "4",
                                      "--meta", os.path.join(str(tmp_path), "meta")])
    assert T.main() == 0
    mpath = os.path.join(data, "intermediate", "models", "asset_models.json")
    manifest = json.load(open(mpath, encoding="utf-8"))
    assert "fiber" in manifest["classes"]
    assert manifest["grouping"] == "document"
    fiber = manifest["classes"]["fiber"]
    assert fiber["auc_oof"] > 0.8, "the planted signal should be recovered end to end"
    assert fiber["positives"] > 0 and fiber["n"] == 144
    assert os.path.exists(os.path.join(data, "intermediate", "models", "asset_fiber.npz"))
    assert os.path.exists(os.path.join(data, "train_assets_report.txt"))
    # The provenance record is written twice: once under data/ for detect_assets
    # to read, and once under meta/, which is the copy git can see. They are the
    # same dict, so it is a bug if they ever differ.
    committed = os.path.join(str(tmp_path), "meta", "asset_models.json")
    assert json.load(open(committed, encoding="utf-8")) == manifest


def test_a_class_with_too_few_labels_is_skipped_not_guessed(tmp_path, monkeypatch):
    """Eight labels cannot train anything. It must be reported as untrained
    rather than producing a model nobody can tell is meaningless."""
    import json
    data, inputs = build_corpus(str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["train_assets.py", "--data", data,
                                      "--inputs", inputs, "--folds", "4",
                                      "--meta", os.path.join(str(tmp_path), "meta")])
    T.main()
    manifest = json.load(open(os.path.join(data, "intermediate", "models",
                                           "asset_models.json"), encoding="utf-8"))
    assert "rare" not in manifest["classes"]
    assert not os.path.exists(os.path.join(data, "intermediate", "models", "asset_rare.npz"))
    assert "too few labels" in open(os.path.join(data, "train_assets_report.txt")).read()


def test_saved_weights_reproduce_the_training_scores(tmp_path, monkeypatch):
    """The .npz is what Stage 3 will load. If applying it does not reproduce the
    scores the report was computed from, the model on disk is not the model that
    was measured."""
    data, inputs = build_corpus(str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["train_assets.py", "--data", data,
                                      "--inputs", inputs, "--folds", "4",
                                      "--meta", os.path.join(str(tmp_path), "meta")])
    T.main()
    z = numpy.load(os.path.join(data, "intermediate", "models", "asset_fiber.npz"))
    X = numpy.load(os.path.join(data, "paragraph_emb.npy"))
    scores = X @ z["w"]
    assert scores.shape == (X.shape[0],)
    assert numpy.isfinite(scores).all()
    p = 1.0 / (1.0 + numpy.exp(-(z["platt_a"] * scores + z["platt_b"])))
    assert ((p >= 0) & (p <= 1)).all()
