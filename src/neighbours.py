#!/usr/bin/env python3
"""Look at what the embeddings actually did: nearest neighbours for a paragraph
or for a query.

Reads  data/paragraph_emb.npy, data/emb_index.csv, data/paragraphs.csv,
       data/clean/{doc_id}.txt
Writes nothing.

A similarity matrix that looks plausible in aggregate tells you nothing. Reading
the ten nearest neighbours of a paragraph you already understand tells you
whether the vectors mean anything, and it is the only check on this stage that
a person can actually judge. This exists so that check is one command rather
than a scratch script rewritten from memory each time.

  python3 src/neighbours.py --id 31167658:p00042
  python3 src/neighbours.py --query "towers sited above the flood line"
  python3 src/neighbours.py --query "procurement of civil works" --block narrative

--id needs no credential: it reads a vector already on disk. --query embeds one
short text, which costs a few millionths of a dollar and needs the key.
"""
import argparse, csv, os, sys, textwrap

import numpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import embed as E  # noqa: E402


def load(data):
    for name in ("emb_index.csv", "paragraph_emb.npy"):
        if not os.path.exists(os.path.join(data, name)):
            raise SystemExit(f"neighbours: no {name} in {data}. "
                             f"Run src/embed.py first.")
    with open(os.path.join(data, "emb_index.csv"), newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    with open(os.path.join(data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        paras = {r["paragraph_id"]: r for r in csv.DictReader(fh)}
    # mmap_mode because the matrix is 415 MB at this cohort and several GB at
    # the next one. Every reader of this file should open it the same way.
    arr = numpy.load(os.path.join(data, "paragraph_emb.npy"), mmap_mode="r")
    if arr.shape[0] != len(index):
        raise SystemExit(f"neighbours: matrix has {arr.shape[0]} rows, index lists "
                         f"{len(index)} - they are from different runs")
    return index, paras, arr


def text_of(data, row, cache):
    did = row["doc_id"]
    if did not in cache:
        with open(os.path.join(data, "clean", f"{did}.txt"), encoding="utf-8") as fh:
            cache[did] = fh.read()
    return cache[did][int(row["char_start"]):int(row["char_end"])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--id", default="", help="paragraph_id to find neighbours of")
    ap.add_argument("--query", default="", help="free text to search for")
    ap.add_argument("--key-file", default="", help="credential file for --query")
    ap.add_argument("--model", default=E.DEFAULT_MODEL)
    ap.add_argument("--base-url", default=E.DEFAULT_BASE)
    ap.add_argument("--block", default="", help="restrict neighbours to one block")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--width", type=int, default=94)
    args = ap.parse_args()

    if bool(args.id) == bool(args.query):
        raise SystemExit("neighbours: give exactly one of --id or --query")

    index, paras, arr = load(args.data)
    rows_by_id = {r["paragraph_id"]: int(r["row"]) for r in index}

    if args.id:
        if args.id not in rows_by_id:
            raise SystemExit(f"neighbours: {args.id} has no vector. It is either not "
                             f"a paragraph_id or it failed to embed - check "
                             f"data/embed_report.txt")
        needle = numpy.asarray(arr[rows_by_id[args.id]], dtype="float32")
        header = f"neighbours of {args.id}"
    else:
        api_key = E.read_key(args.key_file)
        if not api_key:
            raise SystemExit(f"neighbours: --query needs a credential. Set "
                             f"${E.KEY_VAR} or pass --key-file.")
        # The transport seam, reused. A query has to be embedded by the SAME
        # model as the corpus or the distances are meaningless, and going
        # through embed_texts() is what guarantees that - including the
        # provider pin and the served-model check.
        vectors, _meta = E.embed_texts([args.query], model=args.model,
                                       api_key=api_key, base_url=args.base_url)
        needle = numpy.asarray(vectors[0], dtype="float32")
        header = f"neighbours of {args.query!r}"

    # The vectors are unit-normalised by the provider, so the dot product IS
    # the cosine. src/embed.py refuses to store anything whose norm is not ~1,
    # which is what makes that safe to assume here.
    sims = numpy.asarray(arr, dtype="float32") @ needle

    order = numpy.argsort(-sims)
    cache, shown, out = {}, 0, []
    for row_i in order:
        pid = index[int(row_i)]["paragraph_id"]
        if args.id and pid == args.id:
            continue
        meta = paras.get(pid)
        if meta is None:
            continue
        if args.block and meta["block"] != args.block:
            continue
        body = " ".join(text_of(args.data, meta, cache).split())
        out.append(f"  {sims[row_i]:.4f}  {pid}  [{meta['block']}]"
                   f"  {meta['section_path'] or '-'}")
        out.append(textwrap.fill(body, width=args.width,
                                 initial_indent="      ", subsequent_indent="      ",
                                 max_lines=6, placeholder=" ..."))
        out.append("")
        shown += 1
        if shown >= args.k:
            break

    print(header)
    print("=" * args.width)
    if args.id:
        meta = paras[args.id]
        print(textwrap.fill(" ".join(text_of(args.data, meta, cache).split()),
                            width=args.width, initial_indent="  ",
                            subsequent_indent="  ", max_lines=6, placeholder=" ..."))
        print("-" * args.width)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
