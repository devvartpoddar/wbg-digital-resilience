"""Relevance feedback below the gate, the same for an asset and a measure
(docs/method/decisions.md): the sentences that count at the ask floor,
minus feedback.negative_weight times those asked that do not (Rocchio),
rank the corpus; the next feedback.batch unasked sentences are asked, until
a round yields fewer than feedback.min_yield or feedback.max_rounds is
reached. A yield rule for depth, not a recall guarantee.
"""
import numpy

import panel

BLOCK = 20_000


def settings(prefix="feedback."):
    """feedback.* for the measures; asset_feedback.<k> overrides a line for
    the asset map where the panel writes one."""
    def get(k, default):
        v = panel.get(prefix + k)
        return panel.get("feedback." + k, default) if v is None else v
    return {"batch": int(get("batch", 500)),
            "min_yield": int(get("min_yield", 10)),
            "max_rounds": int(get("max_rounds", 6)),
            "negative_weight": float(get("negative_weight", 0.5))}


ASSET = "asset_feedback."


def query(matrix, pos, neg, negative_weight):
    """The Rocchio query from the matrix rows that count (pos) and those
    asked that do not (neg), as a unit vector; None with no pos."""
    if not pos:
        return None
    q = numpy.asarray(matrix[sorted(pos)], dtype="float32").mean(axis=0)
    if neg and negative_weight:
        q -= negative_weight * numpy.asarray(matrix[sorted(neg)], dtype="float32").mean(axis=0)
    norm = numpy.linalg.norm(q)
    return q / norm if norm else None


def ranked(matrix, queries):
    """For each query, every matrix row best first (ties by row): one pass
    over the matrix however many queries, so the asset map's labels share
    a read. Returns [(row order, cosines)], one per query."""
    if not queries:
        return []
    Q = numpy.stack(queries).astype("float32").T
    n = matrix.shape[0]
    sims = numpy.empty((n, Q.shape[1]), dtype="float32")
    for s in range(0, n, BLOCK):
        block = numpy.asarray(matrix[s:s + BLOCK], dtype="float32")
        sims[s:s + block.shape[0]] = block @ Q
    out = []
    for j in range(Q.shape[1]):
        col = sims[:, j]
        out.append((numpy.lexsort((numpy.arange(n), -col)), col))
    return out


def next_rows(order, eligible, n):
    """The first n rows of `order` for which eligible(row) holds."""
    rows = []
    for i in order.tolist():
        if eligible(i):
            rows.append(i)
            if len(rows) >= n:
                break
    return rows
