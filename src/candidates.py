#!/usr/bin/env python3
"""Every unit that may be about a label, found loosely, with what is needed
to narrow it down: recall first, precision after, nothing read by a model.

A unit is a candidate for a label when either route finds it:

  pattern     one of the label's word patterns (<set>_patterns.csv) matches
              its text: the route that finds most commitments;
  embedding   it ranks in the label's best `top` (default 1,000) by its best
              cosine over the label's queries: the route that finds what
              the words miss.

Each candidate carries the commitment scorer's p_commitment and firmness
(src/commitment.py), the types whose patterns it matches (<set>_types.csv),
the units that print the same text elsewhere (copies), and its place in the
reading order (priority, read_order: see prioritise()). Nothing is cut off
here: a cut-off on p_commitment, firmness or score is chosen against
examples a person checked.

  data/candidates/<set>-<version>/candidates.csv
      label_id, unit_id, kind, paragraph_id, doc_id, project_ids,
      component_number, subcomponent_number, component_key, copies,
      route (pattern | embedding | both), rank, score, patterns, types,
      p_commitment, firmness
  data/candidates/<set>-<version>/manifest.json

<version> is a SHA-256 over this code, the search run, the patterns, the
types, `top` and the commitment scores: change any of them and it is a new
version.

words() lists the words that set a label's strongest candidates apart from
the other labels' candidates (or the corpus, for a set of one), the cheap way to see what types a measure has before writing
<set>_types.csv: a person reads 30 words, not 300 sentences.

  ./wbg candidates measures [--top 1000]
  ./wbg words measures fibre_burial [--committed]
"""
import argparse, csv, hashlib, json, math, os, re, sys, time
from collections import Counter

import numpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import commitment  # noqa: E402
import labels  # noqa: E402
import search  # noqa: E402

TOP = 1000
PATTERN_WEIGHT = 10   # a pattern hit outranks every unit found by embedding alone
COMMIT_WEIGHT = 2     # then p_commitment counts twice the embedding percentile
COLS = ["label_id", "unit_id", "kind", "paragraph_id", "doc_id", "project_ids",
        "component_number", "subcomponent_number", "component_key", "copies", "route",
        "rank", "score", "patterns", "types", "p_commitment", "firmness", "priority", "read_order"]
STOP = set("""a an and are as at be been but by can could for from has have in into is it
its of on or such than that the their these this those to was were which while who will
with would also any all may more most other over per under up via within without""".split())
WORD = re.compile(r"[a-z][a-z\-]+")


def version(run, patterns, types, top, commit_version):
    h = hashlib.sha256()
    h.update(commitment._sha_file(os.path.abspath(__file__)).encode())   # this code
    h.update(f"{os.path.basename(run)}\ttop={top}\tcommitment={commit_version}\n".encode())
    for p in patterns:
        h.update(f"p\t{p['label_id']}\t{p['pattern_id']}\t{p['pattern']}\n".encode())
    for t in types:
        h.update(f"t\t{t['label_id']}\t{t['type_id']}\t{t['pattern']}\n".encode())
    return h.hexdigest()[:12]


def prioritise(label_rows):
    """One label's candidates in reading order, each with its priority: a
    pattern hit first, then the commitment score and the embedding score
    (its percentile among the label's candidates), weighted 2 to 1.

    Chosen on the six-measure trial against 1,250 judged sentences: of the
    top 30 per measure, 71 per cent were commitments of the measure, against
    34 per cent for the embedding score alone. Dropping either the commitment
    or the embedding score from the order cost 15 to 27 points; a relevance
    classifier trained on pattern hits, and two cross-encoder re-rankers
    (bge-reranker-base, bge-reranker-v2-m3), did no better."""
    n = len(label_rows)
    by_score = sorted(range(n), key=lambda i: (float(label_rows[i]["score"]), -i))
    pct = [0.0] * n
    for k, i in enumerate(by_score):
        pct[i] = (k + 1) / n
    for i, r in enumerate(label_rows):
        p = float(r["p_commitment"] or 0)
        r["priority"] = round(PATTERN_WEIGHT * (r["route"] != "embedding") +
                              COMMIT_WEIGHT * p + pct[i], 6)
    label_rows.sort(key=lambda r: (-r["priority"], r["rank"]))
    for k, r in enumerate(label_rows, 1):
        r["read_order"] = k
        r["priority"] = f"{r['priority']:.4f}"
    return label_rows


def build(name, data, top=TOP, log=print):
    """Write the candidates of every label in the set; returns the folder."""
    run, _ = search.run_folder(name, data)
    if not os.path.exists(os.path.join(run, "manifest.json")):
        raise SystemExit(f"candidates: {name} has no search run for the current index - "
                         f"run ./wbg search {name} first")
    patterns, types = labels.load_patterns(name), labels.load_types(name)
    commit_folder = commitment.score(data, log=log)
    out = where(data, "candidates",
                f"{name}-{version(run, patterns, types, top, os.path.basename(commit_folder))}")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log(f"candidates: {out} already built")
        return out
    queries, scores, index = search.load_run(run, data)
    texts = commitment.unit_texts(data, index)
    commit = commitment.current_scores(data)
    text_list = [texts[r["unit_id"]] for r in index]
    cols = {}
    for j, q in enumerate(queries):
        cols.setdefault(q["label_id"], []).append(j)
    rows, counts = [], {}
    for lid in sorted(cols):
        best = numpy.asarray(scores[:, cols[lid]]).max(axis=1)
        order = numpy.lexsort((numpy.arange(len(best)), -best))   # best first, ties by row
        rank = numpy.empty(len(best), dtype=int)
        rank[order] = numpy.arange(1, len(best) + 1)
        mine = [p for p in patterns if p["label_id"] == lid]
        kinds = [t for t in types if t["label_id"] == lid]
        n = {"pattern": 0, "embedding": 0, "both": 0}
        label_rows = []
        for i in order.tolist():
            hit = [p["pattern_id"] for p in mine if p["regex"].search(text_list[i])]
            near = rank[i] <= top
            if not hit and not near:
                continue
            route = "both" if hit and near else ("pattern" if hit else "embedding")
            n[route] += 1
            r = index[i]
            p_commit, firm = commit.get(r["unit_id"], (None, ""))
            label_rows.append({**{c: r.get(c, "") for c in search.CONTEXT}, "label_id": lid,
                         "route": route, "rank": int(rank[i]), "score": f"{best[i]:.6f}",
                         "patterns": "|".join(hit),
                         "types": "|".join(t["type_id"] for t in kinds
                                           if t["regex"] and t["regex"].search(text_list[i])),
                         "p_commitment": "" if p_commit is None else f"{p_commit:.4f}",
                         "firmness": firm})
        rows.extend(prioritise(label_rows))
        counts[lid] = n
        log(f"  {lid:<24} {sum(n.values()):>6,} candidates (pattern only {n['pattern']:,}, "
            f"embedding only {n['embedding']:,}, both {n['both']:,})")
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "candidates.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"label_set": name, "version": os.path.basename(out).rsplit("-", 1)[1],
                   "search_run": os.path.basename(run), "top": top,
                   "commitment": os.path.basename(commit_folder),
                   "patterns": len(patterns), "types": len(types), "counts": counts,
                   "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(part, out)
    log(f"candidates: {len(rows):,} rows -> {out}")
    return out


def current(name, data, top=TOP):
    """The candidates folder for the set as it stands now, or None."""
    try:
        run, _ = search.run_folder(name, data)
    except SystemExit:
        return None
    patterns, types = labels.load_patterns(name), labels.load_types(name)
    folder = where(data, "candidates", f"{name}-{version(run, patterns, types, top, commitment.version(data))}")
    return folder if os.path.exists(os.path.join(folder, "manifest.json")) else None


def _terms(text):
    words = [w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 2]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def words(name, label_id, data, top=TOP, committed=False, n=30, near=150):
    """[(term, count among the label's candidates, ratio to the background)]:
    the terms most over-represented among the label's strongest candidates
    (a pattern hit, or within the embedding's best `near`; with p_commitment
    >= 0.5 if `committed`), each seen in at least 3 of them. The background
    is the other labels' candidates in the same set, so words every measure
    shares ('climate', 'resilience') drop out; a set of one label is
    compared with the whole index."""
    folder = current(name, data, top)
    if not folder:
        raise SystemExit(f"words: no candidates for {name} - run ./wbg candidates {name}")
    with open(os.path.join(folder, "candidates.csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    def strong(r):
        return (r["route"] != "embedding" or int(r["rank"]) <= near) and \
            (not committed or (r["p_commitment"] and float(r["p_commitment"]) >= 0.5))
    mine = [r for r in rows if r["label_id"] == label_id and strong(r)]
    if not mine:
        raise SystemExit(f"words: {label_id} has no candidates")
    run, _ = search.run_folder(name, data)
    _, _, index = search.load_run(run, data)
    texts = commitment.unit_texts(data, index)
    own = {r["unit_id"] for r in mine}
    others = {r["unit_id"] for r in rows if r["label_id"] != label_id} - own
    background = [texts[u] for u in sorted(others)] if others else list(texts.values())
    inside = Counter(t for u in sorted(own) for t in set(_terms(texts[u])))
    base = Counter(t for b in background for t in set(_terms(b)))
    n_in, n_bg = len(own), len(background)
    scored = []
    for term, c in inside.items():
        if c < 3:
            continue
        ratio = (c / n_in) / ((base[term] + 1) / (n_bg + 1))
        if ratio > 1:
            scored.append((c * math.log(ratio), term, c, ratio))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(term, c, ratio) for _, term, c, ratio in scored[:n]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("label_id", nargs="?", help="with --words: the label to describe")
    ap.add_argument("--top", type=int, default=TOP)
    ap.add_argument("--words", action="store_true", help="the label's distinctive words")
    ap.add_argument("--committed", action="store_true",
                    help="with --words: only candidates with p_commitment >= 0.5")
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    if args.words:
        if not args.label_id:
            raise SystemExit("candidates --words needs a label id")
        for term, c, ratio in words(args.label_set, args.label_id, args.data, args.top,
                                    args.committed):
            print(f"  {term:<32} {c:>5} candidates  x{ratio:,.1f} the background rate")
        return 0
    build(args.label_set, args.data, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
