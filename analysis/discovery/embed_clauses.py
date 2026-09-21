#!/usr/bin/env python3
"""Embed clauses, reusing src/embed.py's transport and cache.

WHY THIS FILE EXISTS. src/embed.py's main() is wired to paragraphs.csv: it
reads `n_tokens`, it resolves text by offsets INTO THE DOCUMENT, and it writes
one fixed matrix path. Clause offsets are relative to the parent paragraph
(business-rule 23) and clauses.csv carries no doc_id, so the resolver differs.
Everything downstream of the resolver does not, and none of it is
reimplemented here: cache_root, read_cached, write_cached, pack, chunked,
fetch_missing and assemble all come from src/embed.py. The rule is one
embedding path, and this is that path with a different way of finding the text.

THE HASH CHECK IS THE POINT, as it is for paragraphs. clauses.csv stores
offsets rather than text, so if a clean file or the splitter changed, the
offsets still parse and still yield something - just not what was measured.
Re-checking `text_sha256` turns that into an abort instead of a matrix of
confidently wrong vectors.

RE-EMBEDDING IS NEARLY FREE AND THAT IS DELIBERATE. The cache is
content-addressed on the SHA-256 of the text, shared with the paragraph run, so
a change of splitter variant only pays for the clauses whose text actually
moved. The corpus itself is never re-embedded (AGENTS.md rule 7): the model,
dimensions and provider pin all come from meta/embedding_manifest.json.

NO COMMITTED TEXT. The index carries clause ids and hashes. The matrix is a
*.npy and gitignored.
"""
import argparse, csv, hashlib, json, os, sys, time

import numpy

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

import embed as E  # noqa: E402

PROSE_BLOCKS = ("narrative", "annex")


def read_paragraphs(path):
    """paragraph_id -> (doc_id, block, char_start)."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["paragraph_id"]] = (
                r["doc_id"],
                r.get("block_type") or r.get("block") or "",
                int(r["char_start"]),
            )
    return out


def resolve_clauses(data_dir, clause_path, paras, log):
    """[(clause_id, sha)] in table order, plus {sha: text} and the skips.

    A clause is located as paragraph.char_start + clause.char_start, because
    clause offsets are into the parent paragraph and not into the document.
    """
    bodies, order, unique = {}, [], {}
    empty, missing_para, not_prose = [], [], 0
    with open(clause_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            pid = r["paragraph_id"]
            meta = paras.get(pid)
            if meta is None:
                missing_para.append(r["clause_id"])
                continue
            doc, block, base = meta
            if block not in PROSE_BLOCKS:
                not_prose += 1
                continue
            if doc not in bodies:
                path = os.path.join(data_dir, "clean", f"{doc}.txt")
                with open(path, encoding="utf-8") as bf:
                    bodies[doc] = bf.read()
            a = base + int(r["char_start"])
            b = base + int(r["char_end"])
            text = bodies[doc][a:b]
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if sha != r["text_sha256"]:
                raise SystemExit(
                    f"embed_clauses: {r['clause_id']} does not match its recorded "
                    f"hash.\n  clauses.csv and data/clean/ have diverged - re-run "
                    f"src/segment.py (and src/clean.py before it) before embedding.")
            if not text.strip():
                empty.append(r["clause_id"])
                continue
            if len(text) > E.MAX_TEXT_CHARS:
                raise SystemExit(
                    f"embed_clauses: {r['clause_id']} is {len(text):,} characters, "
                    f"over the {E.MAX_TEXT_CHARS:,} ceiling. A clause this long is "
                    f"a segmentation bug, not an embedding problem.")
            order.append((r["clause_id"], sha))
            unique.setdefault(sha, text)
    if missing_para:
        log(f"clauses whose paragraph is not in paragraphs.csv: {len(missing_para)}"
            f" (first: {', '.join(missing_para[:3])})")
    if not_prose:
        log(f"clauses outside {PROSE_BLOCKS}: {not_prose:,} (skipped)")
    return order, unique, empty


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--clauses", default="")
    ap.add_argument("--out-prefix", default="clause",
                    help="writes data/{prefix}_emb.npy and {prefix}_emb_index.csv")
    ap.add_argument("--key-file", default="")
    ap.add_argument("--dry-run", action="store_true",
                    help="counts and estimated spend; makes no network call")
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    manifest_path = os.path.join(ROOT, "meta", "embedding_manifest.json")
    man = json.load(open(manifest_path))
    model, dim, base_url = man["model_requested"], man["dimensions"], man["base_url"]
    log(f"model {model}  dim {dim}  (pinned in meta/embedding_manifest.json)")

    clause_path = args.clauses or os.path.join(
        args.data, "intermediate", "prepared", "clauses.csv")
    if not os.path.exists(clause_path):
        clause_path = os.path.join(args.data, "clauses.csv")
    par_path = os.path.join(args.data, "paragraphs.csv")
    for p in (clause_path, par_path):
        if not os.path.exists(p):
            print(f"embed_clauses: missing {p}", file=sys.stderr)
            return 2

    paras = read_paragraphs(par_path)
    order, unique, empty = resolve_clauses(args.data, clause_path, paras, log)
    root = E.cache_root(args.data, model, dim)

    have = {s for s in unique if E.read_cached(root, s, dim) is not None}
    todo = [s for s in unique if s not in have]
    chars = sum(len(unique[s]) for s in todo)
    lo, hi = E.token_range(sum(len(unique[s].split()) for s in todo), chars)

    log(f"clauses:         {len(order):,}")
    log(f"unique texts:    {len(unique):,}  "
        f"(deduplication removes {len(order) - len(unique):,})")
    log(f"already cached:  {len(have):,}")
    log(f"to embed:        {len(todo):,}  ({chars:,} characters)")
    log(f"estimated:       {E.fmt_range(lo, hi)} tokens  ->  "
        f"${lo / 1e6 * E.PRICE_PER_MTOK:.2f} - ${hi / 1e6 * E.PRICE_PER_MTOK:.2f}")
    if empty:
        log(f"resolved empty:  {len(empty)} (excluded)")
    if args.dry_run:
        log("\n--dry-run: no request made, nothing written")
        return 0

    stats, failures = {"requests": 0, "tokens": 0, "stored": 0,
                       "retried_singly": 0, "rate_limit": {}, "served_model": ""}, []
    if todo:
        api_key = E.read_key(args.key_file)
        if not api_key:
            raise SystemExit(
                f"embed_clauses: no credential. ${E.KEY_VAR} is not set and no "
                f"--key-file was given. Set one; never pass a key as an argument.")
        stats, failures = E.fetch_missing(todo, unique, root, dim, model=model,
                                          api_key=api_key, base_url=base_url, log=log)
    if failures:
        log(f"FAILED to embed {len(failures)} texts; the matrix is not written.")
        for sha, why in failures[:5]:
            log(f"  {sha[:12]} {why}")
        return 1

    npy = os.path.join(args.data, f"{args.out_prefix}_emb.npy")
    idx = os.path.join(args.data, f"{args.out_prefix}_emb_index.csv")
    E.assemble(npy, order, root, dim, log)
    tmp = idx + ".part"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["clause_id", "text_sha256"])
        w.writerows(order)
    os.replace(tmp, idx)

    man_out = {
        "stage": "embed_clauses",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_table": os.path.relpath(clause_path, ROOT),
        "model_requested": model, "dimensions": dim, "base_url": base_url,
        "provider_pin": man.get("provider_pin", {}),
        "clauses": len(order), "unique_texts": len(unique), "rows": len(order),
        "cached_before": len(have), "requests": stats["requests"],
        "tokens_reported": stats["tokens"],
        "usd_spent": round(stats["tokens"] / 1e6 * E.PRICE_PER_MTOK, 4),
        "usd_per_mtok": E.PRICE_PER_MTOK,
        "served_model": stats.get("served_model", ""),
        "resolved_empty": len(empty),
    }
    out_man = os.path.join(args.data, f"{args.out_prefix}_emb_manifest.json")
    with open(out_man, "w", encoding="utf-8") as fh:
        json.dump(man_out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    log(f"wrote {npy}  ({len(order):,} x {dim})")
    log(f"wrote {idx} and {os.path.basename(out_man)}")
    log(f"spend this run: ${man_out['usd_spent']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
