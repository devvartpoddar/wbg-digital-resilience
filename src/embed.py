#!/usr/bin/env python3
"""Embed every cleaned paragraph once, and cache the vectors so it stays once.

Reads  data/paragraphs.csv, data/clean/{doc_id}.txt
Writes data/emb_cache/{model}.{dim}/{aa}/{sha256}.f32   one vector per unique text
       data/paragraph_emb.npy                           (rows, dim) float32
       data/emb_index.csv                               row -> paragraph_id
       data/embed_report.txt
       meta/embedding_manifest.json                     committed; the provenance record

The cache is content-addressed on the SHA-256 of the exact text embedded, under
a directory named for the model and dimension count. So a re-run costs nothing,
an interrupted run resumes, and a later cohort pays only for the paragraphs it
adds. Nothing here is ever re-embedded because a downstream script wanted it.

  python3 src/embed.py --dry-run          # counts and spend, no network call
  python3 src/embed.py --limit 5          # smoke test, artifacts under data/smoke_*
  python3 src/embed.py                    # the corpus
"""
import argparse, csv, hashlib, json, os, socket, stat, sys, time

import numpy
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_MODEL = "openai/text-embedding-3-large"
DEFAULT_DIM = 3072
DEFAULT_BASE = "https://openrouter.ai/api/v1"
KEY_VAR = "OPENROUTER_API_KEY"

# For the report only. The provider's invoice is the real number; this exists so
# a dry run can say roughly what a full run will cost before it costs it.
PRICE_PER_MTOK = 0.13

# A request carries an ARRAY of texts, so the corpus is a few hundred requests
# rather than tens of thousands of calls. Both caps earn their place: the item
# count keeps one failed request cheap to retry, and the character budget stops
# a chunk of long paragraphs from crossing the endpoint's per-request ceiling.
CHUNK_ITEMS = 96
CHUNK_CHARS = 200_000

# text-embedding-3 accepts 8,191 tokens for a single input. The longest
# paragraph measured in the corpus is 11,310 characters, roughly 2,800 tokens,
# so nothing is near this. It aborts rather than truncating: a shortened text
# would put a vector in the cache that does not describe the text it is keyed
# on, and every later run would trust it.
MAX_TEXT_CHARS = 28_000

INDEX_COLS = ["row", "paragraph_id", "text_sha256"]


class TransportError(RuntimeError):
    """A request did not yield usable vectors. Raised by the transport, caught
    by the caller, which decides whether to split the chunk and try again."""


# --------------------------------------------------------------- the transport
#
# Everything below this line and everything above the caller is deliberately
# ignorant of how vectors arrive.

def embed_texts(texts, *, model, api_key, base_url=DEFAULT_BASE, dimensions=None,
                timeout=180, tries=5):
    """Return (vectors, meta) for texts, in input order. THE transport seam.

    This is the only function in the file that knows a network exists.
    Deduplication, caching, the checks that decide what is allowed to be
    stored, assembly and the manifest all sit outside it. So adding the
    asynchronous batch endpoint for a much larger cohort means writing a second
    function with this signature and a flag to choose between them - and
    nothing that decides WHAT gets embedded or WHERE IT LANDS moves at all.

    meta carries {"tokens", "model", "rate_limit"}. A batch implementation
    fills the same three keys.
    """
    url = base_url.rstrip("/") + "/embeddings"
    payload = {
        "model": model,
        "input": list(texts),
        # Unpinned routing can serve identical text from a different provider
        # and return a different vector for it. That is exactly the
        # inconsistency the content-addressed cache exists to rule out, so the
        # provider is pinned and fallbacks refused.
        "provider": {"order": ["openai"], "allow_fallbacks": False},
    }
    if dimensions:
        payload["dimensions"] = dimensions
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/devvartpoddar/wbg-digital-resilience",
        "X-Title": "wbg-digital-resilience",
    }

    last = None
    for attempt in range(tries):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code in (408, 409, 429, 500, 502, 503, 504, 520, 522, 524, 529):
                # Throttling or a provider hiccup. The same request succeeds later.
                raise TransportError(f"HTTP {r.status_code}: {r.text[:200]}")
            if 400 <= r.status_code < 500:
                # Bad key, bad model, bad payload. Retrying changes nothing and
                # burning four more attempts only delays a clear message.
                raise SystemExit(f"embed: HTTP {r.status_code} from {url}\n"
                                 f"  {r.text[:500]}")
            r.raise_for_status()
            return _parse(r, len(texts), model)
        except TransportError as exc:
            last = exc
        except requests.RequestException as exc:
            last = TransportError(f"{type(exc).__name__}: {str(exc)[:300]}")
        if attempt < tries - 1:
            time.sleep(2 ** attempt)
    raise last


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

    # The response carries an explicit index per item. Input order is only
    # convention; the index is the contract, so honour it rather than assume.
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
    # A mismatch means the provider pin did not hold, and vectors from a
    # different model are not comparable with the ones already cached. Abort on
    # the first chunk rather than discover it in a similarity matrix later.
    if served and _model_tail(served) != _model_tail(model):
        raise SystemExit(f"embed: asked for {model!r}, served {served!r}. "
                         f"The provider pin did not hold; stopping before the "
                         f"cache mixes two models.")

    usage = data.get("usage") or {}
    return ordered, {
        "tokens": int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0),
        "model": served or model,
        # Captured because provider rate limits, not price, are what decide
        # whether a synchronous run survives a cohort ten to twenty times this
        # size. Recorded in the manifest so that decision rests on a number.
        "rate_limit": {k.lower(): v for k, v in response.headers.items()
                       if k.lower().startswith("x-ratelimit")},
    }


def read_key(key_file):
    """The credential, from the environment or from a file named on the command
    line. Never from an argument: an argument is visible in `ps` to every
    account on the box and lands in whatever log records the command.

    A key file is checked for its mode before it is read. /srv/yggdrasil is a
    0755 tree and sits inside raven's read-only file jail, so a credential
    dropped there casually is readable by every local account AND through the
    MCP mount. Refusing a loose file up front is cheaper than discovering that
    afterwards.
    """
    if not key_file:
        return os.environ.get(KEY_VAR, "").strip()
    try:
        mode = stat.S_IMODE(os.stat(key_file).st_mode)
    except OSError as exc:
        raise SystemExit(f"embed: cannot read {key_file}: {exc.strerror}")
    if mode & 0o077:
        raise SystemExit(
            f"embed: {key_file} is mode {mode:04o}, readable beyond its owner. "
            f"A credential file must be 0600 - chmod it and re-run.")
    with open(key_file, encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key:
        raise SystemExit(f"embed: {key_file} is empty")
    return key


# -------------------------------------------------------------------- the cache

def cache_root(data_dir, model, dim):
    slug = model.replace("/", "__").replace(":", "_") + f".{dim}"
    return os.path.join(data_dir, "emb_cache", slug)


def cache_path(root, sha):
    # Sharded on the first byte: 256 directories, so a cohort twenty times this
    # one still holds a few thousand files per directory rather than 676,000
    # in one.
    return os.path.join(root, sha[:2], sha + ".f32")


def read_cached(root, sha, dim):
    """The stored vector as bytes, or None if it is not there.

    A file of the wrong length is a torn write from an interrupted run. Treat
    it as absent and pay to fetch it again; do not treat it as data.
    """
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
    allowed into the cache at all.

    Nothing that fails here is stored, and nothing is ever stored as zeros: a
    silently zeroed row would sit in the matrix looking like a vector and
    poison every similarity computed against it.
    """
    if len(vec) != dim:
        raise TransportError(f"expected {dim} dimensions, got {len(vec)}")
    arr = numpy.asarray(vec, dtype="<f4")
    norm = float(numpy.linalg.norm(arr))
    # text-embedding-3 returns unit vectors, so this one comparison carries the
    # whole check: a norm far from 1 means something other than an embedding
    # came back or it arrived truncated, a zero vector norms to 0, and a NaN or
    # an infinity anywhere in the vector makes the norm itself NaN or infinite
    # and fails the same test. An explicit isfinite() pass alongside it was
    # tested and caught nothing this does not.
    if not 0.9 <= norm <= 1.1:
        raise TransportError(f"vector norm {norm:.4f} is not ~1")
    return arr.tobytes()


# ------------------------------------------------------------------ the corpus

def resolve(data_dir, rows):
    """Paragraph text pulled back out of the clean files by offset, with the
    hash recorded at cleaning time re-checked against it.

    The hash is not decoration. paragraphs.csv stores offsets, not text, so if
    a clean file is regenerated by a changed cleaner the offsets still parse
    and still yield SOMETHING - just not what was measured, audited and costed.
    Re-checking turns that into an abort instead of a corpus of confidently
    wrong vectors.

    Returns (order, unique, empty) where order is [(paragraph_id, sha)] in file
    order, unique is {sha: text}, and empty lists paragraphs that resolved to
    nothing.
    """
    cache, order, unique, empty = {}, [], {}, []
    for row in rows:
        did = row["doc_id"]
        if did not in cache:
            path = os.path.join(data_dir, "clean", f"{did}.txt")
            with open(path, encoding="utf-8") as fh:
                cache[did] = fh.read()
        body = cache[did][int(row["char_start"]):int(row["char_end"])]
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if sha != row["text_sha256"]:
            raise SystemExit(
                f"embed: {row['paragraph_id']} does not match its recorded hash.\n"
                f"  paragraphs.csv and data/clean/ have diverged - re-run "
                f"src/clean.py before embedding.")
        if not body.strip():
            empty.append(row["paragraph_id"])
            continue
        if len(body) > MAX_TEXT_CHARS:
            raise SystemExit(
                f"embed: {row['paragraph_id']} is {len(body):,} characters, over "
                f"the {MAX_TEXT_CHARS:,} ceiling. Truncating would cache a vector "
                f"under the hash of text it does not describe; fix the cleaner.")
        order.append((row["paragraph_id"], sha))
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


def fetch_missing(todo, unique, root, dim, *, model, api_key, base_url, log):
    """Embed and cache every text in todo. Returns (stats, failures)."""
    stats = {"requests": 0, "tokens": 0, "stored": 0, "retried_singly": 0}
    rate_limit, served = {}, ""
    failures = []
    batches = list(chunked(todo, unique))

    for n, batch in enumerate(batches, 1):
        texts = [unique[s] for s in batch]
        try:
            vectors, meta = embed_texts(texts, model=model, api_key=api_key,
                                        base_url=base_url)
            stats["requests"] += 1
            stats["tokens"] += meta["tokens"]
            rate_limit = meta["rate_limit"] or rate_limit
            served = meta["model"] or served
            for sha, vec in zip(batch, vectors):
                write_cached(root, sha, pack(vec, dim))
                stats["stored"] += 1
        except TransportError as exc:
            # One pathological input must not cost the other ninety-five their
            # vectors, so the chunk is retried a text at a time and only what
            # actually fails is recorded as failed.
            log(f"  chunk {n}/{len(batches)} failed ({exc}); retrying singly")
            stats["retried_singly"] += 1
            for sha in batch:
                # A chunk can fail partway: vectors ahead of the bad one were
                # validated and cached before it was reached. Re-sending those
                # would pay for them a second time and store what is already
                # there, so the retry covers only what is actually missing.
                if read_cached(root, sha, dim) is not None:
                    continue
                try:
                    vectors, meta = embed_texts([unique[sha]], model=model,
                                                api_key=api_key, base_url=base_url)
                    stats["requests"] += 1
                    stats["tokens"] += meta["tokens"]
                    write_cached(root, sha, pack(vectors[0], dim))
                    stats["stored"] += 1
                except TransportError as inner:
                    failures.append((sha, str(inner)[:200]))
        if n % 10 == 0 or n == len(batches):
            log(f"  {n}/{len(batches)} requests, {stats['stored']:,} vectors cached")

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


def fmt_range(lo, hi, unit=""):
    return f"{lo:,.0f} - {hi:,.0f}{unit}"


def token_range(words, chars):
    """The two rules of thumb, reported as a range because on this corpus they
    disagree by about a third: it runs 6.8 characters per word against roughly
    5.3 for general English, so words break into more subword pieces than the
    per-word rule assumes. Only a real run settles it, and it does - the
    manifest records the tokens the API actually reported."""
    return int(words * 1.3), int(chars / 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "meta"))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dim", type=int, default=DEFAULT_DIM)
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--dry-run", action="store_true",
                    help="counts and estimated spend; makes no network call")
    ap.add_argument("--key-file", default="",
                    help="read the credential from this file (mode 0600) rather "
                         "than from $" + KEY_VAR)
    ap.add_argument("--limit", type=int, default=0,
                    help="first N paragraphs; artifacts go to data/smoke_*")
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    with open(os.path.join(args.data, "paragraphs.csv"), newline="",
              encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    words_by_sha = {}
    if args.limit:
        rows = rows[:args.limit]
    for row in rows:
        words_by_sha[row["text_sha256"]] = int(row["n_tokens"])

    order, unique, empty = resolve(args.data, rows)
    root = cache_root(args.data, args.model, args.dim)

    have = {sha for sha in unique if read_cached(root, sha, args.dim) is not None}
    todo = [sha for sha in unique if sha not in have]
    chars = sum(len(unique[s]) for s in todo)
    words = sum(words_by_sha.get(s, 0) for s in todo)
    lo, hi = token_range(words, chars)

    log(f"paragraphs:      {len(rows):,}" + (f"  (--limit {args.limit})" if args.limit else ""))
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

    api_key = read_key(args.key_file)
    if not api_key and todo:
        raise SystemExit(
            f"embed: no credential. ${KEY_VAR} is not set in this environment "
            f"and no --key-file was given. Set one; never pass a key as an "
            f"argument.")

    stats, failures = ({"requests": 0, "tokens": 0, "stored": 0,
                        "retried_singly": 0, "rate_limit": {}, "served_model": ""}, [])
    started = time.time()
    if todo:
        log("")
        stats, failures = fetch_missing(todo, unique, root, args.dim,
                                        model=args.model, api_key=api_key,
                                        base_url=args.base_url, log=log)
    elapsed = time.time() - started

    # Rebuilt from disk rather than from what the run believes it stored, so a
    # vector that failed to land is absent from the index rather than trusted.
    have = {sha for sha in unique if read_cached(root, sha, args.dim) is not None}
    index = [(pid, sha) for pid, sha in order if sha in have]
    dropped = [pid for pid, sha in order if sha not in have]

    prefix = "smoke_" if args.limit else ""
    npy_path = os.path.join(args.data, f"{prefix}paragraph_emb.npy")
    idx_path = os.path.join(args.data, f"{prefix}emb_index.csv")
    rep_path = os.path.join(args.data, f"{prefix}embed_report.txt")

    log("")
    assemble(npy_path, index, root, args.dim, log)
    with open(idx_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(INDEX_COLS)
        for i, (pid, sha) in enumerate(index):
            w.writerow([i, pid, sha])

    spend = stats["tokens"] / 1e6 * PRICE_PER_MTOK
    manifest = {
        "model_requested": args.model,
        "model_served": stats["served_model"] or None,
        "dimensions": args.dim,
        "endpoint": "synchronous",
        "base_url": args.base_url,
        "provider_pin": {"order": ["openai"], "allow_fallbacks": False},
        "cache_dir": os.path.relpath(root, ROOT),
        "paragraphs": len(rows),
        "unique_texts": len(unique),
        "rows_embedded": len(index),
        "paragraphs_without_a_vector": len(dropped),
        "tokens_reported": stats["tokens"],
        "usd_per_mtok": PRICE_PER_MTOK,
        "usd_spent": round(spend, 4),
        "requests": stats["requests"],
        "rate_limit_headers": stats["rate_limit"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": socket.gethostname(),
    }
    if args.limit:
        man_path = os.path.join(args.data, "smoke_embedding_manifest.json")
    else:
        os.makedirs(args.meta, exist_ok=True)
        man_path = os.path.join(args.meta, "embedding_manifest.json")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    out = [
        f"paragraphs read:            {len(rows):,}",
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
        f"paragraphs with no vector:  {len(dropped)}",
    ]
    if dropped:
        out.append("  " + ", ".join(dropped[:20]))
    if empty:
        out.append(f"paragraphs resolving empty: {len(empty)}")
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
    out.append("Requests are issued serially. A cohort ten to twenty times this "
               "size wants either concurrency here or the batch transport; "
               "embed_texts() is the only function either one changes.")

    body = "\n".join(out)
    with open(rep_path, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    log("")
    log(body)
    log(f"\nwrote {npy_path}\nwrote {idx_path}\nwrote {man_path}\nwrote {rep_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
