#!/usr/bin/env python3
"""Dump cleaned paragraphs in a readable form, for reading by a person.

Reads  data/paragraphs.csv, data/clean/{doc_id}.txt, data/documents.csv
Writes data/review_sample.txt

The paragraph tables carry offsets, not text, so there is no way to eyeball the
corpus without resolving them. This does that.

  python3 src/review.py                      10 random paragraphs, seed 0
  python3 src/review.py --n 40               more of them
  python3 src/review.py --block narrative    only body prose
  python3 src/review.py --section II         only Section II and its subsections
  python3 src/review.py --grep resilien      only paragraphs matching a pattern
"""
import argparse, csv, os, random, re, sys, textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(data):
    with open(os.path.join(data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    docs = {}
    path = os.path.join(data, "documents.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            docs = {r["doc_id"]: r for r in csv.DictReader(fh)}
    return rows, docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0, help="same seed gives the same draw")
    ap.add_argument("--block", default="", help="narrative | annex | frontmatter | template:*")
    ap.add_argument("--section", default="", help="section_path prefix, e.g. II or ANNEX 2")
    ap.add_argument("--grep", default="", help="regex the paragraph text must match")
    ap.add_argument("--min-tokens", type=int, default=0)
    ap.add_argument("--width", type=int, default=94)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows, docs = load(args.data)
    pool = rows
    if args.block:
        pool = [r for r in pool if r["block"] == args.block]
    if args.section:
        pool = [r for r in pool if r["section_path"].startswith(args.section)]
    if args.min_tokens:
        pool = [r for r in pool if int(r["n_tokens"]) >= args.min_tokens]

    cache = {}

    def text_of(row):
        did = row["doc_id"]
        if did not in cache:
            with open(os.path.join(args.data, "clean", f"{did}.txt"), encoding="utf-8") as fh:
                cache[did] = fh.read()
        return cache[did][int(row["char_start"]):int(row["char_end"])]

    if args.grep:
        pat = re.compile(args.grep, re.I)
        pool = [r for r in pool if pat.search(text_of(r))]

    if not pool:
        print("no paragraphs matched", file=sys.stderr)
        return 1

    # Spread the draw across documents rather than letting one long document
    # dominate, then take a seeded sample so the same command reproduces it.
    rng = random.Random(args.seed)
    by_doc = {}
    for r in pool:
        by_doc.setdefault(r["doc_id"], []).append(r)
    order = sorted(by_doc)
    rng.shuffle(order)
    picked, i = [], 0
    while len(picked) < min(args.n, len(pool)):
        did = order[i % len(order)]
        bucket = by_doc[did]
        if bucket:
            picked.append(bucket.pop(rng.randrange(len(bucket))))
        i += 1
        if i > len(order) * 200:
            break

    out = []
    out.append(f"{len(picked)} paragraphs drawn from {len(pool)} matching "
               f"({len(rows)} total), seed {args.seed}")
    filt = [f"{k}={v}" for k, v in (("block", args.block), ("section", args.section),
                                    ("grep", args.grep)) if v]
    out.append("filters: " + (", ".join(filt) if filt else "none"))
    out.append("")
    for n, r in enumerate(picked, 1):
        doc = docs.get(r["doc_id"], {})
        out.append("=" * args.width)
        out.append(f"[{n}] {r['paragraph_id']}")
        out.append(f"    project {r['project_id']} | {doc.get('doc_kind', '?')} | "
                   f"{doc.get('title', '')[:60]}")
        out.append(f"    section {r['section_path'] or '-'} | {r['section_title'] or '-'}")
        out.append(f"    block {r['block']} | {r['n_tokens']} tokens | "
                   f"chars {r['char_start']}-{r['char_end']}")
        out.append("-" * args.width)
        for line in textwrap.wrap(text_of(r), args.width - 2) or [""]:
            out.append("  " + line)
        out.append("")

    body = "\n".join(out)
    dest = args.out or os.path.join(args.data, "review_sample.txt")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    print(body)
    print(f"\nwrote {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
