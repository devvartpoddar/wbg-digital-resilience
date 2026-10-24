#!/usr/bin/env python3
"""Embed every search unit (src/units.py) once and cache the vectors.

Reads  data/appraisal/{sentences,paragraphs,components}.csv and text/
Writes data/embeddings/cache/{model}.{dim}/{aa}/{sha256}.f32 (one vector per text),
       data/embeddings/sentence_emb.npy, sentence_index.csv,
       sentence_embedding_manifest.json, data/reports/embed_sentences.txt
       (--unit paragraphs: paragraph_emb.npy, emb_index.csv, embedding_manifest.json)

The cache is keyed on the SHA-256 of the exact text embedded, so a re-run costs
nothing and an interrupted one resumes.

  python3 src/embed.py [--dry-run] [--limit 5] [--unit paragraphs]
"""
import argparse, csv, hashlib, json, os, socket, sys, time

import numpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api  # noqa: E402
from paths import data_root, where  # noqa: E402
import units  # noqa: E402

DEFAULT_MODEL = "openai/text-embedding-3-large"
DEFAULT_DIM = 3072
DEFAULT_BASE = "https://openrouter.ai/api/v1"

# For the report only.
PRICE_PER_MTOK = 0.13

# A request carries an ARRAY of texts, so the corpus is a few hundred requests rather than
# tens of thousands of calls.
CHUNK_ITEMS = 96
CHUNK_CHARS = 200_000

# text-embedding-3 accepts 8,191 tokens for a single input.
MAX_TEXT_CHARS = 28_000

INDEX_COLS = ["row", "paragraph_id", "text_sha256"]
# A searched unit is never left out or a copy; 'copies' lists the units that print the same
# text in other documents (units.copies), pipe-separated.
SENTENCE_INDEX_COLS = ["row"] + [c for c in units.UNIT_COLS
                                 if c not in ("char_start", "char_end", "n_tokens",
                                              "left_out", "copy_of")] + ["copies",
                                                                         "embedded_sha256"]
# What is put in front of a unit's text before embedding (--context).
CONTEXTS = ("none", "section")

# What each unit writes: (matrix, index, manifest, report).
OUTPUTS = {
    "sentences": ("sentence_emb.npy", "sentence_index.csv",
                  "sentence_embedding_manifest.json", "embed_sentences.txt"),
    "paragraphs": ("paragraph_emb.npy", "emb_index.csv",
                   "embedding_manifest.json", "embed.txt"),
}


class TransportError(RuntimeError):
    """A request did not yield usable vectors. Raised by the transport, caught
    by the caller, which decides whether to split the chunk and try again."""


# --------------------------------------------------------------- the transport
#
# Everything below this line and everything above the caller is deliberately
# ignorant of how vectors arrive.

def embed_texts(texts, *, model, api_key, base_url=DEFAULT_BASE, dimensions=None,
                timeout=180, tries=5):
    """Return (vectors, meta) for texts, in input order. THE transport seam."""
    url = base_url.rstrip("/") + "/embeddings"
    payload = {
        "model": model,
        "input": list(texts),
        # Unpinned routing can serve identical text from a different provider and return a
        # different vector for it.
        "provider": {"order": ["openai"], "allow_fallbacks": False},
    }
    if dimensions:
        payload["dimensions"] = dimensions
    try:
        r = api.request("POST", url, json=payload, timeout=timeout, tries=tries,
                        headers={"Authorization": f"Bearer {api_key}",
                                 "Content-Type": "application/json"})
    except api.Refused as exc:
        # Bad key, bad model, bad payload: retrying changes nothing.
        raise SystemExit(f"embed: {exc}") from None
    except (api.Transport, OSError) as exc:
        raise TransportError(str(exc)[:500]) from None
    return _parse(r, len(texts), model)


def _model_tail(name):
    """openai/text-embedding-3-large and text-embedding-3-large are the same
    model reached two ways, so compare on the part that names it."""
    return (name or "").split("/")[-1].split(":")[0].strip().lower()


def _parse(response, n_expected, model):
    try:
        data = response.json()
    except ValueError:
        raise TransportError(f"response was not JSON: {response.text[:200]}")
    if not data.get("data") and data.get("error"):
        raise TransportError(f"api error: {str(data['error'])[:300]}")

    items = data.get("data") or []
    if len(items) != n_expected:
        raise TransportError(f"asked for {n_expected} vectors, got {len(items)}")

    # The response carries an explicit index per item.
    ordered = [None] * n_expected
    for item in items:
        i = item.get("index")
        if not isinstance(i, int) or not 0 <= i < n_expected:
            raise TransportError(f"item index {i!r} outside 0..{n_expected - 1}")
        if ordered[i] is not None:
            raise TransportError(f"index {i} returned twice")
        vec = item.get("embedding")
        if not isinstance(vec, list) or not vec:
            raise TransportError(f"item {i} carries no embedding")
        ordered[i] = vec

    served = data.get("model") or ""
    # A mismatch means the provider pin did not hold, and vectors from a different model are
    # not comparable with the ones already cached.
    if served and _model_tail(served) != _model_tail(model):
        raise SystemExit(f"embed: asked for {model!r}, served {served!r}. "
                         f"The provider pin did not hold; stopping before the "
                         f"cache mixes two models.")

    usage = data.get("usage") or {}
    return ordered, {
        "tokens": int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0),
        "model": served or model,
        # Provider rate limits, not price, decide whether a synchronous run scales.
        "rate_limit": {k.lower(): v for k, v in response.headers.items()
                       if k.lower().startswith("x-ratelimit")},
    }


# -------------------------------------------------------------------- the cache

def cache_root(data_dir, model, dim):
    slug = model.replace("/", "__").replace(":", "_") + f".{dim}"
    return where(data_dir, "embeddings", "cache", slug)


def cache_path(root, sha):
    # Sharded on the first byte: 256 directories, a few thousand files each.
    return os.path.join(root, sha[:2], sha + ".f32")


def read_cached(root, sha, dim):
    """The stored vector as bytes, or None if it is not there."""
    path = cache_path(root, sha)
    try:
        if os.path.getsize(path) != dim * 4:
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def write_cached(root, sha, blob):
    path = cache_path(root, sha)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, path)


def pack(vec, dim):
    """float32 bytes for one vector, after the checks that decide whether it is
    allowed into the cache at all."""
    if len(vec) != dim:
        raise TransportError(f"expected {dim} dimensions, got {len(vec)}")
    arr = numpy.asarray(vec, dtype="<f4")
    norm = float(numpy.linalg.norm(arr))
    # The model returns unit vectors: a norm far from 1 (a truncated or foreign body, a zero
    # vector, a NaN or an infinity) fails this one test.
    if not 0.9 <= norm <= 1.1:
        raise TransportError(f"vector norm {norm:.4f} is not ~1")
    return arr.tobytes()


# ------------------------------------------------------------------ the corpus

def context_prefix(row, paras):
    """The 'section' design's prefix for a unit, from its paragraph's row."""
    p = paras.get(row.get("paragraph_id", row.get("unit_id")), {})
    parts = []
    if p.get("section_title"):
        parts.append(f"Section: {p['section_title'].strip()}.")
    name = (p.get("subcomponent_name") if row.get("subcomponent_number") else "") or \
        p.get("component_name") or ""
    if name:
        parts.append(f"Component: {name.strip()}.")
    return " ".join(parts)


def resolve(data_dir, rows, id_field="paragraph_id", context="none", paras=None):
    """Paragraph text pulled back out of the clean files by offset, with the
    hash recorded at cleaning time re-checked against it."""
    cache, order, unique, empty = {}, [], {}, []
    for row in rows:
        did = row["doc_id"]
        if did not in cache:
            path = where(data_dir, "text", f"{did}.txt")
            with open(path, encoding="utf-8") as fh:
                cache[did] = fh.read()
        body = cache[did][int(row["char_start"]):int(row["char_end"])]
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if sha != row["text_sha256"]:
            raise SystemExit(
                f"embed: {row[id_field]} does not match its recorded hash.\n"
                f"  paragraphs.csv and data/appraisal/text/ have diverged - re-run "
                f"src/clean.py before embedding.")
        if not body.strip():
            empty.append(row[id_field])
            continue
        # A list item is embedded with the sentence that introduces its list
        # (units.lead_ins), so '(b) towers;' carries what the towers are for.
        if row.get("lead_in_start"):
            lead = cache[did][int(row["lead_in_start"]):int(row["lead_in_end"])]
            body = lead + "\n" + body
        if context == "section":
            prefix = context_prefix(row, paras or {})
            if prefix:
                body = prefix + "\n" + body
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()   # of the text embedded
        if len(body) > MAX_TEXT_CHARS:
            raise SystemExit(
                f"embed: {row[id_field]} is {len(body):,} characters, over "
                f"the {MAX_TEXT_CHARS:,} ceiling. Truncating would cache a vector "
                f"under the hash of text it does not describe; fix the cleaner.")
        order.append((row[id_field], sha))
        unique.setdefault(sha, body)
    return order, unique, empty


def chunked(shas, unique):
    """Group unique texts into requests, capped by count and by characters."""
    batch, chars = [], 0
    for sha in shas:
        size = len(unique[sha])
        if batch and (len(batch) >= CHUNK_ITEMS or chars + size > CHUNK_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append(sha)
        chars += size
    if batch:
        yield batch


EMBED_WORKERS = 4   # requests in flight at once


def _embed_batch(n, total, batch, unique, root, dim, *, model, api_key, base_url, log):
    """One request, with the per-text retry. Returns (requests, tokens,
    stored, retried_singly, rate_limit, served, failures)."""
    texts = [unique[s] for s in batch]
    requests_, tokens, stored, singly, failures, rate_limit, served = 0, 0, 0, 0, [], {}, ""
    try:
        vectors, meta = embed_texts(texts, model=model, api_key=api_key, base_url=base_url)
        requests_ += 1
        tokens += meta["tokens"]
        rate_limit = meta["rate_limit"] or rate_limit
        served = meta["model"] or served
        for sha, vec in zip(batch, vectors):
            write_cached(root, sha, pack(vec, dim))
            stored += 1
    except TransportError as exc:
        # One bad input must not cost the rest of the chunk: retry a text at a time.
        log(f"  chunk {n}/{total} failed ({exc}); retrying singly")
        singly += 1
        for sha in batch:
            if read_cached(root, sha, dim) is not None:
                continue
            try:
                vectors, meta = embed_texts([unique[sha]], model=model, api_key=api_key,
                                            base_url=base_url)
                requests_ += 1
                tokens += meta["tokens"]
                write_cached(root, sha, pack(vectors[0], dim))
                stored += 1
            except TransportError as inner:
                failures.append((sha, str(inner)[:200]))
    return requests_, tokens, stored, singly, rate_limit, served, failures


def fetch_missing(todo, unique, root, dim, *, model, api_key, base_url, log,
                  workers=EMBED_WORKERS):
    """Embed and cache every text in todo, several requests in flight at
    once. Returns (stats, failures)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    stats = {"requests": 0, "tokens": 0, "stored": 0, "retried_singly": 0}
    rate_limit, served = {}, ""
    failures = []
    batches = list(chunked(todo, unique))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_embed_batch, n, len(batches), batch, unique, root, dim,
                               model=model, api_key=api_key, base_url=base_url, log=log)
                   for n, batch in enumerate(batches, 1)]
        for done, f in enumerate(as_completed(futures), 1):
            r, t, st, sg, rl, sv, fl = f.result()
            stats["requests"] += r
            stats["tokens"] += t
            stats["stored"] += st
            stats["retried_singly"] += sg
            rate_limit = rl or rate_limit
            served = sv or served
            failures.extend(fl)
            if done % 10 == 0 or done == len(batches):
                log(f"  {done}/{len(batches)} requests, {stats['stored']:,} vectors cached")
    stats["rate_limit"] = rate_limit
    stats["served_model"] = served
    return stats, failures


def assemble(npy_path, index, root, dim, log):
    """Write the matrix in emb_index order, streaming so memory stays flat."""
    tmp = npy_path + ".part"
    arr = numpy.lib.format.open_memmap(tmp, mode="w+", dtype="float32",
                                       shape=(len(index), dim))
    for i, (_pid, sha) in enumerate(index):
        arr[i] = numpy.frombuffer(read_cached(root, sha, dim), dtype="<f4")
        if (i + 1) % 20000 == 0:
            log(f"  assembled {i + 1:,}/{len(index):,} rows")
    arr.flush()
    del arr
    os.replace(tmp, npy_path)


def _same_file(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _matrix_shape(path):
    try:
        return tuple(numpy.load(path, mmap_mode="r").shape)
    except (OSError, ValueError):
        return None


def fmt_range(lo, hi, unit=""):
    return f"{lo:,.0f} - {hi:,.0f}{unit}"


def token_range(words, chars):
    """(low, high) token estimates from the per-word and per-character rules of
    thumb, which disagree on this corpus; the manifest records the tokens billed."""
    return int(words * 1.3), int(chars / 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dim", type=int, default=DEFAULT_DIM)
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--dry-run", action="store_true",
                    help="counts and estimated spend; makes no network call")
    ap.add_argument("--limit", type=int, default=0,
                    help="first N units; artifacts go to data/embeddings/smoke_*")
    ap.add_argument("--unit", choices=sorted(OUTPUTS), default="sentences",
                    help="what one vector stands for (default: sentences)")
    ap.add_argument("--context", choices=CONTEXTS, default="none",
                    help="what is embedded in front of a sentence: its list lead-in alone "
                         "(none) or also its section title and component heading (section)")
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    if args.unit == "sentences":
        every = units.search_units(args.data)
        dupes = units.copies(every)
        rows = [dict(u, copies="|".join(dupes.get(u["unit_id"], [])))
                for u in every if not u["left_out"] and not u["copy_of"]]
        id_field = "unit_id"
        log(f"left out:        {sum(1 for u in every if u['left_out']):,} references and "
            f"fragments, {sum(1 for u in every if u['copy_of']):,} copies of another unit")
    else:
        with open(where(args.data, "appraisal", "paragraphs.csv"), newline="",
                  encoding="utf-8") as fh:
            # Only what clean.py marks for the model: body prose and footnotes, not front
            # matter, tables or headings.
            rows = [r for r in csv.DictReader(fh) if r.get("for_model", "true") == "true"]
        id_field = "paragraph_id"
    if args.limit:
        rows = rows[:args.limit]

    paras = {}
    if args.context != "none":
        with open(where(args.data, "appraisal", "paragraphs.csv"), newline="",
                  encoding="utf-8") as fh:
            paras = {r["paragraph_id"]: r for r in csv.DictReader(fh)}
    order, unique, empty = resolve(args.data, rows, id_field, args.context, paras)
    root = cache_root(args.data, args.model, args.dim)

    have = {sha for sha in unique if read_cached(root, sha, args.dim) is not None}
    todo = [sha for sha in unique if sha not in have]
    chars = sum(len(unique[s]) for s in todo)
    words = sum(len(unique[s].split()) for s in todo)
    lo, hi = token_range(words, chars)

    log(f"{args.unit + ':':<17}{len(rows):,}" + (f"  (--limit {args.limit})" if args.limit else ""))
    log(f"unique texts:    {len(unique):,}  "
        f"(deduplication removes {len(order) - len(unique):,})")
    log(f"already cached:  {len(have):,}")
    log(f"to embed:        {len(todo):,}  ({chars:,} characters)")
    log(f"estimated:       {fmt_range(lo, hi)} tokens  ->  "
        f"${lo / 1e6 * PRICE_PER_MTOK:.2f} - ${hi / 1e6 * PRICE_PER_MTOK:.2f}")
    if empty:
        log(f"resolved empty:  {len(empty)} (excluded: {', '.join(empty[:5])})")

    if args.dry_run:
        log("\n--dry-run: no request made, nothing written")
        return 0

    api_key = api.key(required=bool(todo))

    stats, failures = ({"requests": 0, "tokens": 0, "stored": 0,
                        "retried_singly": 0, "rate_limit": {}, "served_model": ""}, [])
    started = time.time()
    if todo:
        log("")
        stats, failures = fetch_missing(todo, unique, root, args.dim,
                                        model=args.model, api_key=api_key,
                                        base_url=args.base_url, log=log)
    elapsed = time.time() - started

    # Rebuilt from disk rather than from what the run believes it stored, so a vector that
    # failed to land is absent from the index rather than trusted.
    have = {sha for sha in unique if read_cached(root, sha, args.dim) is not None}
    index = [(pid, sha) for pid, sha in order if sha in have]
    dropped = [pid for pid, sha in order if sha not in have]

    prefix = "smoke_" if args.limit else ""
    npy_name, idx_name, man_name, rep_name = OUTPUTS[args.unit]
    npy_path = where(args.data, "embeddings", f"{prefix}{npy_name}")
    idx_path = where(args.data, "embeddings", f"{prefix}{idx_name}")
    rep_path = where(args.data, "reports", f"{prefix}{rep_name}")
    for d in (os.path.dirname(npy_path), os.path.dirname(rep_path)):
        os.makedirs(d, exist_ok=True)

    log("")
    with open(idx_path + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if args.unit == "sentences":
            # Every unit's context rides with its row, so a search result names its
            # paragraph, document, projects and component directly.
            by_id = {r["unit_id"]: r for r in rows}
            w.writerow(SENTENCE_INDEX_COLS)
            for i, (uid, sha) in enumerate(index):
                r = dict(by_id[uid], embedded_sha256=sha)
                w.writerow([i] + [r[c] for c in SENTENCE_INDEX_COLS[1:]])
        else:
            w.writerow(INDEX_COLS)
            for i, (pid, sha) in enumerate(index):
                w.writerow([i, pid, sha])
    # The matrix is rows of the cache in index order, so when the index is the one on disk
    # and nothing was fetched, the matrix on disk is current and is not written again (it is
    # large; a run with nothing new is cheap).
    if stats["stored"] == 0 and _same_file(idx_path + ".part", idx_path) \
            and _matrix_shape(npy_path) == (len(index), args.dim):
        os.remove(idx_path + ".part")
        log(f"  {os.path.basename(npy_path)} unchanged")
    else:
        assemble(npy_path, index, root, args.dim, log)
        os.replace(idx_path + ".part", idx_path)

    spend = stats["tokens"] / 1e6 * PRICE_PER_MTOK
    if stats["requests"]:
        import db
        with db.connect() as conn:
            db.ensure(conn)
            with api.Spend(conn, "embed", args.unit, args.model) as sp:
                sp.calls, sp.cost = stats["requests"], spend
    manifest = {
        "model_requested": args.model,
        "model_served": stats["served_model"] or None,
        "dimensions": args.dim,
        "endpoint": "synchronous",
        "base_url": args.base_url,
        "provider_pin": {"order": ["openai"], "allow_fallbacks": False},
        "cache_dir": os.path.relpath(root, ROOT),
        "unit": args.unit,
        "context": args.context,
        args.unit: len(rows),
        "unique_texts": len(unique),
        "rows_embedded": len(index),
        f"{args.unit}_without_a_vector": len(dropped),
        "tokens_reported": stats["tokens"],
        "usd_per_mtok": PRICE_PER_MTOK,
        "usd_spent": round(spend, 4),
        "requests": stats["requests"],
        "rate_limit_headers": stats["rate_limit"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": socket.gethostname(),
    }
    man_path = where(args.data, "embeddings", f"{prefix}{man_name}")
    # A run that embedded nothing keeps the manifest of the run that did: the provenance of
    # the vectors is what was paid for and served, not the cache hit that read them back.
    if stats["requests"] or not os.path.exists(man_path):
        with open(man_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
    else:
        log(f"  {os.path.basename(man_path)} kept (nothing embedded this run)")

    out = [
        f"{args.unit + ' read:':<28}{len(rows):,}",
        f"unique texts:               {len(unique):,}",
        f"cache hits before this run: {len(have) - stats['stored']:,}",
        f"requests made:              {stats['requests']:,}"
        f"   (chunks retried singly: {stats['retried_singly']})",
        f"vectors stored this run:    {stats['stored']:,}",
        f"tokens reported by the API: {stats['tokens']:,}",
        f"spend this run:             ${spend:.4f} at ${PRICE_PER_MTOK}/Mtok",
        f"wall clock:                 {elapsed / 60:.1f} min",
        "",
        f"rows in {os.path.basename(npy_path)}: {len(index):,} x {args.dim}"
        f"  ({len(index) * args.dim * 4 / 1e9:.2f} GB)",
        f"{args.unit + ' with no vector:':<28}{len(dropped)}",
    ]
    if dropped:
        out.append("  " + ", ".join(dropped[:20]))
    if empty:
        out.append(f"{args.unit} resolving empty: {len(empty)}")
    if failures:
        out.append("")
        out.append(f"texts that failed after retries ({len(failures)}):")
        for sha, why in failures[:20]:
            out.append(f"  {sha[:12]}  {why}")
    if stats["rate_limit"]:
        out.append("")
        out.append("rate limit headers, for sizing a larger cohort:")
        for k in sorted(stats["rate_limit"]):
            out.append(f"  {k}: {stats['rate_limit'][k]}")
    out.append("")
    out.append(f"Requests are issued {EMBED_WORKERS} at a time (EMBED_WORKERS).")

    body = "\n".join(out)
    with open(rep_path, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    log("")
    log(body)
    log(f"\nwrote {npy_path}\nwrote {idx_path}\nwrote {man_path}\nwrote {rep_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
