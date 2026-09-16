#!/usr/bin/env python3
"""Train one binary classifier per asset class on the paragraph embeddings.

Reads  inputs/labels/paragraph_labels.csv, inputs/taxonomy/assets.csv,
       data/paragraph_emb.npy, data/emb_index.csv, data/paragraphs.csv
Writes data/intermediate/models/asset_{asset_id}.npz   one weight vector each
       data/intermediate/models/asset_models.json      the provenance record
       data/train_assets_report.txt

  python3 src/train_assets.py
  python3 src/train_assets.py --folds 5 --report-only

Three decisions shape this file, and each is a response to the same fact: there
are about 120 labels per class against 3,072 dimensions.

**Ridge regression on +/-1 targets, solved in the dual, rather than logistic
regression.** With n far below d, any unregularised fit separates the training
data perfectly and means nothing, so regularisation is doing all the work and
the choice of loss matters much less than the choice of lambda. Ridge has a
closed form - and in the dual it is a 120x120 solve rather than a 3072x3072
one - so there is no optimiser, no learning rate, and no convergence to doubt.
Scores become probabilities afterwards by Platt scaling on out-of-fold scores,
which is what the data model's probability column wants.

**Folds are grouped by DOCUMENT, not shuffled over rows.** Paragraphs from one
appraisal document share vocabulary, formatting and subject matter, so a random
split leaves near-duplicates on both sides and reports a score that will not
survive contact with a new project. Grouping by document estimates what we
actually need: does this classifier work on a document it has never seen.
It reads worse. It is the honest number.

**A centroid baseline runs alongside every model.** Difference of class means is
about the simplest linear rule there is. If ridge cannot beat it, the extra
machinery is not earning its place and the report says so per class.
"""
import argparse, csv, json, os, sys, time
from collections import defaultdict

import numpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join("data", "intermediate", "models")

# Regularisation path. The winner is chosen per class by grouped cross
# validation, never by looking at the final numbers.
LAMBDAS = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0)


def load(data, inputs):
    with open(os.path.join(inputs, "labels", "paragraph_labels.csv"),
              newline="", encoding="utf-8") as fh:
        labels = list(csv.DictReader(fh))
    with open(os.path.join(inputs, "taxonomy", "assets.csv"),
              newline="", encoding="utf-8") as fh:
        assets = [r for r in csv.DictReader(fh) if r["active"].strip().lower() == "true"]
    with open(os.path.join(data, "emb_index.csv"), newline="", encoding="utf-8") as fh:
        row_of = {r["paragraph_id"]: int(r["row"]) for r in csv.DictReader(fh)}
    with open(os.path.join(data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        doc_of = {r["paragraph_id"]: r["doc_id"] for r in csv.DictReader(fh)}
    arr = numpy.load(os.path.join(data, "paragraph_emb.npy"), mmap_mode="r")
    return labels, assets, row_of, doc_of, arr


def ridge_dual(X, y, lam):
    """Weights for ridge regression on +/-1 targets, solved in the dual.

    w = X.T (X X.T + lam I)^-1 y is algebraically identical to the primal
    (X.T X + lam I)^-1 X.T y but inverts an n x n matrix instead of a d x d one.
    With n around 120 and d 3072 that is the difference between instant and
    slow, and it avoids forming a 3072x3072 matrix that is singular anyway.
    """
    n = X.shape[0]
    K = X @ X.T
    alpha = numpy.linalg.solve(K + lam * numpy.eye(n), y)
    return X.T @ alpha


def centroid(X, y):
    """Difference of class means: the baseline ridge has to beat."""
    pos, neg = X[y > 0], X[y < 0]
    if not len(pos) or not len(neg):
        return numpy.zeros(X.shape[1])
    return pos.mean(axis=0) - neg.mean(axis=0)


def group_folds(groups, k, seed=0):
    """Assign each distinct group to one fold, largest groups first, always to
    the emptiest fold. Keeps fold sizes close without splitting a document."""
    sizes = defaultdict(int)
    for g in groups:
        sizes[g] += 1
    order = sorted(sizes, key=lambda g: (-sizes[g], g))
    load_, assign = [0] * k, {}
    for g in order:
        i = min(range(k), key=lambda j: load_[j])
        assign[g] = i
        load_[i] += sizes[g]
    return numpy.array([assign[g] for g in groups])


def auc(y, s):
    """Rank-based ROC AUC, ties averaged. Threshold-free, so it says whether the
    ranking is any good separately from where a cut-off is drawn."""
    pos, neg = s[y > 0], s[y < 0]
    if not len(pos) or not len(neg):
        return float("nan")
    order = numpy.argsort(s)
    ranks = numpy.empty(len(s), dtype="float64")
    ranks[order] = numpy.arange(1, len(s) + 1)
    # average ranks within ties
    for v in numpy.unique(s):
        m = s == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    r_pos = ranks[y > 0].sum()
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def platt(scores, y):
    """One-dimensional logistic fit mapping a score to a probability, fitted on
    OUT-OF-FOLD scores so it is not calibrated on data the model memorised."""
    t = (y > 0).astype("float64")
    a, b = 1.0, 0.0
    for _ in range(200):
        p = 1.0 / (1.0 + numpy.exp(-(a * scores + b)))
        ga, gb = ((p - t) * scores).sum(), (p - t).sum()
        w = p * (1 - p) + 1e-9
        ha = (w * scores * scores).sum() + 1e-9
        hb = w.sum() + 1e-9
        a -= ga / ha
        b -= gb / hb
    return float(a), float(b)


def prf(y, s, thr):
    pred = s >= thr
    tp = int((pred & (y > 0)).sum())
    fp = int((pred & (y < 0)).sum())
    fn = int((~pred & (y > 0)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f, tp, fp, fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--inputs", default=os.path.join(ROOT, "inputs"))
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    labels, assets, row_of, doc_of, arr = load(args.data, args.inputs)
    by_asset = defaultdict(list)
    for r in labels:
        by_asset[r["asset_id"]].append(r)

    out = [f"trained {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
           f"folds: {args.folds}, grouped by document",
           f"regularisation path: {LAMBDAS}", ""]
    out.append(f"{'asset':<21}{'n':>5}{'pos':>5}{'docs':>6}{'AUC':>7}"
               f"{'prec':>7}{'rec':>7}{'F1':>7}{'base':>7}  lambda")
    out.append("-" * 92)

    os.makedirs(os.path.join(args.data, "intermediate", "models"), exist_ok=True)
    manifest = {"trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "folds": args.folds, "grouping": "document", "classes": {}}
    thin = []

    for asset in assets:
        aid = asset["asset_id"]
        rows = by_asset.get(aid, [])
        keep = [r for r in rows if r["paragraph_id"] in row_of]
        if len(keep) < 20:
            out.append(f"{aid:<21}{len(keep):>5}  too few labels to train")
            continue
        idx = numpy.array([row_of[r["paragraph_id"]] for r in keep])
        X = numpy.asarray(arr[idx], dtype="float64")
        y = numpy.array([1.0 if r["label"] == "true" else -1.0 for r in keep])
        groups = [doc_of.get(r["paragraph_id"], r["paragraph_id"]) for r in keep]
        folds = group_folds(groups, args.folds, args.seed)
        npos = int((y > 0).sum())

        # Choose lambda on out-of-fold AUC, then report with that same protocol.
        best, best_auc, best_oof = None, -1.0, None
        for lam in LAMBDAS:
            oof = numpy.zeros(len(y))
            for f in range(args.folds):
                tr, te = folds != f, folds == f
                if not te.any() or len(numpy.unique(y[tr])) < 2:
                    continue
                oof[te] = X[te] @ ridge_dual(X[tr], y[tr], lam)
            a = auc(y, oof)
            if a == a and a > best_auc:
                best, best_auc, best_oof = lam, a, oof.copy()

        base_oof = numpy.zeros(len(y))
        for f in range(args.folds):
            tr, te = folds != f, folds == f
            if te.any() and len(numpy.unique(y[tr])) > 1:
                base_oof[te] = X[te] @ centroid(X[tr], y[tr])
        base_auc = auc(y, base_oof)

        # Threshold at the score that maximises out-of-fold F1. It is a starting
        # point only - methodology section 6 rule 3 puts the real threshold on a
        # separate calibration draw, which this is deliberately not.
        cands = numpy.unique(best_oof)
        thr = max(cands, key=lambda t: prf(y, best_oof, t)[2]) if len(cands) else 0.0
        p, r, f1, tp, fp, fn = prf(y, best_oof, thr)
        a_, b_ = platt(best_oof, y)

        w = ridge_dual(X, y, best)
        numpy.savez(os.path.join(args.data, "intermediate", "models", f"asset_{aid}.npz"),
                    w=w.astype("float32"), threshold=numpy.float32(thr),
                    platt_a=numpy.float32(a_), platt_b=numpy.float32(b_))
        manifest["classes"][aid] = {
            "n": len(y), "positives": npos, "documents": len(set(groups)),
            "lambda": best, "auc_oof": round(float(best_auc), 4),
            "auc_centroid_baseline": round(float(base_auc), 4),
            "precision_oof": round(p, 4), "recall_oof": round(r, 4),
            "f1_oof": round(f1, 4), "tp": tp, "fp": fp, "fn": fn,
            "threshold": float(thr), "platt_a": a_, "platt_b": b_}
        flag = " *" if npos < 25 else ""
        if npos < 25:
            thin.append(aid)
        out.append(f"{aid:<21}{len(y):>5}{npos:>5}{len(set(groups)):>6}"
                   f"{best_auc:>7.3f}{p:>7.3f}{r:>7.3f}{f1:>7.3f}"
                   f"{base_auc:>7.3f}  {best:g}{flag}")

    out.append("-" * 92)
    out.append("AUC, precision and recall are OUT-OF-FOLD, with folds grouped by")
    out.append("document, so they estimate performance on a project never seen.")
    out.append("'base' is the centroid baseline's AUC: where ridge does not beat it,")
    out.append("the extra machinery is not earning its place.")
    if thin:
        out.append("")
        out.append(f"* fewer than 25 positives ({', '.join(thin)}). Metrics on these are")
        out.append("  noisy; treat them as provisional and oversample in the next draw.")
    out.append("")
    out.append("The threshold is chosen on out-of-fold F1 and is a STARTING POINT.")
    out.append("Methodology section 6 rule 3 puts the operating threshold on a separate")
    out.append("uniform-random calibration draw, which this enriched draw is not.")

    with open(os.path.join(args.data, "intermediate", "models", "asset_models.json"),
              "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True); fh.write("\n")
    body = "\n".join(out)
    with open(os.path.join(args.data, "train_assets_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
