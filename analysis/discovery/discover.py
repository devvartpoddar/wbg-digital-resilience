#!/usr/bin/env python3
"""Discover measure and asset communities in the clause embedding space.

WHAT THIS REPLACES AND WHY. The first attempt reduced clause vectors with PCA
and partitioned them with k-means at k=320. That pair is matched to itself and
mismatched to this problem: PCA keeps the directions of greatest global
variance, k-means forces every clause into a sphere, and "every clause" here
includes the fiduciary, audit and disbursement prose that is two thirds of the
corpus. The run was read as a negative result for clustering. It was not - the
measure communities were in it (aerial/underground/outages/poles; waterproof
coverings/resistant designs; battery/solar/cooling; trenching; redundancy and
continuity) and so were the asset-vocabulary communities, including one that
grouped CNII, CERT, CSIRT, SOC and CIRT without anyone writing a synonym list.
They were buried, and the acceptance test could not see them.

SO THE METHOD CHANGES, NOT THE GOAL. UMAP preserves local neighbourhood
structure rather than global variance, and HDBSCAN finds variable-density
clusters and LABELS THE REST AS NOISE. The noise class does much of the sieve
without anyone deciding anything, and the condensed tree gives nested
granularity - families coarse, measures fine - which a single k cannot.

WHAT A MEASURE LOOKS LIKE IN THIS SPACE. A measure is a technique that several
projects commit to in near-identical language, so it is dense and it recurs
across projects. That is the whole bet. If the tree gives a "fiber" community
but never an "underground routing" community beneath it, this fails the same way
k-means did and no resolution tuning rescues it. `--stage check` answers that
question on a sample, in minutes, before anything else runs.

SIX SCORES PER COMMUNITY, none of which needs a trained model and all of which
are countable:

  project_spread     a measure recurs; a one-project community is boilerplate
  asset_profile      which assets its paragraphs touch, and how spread out
  commitment_share   "the project will bury the cable" vs "cables here flood"
  junk_section_share where its paragraphs sit - Fiduciary vs Project Components
  template_score     dense AND in most documents - Bank template language
  hazard_proximity   distance to a hazard anchor set; a score, never a gate

NO GENERATIVE MODEL TOUCHES A ROW (rule GV-10). Clustering is UMAP plus HDBSCAN
over pinned embeddings with fixed seeds. Naming a community is a person's job,
and this script emits the evidence for that and stops.

NO COMMITTED TEXT. communities.csv and stoplist.csv carry ids and numbers.
Medoid clause text is resolved from data/clean/ at report time and lands only in
shortlist.md, which is gitignored for that reason.

DETERMINISM. Fixed seeds throughout. UMAP with random_state is single-threaded
by construction, which is slower and reproducible; the repo requires the second.
Reductions are cached to .npy keyed on the input checksum, so re-running the
scoring does not re-run the reduction.
"""
import argparse, csv, hashlib, json, os, subprocess, sys, time
from collections import Counter, defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

SEED = 1
DISCOVERY_VERSION = "discover-1"

# The prose set, named as src/segment.py and embed_clauses.py name it. A clause
# exists only for these blocks, so this is the scope the junk sieve can act on;
# the section inventory is printed once over every paragraph and once over this.
PROSE_BLOCKS = ("narrative", "annex")

# Sections whose prose cannot carry a financed commitment. This is a list of
# SECTION NAMES, not of words to look for in clause text: where a document puts
# a paragraph is a fact about the document, and the inventory of distinct
# section titles is small enough to audit. The report prints the full inventory
# with the matched flag so this list can be checked rather than trusted.
JUNK_SECTION_PATTERNS = (
    "fiduciary", "implementation arrangement", "key risk", "procurement",
    "financial management", "environmental and social", "audit",
    "disbursement", "institutional and implementation",
    "lessons learned", "results framework", "monitoring and evaluation",
    "project status and rationale", "description of proposed changes",
)

# A lexical stand-in for Stage 7 modality, used because clauses.csv stores no
# verb. It tests REGISTER, not topic: an agent cue plus a modal or future. A
# real modality pass replaces it and will disagree at the margins; the point
# here is to separate a commitment from a description of the world, and that
# distinction survives a crude test.
AGENT_CUES = ("the project", "the program", "the programme", "the borrower",
              "the recipient", "the bank", "this component", "this subcomponent",
              "the operator", "operators", "the government", "the ministry")
MODAL_CUES = (" will ", " shall ", " must ", " is required to ", " are required to ",
              " would ", " to be ", " is expected to ", " are expected to ",
              " commits to ", " undertakes to ")

# Short hazard and continuity phrases. Their centroid is the anchor for
# hazard_proximity. Deliberately about HAZARD AND CONTINUITY rather than about
# any asset, so the score does not just re-measure topic.
HAZARD_ANCHORS = (
    "flooding and inundation damage",
    "cyclone, typhoon and high wind damage",
    "storm surge and coastal erosion",
    "landslide and slope failure",
    "seismic loading and ground movement",
    "extreme heat and temperature stress",
    "site-specific climate risk assessment",
    "withstand damage from extreme weather events",
    "maintain service continuity during a disaster",
    "restore service after an outage",
)

COMMUNITY_COLS = [
    "community_id", "size", "n_paragraphs", "n_documents", "n_projects",
    "project_spread", "asset_top", "asset_spread", "commitment_share",
    "junk_section_share", "top_section", "template_score", "hazard_proximity",
    "mean_cos_to_centroid", "nearest_reference_id", "nearest_reference_cos",
    "verdict", "verdict_reason", "run_id", "discovery_version",
]


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "nogit"


def norm(txt):
    return " " + " ".join((txt or "").lower().split()) + " "


# --------------------------------------------------------------------- loading

def read_clause_index(path):
    """clause_id -> row number in clause_emb.npy, in matrix order."""
    ids = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            ids.append(r.get("clause_id") or r.get("unit_id"))
    return ids


def read_clauses(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["clause_id"]] = (r["paragraph_id"], int(r["char_start"]),
                                   int(r["char_end"]), r.get("split_rule", ""))
    return out


def read_paragraphs(path):
    """paragraph_id -> (doc_id, block, section, project_ids, char_start)."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["paragraph_id"]] = (
                r["doc_id"],
                r.get("block_type") or r.get("block") or "",
                r.get("section_title") or r.get("section_label") or r.get("section_path") or "",
                tuple(p for p in (r.get("project_ids") or "").split("|") if p),
                int(r.get("char_start") or 0),
            )
    return out


def read_fired_assets(path, thresholds=None):
    """paragraph_id -> set of asset classes that fired. Provisional: the
    classifiers over-fire (see analysis/findings.md), so this is used only as a
    RELATIVE profile - is a community concentrated on one asset or spread over
    many - never as a label."""
    out = defaultdict(set)
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("fired") or "").lower() in ("true", "1"):
                out[r["paragraph_id"]].add(r["asset_id"])
    return out


def clause_texts(clause_rows, paras, data_dir, wanted):
    """Resolve clause text from data/clean/ for the given clause ids only.
    Never written to a committed file."""
    by_doc = defaultdict(list)
    for cid in wanted:
        pid, cs, ce, _ = clause_rows[cid]
        doc = paras[pid][0]
        by_doc[doc].append((cid, pid, cs, ce))
    out = {}
    for doc, items in by_doc.items():
        p = os.path.join(data_dir, "clean", f"{doc}.txt")
        if not os.path.exists(p):
            continue
        body = open(p, encoding="utf-8").read()
        for cid, pid, cs, ce in items:
            base = paras[pid][4]
            out[cid] = body[base + cs: base + ce]
    return out


# ------------------------------------------------------------------- reduction

def l2(mat):
    n = np.linalg.norm(mat, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return mat / n


def reduce_vectors(vecs, out_dir, pca_dims, umap_dims, use_umap, log):
    """L2 -> PCA -> UMAP. Cached on a checksum of the inputs and the settings,
    because the reduction is the slow part and the scoring is re-run often."""
    from sklearn.decomposition import PCA

    key = hashlib.sha256(
        f"{vecs.shape}|{pca_dims}|{umap_dims}|{use_umap}|{SEED}".encode()
    ).hexdigest()[:16]
    cache = os.path.join(out_dir, f"reduced_{key}.npy")
    if os.path.exists(cache):
        log(f"reduction cache hit {os.path.basename(cache)}")
        return np.load(cache), cache

    x = l2(vecs.astype(np.float32))
    d = min(pca_dims, x.shape[1], x.shape[0])
    log(f"PCA {x.shape[1]} -> {d}")
    x = PCA(n_components=d, random_state=SEED).fit_transform(x)

    if use_umap:
        import umap
        log(f"UMAP {d} -> {umap_dims}  (random_state set, single-threaded)")
        x = umap.UMAP(n_components=umap_dims, n_neighbors=30, min_dist=0.0,
                      metric="cosine", random_state=SEED).fit_transform(x)
    x = np.ascontiguousarray(x, dtype=np.float32)
    np.save(cache, x)
    return x, cache


def cluster(x, min_cluster_size, min_samples, log):
    from sklearn.cluster import HDBSCAN
    log(f"HDBSCAN min_cluster_size={min_cluster_size} min_samples={min_samples}")
    h = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples,
                metric="euclidean", store_centers="medoid", copy=True)
    labels = h.fit_predict(x)
    return labels, h


# ---------------------------------------------------------------------- scoring

def is_junk_section(section):
    s = (section or "").lower()
    return any(p in s for p in JUNK_SECTION_PATTERNS)


def commitment_shaped(text):
    t = norm(text)
    return any(a in t for a in AGENT_CUES) and any(m in t for m in MODAL_CUES)


def score_communities(labels, vecs_full, ids, clause_rows, paras, fired,
                      texts, ref_vecs, ref_ids, hazard_centroid, n_projects_total,
                      n_docs_total):
    """One row per community. vecs_full is the ORIGINAL 3072-dim matrix: cosine
    is measured in the space the model produced, not in the reduced space that
    only exists to make the clustering tractable."""
    groups = defaultdict(list)
    for i, lab in enumerate(labels):
        if lab >= 0:
            groups[int(lab)].append(i)

    rows = []
    for lab in sorted(groups):
        idx = groups[lab]
        v = l2(vecs_full[idx].astype(np.float32))
        centroid = l2(v.mean(axis=0, keepdims=True))[0]
        cos_to_centroid = float((v @ centroid).mean())

        pids, docs, projects, sections, junk, commit, assets = (
            set(), set(), set(), Counter(), 0, 0, Counter())
        n_with_text = 0
        for i in idx:
            cid = ids[i]
            pid = clause_rows[cid][0]
            pids.add(pid)
            meta = paras.get(pid)
            if not meta:
                continue
            docs.add(meta[0])
            projects.update(meta[3])
            sections[meta[2]] += 1
            if is_junk_section(meta[2]):
                junk += 1
            for a in fired.get(pid, ()):
                assets[a] += 1
            t = texts.get(cid)
            if t is not None:
                n_with_text += 1
                if commitment_shaped(t):
                    commit += 1

        # Spread over assets, normalised so 0 is one asset and 1 is even across
        # all of them. Uses the provisional fired classes - relative only.
        if assets:
            p = np.array(list(assets.values()), dtype=float)
            p /= p.sum()
            ent = float(-(p * np.log(p + 1e-12)).sum())
            asset_spread = ent / np.log(len(assets)) if len(assets) > 1 else 0.0
        else:
            asset_spread = 0.0

        nr_id, nr_cos = "", 0.0
        if ref_vecs is not None and len(ref_vecs):
            sims = ref_vecs @ centroid
            j = int(np.argmax(sims))
            nr_id, nr_cos = ref_ids[j], float(sims[j])

        hazard = float(centroid @ hazard_centroid) if hazard_centroid is not None else 0.0

        rows.append({
            "community_id": f"c{lab:04d}",
            "size": len(idx),
            "n_paragraphs": len(pids),
            "n_documents": len(docs),
            "n_projects": len(projects),
            "project_spread": round(len(projects) / max(n_projects_total, 1), 4),
            "asset_top": assets.most_common(1)[0][0] if assets else "",
            "asset_spread": round(asset_spread, 4),
            "commitment_share": round(commit / n_with_text, 4) if n_with_text else "",
            "junk_section_share": round(junk / len(idx), 4),
            "top_section": sections.most_common(1)[0][0] if sections else "",
            # Dense AND in most documents: same words everywhere is template,
            # not commitment. A ranking signal - measures also recur, so this
            # never decides alone.
            "template_score": round(cos_to_centroid * (len(docs) / max(n_docs_total, 1)), 4),
            "hazard_proximity": round(hazard, 4),
            "mean_cos_to_centroid": round(cos_to_centroid, 4),
            "nearest_reference_id": nr_id,
            "nearest_reference_cos": round(nr_cos, 4),
        })
    return rows, groups


def apply_sieve(rows, junk_share, min_projects):
    """Programmatic. Nobody reads a community to find out it is junk."""
    for r in rows:
        why = []
        if r["junk_section_share"] >= junk_share:
            why.append(f"junk_section_share>={junk_share}")
        if r["n_projects"] < min_projects:
            why.append(f"n_projects<{min_projects}")
        r["verdict"] = "junk" if why else "shortlist"
        r["verdict_reason"] = ";".join(why)
    return rows


# ---------------------------------------------------------------------- anchors

def embed_anchors(texts, data_dir, key_file, log):
    """The 89 reference measures and the hazard anchors, through the same seam
    and the same content-addressed cache as the corpus. ~100 texts."""
    import embed as E
    manifest = json.load(open(os.path.join(ROOT, "meta", "embedding_manifest.json")))
    model, dim = manifest["model_requested"], manifest["dimensions"]
    root = E.cache_root(data_dir, model, dim)
    shas = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
    out, todo = {}, []
    for t, s in zip(texts, shas):
        v = E.read_cached(root, s, dim)
        if v is None:
            todo.append((t, s))
        else:
            # read_cached returns the stored BYTES, not a vector; src/embed.py's
            # assemble unpacks them the same way. np.asarray(bytes) yields a
            # 0-d array, so a cached anchor and a freshly fetched one cannot be
            # stacked - and vstack is what reports it, after the fetch has been
            # paid for.
            out[s] = np.frombuffer(v, dtype="<f4")
    if todo:
        log(f"embedding {len(todo)} anchor texts (of {len(texts)})")
        key = E.read_key(key_file)
        # embed_texts returns (vectors, meta), as src/embed.py's own
        # fetch_missing unpacks it. Taking the tuple for the vector list packs
        # the list itself as one vector and dies on the first anchor with
        # "expected 3072 dimensions, got 10" - which the caller below catches,
        # so the failure is silent: every community loses its hazard score and
        # the shortlist falls back to size.
        vecs, _meta = E.embed_texts([t for t, _ in todo], model=model,
                                    api_key=key, base_url=manifest["base_url"],
                                    dimensions=dim)
        for (t, s), v in zip(todo, vecs):
            E.write_cached(root, s, E.pack(v, dim))
            out[s] = np.asarray(v, dtype=np.float32)
    return np.vstack([out[s] for s in shas])


# ------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "analysis", "discovery"))
    ap.add_argument("--stage", default="all",
                    choices=["check", "all"],
                    help="check = a sample, to answer the make-or-break question fast")
    ap.add_argument("--sample", type=int, default=0, help="cluster a random sample")
    ap.add_argument("--min-cluster-size", type=int, default=25)
    ap.add_argument("--min-samples", type=int, default=5)
    ap.add_argument("--pca-dims", type=int, default=100)
    ap.add_argument("--umap-dims", type=int, default=10)
    ap.add_argument("--no-umap", action="store_true",
                    help="HDBSCAN straight off the PCA output - faster, cruder")
    ap.add_argument("--junk-share", type=float, default=0.6)
    ap.add_argument("--min-projects", type=int, default=3)
    ap.add_argument("--medoids", type=int, default=5)
    ap.add_argument("--key-file", default=os.environ.get("EMBED_KEY_FILE", ""))
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    run_id = args.run_id or time.strftime("%Y%m%dT%H%M", time.gmtime()) + "-" + git_sha()
    os.makedirs(args.out_dir, exist_ok=True)
    log_lines = []

    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        log_lines.append(msg)

    log(f"run_id {run_id}  discovery_version {DISCOVERY_VERSION}  seed {SEED}")

    emb_path = os.path.join(args.data, "clause_emb.npy")
    idx_path = os.path.join(args.data, "clause_emb_index.csv")
    cl_path = os.path.join(args.data, "intermediate", "prepared", "clauses.csv")
    par_path = os.path.join(args.data, "paragraphs.csv")
    if not os.path.exists(cl_path):
        cl_path = os.path.join(args.data, "clauses.csv")

    for p in (emb_path, idx_path, cl_path, par_path):
        if not os.path.exists(p):
            print(f"discover: missing {p}", file=sys.stderr)
            return 2

    ids = read_clause_index(idx_path)
    vecs = np.load(emb_path, mmap_mode="r")
    if vecs.shape[0] != len(ids):
        print(f"discover: matrix has {vecs.shape[0]} rows, index lists {len(ids)}"
              " - different runs", file=sys.stderr)
        return 2
    clause_rows = read_clauses(cl_path)
    paras = read_paragraphs(par_path)
    fired = read_fired_assets(os.path.join(args.data, "intermediate", "detect",
                                           "paragraph_asset.csv"))
    log(f"clauses {len(ids):,}  vectors {vecs.shape}  paragraphs {len(paras):,}")

    keep = [i for i, c in enumerate(ids)
            if c in clause_rows and clause_rows[c][0] in paras]
    if args.sample and args.sample < len(keep):
        rng = np.random.default_rng(SEED)
        keep = sorted(rng.choice(keep, size=args.sample, replace=False).tolist())
    log(f"clustering {len(keep):,} clauses"
        + (f" (sampled from {len(ids):,})" if args.sample else ""))

    sub_ids = [ids[i] for i in keep]
    sub_vecs = np.asarray(vecs[keep], dtype=np.float32)

    x, cache = reduce_vectors(sub_vecs, args.out_dir, args.pca_dims,
                              args.umap_dims, not args.no_umap, log)
    labels, model = cluster(x, args.min_cluster_size, args.min_samples, log)

    n_comm = len({int(l) for l in labels if l >= 0})
    noise = float((labels < 0).mean())
    log(f"communities {n_comm}  noise {noise:.1%}")

    # The failure mode that matters: one giant cluster, or everything noise.
    # Only meaningful at scale: a 600-clause check run legitimately yields
    # a handful of communities.
    if len(keep) >= 5000 and (n_comm < 20 or noise > 0.80):
        log("DEGENERATE: fewer than 20 communities or over 80% noise. HDBSCAN "
            "has collapsed on this space. Do not tune in circles - report this, "
            "then try Leiden over a cosine k-NN graph (k=30) as the fallback "
            "named in the plan.")

    # Anchors, then scoring. Cosine is measured in the full 3072-dim space.
    ref_ids, ref_texts = [], []
    ref_path = os.path.join(ROOT, "inputs", "taxonomy", "reference_measures.csv")
    if os.path.exists(ref_path):
        for r in csv.DictReader(open(ref_path, newline="", encoding="utf-8")):
            ref_ids.append(r["reference_measure_id"])
            ref_texts.append(f"{r['measure_name']}. {r['definition']}")

    ref_vecs = hazard_centroid = None
    try:
        anchor = embed_anchors(list(HAZARD_ANCHORS) + ref_texts, args.data,
                               args.key_file, log)
        hazard_centroid = l2(anchor[:len(HAZARD_ANCHORS)].mean(axis=0, keepdims=True))[0]
        ref_vecs = l2(anchor[len(HAZARD_ANCHORS):]) if ref_texts else None
    except (Exception, SystemExit) as exc:                      # noqa: BLE001
        # src/embed.py exits on an HTTP error rather than raising, and
        # SystemExit is not an Exception. Catching only Exception here means a
        # missing key file kills the whole run after the clustering has already
        # been paid for. The anchors are optional; the run is not.
        log(f"anchors unavailable ({exc.__class__.__name__}: {exc}); "
            "hazard_proximity and nearest_reference are left empty. Everything "
            "else is unaffected.")

    wanted = set(sub_ids)
    texts = clause_texts(clause_rows, paras, args.data, wanted)
    log(f"resolved text for {len(texts):,} of {len(wanted):,} clauses")

    n_projects_total = len({p for m in paras.values() for p in m[3]})
    n_docs_total = len({m[0] for m in paras.values()})
    rows, groups = score_communities(
        labels, sub_vecs, sub_ids, clause_rows, paras, fired, texts,
        ref_vecs, ref_ids, hazard_centroid, n_projects_total, n_docs_total)
    rows = apply_sieve(rows, args.junk_share, args.min_projects)
    for r in rows:
        r["run_id"] = run_id
        r["discovery_version"] = DISCOVERY_VERSION

    comm_path = os.path.join(args.out_dir, "communities.csv")
    with open(comm_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COMMUNITY_COLS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: -r["size"]))
    stop = [r for r in rows if r["verdict"] == "junk"]
    with open(os.path.join(args.out_dir, "stoplist.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["community_id", "size", "top_section",
                                           "junk_section_share", "n_projects",
                                           "verdict_reason", "run_id"])
        w.writeheader()
        for r in stop:
            w.writerow({k: r[k] for k in w.fieldnames})
    log(f"wrote communities.csv ({len(rows)}) and stoplist.csv ({len(stop)})")

    write_shortlist(rows, groups, sub_ids, sub_vecs, texts, args, run_id)
    write_report(rows, labels, paras, args, run_id, n_comm, noise, cache, log_lines)
    return 0


def write_shortlist(rows, groups, sub_ids, sub_vecs, texts, args, run_id):
    """Medoid clauses per surviving community, for naming. CARRIES CLAUSE TEXT,
    so it is gitignored. A person names a community from real sentences; a term
    list is not enough to name anything."""
    keep = [r for r in rows if r["verdict"] == "shortlist"]
    keep.sort(key=lambda r: (-r["hazard_proximity"], -r["size"]))
    path = os.path.join(args.out_dir, "shortlist.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# Communities to name — run {run_id}\n\n")
        fh.write(f"{len(keep)} of {len(rows)} communities survived the sieve, "
                 "ranked by hazard proximity then size. Five clauses nearest "
                 "each centroid.\n\n")
        fh.write("For each: name it, or mark it junk and say which signal "
                 "should have caught it.\n\n")
        by_id = {r["community_id"]: r for r in rows}
        for r in keep:
            lab = int(r["community_id"][1:])
            idx = groups[lab]
            v = l2(sub_vecs[idx].astype(np.float32))
            centroid = l2(v.mean(axis=0, keepdims=True))[0]
            order = np.argsort(-(v @ centroid))[:args.medoids]
            fh.write(f"## {r['community_id']}  n={r['size']}  "
                     f"projects={r['n_projects']}  "
                     f"hazard={r['hazard_proximity']}  "
                     f"commitment={r['commitment_share']}\n\n")
            fh.write(f"- section: {r['top_section']}  |  asset: {r['asset_top']}"
                     f"  |  nearest reference: {r['nearest_reference_id']} "
                     f"({r['nearest_reference_cos']})\n\n")
            for k in order:
                t = " ".join((texts.get(sub_ids[idx[int(k)]]) or "").split())
                if t:
                    fh.write(f"  - {t[:300]}\n")
            fh.write("\n")
    return path


def write_report(rows, labels, paras, args, run_id, n_comm, noise, cache, log_lines):
    out = ["discovery — measure and asset communities in the clause space",
           "=" * 78, "",
           "ASSUMPTIONS", "-" * 78, "",
           "1. asset_profile uses the EXISTING fired asset classes, which over-fire",
           "   (analysis/findings.md). It is read as a RELATIVE spread - is this",
           "   community concentrated on one asset or spread across many - and never",
           "   as an asset label. IF THE CLASSIFIER IS REPLACED, asset_spread moves",
           "   and nothing else in this table does.", "",
           "2. commitment_share is a LEXICAL stand-in for Stage 7 modality, because",
           "   clauses.csv stores no verb. It tests register - an agent cue plus a",
           "   modal - not topic. A real modality pass will disagree at the margins.", "",
           "3. The junk sieve is section provenance plus project spread. Section",
           "   provenance is a fact about where the document puts a paragraph, not a",
           "   judgement about what it says. The full section inventory is below so",
           "   the pattern list can be audited.", "",
           "4. template_score never decides alone: measures recur across documents",
           "   too. It ranks, and section provenance separates.", "",
           "5. The section inventory below is printed in FULL - every distinct",
           "   title, over all paragraphs and again over prose only - because the",
           "   audit is of the pattern list, and a section that carries a financed",
           "   commitment while being flagged junk need not be a large one. A",
           "   section title is a column of paragraphs.csv rather than clause text,",
           "   and the tail of that column holds a document's first sentence rather",
           "   than a title, which is itself part of what the audit has to see. IF",
           "   NO DOCUMENT-DERIVED STRING WAS MEANT TO BE COMMITTED, this inventory",
           "   belongs in a gitignored file beside shortlist.md and the audit reads",
           "   it there; every JUNK flag and every count would be unchanged.", "",
           "6. The two scopes are printed separately because they are not the same",
           "   set: junk_section_share is computed over a community's clauses, and",
           "   a clause exists only for a narrative or annex paragraph. A JUNK flag",
           "   on a section holding no prose cannot move a verdict.", "",
           "RUN", "-" * 78, ""]
    out.append(f"run_id              {run_id}")
    out.append(f"discovery_version   {DISCOVERY_VERSION}")
    out.append(f"seed                {SEED}")
    out.append(f"reduction           {'PCA only' if args.no_umap else 'PCA + UMAP'}"
               f"  ({os.path.basename(cache)})")
    out.append(f"min_cluster_size    {args.min_cluster_size}")
    out.append(f"clauses clustered   {len(labels):,}")
    out.append(f"communities         {n_comm}")
    out.append(f"noise               {noise:.1%}")
    short = [r for r in rows if r["verdict"] == "shortlist"]
    out.append(f"shortlist           {len(short)}")
    out.append(f"stoplist            {len(rows) - len(short)}")
    out += ["", "THE QUESTION THIS RUN EXISTS TO ANSWER", "-" * 78, "",
            "Does a community appear at MEASURE granularity, or only at topic",
            "granularity? A 'fiber' community with no 'underground routing'",
            "community beneath it is the failure mode, and no amount of resolution",
            "tuning fixes it. Read the top of shortlist.md and decide.", "",
            "TOP OF THE SHORTLIST, by hazard proximity", "-" * 78, ""]
    for r in sorted(short, key=lambda r: (-r["hazard_proximity"], -r["size"]))[:25]:
        out.append(f"  {r['community_id']}  n={r['size']:>5}  proj={r['n_projects']:>3}  "
                   f"haz={r['hazard_proximity']:.3f}  commit={r['commitment_share']}  "
                   f"{str(r['top_section'])[:34]}")
    out += ["", "SECTION INVENTORY — audit the junk pattern list against this",
            "-" * 78, "",
            "Every distinct section title in paragraphs.csv, with the JUNK flag the",
            "sieve applies to it. The full list, not the top of it: a section that",
            "carries a financed commitment and is flagged junk is the one failure",
            "this list exists to make visible, and it need not be a large one.",
            ""]
    inv = Counter(m[2] for m in paras.values())
    for sec, n in inv.most_common():
        out.append(f"  {'JUNK' if is_junk_section(sec) else '    '}  {n:>6}  {sec[:90]}")
    out += ["", f"  {len(inv):,} distinct section titles, "
            f"{len(paras):,} paragraphs", "",
            "PROSE ONLY — the scope the sieve can actually act on",
            "-" * 78,
            "junk_section_share is computed over a community's clauses, and a",
            "clause exists only for a narrative or annex paragraph. A JUNK flag on",
            "a section holding no prose cannot move any community's verdict; it is",
            "listed here separately so the two scopes are not read as one.",
            ""]
    prose_inv = Counter(m[2] for m in paras.values() if m[1] in PROSE_BLOCKS)
    for sec, n in prose_inv.most_common():
        out.append(f"  {'JUNK' if is_junk_section(sec) else '    '}  {n:>6}  {sec[:90]}")
    out += ["", f"  {len(prose_inv):,} distinct section titles, "
            f"{sum(prose_inv.values()):,} prose paragraphs"]
    out += ["", "LOG", "-" * 78, ""] + [f"  {l}" for l in log_lines]
    path = os.path.join(args.out_dir, "discovery_report.txt")
    open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
