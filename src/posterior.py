"""The probability layer after every graded model question.

The decision model gives each sentence a score for a label: its probability
of the grades the control panel counts (read.grades, 2 and 3). This layer
turns that score into the probability that a person would grade the sentence
so: a logistic regression stacked on the score and on how similar the
sentence is, by its embedding, to the sentences a person graded for the same
label. Stacking, not strict Bayes: the score and the similarities read the
same sentence, so they are not independent, and a fitted weight on each
absorbs what they share (docs/method/decisions.md).

Two similarity numbers, both from the graded sentences of the label:

  vote      a weighted vote of the graded sentences, each weighted by its
            design weight times exp(sharpness x (similarity - 1)): the
            closest count most.
  centroid  similarity to the (weighted) average graded yes minus
            similarity to the average graded no.

A graded sentence never informs a sentence it nearly copies (similarity at
posterior.near_copy or more): appraisal documents reuse wording across
projects and between a project and its additional financing, and a
near-copy's grade would otherwise decide the vote, so the fit would learn to
trust the vote more than it deserves. The same rule holds when fitting, when
checking and when applying, so the weights learned are the weights used.
Sentences of the same project do vote: they are different sentences, and
leaving them out starves the vote of a measure whose graded yes sit in a few
projects. Leakage between projects is the out-of-fold check's job (folds by
whole project), not the vote's.

The grades come from sheets drawn by band, not at random, so each graded
sentence carries its design weight (the stratum's size over the rows drawn
from it) into the fit, the vote and every precision and recall reported.
The fit is checked out of sample: folds by project (no project in two
folds), each fold scored by a layer fitted on the others; the floor the
evaluation suggests is read off those out-of-fold probabilities.

The fit is made by the evaluation (src/check_units.py --evaluate, the only
reader of grades, AGENTS.md rule 9), on the grades given under the exact
question now asked, and written to a file of identifiers, numbers and
weights keyed on that question; a run reads the file. With no file, another
question, or too few graded sentences, the probability is the model's score.
"""
import hashlib, json, math, os, zlib

import numpy

import panel


def _logit(p):
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _unit(v):
    v = numpy.asarray(v, dtype="float64")
    n = numpy.linalg.norm(v)
    return v / n if n else v


def _projects(p):
    """A set of project ids from a set, a list or a 'P1|P2' string."""
    if not p:
        return frozenset()
    if isinstance(p, str):
        return frozenset(x for x in p.split("|") if x)
    return frozenset(p)


def settings():
    return {"sharpness": float(panel.get("posterior.sharpness", 20)),
            "centroid": bool(panel.get("posterior.centroid", True)),
            "ridge": float(panel.get("posterior.ridge", 1.0)),
            "min_graded": int(panel.get("posterior.min_graded", 20)),
            "min_each": int(panel.get("posterior.min_each", 5)),
            "near_copy": float(panel.get("posterior.near_copy", 0.97)),
            "folds": int(panel.get("posterior.folds", 5))}


def features(vec, G, y, w, sharpness, centroid, keep):
    """[vote, centroid difference] for one vector against the graded vectors
    G (rows, unit length) with grades y (0/1) and design weights w; only the
    rows where `keep` is true count."""
    s, yy, ww = (G @ vec)[keep], y[keep], w[keep]
    k = ww * numpy.exp(sharpness * (s - 1.0))
    vote = float((k * yy).sum() / k.sum()) if k.sum() else 0.5
    out = [vote]
    if centroid:
        Gk = G[keep]
        pos, neg = yy > 0, yy == 0
        cp = float(_unit((Gk[pos] * ww[pos, None]).sum(axis=0)) @ vec) if pos.any() else 0.0
        cn = float(_unit((Gk[neg] * ww[neg, None]).sum(axis=0)) @ vec) if neg.any() else 0.0
        out.append(cp - cn)
    return out


def _keep(vec, G, projects, P, near_copy, skip=None):
    """Which graded rows may inform a sentence: not itself, not a near-copy
    of it. (projects and P are kept for the out-of-fold split.)"""
    keep = (G @ vec) < near_copy
    if skip is not None:
        keep[skip] = False
    return keep


def _fit(X, y, w, ridge, iters=3000, step=0.5):
    """Weighted logistic regression on standardised inputs, ridge-pulled
    towards zero; w is normalised to a mean of 1."""
    w = w / w.mean()
    mu = (X * w[:, None]).sum(axis=0) / w.sum()
    sd = numpy.sqrt(((X - mu) ** 2 * w[:, None]).sum(axis=0) / w.sum()) + 1e-9
    Z = numpy.c_[numpy.ones(len(X)), (X - mu) / sd]
    b = numpy.zeros(Z.shape[1])
    for _ in range(iters):
        p = 1 / (1 + numpy.exp(-Z @ b))
        g = Z.T @ (w * (p - y)) / w.sum()
        g[1:] += ridge * b[1:] / w.sum()
        b -= step * g
    return mu.tolist(), sd.tolist(), b.tolist()


def _rows(graded, scores, vectors, groups):
    """[(unit_id, y, weight, projects)] of the graded sentences that have a
    score and a vector. graded holds (unit_id, y[, weight[, projects]])."""
    out = []
    for g in graded:
        u, y = g[0], int(g[1])
        if u not in scores or u not in vectors:
            continue
        w = float(g[2]) if len(g) > 2 and g[2] not in ("", None) else 1.0
        p = _projects(g[3]) if len(g) > 3 else _projects((groups or {}).get(u))
        out.append((u, y, w, p))
    return out


def _design(rows, scores, vectors, s):
    """The fit's inputs: X (logit score, vote, centroid), y, w, G, P."""
    y = numpy.array([r[1] for r in rows], dtype="float64")
    w = numpy.array([r[2] for r in rows], dtype="float64")
    P = [r[3] for r in rows]
    G = numpy.array([_unit(vectors[r[0]]) for r in rows])
    X = numpy.array([[_logit(scores[u])] + features(
        G[i], G, y, w, s["sharpness"], s["centroid"],
        _keep(G[i], G, p, P, s["near_copy"], skip=i))
        for i, (u, _, _, p) in enumerate(rows)])
    return X, y, w, G, P


def _enough(rows, s):
    n_yes = sum(r[1] for r in rows)
    return len(rows) >= s["min_graded"] and n_yes >= s["min_each"] and \
        len(rows) - n_yes >= s["min_each"]


def fit(graded, scores, vectors, question_sha256, label, s=None, groups=None):
    """The layer for one label, or None when there are too few graded
    sentences. graded = [(unit_id, 0/1[, design weight[, projects]])],
    scores = {unit_id: model score}, vectors = {unit_id: embedding},
    groups = {unit_id: projects} when graded does not carry them. Units with
    no score or vector are left out."""
    s = dict(settings(), **(s or {}))
    rows = _rows(graded, scores, vectors, groups)
    if not _enough(rows, s):
        return None
    X, y, w, _, _ = _design(rows, scores, vectors, s)
    mu, sd, b = _fit(X, y, w, s["ridge"])
    return {"label": label, "question_sha256": question_sha256, "settings": s,
            "mu": mu, "sd": sd, "coef": b,
            "graded": [[u, v, round(wt, 6), "|".join(sorted(p))] for u, v, wt, p in rows],
            "n_yes": int(y.sum()), "n_no": int((1 - y).sum())}


def folds_of(rows, k):
    """{unit_id: fold}: whole projects to a fold (a sentence of several
    projects goes with the first), assigned by a hash of the project so the
    split is the same on every run."""
    out = {}
    for u, _, _, p in rows:
        key = min(p) if p else u
        out[u] = zlib.crc32(key.encode("utf-8")) % k
    return out


def out_of_fold(graded, scores, vectors, s=None, groups=None):
    """{unit_id: probability} for every graded sentence, each from a layer
    fitted on the other folds (folds by project). A fold whose training part
    is too small keeps the model's score. Empty when the label has too few
    graded sentences for a layer."""
    s = dict(settings(), **(s or {}))
    rows = _rows(graded, scores, vectors, groups)
    if not _enough(rows, s):
        return {}
    fold = folds_of(rows, max(2, s["folds"]))
    out = {}
    for f in sorted(set(fold.values())):
        train = [r for r in rows if fold[r[0]] != f]
        test = [r for r in rows if fold[r[0]] == f]
        if not test:
            continue
        spec = fit(train, scores, vectors, "", "", s) if _enough(train, s) else None
        if spec is None:
            out.update({r[0]: float(scores[r[0]]) for r in test})
            continue
        layer = Layer(spec, vectors.get)
        out.update({r[0]: layer.probability(r[0], scores[r[0]], r[3]) for r in test})
    return out


def write(path, layer):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump(layer, fh, indent=1)
    os.replace(path + ".part", path)


def digest(layer):
    """A short hash of a layer (part of a run's key), '' for none."""
    if not layer:
        return ""
    return hashlib.sha256(json.dumps(layer, sort_keys=True).encode()).hexdigest()[:12]


class Layer:
    """A fitted layer ready to apply: probability(unit_id, score)."""

    def __init__(self, spec, vector_of, projects_of=None):
        self.spec = spec
        rows = [g for g in spec["graded"] if vector_of(g[0]) is not None]
        self.ids = {g[0]: i for i, g in enumerate(rows)}
        self.y = numpy.array([g[1] for g in rows], dtype="float64")
        self.w = numpy.array([float(g[2]) if len(g) > 2 else 1.0 for g in rows], dtype="float64")
        self.P = [_projects(g[3]) if len(g) > 3 else frozenset() for g in rows]
        self.G = numpy.array([_unit(vector_of(g[0])) for g in rows]) if rows else numpy.zeros((0, 1))
        self.vector_of, self.projects_of = vector_of, projects_of
        st = spec["settings"]
        self.sharp, self.cent = st["sharpness"], st["centroid"]
        self.near = float(st.get("near_copy", 1.01))
        self.mu, self.sd, self.b = (numpy.array(spec[k]) for k in ("mu", "sd", "coef"))

    def probability(self, unit_id, score, projects=None):
        v = self.vector_of(unit_id)
        if v is None or not len(self.y):
            return float(score)
        v = _unit(v)
        if projects is None and self.projects_of is not None:
            projects = self.projects_of(unit_id)
        keep = _keep(v, self.G, _projects(projects), self.P, self.near, skip=self.ids.get(unit_id))
        x = numpy.array([_logit(score)] + features(v, self.G, self.y, self.w, self.sharp,
                                                   self.cent, keep))
        z = self.b[0] + float(((x - self.mu) / self.sd) @ self.b[1:])
        z = max(-30.0, min(30.0, z))
        return 1 / (1 + math.exp(-z))


def vector_lookup(data):
    """vector_of(unit_id) over the sentence embeddings (None for a unit
    not embedded)."""
    return lookups(data)[0]


def lookups(data):
    """(vector_of, projects_of) over the sentence index: a unit's embedding
    (None when not embedded) and its projects ('P1|P2')."""
    import csv
    from paths import where
    row_of, proj = {}, {}
    with open(where(data, "embeddings", "sentence_index.csv"), newline="", encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            row_of[r["unit_id"]] = i
            proj[r["unit_id"]] = r.get("project_ids", "")
    npy = where(data, "embeddings", "sentence_emb.npy")
    if not os.path.exists(npy):                     # nothing embedded yet: no vectors
        return (lambda u: None), (lambda u: proj.get(u, ""))
    matrix = numpy.load(npy, mmap_mode="r")
    vector_of = lambda u: numpy.asarray(matrix[row_of[u]], dtype="float64") if u in row_of else None
    return vector_of, lambda u: proj.get(u, "")


def weighted_pr(probs, ys, ws, t):
    """(precision, recall, kept) at floor t, every row weighted by its
    design weight; None where undefined."""
    kept = sum(w for p, y, w in zip(probs, ys, ws) if p >= t)
    tp = sum(w for p, y, w in zip(probs, ys, ws) if p >= t and y)
    pos = sum(w for y, w in zip(ys, ws) if y)
    return (tp / kept if kept else None), (tp / pos if pos else None), kept


def suggest_floor(probs, ys, target, ws=None):
    """(floor, precision, recall) on a 0.05 grid with the most recall whose
    weighted precision reaches target (the highest such floor on ties, for
    margin), or the most precise one when none does. Read it off
    out-of-fold probabilities: a floor chosen on the rows a layer was fitted
    on looks better than it is."""
    ws = ws or [1.0] * len(probs)
    best, top = None, None
    for t in [x / 20 for x in range(1, 20)]:
        prec, rec, kept = weighted_pr(probs, ys, ws, t)
        if not kept:
            continue
        if prec >= target and (best is None or rec >= best[2]):
            best = (t, prec, rec)
        if top is None or prec > top[1]:
            top = (t, prec, rec)
    return best or top


def effective_n(ws):
    """Kish's effective sample size of weighted rows."""
    s, s2 = sum(ws), sum(w * w for w in ws)
    return (s * s / s2) if s2 else 0.0


def load(path, question_sha256, vector_of, projects_of=None):
    """The layer at path when the panel has it on and it was fitted on the
    same question, else None (the model's score stands)."""
    spec = read_spec(path, question_sha256)
    return Layer(spec, vector_of, projects_of) if spec else None


def read_spec(path, question_sha256):
    """The layer's file contents when it applies (for a run key), else None."""
    if not panel.get("posterior.on", True) or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    return spec if spec.get("question_sha256") == question_sha256 else None
