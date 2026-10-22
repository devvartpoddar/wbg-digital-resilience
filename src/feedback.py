"""Relevance feedback: the second look below the gate, the same for a
measure and for an asset (docs/method/decisions.md).

After a pass of answers, the sentences that count (a measure's committed
sentences; an asset's sentences whose own posterior reaches asset.floor) are
averaged into a centroid, minus feedback.negative_weight times the centroid
of the sentences asked that do not count (Rocchio, with no query term); the
corpus is ranked by cosine to it, and the next feedback.batch sentences not
yet asked are asked. A label stops when a round yields fewer than
feedback.min_yield sentences that count, or after feedback.max_rounds
rounds. A yield rule for how deep to read, not a recall guarantee: the
elusion sheet looks at what it leaves.
"""
import numpy

import panel

BLOCK = 20_000


def settings():
    return {"batch": int(panel.get("feedback.batch", 500)),
            "min_yield": int(panel.get("feedback.min_yield", 10)),
            "max_rounds": int(panel.get("feedback.max_rounds", 6)),
            "negative_weight": float(panel.get("feedback.negative_weight", 0.5))}


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
