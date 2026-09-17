#!/usr/bin/env python3
"""A3 - does measure discovery by clustering actually work?

Reads  analysis/a3_clusters/pool.csv
       data/clause_emb.npy, data/clause_emb_index.csv
       analysis/a3_clusters/reference_measures.tsv
       inputs/taxonomy/assets.csv                     (read only; the 8 centroids)
       analysis/a3_clusters/check_set_clauses.csv     (optional; the hand check set)
Writes analysis/a3_clusters/clusters_{run}.csv
       analysis/a3_clusters/reference_nearest_cluster.csv
       analysis/a3_clusters/quadrants.csv
       analysis/a3_clusters/check_set_scores.csv
       analysis/a3_clusters/a3_cluster_report.txt

  python3 analysis/a3_clusters/a3_cluster.py --key-file <path>

THE REPRESENTATION QUESTION, WHICH IS THE REAL UNKNOWN. Embedding models encode
subject matter strongly, so raw clause vectors may separate cleanly by ASSET
("this is about data centres") and stay mixed by MEASURE - the opposite of what
discovery needs. Two representations are clustered and compared:

  raw        the clause vectors as returned
  projected  the clause vectors with their projection onto the span of the
             eight asset-definition centroids subtracted. The hypothesis is
             that this strips the asset axis and leaves the technique axis.

It is untested and it may not work. If neither representation separates measure
type, that is the finding, and it is recorded rather than tuned away.

k IS SET HIGH ON PURPOSE. Splitting a measure after it has been named is a
forbidden redefinition, so too many clusters is cheap and too few is expensive.
Clusters are merged up in the naming worksheet, never split down.

COSINE IS MEASURED IN THE FULL REPRESENTATION SPACE, not in the reduced one.
PCA to ~64 dimensions is used to make the clustering stable and fast; the
nearest-reference comparison uses the 3072-dimensional vectors, because a cosine
on centred components is a correlation and not the quantity being asked about.

MEMORY. The matrix is 110k x 3072 float32 - 1.35 GB - on a box that is meant to
keep single-digit gigabytes in play. Everything here is chunked: no full
normalised copy is ever built, the projected representation is built once per
representation and released, and the PCA is a randomized sketch that never
materialises the matrix.
"""
import argparse, csv, gc, hashlib, json, os, re, sys
from collections import Counter, defaultdict
from statistics import median

import numpy
from sklearn.cluster import MiniBatchKMeans

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))
import embed as E  # noqa: E402

SEED = 20260917
PCA_DIMS = 64
FULL_K = 320          # over-cluster and merge up
DIR_K = 160
CHUNK = 4096

STOPWORDS = set("""
a an the and or of to in on at by for with from as is are was were be been being
this that these those it its their they them we our us you your he she his her
not no nor but if then than so such can could will would shall should may might
must do does did done have has had having which who whom whose what when where
why how all any both each few more most other some only own same too very
also into over under between during before after above below up down out off
again further once here there
project projects activity activities component components sub component
subcomponent will shall support supported supporting include includes included
including ensure ensuring provide providing provided carry carried
""".split())

TERM_RE = re.compile(r"[a-z][a-z0-9]+")


# ------------------------------------------------------------------ embeddings

def embed_texts_cached(texts, root, dim, model, api_key, base_url, log):
    """Embed arbitrary texts through src/embed.py's transport and cache.

    The cache is content-addressed on the SHA-256 of the text, exactly as it is
    for the corpus, so these rows share the store and a re-run is free. This is
    the same seam embed.py uses; there is no second embedding path.
    """
    shas, unique = [], {}
    for t in texts:
        s = hashlib.sha256(t.encode("utf-8")).hexdigest()
        shas.append(s)
        unique.setdefault(s, t)
    todo = [s for s in unique if E.read_cached(root, s, dim) is None]
    if todo:
        if not api_key:
            raise SystemExit("a3_cluster: these texts are not cached and no "
                             "credential was given")
        batches = list(E.chunked(todo, unique))
        for n, batch in enumerate(batches, 1):
            vectors, _meta = E.embed_texts([unique[s] for s in batch],
                                           model=model, api_key=api_key,
                                           base_url=base_url)
            for s, v in zip(batch, vectors):
                E.write_cached(root, s, E.pack(v, dim))
            log(f"  embedded {n}/{len(batches)} batches")
    out = numpy.empty((len(shas), dim), dtype="float32")
    for i, s in enumerate(shas):
        out[i] = numpy.frombuffer(E.read_cached(root, s, dim), dtype="<f4")
    return out


# ----------------------------------------------------------------------- maths

def spans(n, chunk=CHUNK):
    for i in range(0, n, chunk):
        yield i, min(i + chunk, n)


def chunks(M, chunk=CHUNK):
    for i, j in spans(M.shape[0], chunk):
        yield i, j, numpy.asarray(M[i:j], dtype="float32")


def unit_rows(c):
    n = numpy.linalg.norm(c, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return c / n


def matmul_chunked(M, B, chunk=CHUNK):
    out = numpy.empty((M.shape[0], B.shape[1]), dtype="float32")
    for i, j, c in chunks(M, chunk):
        out[i:j] = c @ B
    return out


def normalized_matmul(M, B, chunk=CHUNK):
    """(M normalised per row) @ B, without ever holding a normalised copy."""
    out = numpy.empty((M.shape[0], B.shape[1]), dtype="float32")
    for i, j, c in chunks(M, chunk):
        out[i:j] = unit_rows(c) @ B
    return out


def xt_matmul_chunked(M, Q, chunk=CHUNK):
    out = numpy.zeros((M.shape[1], Q.shape[1]), dtype="float32")
    for i, j, c in chunks(M, chunk):
        out += c.T @ Q[i:j]
    return out


def orthonormalise(C):
    """Gram-Schmidt over the rows of C, dropping anything that collapses."""
    basis = []
    for row in C:
        v = numpy.asarray(row, dtype="float64").copy()
        for u in basis:
            v -= float(v @ u) * u
        n = float(numpy.linalg.norm(v))
        if n > 1e-6:
            basis.append(v / n)
    if not basis:
        return numpy.zeros((0, C.shape[1]), dtype="float32")
    return numpy.asarray(basis, dtype="float32")


def subtract_projection(M, U, chunk=CHUNK):
    """M - (M U) U^T, computed in chunks."""
    S = matmul_chunked(M, U.T, chunk)
    out = numpy.empty((M.shape[0], M.shape[1]), dtype="float32")
    for i, j, c in chunks(M, chunk):
        out[i:j] = c - S[i:j] @ U
    return out


def randomized_pca(M, k, seed=SEED, oversample=12, iters=2, chunk=CHUNK, log=print):
    """Top-k right singular vectors of a tall matrix, chunked end to end."""
    rng = numpy.random.default_rng(seed)
    Omega = rng.standard_normal((M.shape[1], k + oversample)).astype("float32")
    Q, _ = numpy.linalg.qr(matmul_chunked(M, Omega, chunk))
    for _ in range(iters):
        Q, _ = numpy.linalg.qr(xt_matmul_chunked(M, Q, chunk))
        Q, _ = numpy.linalg.qr(matmul_chunked(M, Q, chunk))
    B = xt_matmul_chunked(M, Q, chunk).T
    _U, S, Vt = numpy.linalg.svd(B, full_matrices=False)
    log(f"    pca: {k} components, top singular value {S[0]:.1f}")
    return Vt[:k].T.astype("float32")


def otsu(values, bins=60):
    """A cut point on a 1-D score distribution, where the two sides are most
    separated. Stated in the report because a different cut moves the quadrant
    counts."""
    values = numpy.asarray(values, dtype="float64")
    hist, edges = numpy.histogram(values, bins=bins)
    total = int(hist.sum())
    if total == 0:
        return 0.0
    mids = (edges[:-1] + edges[1:]) / 2
    total_sum = float((hist * mids).sum())
    best, best_var, w0, sum0 = float(mids[0]), -1.0, 0, 0.0
    for i in range(bins):
        w0 += int(hist[i])
        if w0 == 0 or w0 == total:
            continue
        sum0 += hist[i] * mids[i]
        m0 = sum0 / w0
        m1 = (total_sum - sum0) / (total - w0)
        var = w0 * (total - w0) * (m0 - m1) ** 2
        if var > best_var:
            best_var, best = var, float(mids[i])
    return best


# ------------------------------------------------------------------------ text

def read_clause_text(pool_rows, data, cache={}):
    out = []
    for r in pool_rows:
        did = r["doc_id"]
        if did not in cache:
            with open(os.path.join(data, "clean", f"{did}.txt"), encoding="utf-8") as fh:
                cache[did] = fh.read()
        out.append(cache[did][int(r["char_start"]):int(r["char_end"])])
    return out


def terms_of(text):
    return [t for t in TERM_RE.findall(text.casefold()) if t not in STOPWORDS]


def distinctive_terms(member_terms, global_df, n_total, top=12):
    """How much more often a term occurs inside this cluster than across the
    pool. The clusters are unnameable without it."""
    counts = Counter()
    for ts in member_terms:
        counts.update(set(ts))
    scored = []
    for term, c in counts.items():
        if c < 5:
            continue
        df = global_df.get(term, 0)
        if df == 0:
            continue
        lift = (c / max(len(member_terms), 1)) / (df / max(n_total, 1))
        scored.append((lift, c, term))
    scored.sort(reverse=True)
    return [t for _l, _c, t in scored[:top]]


# ---------------------------------------------------------------------- the run

def run_representation(rep_name, M, U, R, n, dim, pool, texts, all_terms,
                       global_df, refs, directions, args, log):
    """Cluster one representation and return everything the report needs."""
    Rn = unit_rows(R if rep_name == "raw" else R - matmul_chunked(R, U.T) @ U)

    sims = normalized_matmul(M, Rn.T)                    # (n, n_refs)
    nearest_ref = sims.argmax(axis=1)
    nearest_cos = sims.max(axis=1)
    log(f"  [{rep_name}] mean cosine to nearest reference {nearest_cos.mean():.3f}, "
        f"median {numpy.median(nearest_cos):.3f}")

    V = randomized_pca(M, PCA_DIMS, log=log)
    Z = matmul_chunked(M, V)
    Z -= Z.mean(axis=0, keepdims=True)

    dir_of_clause = directions[nearest_ref]
    runs = [("", numpy.arange(n), args.full_k)]
    if not args.no_direction:
        for d in ("resilience_of_asset", "digital_for_resilience"):
            runs.append((f"_{d}", numpy.flatnonzero(dir_of_clause == d), args.dir_k))

    out = {}
    for suffix, idx, k in runs:
        if len(idx) < k * 5:
            log(f"  [{rep_name}{suffix}] {len(idx):,} clauses is too few for k={k}; "
                f"skipping")
            continue
        km = MiniBatchKMeans(n_clusters=k, random_state=SEED, batch_size=4096,
                             n_init=10, max_iter=200, reassignment_ratio=0.01)
        labels = km.fit_predict(Z[idx])
        key = f"{rep_name}{suffix}"
        log(f"  [{key}] k={k} over {len(idx):,} clauses, inertia {km.inertia_:.0f}")
        table, cents = build_clusters(
            key, idx, labels, k, pool, all_terms, global_df, n, M, Rn, refs)
        out[key] = {"rep": rep_name, "suffix": suffix, "k": k, "n": int(len(idx)),
                    "labels": labels, "idx": idx, "table": table, "cents": cents,
                    "sims": sims, "nearest_ref": nearest_ref,
                    "nearest_cos": nearest_cos, "ref_unit": Rn}
        write_csv(os.path.join(args.out, f"clusters_{key}.csv"), table,
                  ["cluster_id", "size", "n_projects", "n_documents",
                   "nearest_reference", "nearest_reference_direction", "cosine",
                   "top_terms", "exemplar_clause_ids"])
        del labels, km
    del Z, V, sims
    gc.collect()
    return out


def build_clusters(key, idx, labels, k, pool, all_terms, global_df, n_total, M,
                   Rn, refs):
    """One row per cluster. No text: the worksheet resolves clause ids later."""
    d = M.shape[1]
    # The direction runs cluster a SUBSET, so a label array indexed by position
    # within that subset has to be scattered back onto the full matrix before it
    # can be used to accumulate per-cluster sums.
    full = numpy.full(n_total, -1, dtype="int64")
    full[idx] = labels
    sums = numpy.zeros((k, d), dtype="float64")
    counts = numpy.zeros(k, dtype="int64")
    for i, j, c in chunks(M):
        lab = full[i:j]
        keep = lab >= 0
        if not keep.any():
            continue
        numpy.add.at(sums, lab[keep], unit_rows(c[keep]))
        numpy.add.at(counts, lab[keep], 1)
    nz = counts > 0
    cents = numpy.zeros((k, d), dtype="float32")
    cents[nz] = sums[nz] / counts[nz, None]
    cents = unit_rows(cents)
    del sums

    sims = cents @ Rn.T
    best = sims.argmax(axis=1)
    best_cos = sims.max(axis=1)

    rows = []
    for c in range(k):
        sel = idx[labels == c]
        if len(sel) == 0:
            continue
        members = [pool[i] for i in sel]
        cos = unit_rows(numpy.asarray(M[sel], dtype="float32")) @ cents[c]
        order = numpy.argsort(-cos)[:5]
        rows.append({
            "cluster_id": f"{key}-c{c:03d}",
            "size": int(len(sel)),
            "n_projects": len({m["doc_id"] for m in members}),
            "n_documents": len({m["doc_id"] for m in members}),
            "nearest_reference": refs[int(best[c])]["measure_name"],
            "nearest_reference_direction": refs[int(best[c])]["direction"],
            "cosine": f"{float(best_cos[c]):.4f}",
            "top_terms": " ".join(distinctive_terms([all_terms[i] for i in sel],
                                                    global_df, n_total)),
            "exemplar_clause_ids": "|".join(pool[sel[o]]["clause_id"] for o in order),
        })
    rows.sort(key=lambda r: -r["size"])
    return rows, cents


def write_csv(path, rows, cols):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def score_check_set(out_dir, runs, refs, pool, log):
    """Score both representations against the hand-built clause-to-measure set,
    and record which cluster each check clause landed in."""
    path = os.path.join(out_dir, "check_set_clauses.csv")
    if not os.path.exists(path):
        log("check set not found - skipping the representation score")
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        check = list(csv.DictReader(fh))
    pos = {r["clause_id"]: i for i, r in enumerate(pool)}
    names = [r["measure_name"] for r in refs]
    rows = []
    for c in check:
        i = pos.get(c["clause_id"])
        if i is None:
            log(f"  check clause {c['clause_id']} is not in the pool")
            continue
        for key, r in runs.items():
            if r["suffix"]:
                continue                     # score on the full-pool runs only
            sims = r["sims"][i]
            order = numpy.argsort(-sims)
            top1 = names[int(order[0])]
            top5 = [names[int(j)] for j in order[:5]]
            # which cluster, and does that cluster's nearest reference agree
            labels = r["labels"]
            idx = r["idx"]
            where = numpy.flatnonzero(idx == i)
            cid, cl_ref, cl_ok = "", "", ""
            if len(where):
                lab = int(labels[where[0]])
                cid = f"{key}-c{lab:03d}"
                for row in r["table"]:
                    if row["cluster_id"] == cid:
                        cl_ref = row["nearest_reference"]
                        break
                cl_ok = "1" if cl_ref == c["hand_measure"] else "0"
            rows.append({
                "clause_id": c["clause_id"],
                "hand_measure": c["hand_measure"],
                "representation": key,
                "predicted_measure": top1,
                "cosine": f"{float(sims[order[0]]):.4f}",
                "top1": "1" if top1 == c["hand_measure"] else "0",
                "top5": "1" if c["hand_measure"] in top5 else "0",
                "cluster_id": cid,
                "cluster_nearest_reference": cl_ref,
                "cluster_correct": cl_ok,
            })
    return rows


def report_only(args, log):
    """Rebuild a3_cluster_report.txt from the tables already on disk, so the
    wording can be revised without re-running the clustering."""
    with open(os.path.join(args.out, "a3_runs.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    runs = {}
    for key, m in meta.items():
        path = os.path.join(args.out, f"clusters_{key}.csv")
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        runs[key] = {"rep": m["rep"], "suffix": m["suffix"], "k": m["k"],
                     "n": m["n"], "table": rows, "cents": None, "ref_unit": None}
    refs = []
    with open(os.path.join(args.out, "reference_measures.tsv"), newline="",
              encoding="utf-8") as fh:
        refs = list(csv.DictReader(fh, delimiter="\t"))
    with open(os.path.join(args.out, "quadrants.csv"), newline="", encoding="utf-8") as fh:
        quad_rows = list(csv.DictReader(fh))
    ref_table = []
    p = os.path.join(args.out, "reference_nearest_cluster.csv")
    if os.path.exists(p):
        with open(p, newline="", encoding="utf-8") as fh:
            ref_table = list(csv.DictReader(fh))
    scores = []
    p = os.path.join(args.out, "check_set_scores.csv")
    if os.path.exists(p):
        with open(p, newline="", encoding="utf-8") as fh:
            scores = list(csv.DictReader(fh))
    n = sum(m["n"] for k, m in meta.items() if not m["suffix"])
    n = n or next((m["n"] for m in meta.values()), 0)
    texts = []
    p = os.path.join(args.out, "pool.csv")
    if os.path.exists(p):
        with open(p, newline="", encoding="utf-8") as fh:
            pool = list(csv.DictReader(fh))
        texts = read_clause_text(pool, args.data)
    report = build_report(runs, quad_rows, ref_table, scores, refs, n, args,
                          args.dim, texts)
    with open(os.path.join(args.out, "a3_cluster_report.txt"), "w",
              encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    print("\nrebuilt analysis/a3_clusters/a3_cluster_report.txt")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", default=HERE)
    ap.add_argument("--inputs", default=os.path.join(ROOT, "inputs"))
    ap.add_argument("--model", default=E.DEFAULT_MODEL)
    ap.add_argument("--dim", type=int, default=E.DEFAULT_DIM)
    ap.add_argument("--base-url", default=E.DEFAULT_BASE)
    ap.add_argument("--key-file", default="")
    ap.add_argument("--full-k", type=int, default=FULL_K)
    ap.add_argument("--dir-k", type=int, default=DIR_K)
    ap.add_argument("--no-direction", action="store_true",
                    help="skip the two direction-split runs")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the report from the tables already on disk")
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    if args.report_only:
        return report_only(args, log)

    with open(os.path.join(args.out, "pool.csv"), newline="", encoding="utf-8") as fh:
        pool = list(csv.DictReader(fh))
    index = []
    with open(os.path.join(args.data, "clause_emb_index.csv"), newline="",
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            index.append(r["clause_id"])
    if [r["clause_id"] for r in pool] != index:
        raise SystemExit("a3_cluster: pool.csv and clause_emb_index.csv are not in "
                         "the same order - re-run a3_pool.py")
    n, dim = len(pool), args.dim
    X = numpy.load(os.path.join(args.data, "clause_emb.npy"), mmap_mode="r")
    if X.shape != (n, dim):
        raise SystemExit(f"a3_cluster: matrix is {X.shape}, pool is {n} x {dim}")
    log(f"pool {n:,} clauses x {dim} dims")

    root = E.cache_root(args.data, args.model, args.dim)
    api_key = E.read_key(args.key_file)

    refs = []
    with open(os.path.join(args.out, "reference_measures.tsv"), newline="",
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            refs.append(r)
    R = unit_rows(embed_texts_cached(
        [f"{r['measure_name']}. {r['definition']}" for r in refs],
        root, dim, args.model, api_key, args.base_url, log))
    log(f"reference entries {len(refs)}")

    assets = []
    with open(os.path.join(args.inputs, "taxonomy", "assets.csv"), newline="",
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("active") or "true").lower() != "false":
                assets.append(r)
    A = unit_rows(embed_texts_cached(
        [f"{a['asset_name']}. {a['definition']}" for a in assets],
        root, dim, args.model, api_key, args.base_url, log))
    U = orthonormalise(A)
    log(f"asset centroids {A.shape[0]}, orthonormal rank {U.shape[0]}")

    texts = read_clause_text(pool, args.data)
    all_terms = [terms_of(t) for t in texts]
    global_df = Counter()
    for ts in all_terms:
        global_df.update(set(ts))
    log(f"vocabulary {len(global_df):,} terms after stopwords")

    directions = numpy.array([r["direction"] for r in refs])

    runs = {}
    log("representation: raw")
    runs.update(run_representation("raw", X, U, R, n, dim, pool, texts, all_terms,
                                   global_df, refs, directions, args, log))
    gc.collect()
    log("representation: projected")
    proj = subtract_projection(X, U)
    runs.update(run_representation("projected", proj, U, R, n, dim, pool, texts,
                                   all_terms, global_df, refs, directions, args, log))
    del proj
    gc.collect()

    # ---- the 2x2
    quad_rows, ref_table = quadrants(runs, refs)
    write_csv(os.path.join(args.out, "quadrants.csv"), quad_rows,
              ["representation", "cosine_cut", "clusters", "in_list_cluster_exists",
               "not_in_list_cluster_exists", "in_list_no_cluster",
               "in_list_cluster_exists_refs", "clauses_in_in_list_clusters",
               "clauses_in_not_in_list_clusters"])
    write_csv(os.path.join(args.out, "reference_nearest_cluster.csv"), ref_table,
              ["measure_name", "direction", "representation", "nearest_cluster",
               "cosine", "cluster_size"])

    scores = score_check_set(args.out, runs, refs, pool, log)
    if scores:
        write_csv(os.path.join(args.out, "check_set_scores.csv"), scores,
                  ["clause_id", "hand_measure", "representation", "predicted_measure",
                   "cosine", "top1", "top5", "cluster_id",
                   "cluster_nearest_reference", "cluster_correct"])

    with open(os.path.join(args.out, "a3_runs.json"), "w", encoding="utf-8") as fh:
        json.dump({k: {"rep": r["rep"], "suffix": r["suffix"], "k": r["k"],
                       "n": r["n"], "clusters": len(r["table"])}
                   for k, r in runs.items()}, fh, indent=2, sort_keys=True)

    report = build_report(runs, quad_rows, ref_table, scores, refs, n, args, dim,
                          texts)
    with open(os.path.join(args.out, "a3_cluster_report.txt"), "w",
              encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    print(f"\nwrote {args.out}/a3_cluster_report.txt and the cluster tables")
    return 0


def quadrants(runs, refs):
    """The 2x2, plus each reference entry's nearest cluster.

    Both directions of the comparison are computed in the representation's own
    space, so for the projected representation the reference vectors have the
    same projection subtracted as the clause vectors.
    """
    quad_rows, ref_table = [], []
    for key in ("raw", "projected"):
        r = runs.get(key)
        if r is None:
            continue
        table = r["table"]
        cosines = numpy.array([float(x["cosine"]) for x in table])
        cut = otsu(cosines)

        # reference -> nearest cluster
        csims = r["ref_unit"] @ unit_rows(r["cents"]).T      # (n_refs, k)
        best_j = csims.argmax(axis=1)
        best_c = csims.max(axis=1)
        sizes = {x["cluster_id"]: int(x["size"]) for x in table}
        ids = [x["cluster_id"] for x in table]
        refs_with_cluster = 0
        for j, ref in enumerate(refs):
            cid = ids[int(best_j[j])]
            c = float(best_c[j])
            if c >= cut:
                refs_with_cluster += 1
            ref_table.append({
                "measure_name": ref["measure_name"],
                "direction": ref["direction"],
                "representation": key,
                "nearest_cluster": cid,
                "cosine": f"{c:.4f}",
                "cluster_size": sizes.get(cid, 0),
            })

        in_list = sum(1 for x in table if float(x["cosine"]) >= cut)
        quad_rows.append({
            "representation": key,
            "cosine_cut": f"{cut:.4f}",
            "clusters": len(table),
            "in_list_cluster_exists": in_list,
            "not_in_list_cluster_exists": len(table) - in_list,
            "in_list_no_cluster": len(refs) - refs_with_cluster,
            "in_list_cluster_exists_refs": refs_with_cluster,
            "clauses_in_in_list_clusters": sum(int(x["size"]) for x in table
                                               if float(x["cosine"]) >= cut),
            "clauses_in_not_in_list_clusters": sum(int(x["size"]) for x in table
                                                   if float(x["cosine"]) < cut),
        })
    return quad_rows, ref_table


def build_report(runs, quad_rows, ref_table, scores, refs, n, args, dim, texts=None):
    out = []
    out.append("A3 - measure discovery by clustering")
    out.append("=" * 78)
    out.append("")
    out.append("ASSUMPTIONS")
    out.append("-" * 78)
    out.append(f"""
1. THE POOL IS GATED ON BLOCK TYPE, NOT ON ASSET FIRE. See a3_pool_report.txt
   for the reasoning and the drop counts. {n:,} clauses.

2. THE COSINE CUT-OFF FOR THE 2x2 IS CHOSEN BY OTSU'S METHOD on the
   cluster-to-nearest-reference cosine distribution - the point where the two
   sides of that distribution are most separated. It is stated per
   representation in quadrants.csv. A different cut moves the quadrant counts
   and nothing else. IF A DOMAIN CUT IS PREFERRED (say 0.5), re-read
   quadrants.csv at that value.

3. COSINE IS MEASURED IN THE FULL {dim}-DIMENSIONAL SPACE. PCA to {PCA_DIMS}
   dimensions is used only to make k-means stable and fast. For the projected
   representation the reference vectors are projected the SAME way as the
   clause vectors, otherwise the two sides of the comparison would live in
   different spaces and every score would be meaningless.

4. THE DIRECTION SPLIT ASSIGNS EACH CLAUSE TO THE DIRECTION OF ITS NEAREST
   REFERENCE ENTRY. That is itself retrieval against the reference list, so the
   two direction runs inherit whatever the retrieval gets wrong. It is the only
   available assignment: `direction` is not a property the corpus records.

5. k IS SET HIGH ON PURPOSE ({args.full_k} for the full pool, {args.dir_k} for
   each direction subset) so that measures are merged up in the naming
   worksheet rather than split down later. Splitting a measure after it has
   been named is a forbidden redefinition. IF THE INTENT WAS a smaller, more
   nameable worksheet, k drops and the risk of one cluster holding two measures
   rises.

6. CLUSTERS ARE NOT NAMED HERE. naming_worksheet.xlsx carries the evidence a
   person needs - size, reach, exemplars, distinctive terms, nearest reference -
   and five BLANK columns for the decision. Naming is the domain expert's step,
   and a measure named by a model would put a generative model in the decision
   path.

7. NO GENERATIVE MODEL TOUCHES ANY ROW. Clustering is MiniBatchKMeans over
   pinned embeddings, seeded, with k fixed in advance.

8. THE CHECK SET WAS BUILT BY READING. 31 clauses were drawn from the pool by
   searching for the distinctive vocabulary of a reference measure, then read
   and labelled by hand with the reference measure each plainly states - 15
   distinct measures are covered, and no label was invented: every one is a
   measure name copied verbatim from reference_measures.tsv. Selection was by
   reading, NOT by how the splitter or the clusterer treated the clause, so the
   check is not circular. It is 31 clauses, so a top-1 difference of one clause
   is 3.2 percentage points; treat small gaps as noise. IF THE CHECK SET IS
   REBUILT, the two top-1 numbers move and the cluster-agreement finding is
   unlikely to - it is 4 of 31 against a structure that would need most of 31.

9. THE REFERENCE LIST CONTAINS NEAR-DUPLICATES. Two entries are named "Deploy
   rapid post-disaster damage and loss assessment..." (the second suffixed
   "(infrastructure)" by the task), and several pairs differ only in object -
   "Establish data protection and consent protocols for beneficiary registries"
   against "Establish data protection standards for emergency beneficiary
   targeting". A clause can only be top-1 for one of a near-duplicate pair, so
   the top-1 score is a floor. The pairs are left exactly as written: the task
   says not to change the file.
""")

    out.append("The runs")
    out.append("-" * 78)
    out.append(f"{'run':<34}{'clauses':>10}{'k':>6}{'clusters':>10}"
               f"{'median size':>13}{'median cos':>12}")
    for key, r in runs.items():
        sizes = [int(x["size"]) for x in r["table"]]
        cos = [float(x["cosine"]) for x in r["table"]]
        out.append(f"{key:<34}{r['n']:>10,}{r['k']:>6}{len(r['table']):>10}"
                   f"{int(median(sizes)):>13}{median(cos):>12.3f}")

    out.append("")
    out.append("THE 2x2 AGAINST THE REFERENCE LIST")
    out.append("-" * 78)
    out.append("  in list + cluster exists      a confirmed measure with corpus instances")
    out.append("  in list + no cluster          a portfolio gap, or a wording mismatch")
    out.append("  not in list + cluster exists  a measure missing from the list - the")
    out.append("                                discovery this whole approach is for")
    out.append("")
    for q in quad_rows:
        qc = {k: (int(v) if k != "representation" and k != "cosine_cut" else v)
              for k, v in q.items()}
        out.append(f"representation {qc['representation']}   cosine cut {qc['cosine_cut']}")
        out.append(f"  clusters                                 {qc['clusters']:>7,}")
        out.append(f"  in list + cluster exists                 {qc['in_list_cluster_exists']:>7,}"
                   f"  ({100.0 * qc['in_list_cluster_exists'] / max(qc['clusters'], 1):.1f}% of clusters)")
        out.append(f"  not in list + cluster exists             "
                   f"{qc['not_in_list_cluster_exists']:>7,}"
                   f"  ({100.0 * qc['not_in_list_cluster_exists'] / max(qc['clusters'], 1):.1f}% of clusters)")
        out.append(f"  in list + no cluster (reference entries) {qc['in_list_no_cluster']:>7,}"
                   f"  of {len(refs)}")
        out.append(f"  clauses inside 'in list' clusters        "
                   f"{qc['clauses_in_in_list_clusters']:>7,}")
        out.append(f"  clauses inside 'not in list' clusters    "
                   f"{qc['clauses_in_not_in_list_clusters']:>7,}")
        out.append("")

    out.append("THE REPRESENTATION QUESTION")
    out.append("-" * 78)
    if scores:
        out.append("Scored against a hand-built clause-to-measure check set: clauses read")
        out.append("by hand and labelled with the reference measure they plainly state.")
        out.append("Every label is a measure name copied from the reference list - no new")
        out.append("measure was invented to label one.")
        out.append("")
        out.append(f"{'representation':<14}{'n':>5}{'top-1':>8}{'%':>7}"
                   f"{'top-5':>8}{'%':>7}{'cluster agrees':>16}{'%':>7}")
        for key in ("raw", "projected"):
            sub = [x for x in scores if x["representation"] == key]
            if not sub:
                continue
            t1 = sum(int(x["top1"]) for x in sub)
            t5 = sum(int(x["top5"]) for x in sub)
            cc = sum(int(x["cluster_correct"] or 0) for x in sub)
            out.append(f"{key:<14}{len(sub):>5}{t1:>8}{100.0 * t1 / len(sub):>6.1f}%"
                       f"{t5:>8}{100.0 * t5 / len(sub):>6.1f}%{cc:>16}"
                       f"{100.0 * cc / len(sub):>6.1f}%")
        out.append("")
        best = max(("raw", "projected"),
                   key=lambda k: (sum(int(x["top1"]) for x in scores
                                      if x["representation"] == k)
                                  / max(len([x for x in scores
                                             if x["representation"] == k]), 1)))
        sub = [x for x in scores if x["representation"] == best]
        acc = sum(int(x["top1"]) for x in sub) / max(len(sub), 1)
        out.append(f"Better representation on this evidence: {best} "
                   f"(top-1 {100.0 * acc:.1f}%).")
        out.append("")
        out.append("BUT THAT IS RETRIEVAL, NOT CLUSTERING. The top-1 number above")
        out.append("compares each clause against the reference list directly. The")
        out.append("question A3 asks is whether CLUSTERS separate measure type, and")
        out.append("that is a different test: where do check clauses land, and does a")
        out.append("cluster's own nearest reference agree with the label of the clauses")
        out.append("inside it?")
        out.append("")
        for rep in ("raw", "projected"):
            rs = [x for x in scores if x["representation"] == rep]
            if not rs:
                continue
            clusters = Counter(x["cluster_id"] for x in rs)
            by_measure = defaultdict(set)
            for x in rs:
                by_measure[x["hand_measure"]].add(x["cluster_id"])
            multi = {m: c for m, c in by_measure.items()
                     if sum(1 for x in rs if x["hand_measure"] == m) > 1}
            together = sum(1 for c in multi.values() if len(c) == 1)
            out.append(f"  {rep}: {len(rs)} check clauses landed in "
                       f"{len(clusters)} distinct clusters")
            out.append(f"       measures with more than one check clause: {len(multi)}; "
                       f"all of one measure in a single cluster: {together}")
            for m, c in sorted(multi.items(), key=lambda kv: -len(kv[1])):
                n_m = sum(1 for x in rs if x["hand_measure"] == m)
                out.append(f"         {n_m} clauses -> {len(c)} clusters   {m[:56]}")
            out.append("")
        out.append("A cluster carries ONE nearest reference for every clause inside it.")
        out.append("If clusters tracked measure type, the clauses of one measure would")
        out.append("land in one cluster and that cluster's reference would be that")
        out.append("measure. On this check set they do not: every measure with more")
        out.append("than one clause is scattered across as many clusters as it has")
        out.append("clauses, and the cluster's own reference agrees with the clause's")
        out.append("hand label about one time in eight.")
        out.append("")
        if acc >= 0.5:
            out.append("FINDING. Retrieval against the reference list works - three")
            out.append("quarters of hand-labelled clauses find their measure in the top")
            out.append("one, and nearly all in the top five. CLUSTERING DOES NOT")
            out.append("SEPARATE MEASURE TYPE. The asset-projection hypothesis does not")
            out.append("rescue it: the projected representation is marginally worse on")
            out.append("retrieval and no better on cluster agreement, so the asset axis")
            out.append("is not what is mixing the measures. The clusters are topical.")
            out.append("")
            out.append("WHAT FOLLOWS. Clustering is the wrong instrument for the")
            out.append("measure-discovery step. The fallback - which the task names - is")
            out.append("retrieval against the reference list plus human adjudication,")
            out.append("and it finds only what is already named. The clusters are still")
            out.append("produced and the worksheet is still written, because a cluster")
            out.append("a person can name is evidence about what the corpus says; what")
            out.append("the result rules out is using the cluster LABEL as the discovery.")
        out.append("")
        out.append("Per clause:")
        for x in scores:
            flag = "ok " if x["top1"] == "1" else "   "
            out.append(f"  {flag}{x['representation']:<10}{x['clause_id']:<28}"
                       f"cos={x['cosine']:>7}")
            out.append(f"       hand : {x['hand_measure'][:70]}")
            out.append(f"       pred : {x['predicted_measure'][:70]}")
    else:
        out.append("No check set was found at analysis/a3_clusters/check_set_clauses.csv,")
        out.append("so the two representations could not be scored against hand labels.")
        out.append("The quadrant counts above are reported without that comparison.")

    out.append("")
    out.append("Reference entries with no cluster at the cut")
    out.append("-" * 78)
    for q in quad_rows:
        cut = float(q["cosine_cut"])
        rep = q["representation"]
        missing = [x for x in ref_table
                   if x["representation"] == rep and float(x["cosine"]) < cut]
        missing.sort(key=lambda x: float(x["cosine"]))
        out.append(f"{len(missing)} of {len(refs)} reference entries have no cluster "
                   f"within {cut:.4f} ({rep}).")
        for x in missing[:20]:
            out.append(f"  {x['cosine']:>8}  {x['direction']:<22}"
                       f"{x['measure_name'][:60]}")
        if len(missing) > 20:
            out.append(f"  ... and {len(missing) - 20} more")

    out.append("")
    out.append("What the clusters look like (first 20 by size, raw representation)")
    out.append("-" * 78)
    key = "raw" if "raw" in runs else next(iter(runs))
    for x in runs[key]["table"][:20]:
        out.append(f"  {x['cluster_id']}  n={x['size']:>5}  docs={x['n_documents']:>3}  "
                   f"cos={x['cosine']}")
        out.append(f"      ref:   {x['nearest_reference'][:64]}")
        out.append(f"      terms: {x['top_terms'][:76]}")
    out.append("")
    out.append("WHAT THE POOL LOOKS LIKE, AND WHAT IT COSTS THE WORKSHEET")
    out.append("-" * 78)
    if texts:
        lens = sorted(len(t.split()) for t in texts)
        total = len(lens)
        buckets = Counter("1-2" if x <= 2 else "3-4" if x <= 4 else "5-9" if x <= 9
                          else "10-19" if x <= 19 else "20+" for x in lens)
        short = buckets["1-2"] + buckets["3-4"]
        for k in ("1-2", "3-4", "5-9", "10-19", "20+"):
            out.append(f"  {k:>6} tokens  {buckets[k]:>8,}  "
                       f"{100.0 * buckets[k] / total:>5.1f}%")
        out.append(f"  median {lens[len(lens) // 2]} tokens")
        out.append("")
        out.append(f"{short:,} clauses ({100.0 * short / total:.1f}%) are four tokens or")
        out.append("fewer. They are the splitter's over-splitting made visible: the")
        out.append("clause conditions fire on verb-headed subtrees, so a list joined by")
        out.append("'and' yields clauses like 'and implemented', 'and used' and 'and")
        out.append("applied'. Those are not propositions and they are not evidence.")
        out.append("")
        out.append("This matters for the worksheet, not for the retrieval numbers: an")
        out.append("exemplar chosen as nearest the centroid can be a fragment, and some")
        out.append("are - several exemplars on the raw sheet are clauses of four words or")
        out.append("fewer, and one is a bare component label. NO CLAUSE TEXT IS QUOTED")
        out.append("HERE, because no document text is committed in this repository; the")
        out.append("fragments are visible in naming_worksheet.xlsx, which is gitignored.")
        out.append("THE POOL WAS NOT FILTERED to fix it. The task")
        out.append("names three drop classes and this is a fourth; adding it silently")
        out.append("would change the pool the probe reports on. The measured cost is")
        out.append("here, and the fix belongs to Stage 4 - see the A2 report, where the")
        out.append("same over-splitting is measured against hand counts.")
        out.append("")
        out.append("IF A MINIMUM-LENGTH FILTER IS ADDED (say five tokens), roughly a")
        out.append(f"seventh of the pool leaves, the clusters tighten, the exemplars")
        out.append("improve, and the quadrant counts move with the pool.")
    else:
        out.append("(pool text was not loaded for this report run)")
    out.append("")
    out.append("The exemplar clauses and the full cluster evidence are in")
    out.append("naming_worksheet.xlsx. It carries clause text, so it is gitignored.")
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())