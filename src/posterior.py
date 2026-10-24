"""The probability layer: a logistic regression stacked on the model's score
and on the sentence's similarity to the sentences a person graded for the
same label (a sharpness-weighted vote, and the distance between the graded
yes and no centroids). A graded sentence never votes for itself. Labels of
one decision kind are pooled (posterior.pool): one intercept each, shared
slopes. Checked out of fold by project; fits weight each row by its design
weight to posterior.fit_weight_power, reports by the full weight.
Fitted by src/grading.py, read through src/fits.py (docs/method/decisions.md).
"""
import hashlib, json, math, zlib

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


def _near(v):
    """posterior.near_copy as a number: 'off' (or blank) lets every graded
    sentence vote but the sentence itself."""
    if isinstance(v, str) and v.strip().lower() in ("", "off", "none"):
        return 2.0
    return float(v)


def settings():
    return {"sharpness": float(panel.get("posterior.sharpness", 20)),
            "centroid": bool(panel.get("posterior.centroid", True)),
            "ridge": float(panel.get("posterior.ridge", 1.0)),
            "min_graded": int(panel.get("posterior.min_graded", 20)),
            "min_each": int(panel.get("posterior.min_each", 5)),
            "near_copy": _near(panel.get("posterior.near_copy", "off")),
            "pool": bool(panel.get("posterior.pool", True)),
            "folds": int(panel.get("posterior.folds", 5)),
            "fit_weight_power": float(panel.get("posterior.fit_weight_power", 0.0))}


class _Graded:
    """The graded sentences of one label (unit vectors G, grades y, design
    weights w) with what the two similarity features need precomputed, so a
    sentence's features cost one product with G."""

    def __init__(self, G, y, w):
        self.G, self.y, self.w = G, y, w
        self.S = G @ G.T if len(G) else numpy.zeros((0, 0))
        self.wc = {c: w * ((y > 0) if c else (y == 0)) for c in (1, 0)}
        self.Sw = {c: self.S @ self.wc[c] for c in self.wc}
        self.N = {c: float(self.wc[c] @ self.Sw[c]) for c in self.wc}

    def drop(self, s, near_copy, own=None):
        """The rows that may not inform a sentence with similarities s: its
        near-copies (similarity at near_copy or more) and itself."""
        d = numpy.flatnonzero(s >= near_copy)
        return numpy.union1d(d, [own]).astype(int) if own is not None else d

    def features(self, s, drop, sharpness, centroid):
        """[vote, centroid difference] for a sentence whose similarities to
        the graded rows are s (unit vectors, so s = G @ vec), the rows in
        `drop` left out. The centroid of a class is the unit vector of its
        weighted sum; its norm comes from the Gram matrix less the dropped
        rows, so nothing is summed again."""
        keep = numpy.ones(len(s), dtype=bool)
        keep[drop] = False
        k = self.w * numpy.exp(sharpness * (s - 1.0)) * keep
        vote = float((k * self.y).sum() / k.sum()) if k.sum() else 0.5
        out = [vote]
        if centroid:
            c = []
            for cls in (1, 0):
                wc = self.wc[cls]
                R = drop[wc[drop] > 0]
                n2 = self.N[cls] - 2 * float(wc[R] @ self.Sw[cls][R]) + \
                    float(wc[R] @ self.S[numpy.ix_(R, R)] @ wc[R])
                c.append(float((wc * keep) @ s) / math.sqrt(n2) if n2 > 1e-12 else 0.0)
            out.append(c[0] - c[1])
        return out


def _fit(X, y, w, ridge, group=None, n_groups=1):
    """Weighted logistic regression on standardised inputs, one intercept
    per group (a label, when labels are pooled), the slopes shared and
    pulled towards zero: ridge is the precision of a normal prior on each
    standardised slope (1 = a prior standard deviation of 1). w is
    normalised to a mean of 1. Newton steps to convergence. Returns (mu, sd,
    intercepts, slopes)."""
    w = w / w.mean()
    mu = (X * w[:, None]).sum(axis=0) / w.sum()
    sd = numpy.sqrt(((X - mu) ** 2 * w[:, None]).sum(axis=0) / w.sum()) + 1e-9
    group = numpy.zeros(len(X), dtype=int) if group is None else numpy.asarray(group)
    Z = numpy.c_[numpy.eye(n_groups)[group], (X - mu) / sd]
    pen = numpy.r_[numpy.full(n_groups, 1e-6), numpy.full(X.shape[1], ridge)]
    b = numpy.zeros(Z.shape[1])
    for _ in range(100):
        p = 1 / (1 + numpy.exp(-numpy.clip(Z @ b, -30, 30)))
        g = Z.T @ (w * (p - y)) + pen * b
        H = (Z * (w * p * (1 - p))[:, None]).T @ Z + numpy.diag(pen)
        step = numpy.linalg.solve(H, g)
        m = numpy.abs(step).max()
        b -= step * (min(1.0, 5.0 / m) if m else 1.0)
        if m < 1e-9:
            break
    return mu.tolist(), sd.tolist(), b[:n_groups].tolist(), b[n_groups:].tolist()


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
    """The fit's inputs: X (logit score, vote[, centroid]), y, w. w is the
    design weight raised to posterior.fit_weight_power (0 = every graded
    row counts once in the fit; the reports keep the full weights). Each
    graded sentence's similarities leave itself out."""
    y = numpy.array([r[1] for r in rows], dtype="float64")
    w = numpy.array([r[2] for r in rows], dtype="float64") ** s.get("fit_weight_power", 0.0)
    gr = _Graded(numpy.array([_unit(vectors[r[0]]) for r in rows]), y, w)
    X = numpy.array([[_logit(scores[u])] + gr.features(
        gr.S[i], gr.drop(gr.S[i], s["near_copy"], i), s["sharpness"], s["centroid"])
        for i, (u, *_) in enumerate(rows)])
    return X, y, w


def _enough(rows, s, total=True):
    n_yes = sum(r[1] for r in rows)
    each = n_yes >= s["min_each"] and len(rows) - n_yes >= s["min_each"]
    return each and (len(rows) >= s["min_graded"] or not total)


def _spec(label, question_sha256, s, mu, sd, intercept, slopes, rows, pooled=None):
    spec = {"label": label, "question_sha256": question_sha256, "settings": s,
            "mu": mu, "sd": sd, "coef": [intercept] + slopes,
            "graded": [[u, v, round(wt, 6), "|".join(sorted(p))] for u, v, wt, p in rows],
            "n_yes": int(sum(r[1] for r in rows)), "n_no": int(sum(1 - r[1] for r in rows))}
    if pooled:
        spec["pooled"] = sorted(pooled)
    return spec


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
    X, y, w = _design(rows, scores, vectors, s)
    mu, sd, b0, b = _fit(X, y, w, s["ridge"])
    return _spec(label, question_sha256, s, mu, sd, b0[0], b, rows)


def fit_pooled(blocks, s=None):
    """One layer per label, the labels pooled (posterior.pool): each label
    keeps its own intercept (its own base rate) and its own graded sentences
    for the vote, and the weights on the score and the similarities are
    shared, fitted on every label's grades at once. blocks = {label:
    (graded, scores, vectors, question_sha256)}. A label joins with at
    least posterior.min_each graded yes and no; the pool needs
    posterior.min_graded sentences in all. Returns {label: spec}."""
    s = dict(settings(), **(s or {}))
    parts = []
    for label, (graded, scores, vectors, sha) in sorted(blocks.items()):
        rows = _rows(graded, scores, vectors, None)
        if _enough(rows, s, total=False):
            parts.append((label, sha, rows) + _design(rows, scores, vectors, s))
    if not parts or sum(len(p[2]) for p in parts) < s["min_graded"]:
        return {}
    X = numpy.vstack([p[3] for p in parts])
    y = numpy.concatenate([p[4] for p in parts])
    # each label's design weights sum to its rows, so no label outweighs
    # the rest by the size of its strata
    w = numpy.concatenate([p[5] * len(p[5]) / p[5].sum() for p in parts])
    group = numpy.concatenate([numpy.full(len(p[2]), i) for i, p in enumerate(parts)])
    mu, sd, b0, b = _fit(X, y, w, s["ridge"], group, len(parts))
    names = [p[0] for p in parts]
    return {label: _spec(label, sha, s, mu, sd, b0[i], b, rows, names)
            for i, (label, sha, rows, *_) in enumerate(parts)}


def folds_of(rows, k):
    """{unit_id: fold}: whole projects to a fold (a sentence of several
    projects goes with the first), assigned by a hash of the project so the
    split is the same on every run and for every label."""
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
        out.update({r[0]: layer.probability(r[0], scores[r[0]]) for r in test})
    return out


def out_of_fold_all(blocks, s=None):
    """{label: {unit_id: out-of-fold probability}} over every label of
    blocks (as fit_pooled), pooled or per label as s['pool'] says. The folds
    are the same for every label (by project), so a pooled fit never sees
    the test fold of any label. A sentence whose label has no layer in a
    fold keeps the model's score."""
    s = dict(settings(), **(s or {}))
    if not s["pool"]:
        out = {}
        for label, (graded, scores, vectors, _) in blocks.items():
            rows = _rows(graded, scores, vectors, None)
            out[label] = out_of_fold(rows, scores, vectors, s) or \
                {r[0]: float(scores[r[0]]) for r in rows}
        return out
    rows = {label: _rows(g, sc, v, None) for label, (g, sc, v, _) in blocks.items()}
    k = max(2, s["folds"])
    fold = {}
    for rs in rows.values():
        fold.update(folds_of(rs, k))
    out = {label: {} for label in blocks}
    for f in range(k):
        train = {label: ([r for r in rows[label] if fold[r[0]] != f],) + blocks[label][1:]
                 for label in blocks}
        specs = fit_pooled(train, s)
        for label, (_, scores, vectors, _) in blocks.items():
            test = [r for r in rows[label] if fold[r[0]] == f]
            if not test:
                continue
            if label not in specs:
                out[label].update({r[0]: float(scores[r[0]]) for r in test})
                continue
            layer = Layer(specs[label], vectors.get)
            out[label].update({r[0]: layer.probability(r[0], scores[r[0]]) for r in test})
    return out






class Layer:
    """A fitted layer ready to apply: probability(unit_id, score)."""

    def __init__(self, spec, vector_of, projects_of=None):
        self.spec = spec
        rows = [g for g in spec["graded"] if vector_of(g[0]) is not None]
        self.ids = {g[0]: i for i, g in enumerate(rows)}
        y = numpy.array([g[1] for g in rows], dtype="float64")
        w = numpy.array([float(g[2]) if len(g) > 2 else 1.0 for g in rows], dtype="float64")
        G = numpy.array([_unit(vector_of(g[0])) for g in rows]) if rows else numpy.zeros((0, 1))
        self.graded = _Graded(G, y, w)
        self.y, self.G = y, G
        self.vector_of = vector_of
        st = spec["settings"]
        self.sharp, self.cent = st["sharpness"], st["centroid"]
        self.near = _near(st.get("near_copy", "off"))
        self.mu, self.sd, self.b = (numpy.array(spec[k]) for k in ("mu", "sd", "coef"))

    def probability(self, unit_id, score, projects=None):
        """The posterior for a sentence from its model score. (projects is
        accepted for older callers and not used: sentences of the same
        project vote.)"""
        v = self.vector_of(unit_id)
        if v is None or not len(self.y):
            return float(score)
        sims = self.G @ _unit(v)
        drop = self.graded.drop(sims, self.near, self.ids.get(unit_id))
        x = numpy.array([_logit(score)] + self.graded.features(sims, drop, self.sharp, self.cent))
        z = self.b[0] + float(((x - self.mu) / self.sd) @ self.b[1:])
        z = max(-30.0, min(30.0, z))
        return 1 / (1 + math.exp(-z))








def weighted_pr(probs, ys, ws, t):
    """(precision, recall, kept) at floor t, every row weighted by its
    design weight; None where undefined."""
    kept = sum(w for p, y, w in zip(probs, ys, ws) if p >= t)
    tp = sum(w for p, y, w in zip(probs, ys, ws) if p >= t and y)
    pos = sum(w for y, w in zip(ys, ws) if y)
    return (tp / kept if kept else None), (tp / pos if pos else None), kept


def suggest_floor(probs, ys, target, ws=None):
    """(floor, precision, recall) on a 0.01 grid with the most recall whose
    weighted precision reaches target (the highest such floor on ties, for
    margin), or the most precise one when none does. Read it off
    out-of-fold probabilities: a floor chosen on the rows a layer was fitted
    on looks better than it is."""
    ws = ws or [1.0] * len(probs)
    best, top = None, None
    for t in [x / 100 for x in range(1, 100)]:
        prec, rec, kept = weighted_pr(probs, ys, ws, t)
        if not kept:
            continue
        if prec >= target and (best is None or rec >= best[2]):
            best = (t, prec, rec)
        if top is None or prec > top[1]:
            top = (t, prec, rec)
    return best or top


def wauc(ps, ys, ws):
    """Weighted AUC: the weighted share of (yes, no) pairs ranked right; None
    without both classes."""
    pos = [(p, w) for p, y, w in zip(ps, ys, ws) if y]
    neg = [(p, w) for p, y, w in zip(ps, ys, ws) if not y]
    if not pos or not neg:
        return None
    num = sum(wp * wn * (1.0 if pp > pn else 0.5 if pp == pn else 0.0) for pp, wp in pos for pn, wn in neg)
    return num / (sum(w for _, w in pos) * sum(w for _, w in neg))


def effective_n(ws):
    """Kish's effective sample size of weighted rows."""
    s, s2 = sum(ws), sum(w * w for w in ws)
    return (s * s / s2) if s2 else 0.0



