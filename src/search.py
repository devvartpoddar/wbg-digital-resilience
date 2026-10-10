#!/usr/bin/env python3
"""Score every search unit against every query of a label set, then filter.
Sentences are where we search, paragraphs where we work.

A label set is two files in inputs/taxonomy/, kept by people and only read
here:

  <set>.csv           one row per label: <x>_id, <x>_name, definition, active
  <set>_queries.csv   any number of search phrases per label: <x>_id,
                      query_id, query, active (optional file)

Every label is searched with its definition ("<name>: <definition>") and
with each of its phrases. A phrase finds what a definition is too broad to
rank first ("cable landing station", "tier III data centre"), so a label has
as many queries as it needs.

Scoring (search) is done once per version of the label set and keeps every
score; nothing is cut off there. Filtering (hits) reads the scores and keeps
what a filter asks for. So a filter can be tightened, loosened or compared
without embedding or scoring anything again.

  search    data/search/<set>-<version>/
              queries.csv     query_id, label_id, kind (definition | phrase),
                              text, text_sha256
              scores.npy      (units, queries) cosine similarities, float32,
                              rows in sentence_index.csv order
              manifest.json   what was run
            <version> is a SHA-256 over the query texts, the model, the
            dimensions and the sentence index: a revised phrase or definition
            is a new version, which embeds only what changed; an unchanged
            set is not scored again.

  hits      for each unit and label: the best score over the label's queries,
            the query that gave it, that score's percentile among all units
            for the same query, and how many of the label's queries rank the
            unit at or above --min-percentile. Kept when it passes every filter
            given:
              --min-score       cosine similarity
              --min-percentile  within its query: raw cosine is not comparable
                                between a short phrase and a long definition,
                                a percentile is
              --top             best N units per label
              --label, --project, --kind (sentence | footnote)
            Writes hits.csv and its paragraph roll-up paragraph_hits.csv.

No filter is the right one by default: which works is decided against
examples a person checked (AGENTS.md).

  python src/search.py assets                       # score
  python src/search.py assets --hits --min-percentile 99.5 --top 100
"""
import argparse, csv, hashlib, json, os, sys, time
from collections import defaultdict

import numpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import embed  # noqa: E402

TAXONOMY = os.path.join(ROOT, "inputs", "taxonomy")
QUERY_COLS = ["query_id", "label_id", "kind", "text", "text_sha256"]
CONTEXT = ["unit_id", "kind", "paragraph_id", "doc_id", "project_ids", "component_number",
           "subcomponent_number", "component_key"]
HIT_COLS = ["label_id", "rank", "score", "percentile", "best_query_id", "best_query",
            "queries_agreeing", "queries"] + CONTEXT
PARA_COLS = ["label_id", "paragraph_id", "doc_id", "project_ids", "component_number",
             "component_key", "best_score", "best_percentile", "best_unit_id",
             "best_query_id", "units_hit"]
# Rows of the sentence matrix scored at a time, so memory stays flat.
BLOCK = 20_000


# ------------------------------------------------------------------ the labels

def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _active(r):
    return (r.get("active") or "true").strip().lower() == "true"


def load_queries(name, folder=TAXONOMY):
    """[{query_id, label_id, kind, text, text_sha256}] for a label set: each
    active label's definition, then its active phrases."""
    path = os.path.join(folder, f"{name}.csv")
    rows = _rows(path)
    if not rows:
        raise SystemExit(f"search: {path} has no rows")
    id_col = next((c for c in rows[0] if c.endswith("_id")), None)
    name_col = next((c for c in rows[0] if c.endswith("_name")), None)
    if not id_col or not name_col or "definition" not in rows[0]:
        raise SystemExit(f"search: {path} needs an *_id, a *_name and a definition column")
    labels = [r for r in rows if _active(r)]
    ids = [r[id_col].strip() for r in labels]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"search: {path} repeats a label id")
    out = []
    for r in labels:
        lid = r[id_col].strip()
        text = f"{r[name_col].strip()}: {' '.join(r['definition'].split())}"
        out.append({"query_id": f"{lid}:definition", "label_id": lid,
                    "kind": "definition", "text": text})
    qpath = os.path.join(folder, f"{name}_queries.csv")
    if os.path.exists(qpath):
        seen = set()
        for r in _rows(qpath):
            if not _active(r):
                continue
            lid = r[id_col].strip()
            if lid not in ids:
                raise SystemExit(f"search: {qpath} names {lid!r}, which is not an active "
                                 f"label in {name}.csv")
            qid = f"{lid}:{r['query_id'].strip()}"
            if qid in seen:
                raise SystemExit(f"search: {qpath} repeats query {qid}")
            seen.add(qid)
            out.append({"query_id": qid, "label_id": lid, "kind": "phrase",
                        "text": " ".join(r["query"].split())})
    for q in out:
        q["text_sha256"] = hashlib.sha256(q["text"].encode("utf-8")).hexdigest()
    return out


def run_version(queries, model, dim, index_path):
    """A SHA-256 over everything a run's scores depend on."""
    h = hashlib.sha256()
    for q in queries:
        h.update(f"{q['query_id']}\t{q['text']}\n".encode())
    h.update(f"{model}:{dim}\n".encode())
    with open(index_path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:12]


# ----------------------------------------------------------------- the scoring

def query_vectors(queries, data, model, dim, base_url, api_key, log=print):
    """(Q, dim) unit vectors for the query texts, from the cache or embedded
    now (and cached)."""
    root = embed.cache_root(data, model, dim)
    unique = {q["text_sha256"]: q["text"] for q in queries}
    todo = [s for s in unique if embed.read_cached(root, s, dim) is None]
    if todo:
        if not api_key:
            raise SystemExit(f"search: {len(todo)} query text(s) are not embedded yet and "
                             f"${embed.KEY_VAR} is not set")
        _stats, failures = embed.fetch_missing(todo, unique, root, dim, model=model,
                                               api_key=api_key, base_url=base_url, log=log)
        if failures:
            raise SystemExit(f"search: {len(failures)} query text(s) failed to embed")
    out = numpy.zeros((len(queries), dim), dtype="float32")
    for i, q in enumerate(queries):
        out[i] = numpy.frombuffer(embed.read_cached(root, q["text_sha256"], dim), dtype="<f4")
    return out


def score_all(matrix, queries):
    """(units, queries) cosine similarities. Both sides are unit vectors."""
    n = matrix.shape[0]
    scores = numpy.empty((n, queries.shape[0]), dtype="float32")
    for start in range(0, n, BLOCK):
        block = numpy.asarray(matrix[start:start + BLOCK], dtype="float32")
        scores[start:start + block.shape[0]] = block @ queries.T
    return scores


def _paths(data):
    return (where(data, "embeddings", "sentence_emb.npy"),
            where(data, "embeddings", "sentence_index.csv"))


def run_folder(name, data, model=embed.DEFAULT_MODEL, dim=embed.DEFAULT_DIM,
               taxonomy=TAXONOMY):
    """(folder, queries) for the label set as it stands now."""
    queries = load_queries(name, taxonomy)
    npy, idx = _paths(data)
    if not (os.path.exists(npy) and os.path.exists(idx)):
        raise SystemExit("search: no sentence embeddings yet - run src/embed.py first")
    return where(data, "search", f"{name}-{run_version(queries, model, dim, idx)}"), queries


def search(name, data, model=embed.DEFAULT_MODEL, dim=embed.DEFAULT_DIM,
           base_url=embed.DEFAULT_BASE, api_key="", taxonomy=TAXONOMY, dry_run=False,
           log=print):
    """Score every unit against every query of the label set; returns the run
    folder (None on a dry run)."""
    out, queries = run_folder(name, data, model, dim, taxonomy)
    labels = len({q["label_id"] for q in queries})
    log(f"{name}: {labels} labels, {len(queries)} queries -> {out}")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log("  already scored")
        return out
    if dry_run:
        root = embed.cache_root(data, model, dim)
        new = sum(1 for q in queries if embed.read_cached(root, q["text_sha256"], dim) is None)
        log(f"  --dry-run: {new} query text(s) to embed; nothing written")
        return None
    npy, idx = _paths(data)
    matrix = numpy.load(npy, mmap_mode="r")
    with open(idx, newline="", encoding="utf-8") as fh:
        n_units = sum(1 for _ in csv.DictReader(fh))
    if matrix.shape[0] != n_units:
        raise SystemExit("search: sentence_emb.npy and sentence_index.csv disagree on rows")
    scores = score_all(matrix, query_vectors(queries, data, model, dim, base_url, api_key, log))
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    _write(os.path.join(part, "queries.csv"), QUERY_COLS, queries)
    numpy.save(os.path.join(part, "scores.npy"), scores)
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"label_set": name, "version": os.path.basename(out).rsplit("-", 1)[1],
                   "labels": labels, "queries": len(queries), "units": n_units,
                   "model": model, "dimensions": dim, "created_at":
                   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  fh, indent=2, sort_keys=True)
        fh.write("\n")
    # The folder appears whole or not at all: an interrupted run leaves only
    # the .part folder, which the next run overwrites.
    os.replace(part, out)
    log("  scored")
    return out


# ---------------------------------------------------------------- the filters

def percentiles(scores):
    """Each score's percentile among all units for the same query (0-100],
    ties sharing the higher rank."""
    n = scores.shape[0]
    out = numpy.empty_like(scores)
    for j in range(scores.shape[1]):
        col = scores[:, j]
        order = numpy.sort(col)
        out[:, j] = 100.0 * numpy.searchsorted(order, col, side="right") / n
    return out


def hits(folder, data, min_score=None, min_percentile=None, top=None, labels=None,
         projects=None, kinds=None):
    """The units that pass every filter given, per label, best first."""
    queries = _rows(os.path.join(folder, "queries.csv"))
    scores = numpy.load(os.path.join(folder, "scores.npy"))
    pct = percentiles(scores)
    index = _rows(_paths(data)[1])
    by_label = defaultdict(list)
    for j, q in enumerate(queries):
        by_label[q["label_id"]].append(j)
    out = []
    for lid in sorted(by_label):
        if labels and lid not in labels:
            continue
        cols = by_label[lid]
        sub = scores[:, cols]
        best_col = sub.argmax(axis=1)
        best = sub[numpy.arange(len(sub)), best_col]
        best_pct = pct[:, cols][numpy.arange(len(sub)), best_col]
        agree = (pct[:, cols] >= (min_percentile or 100.0)).sum(axis=1) \
            if min_percentile is not None else numpy.zeros(len(sub), dtype=int)
        keep = numpy.ones(len(sub), dtype=bool)
        if min_score is not None:
            keep &= best >= min_score
        if min_percentile is not None:
            keep &= best_pct >= min_percentile
        rows = [i for i in numpy.nonzero(keep)[0].tolist()
                if (not projects or set(index[i]["project_ids"].split("|")) & set(projects))
                and (not kinds or index[i]["kind"] in kinds)]
        # Best first; ties by row, so the same filter gives the same list.
        rows.sort(key=lambda i: (-float(best[i]), i))
        if top:
            rows = rows[:top]
        for rank, i in enumerate(rows, 1):
            q = queries[cols[best_col[i]]]
            out.append(dict({c: index[i][c] for c in CONTEXT}, label_id=lid, rank=rank,
                            score=f"{best[i]:.6f}", percentile=f"{best_pct[i]:.3f}",
                            best_query_id=q["query_id"], best_query=q["text"],
                            queries_agreeing=int(agree[i]), queries=len(cols)))
    return out


def paragraph_rollup(rows):
    """paragraph_hits rows: per label and paragraph, its best unit."""
    best, count = {}, defaultdict(int)
    for h in rows:
        key = (h["label_id"], h["paragraph_id"])
        count[key] += 1
        if key not in best or float(h["score"]) > float(best[key]["score"]):
            best[key] = h
    out = [{"label_id": h["label_id"], "paragraph_id": h["paragraph_id"],
            "doc_id": h["doc_id"], "project_ids": h["project_ids"],
            "component_number": h["component_number"], "component_key": h["component_key"],
            "best_score": h["score"], "best_percentile": h["percentile"],
            "best_unit_id": h["unit_id"], "best_query_id": h["best_query_id"],
            "units_hit": count[key]} for key, h in best.items()]
    out.sort(key=lambda r: (r["label_id"], -float(r["best_score"]), r["paragraph_id"]))
    return out


def filter_name(min_score=None, min_percentile=None, top=None, labels=None, projects=None,
                kinds=None):
    """A file-name tag for a filter, so different filters sit side by side."""
    parts = [f"score{min_score}" if min_score is not None else "",
             f"pct{min_percentile}" if min_percentile is not None else "",
             f"top{top}" if top else "", "-".join(sorted(labels or [])),
             "-".join(sorted(projects or [])), "-".join(sorted(kinds or []))]
    return "_".join(p for p in parts if p) or "all"


def write_hits(folder, data, **filters):
    """Run a filter and write hits-<filter>.csv and paragraphs-<filter>.csv
    into the run folder; returns their paths."""
    rows = hits(folder, data, **filters)
    tag = filter_name(**filters)
    a = os.path.join(folder, f"hits-{tag}.csv")
    b = os.path.join(folder, f"paragraphs-{tag}.csv")
    _write(a, HIT_COLS, rows)
    _write(b, PARA_COLS, paragraph_rollup(rows))
    return a, b


def _write(path, cols, rows):
    with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(path + ".part", path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set", help="a file in inputs/taxonomy/ without .csv: assets, measures")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--model", default=embed.DEFAULT_MODEL)
    ap.add_argument("--dim", type=int, default=embed.DEFAULT_DIM)
    ap.add_argument("--base-url", default=embed.DEFAULT_BASE)
    ap.add_argument("--key-file", default="", help="as for src/embed.py")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hits", action="store_true",
                    help="filter the scores of the current version (scoring it first)")
    ap.add_argument("--min-score", type=float)
    ap.add_argument("--min-percentile", type=float)
    ap.add_argument("--top", type=int)
    ap.add_argument("--label", action="append")
    ap.add_argument("--project", action="append")
    ap.add_argument("--kind", action="append", choices=["sentence", "footnote"])
    args = ap.parse_args()
    folder, _ = run_folder(args.label_set, args.data, args.model, args.dim)
    if not os.path.exists(os.path.join(folder, "manifest.json")) or args.dry_run:
        key = "" if args.dry_run else embed.read_key(args.key_file)
        folder = search(args.label_set, args.data, args.model, args.dim, args.base_url,
                        key, dry_run=args.dry_run)
    if args.hits and folder:
        for path in write_hits(folder, args.data, min_score=args.min_score,
                               min_percentile=args.min_percentile, top=args.top,
                               labels=args.label, projects=args.project, kinds=args.kind):
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
