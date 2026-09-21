#!/usr/bin/env python3
"""A2b - choose the verb_subtree variant by measurement, not by argument.

Reads  analysis/a2_segmentation/check_set.csv          hand counts, no text
       <run>/intermediate/prepared/clauses.csv        one variant's output
       <run>/paragraphs.csv, <run>/clean/{doc_id}.txt (through --data)
Writes <out>/splitter_choice_report.txt
       <run>/report.txt                               per variant, via
                                                      check_segmentation.py

  python3 analysis/a2_segmentation/splitter_choice.py \
      --runs split-1=data/scratch/seg1/data,split-2=data/scratch/seg2/data

WHY THIS FILE EXISTS. `src/segment.py --splitter-version` names two variants of
the verb_subtree boundary condition, and which one is right is a measurement:
the variant with the lower four-token share that still passes at least 16 of the
20 hand-counted paragraphs. check_segmentation.py scores the second half of that
criterion and only ever reads the canonical
`data/intermediate/prepared/clauses.csv`, so it cannot compare two variants side
by side. This runs it once per variant against that variant's own table and adds
the two numbers it does not produce - the share of clauses at four tokens or
fewer, and clauses per paragraph.

WHAT A TOKEN IS HERE. The whitespace token of the clause text as it resolves
from `data/clean/{doc_id}.txt`, not the parser's tokenisation. clauses.csv
stores offsets rather than text and carries no token count, so there is no
column to read; the probe's 14.8% was measured on the same plain reading of the
text. IF THE INTENT WAS THE PARSER'S TOKEN COUNT, every figure in the <=4 column
moves by a few per cent and the RANKING between the variants does not - both
variants are counted the same way.

NO CLAUSE TEXT IS WRITTEN HERE. Counts and rule names only.
"""
import argparse
import csv
import os
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECK = os.path.join(ROOT, "analysis", "a2_segmentation", "check_segmentation.py")
SHORT = 4


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


def read_clauses(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def measure(data_dir, clauses_path, log):
    """Token counts, four-token share, split-rule tally, clauses per paragraph."""
    paras = read_paragraphs(os.path.join(data_dir, "paragraphs.csv"))
    rows = read_clauses(clauses_path)
    bodies, tokens = {}, []
    rules = Counter()
    per_para = Counter()
    unresolved = 0
    for r in rows:
        meta = paras.get(r["paragraph_id"])
        if meta is None:
            unresolved += 1
            continue
        doc, _block, base = meta
        if doc not in bodies:
            with open(os.path.join(data_dir, "clean", f"{doc}.txt"),
                      encoding="utf-8") as bf:
                bodies[doc] = bf.read()
        a = base + int(r["char_start"])
        b = base + int(r["char_end"])
        tokens.append(len(bodies[doc][a:b].split()))
        rules[r["split_rule"]] += 1
        per_para[r["paragraph_id"]] += 1
    if unresolved:
        log(f"  clauses whose paragraph is not in paragraphs.csv: {unresolved}")
    short = sum(1 for n in tokens if n <= SHORT)
    n = len(tokens) or 1
    return {
        "clauses": len(rows),
        "paragraphs": len(per_para),
        "clauses_per_paragraph": len(rows) / max(len(per_para), 1),
        "short": short,
        "short_share": 100.0 * short / n,
        "median_tokens": sorted(tokens)[len(tokens) // 2] if tokens else 0,
        "rules": rules,
    }


def score_variant(data_dir, log):
    """check_segmentation.py against this variant's table, in place."""
    check_set = os.path.join(data_dir, "check_set.csv")
    src = os.path.join(ROOT, "analysis", "a2_segmentation", "check_set.csv")
    if not os.path.exists(check_set):
        with open(src, newline="", encoding="utf-8") as a, \
                open(check_set, "w", newline="", encoding="utf-8") as b:
            b.write(a.read())
    proc = subprocess.run([sys.executable, CHECK, "--data", data_dir,
                           "--out", data_dir],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"  check_segmentation failed: {proc.stderr.strip()[:200]}")
        return None
    met = verdict = "?"
    for line in proc.stdout.splitlines():
        if line.startswith("met "):
            met = line.split()[1]
        elif line.startswith("VERDICT"):
            verdict = line.split()[1]
    return {"met": met, "verdict": verdict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True,
                    help="version=<data dir> pairs, comma separated")
    ap.add_argument("--out", default=os.path.join(ROOT, "analysis", "a2_segmentation"))
    args = ap.parse_args()

    lines = []

    def log(msg):
        print(msg, flush=True)
        lines.append(msg)

    runs = []
    for pair in args.runs.split(","):
        version, data_dir = pair.split("=", 1)
        runs.append((version.strip(), data_dir.strip()))

    log("A2b - splitter variant choice")
    log("=" * 78)
    log("")
    log("ASSUMPTIONS")
    log("-" * 78)
    log("1. A token here is a whitespace-delimited token of the clause as it")
    log("   resolves from data/clean/. clauses.csv carries offsets, not text, and")
    log("   no token count. Both variants are counted the same way, so the")
    log("   comparison holds even if the absolute share differs from the probe's")
    log(f"   by a few points. If the parser's token count was meant instead, the")
    log("   <=4 column moves and the ranking does not.")
    log("")
    log("2. The acceptance half of the criterion is check_segmentation.py's, run")
    log("   as a subprocess once per variant against that variant's own table.")
    log("   Its report lands beside that table under data/scratch/, not in the")
    log("   repository; the variant that is kept is re-scored into")
    log("   analysis/a2_segmentation/report.txt so the committed report describes")
    log("   the splitter that is actually in the tree.")
    log("")
    log("3. The four-token cut is the probe's, not a threshold anyone tuned.")
    log("")
    log("MEASUREMENT")
    log("-" * 78)
    results = []
    for version, data_dir in runs:
        clauses = os.path.join(data_dir, "intermediate", "prepared", "clauses.csv")
        log(f"variant {version}  {clauses}")
        m = measure(data_dir, clauses, log)
        s = score_variant(data_dir, log)
        m["version"], m["score"] = version, s
        results.append(m)
        log(f"  clauses {m['clauses']:,}  paragraphs {m['paragraphs']:,}  "
            f"clauses/paragraph {m['clauses_per_paragraph']:.2f}")
        log(f"  <= {SHORT} tokens {m['short']:,}  ({m['short_share']:.1f}%)  "
            f"median {m['median_tokens']} tokens")
        log(f"  check set met {s['met'] if s else '?'} of 20  "
            f"verdict {s['verdict'] if s else '?'}")
        log("  split_rule tally")
        for rule, n in m["rules"].most_common():
            log(f"    {rule:<14}{n:>9,}  {100.0 * n / max(m['clauses'], 1):>5.1f}%")
        log("")

    log("TABLE")
    log("-" * 78)
    log(f"{'variant':<10}{'clauses':>10}{'paras':>9}{'cl/para':>9}{'<=4 tok':>10}"
        f"{'<=4 share':>11}{'met/20':>8}  verdict")
    for m in results:
        s = m["score"] or {}
        log(f"{m['version']:<10}{m['clauses']:>10,}{m['paragraphs']:>9,}"
            f"{m['clauses_per_paragraph']:>9.2f}{m['short']:>10,}"
            f"{m['short_share']:>10.1f}%{str(s.get('met', '?')) + '/20':>8}  "
            f"{s.get('verdict', '?')}")
    log("")

    # The criterion, in the card's order: lower four-token share, but only among
    # variants that still pass at least 16 of 20. A variant that meets the check
    # set at 16+ and cuts the four-token share is the one to keep; if it drops
    # below 16 the measurement keeps split-1 and says so.
    passing = [m for m in results
               if m["score"] and int(str(m["score"].get("met", 0) or 0)) >= 16]
    chosen = None
    if passing:
        chosen = min(passing, key=lambda m: m["short_share"])
    log("CHOICE")
    log("-" * 78)
    if chosen is None:
        log("No variant reached 16 of 20 on the check set. The criterion cannot")
        log("be applied as written; split-1 is retained as the variant the")
        log("downstream tables were built on, and this is a FAILED measurement")
        log("rather than a decision.")
    else:
        log(f"{chosen['version']} - it passes {chosen['score']['met']} of 20 and "
            f"has the lower four-token share at {chosen['short_share']:.1f}%")
        for m in passing:
            if m is not chosen:
                log(f"  (also passing: {m['version']} at {m['short_share']:.1f}%, "
                    f"met {m['score']['met']} of 20)")
    log("")

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "splitter_choice_report.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
