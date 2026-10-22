"""The probability layer after every graded model question.

The decision model gives each sentence a score for a label: its probability
of the grades the control panel counts (read.grades, 2 and 3). This layer
turns that score into the probability that a person would grade the sentence
so, using what is already known about the sentence: how similar it is, by
its embedding, to the sentences a person graded for the same label.

Two similarity numbers, both from the graded sentences of the label (never
the sentence's own grade):

  vote      a weighted vote of every graded sentence, each weighted by
            exp(sharpness x (similarity - 1)): the closest count most.
  centroid  similarity to the average graded yes minus similarity to the
            average graded no.

A logistic fit of the person's grade on log-odds(score), vote and centroid
(standardised, with a ridge pull towards zero) gives the probability. Tested
on 742 graded sentences on 2026-10-22: recall at 0.85 precision 0.78 -> 0.82,
at 0.90 0.60 -> 0.67 (held-out halves); sharpness 10 to 20 best.

The fit is made by the evaluation (src/check_units.py --evaluate, the only
reader of grades, AGENTS.md rule 9) and written to a file of identifiers,
numbers and weights, keyed on the exact question; a run reads the file. With
no file, or a different question, or too few graded sentences, the
probability is the model's score.
"""
import hashlib, json, math, os

import numpy

import panel


def _logit(p):
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _unit(v):
    v = numpy.asarray(v, dtype="float64")
    n = numpy.linalg.norm(v)
    return v / n if n else v


def features(vec, G, y, sharpness, centroid, skip=None):
    """[vote, centroid difference] for one vector against the graded vectors
    G (rows, unit length) with grades y (0/1); row `skip` left out."""
    sims = G @ vec
    keep = numpy.ones(len(y), dtype=bool)
    if skip is not None:
        keep[skip] = False
    s, yy = sims[keep], y[keep]
    w = numpy.exp(sharpness * (s - 1.0))
    vote = float((w * yy).sum() / w.sum()) if w.sum() else 0.5
    out = [vote]
    if centroid:
        Gk = G[keep]
        pos, neg = Gk[yy > 0], Gk[yy == 0]
        cp = float(_unit(pos.mean(axis=0)) @ vec) if len(pos) else 0.0
        cn = float(_unit(neg.mean(axis=0)) @ vec) if len(neg) else 0.0
        out.append(cp - cn)
    return out


def _fit(X, y, ridge, iters=3000, step=0.5):
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = numpy.c_[numpy.ones(len(X)), (X - mu) / sd]
    b = numpy.zeros(Z.shape[1])
    for _ in range(iters):
        p = 1 / (1 + numpy.exp(-Z @ b))
        g = Z.T @ (p - y) / len(y)
        g[1:] += ridge * b[1:] / len(y)
        b -= step * g
    return mu.tolist(), sd.tolist(), b.tolist()


def settings():
    return {"sharpness": float(panel.get("posterior.sharpness", 20)),
            "centroid": bool(panel.get("posterior.centroid", True)),
            "ridge": float(panel.get("posterior.ridge", 1.0)),
            "min_graded": int(panel.get("posterior.min_graded", 20))}


def fit(graded, scores, vectors, question_sha256, label, s=None):
    """The layer for one label, or None when there are too few graded
    sentences. graded = [(unit_id, 0/1)], scores = {unit_id: model score},
    vectors = {unit_id: embedding}. Units with no score or vector are left
    out."""
    s = s or settings()
    rows = [(u, int(v)) for u, v in graded if u in scores and u in vectors]
    y = numpy.array([v for _, v in rows], dtype="float64")
    if len(rows) < s["min_graded"] or y.sum() < 5 or (1 - y).sum() < 5:
        return None
    G = numpy.array([_unit(vectors[u]) for u, _ in rows])
    X = numpy.array([[_logit(scores[u])] + features(G[i], G, y, s["sharpness"], s["centroid"], skip=i)
                     for i, (u, _) in enumerate(rows)])
    mu, sd, b = _fit(X, y, s["ridge"])
    return {"label": label, "question_sha256": question_sha256, "settings": s,
            "mu": mu, "sd": sd, "coef": b, "graded": [[u, v] for u, v in rows],
            "n_yes": int(y.sum()), "n_no": int((1 - y).sum())}


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

    def __init__(self, spec, vector_of):
        self.spec = spec
        rows = [(u, v) for u, v in spec["graded"] if vector_of(u) is not None]
        self.ids = {u: i for i, (u, _) in enumerate(rows)}
        self.y = numpy.array([v for _, v in rows], dtype="float64")
        self.G = numpy.array([_unit(vector_of(u)) for u, _ in rows]) if rows else numpy.zeros((0, 1))
        self.vector_of = vector_of
        st = spec["settings"]
        self.sharp, self.cent = st["sharpness"], st["centroid"]
        self.mu, self.sd, self.b = (numpy.array(spec[k]) for k in ("mu", "sd", "coef"))

    def probability(self, unit_id, score):
        v = self.vector_of(unit_id)
        if v is None or not len(self.y):
            return float(score)
        x = numpy.array([_logit(score)] + features(_unit(v), self.G, self.y, self.sharp, self.cent,
                                                   skip=self.ids.get(unit_id)))
        z = self.b[0] + float(((x - self.mu) / self.sd) @ self.b[1:])
        z = max(-30.0, min(30.0, z))
        return 1 / (1 + math.exp(-z))


def vector_lookup(data):
    """vector_of(unit_id) over the sentence embeddings (None for a unit
    not embedded)."""
    import csv
    from paths import where
    with open(where(data, "embeddings", "sentence_index.csv"), newline="", encoding="utf-8") as fh:
        row_of = {r["unit_id"]: i for i, r in enumerate(csv.DictReader(fh))}
    matrix = numpy.load(where(data, "embeddings", "sentence_emb.npy"), mmap_mode="r")
    return lambda u: numpy.asarray(matrix[row_of[u]], dtype="float64") if u in row_of else None


def suggest_floor(probs, ys, target):
    """(floor, precision, recall) of the floor on a 0.05 grid with the most
    recall whose precision reaches target (the highest such floor on ties,
    for margin), or the most precise one when none does."""
    best, top = None, None
    for t in [x / 20 for x in range(1, 20)]:
        kept = [y for p, y in zip(probs, ys) if p >= t]
        if not kept:
            continue
        prec, rec = sum(kept) / len(kept), sum(kept) / max(sum(ys), 1)
        if prec >= target and (best is None or rec >= best[2]):
            best = (t, prec, rec)
        if top is None or prec > top[1]:
            top = (t, prec, rec)
    return best or top


def load(path, question_sha256, vector_of):
    """The layer at path when the panel has it on and it was fitted on the
    same question, else None (the model's score stands)."""
    if not panel.get("posterior.on", True) or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    if spec.get("question_sha256") != question_sha256:
        return None
    return Layer(spec, vector_of)


def read_spec(path, question_sha256):
    """The layer's file contents when it applies (for a run key), else None."""
    if not panel.get("posterior.on", True) or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    return spec if spec.get("question_sha256") == question_sha256 else None
