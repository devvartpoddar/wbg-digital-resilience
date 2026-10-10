#!/usr/bin/env python3
"""Score every search unit against a label set, and roll the scores up to
paragraphs. Sentences are where we search, paragraphs where we work.

Reads  inputs/taxonomy/<label set>.csv     kept by people, read only: one row
                                           per label, with <x>_id, <x>_name,
                                           definition and active columns
       data/embeddings/sentence_emb.npy, sentence_index.csv   (src/embed.py)
       the embedding cache, for the definitions' vectors
Writes data/search/<label set>-<version>/
         labels.csv          the labels scored, with each query text's SHA-256
         sentence_hits.csv   per label, its top units by cosine similarity,
                             each with its paragraph, document, projects and
                             component, score and rank
         paragraph_hits.csv  per label and paragraph among those hits: the best
                             score, the unit that gave it, how many of the
                             paragraph's units are in the label's top list
         manifest.json       what was run

A label is searched with "<name>: <definition>", embedded by the same model
and cached like every other text. <version> is a SHA-256 over the query texts,
the model, the dimensions and the sentence index, so changing a definition or
re-embedding the corpus gives a new folder, and an unchanged run is skipped:
the label lists can be revised and re-run as often as needed, at the cost of
embedding only what changed.

No cut-off is applied: every label keeps its top --top units with their
scores. Where the line between a match and a non-match falls is a judgement
made against examples a person checked (AGENTS.md), not here.

  python src/search.py assets               # top 200 units per asset
  python src/search.py measures --top 500
  python src/search.py assets --dry-run     # what would be embedded and scored
"""
import argparse, csv, hashlib, json, os, sys, time
from collections import defaultdict

import numpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import embed  # noqa: E402

TAXONOMY = os.path.join(ROOT, "inputs", "taxonomy")
HIT_COLS = ["label_id", "rank", "score", "unit_id", "kind", "paragraph_id", "doc_id",
            "project_ids", "component_number", "subcomponent_number", "component_key"]
PARA_COLS = ["label_id", "paragraph_id", "doc_id", "project_ids", "component_number",
             "component_key", "best_score", "best_unit_id", "units_in_top"]
# Rows of the sentence matrix scored at a time, so memory stays flat.
BLOCK = 20_000


def load_labels(name, folder=TAXONOMY):
    """[(label_id, query text)] for the active rows of <folder>/<name>.csv.

    The id is the column ending in '_id', the name the column ending in
    '_name'; a row with active other than true is left out."""
    path = os.path.join(folder, f"{name}.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"search: {path} has no rows")
    id_col = next((c for c in rows[0] if c.endswith("_id")), None)
    name_col = next((c for c in rows[0] if c.endswith("_name")), None)
    if not id_col or not name_col or "definition" not in rows[0]:
        raise SystemExit(f"search: {path} needs an *_id, a *_name and a definition column")
    out = []
    for r in rows:
        if (r.get("active") or "true").strip().lower() != "true":
            continue
        text = f"{r[name_col].strip()}: {' '.join(r['definition'].split())}"
        out.append((r[id_col].strip(), text))
    ids = [i for i, _ in out]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"search: {path} repeats a label id")
    return out


def run_version(labels, model, dim, index_path):
    """A SHA-256 over everything a run's scores depend on."""
    h = hashlib.sha256()
    for lid, text in labels:
        h.update(f"{lid}\t{text}\n".encode())
    h.update(f"{model}:{dim}\n".encode())
    with open(index_path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:12]


def query_vectors(labels, data, model, dim, base_url, api_key, log=print):
    """(L, dim) unit vectors for the labels' query texts, from the cache or
    embedded now (and cached)."""
    root = embed.cache_root(data, model, dim)
    unique = {hashlib.sha256(t.encode("utf-8")).hexdigest(): t for _, t in labels}
    todo = [s for s in unique if embed.read_cached(root, s, dim) is None]
    if todo:
        if not api_key:
            raise SystemExit(f"search: {len(todo)} label text(s) are not embedded yet and "
                             f"${embed.KEY_VAR} is not set")
        _stats, failures = embed.fetch_missing(todo, unique, root, dim, model=model,
                                               api_key=api_key, base_url=base_url, log=log)
        if failures:
            raise SystemExit(f"search: {len(failures)} label text(s) failed to embed")
    out = numpy.zeros((len(labels), dim), dtype="float32")
    for i, (_lid, text) in enumerate(labels):
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        out[i] = numpy.frombuffer(embed.read_cached(root, sha, dim), dtype="<f4")
    return out


def top_units(matrix, queries, top):
    """For each query row, (indices, scores) of its `top` best rows of the
    matrix, best first. Cosine similarity: both sides are unit vectors."""
    n = matrix.shape[0]
    scores = numpy.empty((queries.shape[0], n), dtype="float32")
    for start in range(0, n, BLOCK):
        block = numpy.asarray(matrix[start:start + BLOCK], dtype="float32")
        scores[:, start:start + block.shape[0]] = queries @ block.T
    k = min(top, n)
    out = []
    for row in scores:
        idx = numpy.argpartition(-row, k - 1)[:k] if k < n else numpy.arange(n)
        # Ties broken by row number, so the ranking is the same every run.
        idx = sorted(idx.tolist(), key=lambda i: (-float(row[i]), i))
        out.append((idx, [float(row[i]) for i in idx]))
    return out


def paragraph_rollup(hits):
    """paragraph_hits rows from sentence_hits rows (one label's or many)."""
    best = {}
    count = defaultdict(int)
    for h in hits:
        key = (h["label_id"], h["paragraph_id"])
        count[key] += 1
        if key not in best or float(h["score"]) > float(best[key]["score"]):
            best[key] = h
    out = []
    for key, h in best.items():
        out.append({"label_id": h["label_id"], "paragraph_id": h["paragraph_id"],
                    "doc_id": h["doc_id"], "project_ids": h["project_ids"],
                    "component_number": h["component_number"],
                    "component_key": h["component_key"], "best_score": h["score"],
                    "best_unit_id": h["unit_id"], "units_in_top": count[key]})
    out.sort(key=lambda r: (r["label_id"], -float(r["best_score"]), r["paragraph_id"]))
    return out


def _write(path, cols, rows):
    with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(path + ".part", path)


def search(name, data, top=200, model=embed.DEFAULT_MODEL, dim=embed.DEFAULT_DIM,
           base_url=embed.DEFAULT_BASE, api_key="", taxonomy=TAXONOMY, dry_run=False,
           log=print):
    """Run one label set; returns the output folder (or None on a dry run)."""
    labels = load_labels(name, taxonomy)
    npy = where(data, "embeddings", "sentence_emb.npy")
    idx = where(data, "embeddings", "sentence_index.csv")
    if not (os.path.exists(npy) and os.path.exists(idx)):
        raise SystemExit("search: no sentence embeddings yet - run src/embed.py first")
    version = run_version(labels, model, dim, idx)
    out = where(data, "search", f"{name}-{version}")
    log(f"{name}: {len(labels)} labels, run {version}")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log(f"  already scored: {out}")
        return out
    if dry_run:
        root = embed.cache_root(data, model, dim)
        new = sum(1 for _, t in labels if embed.read_cached(
            root, hashlib.sha256(t.encode("utf-8")).hexdigest(), dim) is None)
        log(f"  --dry-run: {new} label text(s) to embed; nothing written")
        return None
    with open(idx, newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    matrix = numpy.load(npy, mmap_mode="r")
    if matrix.shape[0] != len(index):
        raise SystemExit("search: sentence_emb.npy and sentence_index.csv disagree on rows")
    queries = query_vectors(labels, data, model, dim, base_url, api_key, log)
    hits = []
    for (lid, _text), (rows, scores) in zip(labels, top_units(matrix, queries, top)):
        for rank, (i, s) in enumerate(zip(rows, scores), 1):
            u = index[i]
            hits.append(dict(u, label_id=lid, rank=rank, score=f"{s:.6f}"))
    os.makedirs(out + ".part", exist_ok=True)
    _write(os.path.join(out + ".part", "labels.csv"), ["label_id", "query_text_sha256"],
           [{"label_id": lid, "query_text_sha256": hashlib.sha256(t.encode()).hexdigest()}
            for lid, t in labels])
    _write(os.path.join(out + ".part", "sentence_hits.csv"), HIT_COLS, hits)
    _write(os.path.join(out + ".part", "paragraph_hits.csv"), PARA_COLS,
           paragraph_rollup(hits))
    with open(os.path.join(out + ".part", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"label_set": name, "version": version, "labels": len(labels),
                   "top": top, "model": model, "dimensions": dim,
                   "units": len(index), "created_at":
                   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  fh, indent=2, sort_keys=True)
        fh.write("\n")
    # The folder appears whole or not at all: an interrupted run leaves only
    # the .part folder, which the next run overwrites.
    if os.path.exists(out):
        raise SystemExit(f"search: {out} appeared during the run")
    os.replace(out + ".part", out)
    log(f"  wrote {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set", help="a file in inputs/taxonomy/ without .csv: assets, measures")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--top", type=int, default=200, help="units kept per label")
    ap.add_argument("--model", default=embed.DEFAULT_MODEL)
    ap.add_argument("--dim", type=int, default=embed.DEFAULT_DIM)
    ap.add_argument("--base-url", default=embed.DEFAULT_BASE)
    ap.add_argument("--key-file", default="", help="as for src/embed.py")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    key = "" if args.dry_run else embed.read_key(args.key_file)
    search(args.label_set, args.data, args.top, args.model, args.dim, args.base_url,
           key, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
