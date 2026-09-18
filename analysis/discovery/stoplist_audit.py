#!/usr/bin/env python3
"""Step 5's adversarial half: read the stoplist and look for real measures in it.

Reads  analysis/discovery/stoplist.csv, communities.csv
       data/clause_emb.npy, data/clause_emb_index.csv
       data/intermediate/prepared/clauses.csv, data/paragraphs.csv
       analysis/discovery/reduced_*.npy        (the cached reduction)
Writes analysis/discovery/stoplist_audit.md      CLAUSE TEXT - gitignored

  python3 analysis/discovery/stoplist_audit.py --data data

WHY THIS FILE EXISTS. The junk sieve is section provenance plus project spread,
and it decides what a person is allowed to see. A sieve that is wrong in the
direction of discarding throws away signal silently: the communities it drops
never reach shortlist.md, so nobody reads them and nobody notices. The check is
to pull a sample of stoplisted communities and read their medoids - the clauses
nearest each centroid, which is what a person would name the community from.

THE LABELS ARE REBUILT, NOT STORED. communities.csv carries ids and scores but
not membership, so this re-runs the same clustering the report describes:
identical matrix, identical cached reduction, identical HDBSCAN settings and
seed. `--min-cluster-size` must therefore name the run whose stoplist is being
audited. IF THE REDUCTION CACHE IS MISSING, this recomputes it - the same
twelve-to-sixty minutes the discovery run pays.

NO COMMITTED TEXT. The medoids land in stoplist_audit.md, which is gitignored
for the same reason shortlist.md is; stdout carries ids, sizes and sections
only.
"""
import argparse
import csv
import os
import random
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "analysis", "discovery"))

import discover as D  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "analysis", "discovery"))
    ap.add_argument("--min-cluster-size", type=int, default=25)
    ap.add_argument("--min-samples", type=int, default=5)
    ap.add_argument("--pca-dims", type=int, default=100)
    ap.add_argument("--umap-dims", type=int, default=10)
    ap.add_argument("--no-umap", action="store_true")
    ap.add_argument("--n", type=int, default=10, help="communities to pull")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--medoids", type=int, default=5)
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    emb_path = os.path.join(args.data, "clause_emb.npy")
    idx_path = os.path.join(args.data, "clause_emb_index.csv")
    cl_path = os.path.join(args.data, "intermediate", "prepared", "clauses.csv")
    par_path = os.path.join(args.data, "paragraphs.csv")
    stop_path = os.path.join(args.out_dir, "stoplist.csv")

    ids = D.read_clause_index(idx_path)
    vecs = np.load(emb_path, mmap_mode="r")
    clause_rows = D.read_clauses(cl_path)
    paras = D.read_paragraphs(par_path)

    keep = [i for i, c in enumerate(ids)
            if c in clause_rows and clause_rows[c][0] in paras]
    sub_ids = [ids[i] for i in keep]
    sub_vecs = np.asarray(vecs[keep], dtype=np.float32)
    log(f"clauses {len(ids):,}  clustered {len(keep):,}")

    x, cache = D.reduce_vectors(sub_vecs, args.out_dir, args.pca_dims,
                                args.umap_dims, not args.no_umap, log)
    labels, _model = D.cluster(x, args.min_cluster_size, args.min_samples, log)

    groups = {}
    for i, lab in enumerate(labels):
        if lab >= 0:
            groups.setdefault(int(lab), []).append(i)

    with open(stop_path, newline="", encoding="utf-8") as fh:
        stop = list(csv.DictReader(fh))
    with open(os.path.join(args.out_dir, "communities.csv"), newline="",
              encoding="utf-8") as fh:
        comm = {r["community_id"]: r for r in csv.DictReader(fh)}

    rng = random.Random(args.seed)
    picked = sorted(rng.sample([r["community_id"] for r in stop],
                               min(args.n, len(stop))))
    log(f"stoplist {len(stop)} communities; pulled {len(picked)} at seed {args.seed}")

    texts = D.clause_texts(clause_rows, paras, args.data, set(sub_ids))

    lines = [f"# Stoplist audit - {len(picked)} of {len(stop)} junk-flagged "
             f"communities", "",
             "Read the medoids. A stoplisted community whose clauses state a "
             "technique is signal the sieve discarded.", "",
             f"run: min_cluster_size={args.min_cluster_size} "
             f"min_samples={args.min_samples} "
             f"reduction={'PCA only' if args.no_umap else 'PCA + UMAP'} "
             f"seed={D.SEED}", ""]
    log("")
    log(f"{'community':<11}{'size':>7}{'proj':>6}{'junk':>7}  top_section")
    for cid in picked:
        lab = int(cid[1:])
        idx = groups.get(lab, [])
        r = comm.get(cid, {})
        log(f"{cid:<11}{r.get('size', '?'):>7}{r.get('n_projects', '?'):>6}"
            f"{str(r.get('junk_section_share', '?')):>7}  "
            f"{str(r.get('top_section', ''))[:50]}")
        lines.append(f"## {cid}  n={r.get('size')}  projects={r.get('n_projects')}"
                     f"  junk_section_share={r.get('junk_section_share')}"
                     f"  reason={r.get('verdict_reason')}")
        lines.append("")
        lines.append(f"- top_section: {r.get('top_section')}  |  "
                     f"asset: {r.get('asset_top')}  |  "
                     f"commitment_share: {r.get('commitment_share')}  |  "
                     f"hazard: {r.get('hazard_proximity')}")
        lines.append("")
        if not idx:
            lines.append("  (no members in this rebuild - the settings do not "
                         "match the run that wrote stoplist.csv)")
            lines.append("")
            continue
        v = D.l2(sub_vecs[idx].astype(np.float32))
        centroid = D.l2(v.mean(axis=0, keepdims=True))[0]
        order = np.argsort(-(v @ centroid))[:args.medoids]
        for k in order:
            t = " ".join((texts.get(sub_ids[idx[int(k)]]) or "").split())
            if t:
                lines.append(f"  - {t[:300]}")
        lines.append("")

    path = os.path.join(args.out_dir, "stoplist_audit.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log("")
    log(f"wrote {path}  (carries clause text, gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
