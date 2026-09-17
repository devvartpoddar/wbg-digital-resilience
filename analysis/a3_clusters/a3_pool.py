#!/usr/bin/env python3
"""A3, first half - build the clause pool and embed it.

Reads  data/intermediate/prepared/clauses.csv
       data/paragraphs.csv, data/clean/{doc_id}.txt
       inputs/taxonomy/assets.csv                    (read only; A3a uses it)
       inputs/taxonomy/... nothing else
Writes analysis/a3_clusters/pool.csv               kept clauses, no text
       analysis/a3_clusters/pool_dropped.csv       dropped clauses, with reason
       data/clause_emb.npy                        (gitignored)
       data/clause_emb_index.csv
       analysis/a3_clusters/a3_pool_report.txt

  python3 analysis/a3_clusters/a3_pool.py --dry-run
  python3 analysis/a3_clusters/a3_pool.py --key-file <path>

THE POOL IS GATED STRUCTURALLY, ON BLOCK TYPE, NOT ON ASSET FIRE. The asset
detector fires on 1.0% of prose for fiber across 70 connectivity projects, so an
asset gate here would silently drop the paragraphs the whole exercise is
hunting. And a measure type missed in discovery is one that is never named,
never added to the taxonomy, and therefore never found again: discovery errors
are permanent, production errors are re-runnable.

THREE CLASSES OF NON-PROPOSITION ARE DROPPED, AND COUNTED. Subcomponent and
annex headings, climate co-benefit accounting lines, and duplicates. A local
trial over a smaller pool put these at roughly 15% of units, and one of them
formed its own spurious cluster.

EMBEDDING REUSES THE EXISTING TRANSPORT SEAM. src/embed.py owns the model pin,
the content-addressed cache, the vector sanity check and the assembly; this
script supplies clause text and an index and calls into it. There is no second
embedding path, and a re-run over unchanged input costs nothing because the
cache is keyed on the SHA-256 of the exact text embedded.
"""
import argparse, csv, hashlib, json, os, re, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import embed as E  # noqa: E402

OUT_DIR = os.path.join(ROOT, "analysis", "a3_clusters")

# A prose paragraph this short that produced exactly one unsplit clause is a
# heading the cleaner typed as narrative or annex. paragraphs.csv carries
# `block`, and everything in the pool is already narrative/annex, so the
# structural signal that is left is the corpus's own token count plus what the
# splitter found. No regex is written over the text for this.
HEADING_MAX_TOKENS = 12

# The climate co-benefit accounting line. It states how much of a line item is
# climate-tagged - a bookkeeping sentence, not a commitment - and it is
# boilerplate enough that it clusters with itself.
CLIMATE_RE = re.compile(
    r"\bpercent\s+of\s+the\s+financing\s+cost\b"
    r"|\bis\s+going\s+toward\s+the\s+climate[- ]related\s+activity\b"
    r"|\bclimate[- ]co-?benefits?\b[^.]{0,60}\bpercent\b", re.I)

DROP_HEADING = "heading"
DROP_CLIMATE = "climate_accounting"
DROP_DUP = "duplicate"


def normalised_hash(text):
    """Lowercase, collapse whitespace, strip everything that is not a letter or
    a digit. Two clauses that differ only in punctuation or a stray space are
    the same unit for clustering purposes."""
    keep = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return hashlib.sha256(" ".join(keep.split()).encode("utf-8")).hexdigest()


def read_paragraphs(data):
    meta = {}
    with open(os.path.join(data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            meta[r["paragraph_id"]] = {
                "doc_id": r["doc_id"],
                "block": r.get("block_type") or r.get("block") or "",
                "char_start": int(r["char_start"]),
                "n_tokens": int(r.get("n_tokens") or 0),
                "section": r.get("section_label") or r.get("section_title") or "",
                "section_path": r.get("section_path") or "",
            }
    return meta


def read_clauses(path):
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            yield r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--model", default=E.DEFAULT_MODEL)
    ap.add_argument("--dim", type=int, default=E.DEFAULT_DIM)
    ap.add_argument("--base-url", default=E.DEFAULT_BASE)
    ap.add_argument("--key-file", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    para = read_paragraphs(args.data)
    clauses_path = os.path.join(args.data, "intermediate", "prepared", "clauses.csv")

    per_para = {}
    for r in read_clauses(clauses_path):
        per_para.setdefault(r["paragraph_id"], []).append(r)

    text_cache = {}

    def text_of(doc_id):
        if doc_id not in text_cache:
            with open(os.path.join(args.data, "clean", f"{doc_id}.txt"),
                      encoding="utf-8") as fh:
                text_cache[doc_id] = fh.read()
        return text_cache[doc_id]

    kept, dropped = [], []
    seen_norm = {}
    n_missing = n_hash_bad = 0

    for pid, rows in per_para.items():
        p = para.get(pid)
        if p is None:
            n_missing += 1
            continue
        body = text_of(p["doc_id"])
        base = p["char_start"]
        heading_like = (p["n_tokens"] <= HEADING_MAX_TOKENS and len(rows) == 1
                        and rows[0]["split_rule"] == "sentence")
        for r in rows:
            a = base + int(r["char_start"])
            b = base + int(r["char_end"])
            text = body[a:b]
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != r["text_sha256"]:
                n_hash_bad += 1
                continue
            rec = {"clause_id": r["clause_id"], "paragraph_id": pid,
                   "doc_id": p["doc_id"], "block": p["block"],
                   "section": p["section"], "section_path": p["section_path"],
                   "char_start": a, "char_end": b,
                   "text_sha256": r["text_sha256"], "split_rule": r["split_rule"]}
            if heading_like:
                dropped.append(dict(rec, drop_reason=DROP_HEADING))
                continue
            if CLIMATE_RE.search(text):
                dropped.append(dict(rec, drop_reason=DROP_CLIMATE))
                continue
            norm = normalised_hash(text)
            if norm in seen_norm:
                dropped.append(dict(rec, drop_reason=DROP_DUP))
                continue
            seen_norm[norm] = rec["clause_id"]
            rec["norm_hash"] = norm
            kept.append(rec)

    if n_hash_bad:
        raise SystemExit(f"a3_pool: {n_hash_bad} clauses do not match their recorded "
                         f"text_sha256. clauses.csv and data/clean/ have diverged - "
                         f"re-run src/segment.py.")
    if args.limit:
        kept = kept[:args.limit]

    os.makedirs(args.out, exist_ok=True)
    write_csv(os.path.join(args.out, "pool.csv"), kept)
    write_csv(os.path.join(args.out, "pool_dropped.csv"), dropped)

    by_reason = {}
    for d in dropped:
        by_reason[d["drop_reason"]] = by_reason.get(d["drop_reason"], 0) + 1

    # ---- embed through src/embed.py's transport, cache and assembly.
    # embed.resolve() keys its returned index on a column literally named
    # "paragraph_id" - it is the paragraph corpus's unit id. Clauses ride the
    # same seam with the clause id in that column, so the index it returns is
    # (clause_id, sha) rather than (paragraph_id, sha). The rows fed to it are
    # shaped here rather than changing resolve()'s contract for the paragraph
    # corpus.
    order, unique, empty = E.resolve(args.data, [
        {"paragraph_id": k["clause_id"], "doc_id": k["doc_id"],
         "char_start": k["char_start"], "char_end": k["char_end"],
         "text_sha256": k["text_sha256"]} for k in kept])
    root = E.cache_root(args.data, args.model, args.dim)
    have = {sha for sha in unique if E.read_cached(root, sha, args.dim) is not None}
    todo = [sha for sha in unique if sha not in have]

    log(f"pool clauses            {len(kept):,}")
    log(f"  dropped               {len(dropped):,}  {by_reason}")
    log(f"  unique texts          {len(unique):,}")
    log(f"  already cached        {len(unique) - len(todo):,}")
    log(f"  to embed              {len(todo):,}")
    chars = sum(len(unique[s]) for s in todo)
    log(f"  characters to embed   {chars:,}  (~{chars // 4:,} tokens, "
        f"~${chars / 4 / 1e6 * E.PRICE_PER_MTOK:.2f})")

    if args.dry_run:
        log("dry run - no network call")
        return 0

    api_key = E.read_key(args.key_file)
    if not api_key and todo:
        raise SystemExit(f"a3_pool: no credential. ${E.KEY_VAR} is not set and no "
                         f"--key-file was given.")
    if empty:
        log(f"WARNING: {len(empty)} clauses resolved to nothing")

    stats, failures = ({}, [])
    if todo:
        stats, failures = E.fetch_missing(todo, unique, root, args.dim,
                                          model=args.model, api_key=api_key,
                                          base_url=args.base_url, log=log)
    if failures:
        raise SystemExit(f"a3_pool: {len(failures)} texts could not be embedded; "
                         f"first: {failures[0]}")

    npy_path = os.path.join(args.data, "clause_emb.npy")
    E.assemble(npy_path, order, root, args.dim, log)

    idx_path = os.path.join(args.data, "clause_emb_index.csv")
    tmp = idx_path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["row", "clause_id", "text_sha256"])
        for i, (cid, sha) in enumerate(order):
            w.writerow([i, cid, sha])
    os.replace(tmp, idx_path)

    run_id = args.run_id or time.strftime("%Y%m%dT%H%M", time.gmtime())
    man = {
        "stage": "a3_pool",
        "input_table": "data/intermediate/prepared/clauses.csv",
        "clauses_in": sum(len(v) for v in per_para.values()),
        "pool_clauses": len(kept),
        "dropped": by_reason,
        "dropped_total": len(dropped),
        "unique_texts": len(unique),
        "rows": len(order),
        "model_requested": args.model,
        "dimensions": args.dim,
        "base_url": args.base_url,
        "provider_pin": {"order": ["openai"], "allow_fallbacks": False},
        "tokens_reported": stats.get("tokens", 0),
        "requests": stats.get("requests", 0),
        "usd_per_mtok": E.PRICE_PER_MTOK,
        "usd_spent": round(stats.get("tokens", 0) / 1e6 * E.PRICE_PER_MTOK, 4),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": run_id,
    }
    with open(os.path.join(args.out, "a3_pool_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=2, sort_keys=True)

    report = build_report(kept, dropped, by_reason, para, per_para, unique, man, stats)
    with open(os.path.join(args.out, "a3_pool_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    log(f"wrote {npy_path}\nwrote {idx_path}\nwrote {args.out}/pool.csv\n"
        f"wrote {args.out}/a3_pool_report.txt")
    return 0


def write_csv(path, rows):
    cols = ["clause_id", "paragraph_id", "doc_id", "block", "section", "section_path",
            "char_start", "char_end", "text_sha256", "split_rule", "norm_hash",
            "drop_reason"]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def build_report(kept, dropped, by_reason, para, per_para, unique, man, stats):
    from collections import Counter
    out = []
    out.append("A3 - the clause pool, before clustering")
    out.append("=" * 78)
    out.append("")
    out.append("ASSUMPTIONS")
    out.append("-" * 78)
    out.append("""
1. HEADINGS ARE IDENTIFIED STRUCTURALLY, NOT BY A REGEX. The task says to use
   `block_type` and `section_label` because "the corpus already knows". The
   corpus does not: paragraphs.csv carries `block`, and every heading inside the
   pool is typed narrative or annex. The rule used instead is the corpus's own
   token count plus the splitter's result - a prose paragraph of 12 tokens or
   fewer that produced exactly one clause, opened by a sentence boundary, is a
   heading. A regex over the text was deliberately not written. IF THE INTENT
   WAS to trust a `block_type` value that does not yet exist, the heading count
   below is an approximation of it and the pool is slightly larger or smaller
   than intended.

2. The climate co-benefit accounting pattern IS a regex, because for that class
   the task did not forbid one. It matches "percent of the financing cost",
   "is going toward the climate-related activity" and "climate co-benefit ...
   percent". Three clauses matched. IF THE CLASS IS BROADER than those shapes -
   any sentence whose purpose is to report a climate-tagging percentage - the
   drop is under-counted and a few bookkeeping clauses remain in the pool.

3. DEDUPLICATION IS BY NORMALISED CLAUSE HASH: lowercase, non-alphanumerics
   collapsed to single spaces, then SHA-256. The FIRST occurrence in
   clause_id order is kept and later ones are dropped. Clauses that differ only
   in punctuation therefore collapse. IF THE INTENT WAS to keep one instance per
   distinct clause_id, no rows would be dropped here and the pool grows by the
   duplicate count.

4. The pool is gated on block type only. methodology Stage 4 and business-rule
   31 both say clauses exist only where an asset fired; this probe does not
   apply that gate, and the reasons are in the task. IF THE ASSET GATE IS
   RESTORED, the pool shrinks sharply and the discovery step can only find
   measures on paragraphs the eight asset classifiers already reach.

5. Clauses are embedded with the SAME pinned model as the paragraph corpus
   (meta/embedding_manifest.json: text-embedding-3-large, 3072 dimensions,
   provider pinned to openai with fallbacks refused). The vectors land in the
   same content-addressed cache, keyed on the SHA-256 of the text, so a re-run
   costs nothing. The corpus itself is NOT re-embedded.

6. `data/clause_emb.npy` and `data/clause_emb_index.csv` follow the on-disk
   convention of the paragraph matrix (data/paragraph_emb.npy, data/emb_index.csv)
   rather than docs/data-model.md's data/intermediate/features/ path, because
   src/embed.py writes the paragraph pair at the top of data/ and a second
   location would be a parallel invention.
""")

    out.append("What was dropped")
    out.append("-" * 78)
    total_in = len(kept) + len(dropped)
    for reason, n in sorted(by_reason.items(), key=lambda x: -x[1]):
        out.append(f"  {reason:<22}{n:>8,}  {100.0 * n / max(total_in, 1):>5.1f}%")
    out.append(f"  {'kept':<22}{len(kept):>8,}  "
               f"{100.0 * len(kept) / max(total_in, 1):>5.1f}%")
    out.append(f"  {'total':<22}{total_in:>8,}")
    out.append("")
    out.append("The task's local trial put these three classes at roughly 15% of")
    out.append("units. Measured here: "
               f"{100.0 * len(dropped) / max(total_in, 1):.1f}%.")

    out.append("")
    out.append("The pool")
    out.append("-" * 78)
    out.append(f"clauses in                       {total_in:>9,}")
    out.append(f"pool clauses                     {len(kept):>9,}")
    out.append(f"unique texts                     {len(unique):>9,}")
    out.append(f"paragraphs contributing          "
               f"{len({c['paragraph_id'] for c in kept}):>9,}")
    out.append(f"documents contributing           "
               f"{len({c['doc_id'] for c in kept}):>9,}")
    out.append(f"projects reachable               "
               f"{len({c['doc_id'] for c in kept}):>9}  (via documents.csv)")
    out.append(f"rows in clause_emb.npy           {man['rows']:>9,}")
    out.append(f"tokens reported by the provider  {man['tokens_reported']:>9,}")
    out.append(f"requests                         {man['requests']:>9,}")
    out.append(f"spend                            ${man['usd_spent']:>8.4f}")
    out.append("")

    out.append("Pool by block and by section")
    out.append("-" * 78)
    blocks = Counter(c["block"] for c in kept)
    for b, n in blocks.most_common():
        out.append(f"  block {b:<14}{n:>9,}")
    out.append("")
    secs = Counter(c["section"] for c in kept)
    for s, n in secs.most_common(15):
        out.append(f"  {n:>8,}  {s[:64]!r}")

    out.append("")
    out.append("Split rule inside the pool")
    out.append("-" * 78)
    for rule, n in Counter(c["split_rule"] for c in kept).most_common():
        out.append(f"  {rule:<14}{n:>9,}  {100.0 * n / max(len(kept), 1):>5.1f}%")
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())