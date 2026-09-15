#!/usr/bin/env python3
"""Draw paragraphs for asset labelling, and write a yes/no worksheet.

Reads  data/paragraphs.csv, data/clean/{doc_id}.txt,
       data/paragraph_emb.npy, data/emb_index.csv,
       inputs/taxonomy/assets.csv
Writes inputs/labels/samples.csv                    (the draw record, appended)
       inputs/labels/samples/{sample_id}.csv        (units with text)
       inputs/labels/worksheet_{sample_id}.csv      (one row per question)

The worksheet is one row per QUESTION, not per paragraph: "does this paragraph
involve <asset>?", answered yes or no. Deciding which of eight classes a
paragraph belongs to is a hard judgement made under time pressure; answering one
yes/no at a time is not, and it produces exactly the shape
inputs/labels/paragraph_labels.csv wants - one row per (paragraph, asset) with a
boolean. Every "no" is a real negative example, so nothing is wasted.

  python3 src/sample.py --purpose training --n-per-class 50 --key-file KEY
  python3 src/sample.py --purpose calibration --n 200 --seed 7

A note on where the candidates come from, because it decides what the labels
can mean. Candidates for the training draw are found by embedding each asset's
DEFINITION from inputs/taxonomy/assets.csv and taking paragraphs near it. That
definition is the same one the taxonomy already requires, so there is no second
list to keep in step with it. The query decides what gets LOOKED AT; the human
decides the answer. It is not a decision rule and it never becomes one - which
matters, because a sample drawn only from a query would teach a classifier the
query rather than the class. Two things guard that: a share of every class's
candidates is drawn at random rather than by similarity, and the calibration
draw is random throughout.
"""
import argparse, csv, hashlib, json, os, random, sys, time

import numpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import embed as E  # noqa: E402

# Blocks a person can actually answer a question about. A table row or a
# template field carries no proposition, so asking whether it "involves fiber"
# produces a coin flip rather than a label. They are still embedded and still
# classified later; they are just not what a human should be asked to judge.
LABELLABLE = ("narrative", "annex")

# Share of each class's training candidates drawn at random rather than by
# similarity to the definition. Without it the labels describe the neighbourhood
# of one sentence; with it the sample keeps a path to whatever the definition
# failed to say.
RANDOM_SHARE = 0.25

# No single document may supply more than this share of one class's candidates.
# The corpus has 146 documents and some are far longer than others; unchecked,
# top-k similarity returns the same annex of the same project repeatedly and the
# labels describe one operation rather than the class.
MAX_PER_DOC_SHARE = 0.12

# Extra classes asked about a paragraph that similarity drew for one class. See
# the note where the questions are built.
CONTROL_ASSETS = 2

SAMPLE_COLS = ["sample_id", "unit_id", "unit_type", "stratum", "text", "text_sha256"]
DRAW_COLS = ["sample_id", "purpose", "unit_type", "drawn_at", "stratification",
             "seed", "n_drawn", "source_run_id"]
WORKSHEET_COLS = ["sample_id", "paragraph_id", "asset_id", "asset_name", "label",
                  "project_ids", "section_path", "text"]


def load_corpus(data):
    with open(os.path.join(data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        paras = {r["paragraph_id"]: r for r in csv.DictReader(fh)}
    with open(os.path.join(data, "emb_index.csv"), newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    arr = numpy.load(os.path.join(data, "paragraph_emb.npy"), mmap_mode="r")
    if arr.shape[0] != len(index):
        raise SystemExit(f"sample: matrix has {arr.shape[0]} rows, index lists "
                         f"{len(index)} - they are from different runs")
    return paras, index, arr


def text_of(data, row, cache):
    did = row["doc_id"]
    if did not in cache:
        with open(os.path.join(data, "clean", f"{did}.txt"), encoding="utf-8") as fh:
            cache[did] = fh.read()
    body = cache[did][int(row["char_start"]):int(row["char_end"])]
    return " ".join(body.split())


def spread(ranked, paras, want, per_doc_cap):
    """Take `want` from a similarity-ranked list without letting one document
    dominate, and without taking only the very top.

    The top of a similarity ranking is the least informative part of a training
    sample: it is where the answer is obvious and where near-duplicates cluster.
    Walking the ranking and capping per document keeps the obvious cases, which
    are needed, while leaving room for the ones nearer the boundary, which are
    where a classifier is actually decided.
    """
    per_doc, out = {}, []
    for pid in ranked:
        if len(out) >= want:
            break
        doc = paras[pid]["doc_id"]
        if per_doc.get(doc, 0) >= per_doc_cap:
            continue
        per_doc[doc] = per_doc.get(doc, 0) + 1
        out.append(pid)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--inputs", default=os.path.join(ROOT, "inputs"))
    ap.add_argument("--purpose", default="training", choices=("training", "calibration"))
    ap.add_argument("--n-per-class", type=int, default=30,
                    help="training: candidates per asset class")
    ap.add_argument("--n", type=int, default=200,
                    help="calibration: paragraphs drawn at random, all classes asked")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--key-file", default="", help="credential for embedding the definitions")
    ap.add_argument("--model", default=E.DEFAULT_MODEL)
    ap.add_argument("--base-url", default=E.DEFAULT_BASE)
    ap.add_argument("--sample-id", default="")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    paras, index, arr = load_corpus(args.data)
    with open(os.path.join(args.inputs, "taxonomy", "assets.csv"),
              newline="", encoding="utf-8") as fh:
        assets = [r for r in csv.DictReader(fh) if r["active"].strip().lower() == "true"]

    # Only rows that are both embedded and worth a human's judgement.
    rows = [(int(r["row"]), r["paragraph_id"]) for r in index
            if paras.get(r["paragraph_id"], {}).get("block") in LABELLABLE]
    eligible_rows = numpy.array([i for i, _p in rows])
    eligible_ids = [p for _i, p in rows]
    print(f"eligible paragraphs ({'+'.join(LABELLABLE)}): {len(eligible_ids):,} "
          f"of {len(index):,} embedded", flush=True)

    sample_id = args.sample_id or f"{args.purpose}-{time.strftime('%Y%m%d')}-s{args.seed}"
    # paragraph_id -> {"near": {asset_id, ...}, "random": bool}
    drawn = {}
    strat_note = ""

    def mark(pid, asset_id=None):
        rec = drawn.setdefault(pid, {"near": set(), "random": False})
        if asset_id:
            rec["near"].add(asset_id)
        else:
            rec["random"] = True

    if args.purpose == "calibration":
        # Representative, so thresholds set on it mean something. No similarity
        # anywhere in this path: methodology section 6 rule 2 is explicit that a
        # score-stratified draw helps training and breaks calibration.
        for pid in rng.sample(eligible_ids, min(args.n, len(eligible_ids))):
            mark(pid)
        strat_note = f"uniform random over {'+'.join(LABELLABLE)} blocks"
    else:
        api_key = E.read_key(args.key_file)
        if not api_key:
            raise SystemExit(f"sample: --purpose training embeds the asset "
                             f"definitions, so it needs ${E.KEY_VAR} or --key-file")
        definitions = [a["definition"] for a in assets]
        vectors, _meta = E.embed_texts(definitions, model=args.model,
                                       api_key=api_key, base_url=args.base_url)
        sub = numpy.asarray(arr[eligible_rows], dtype="float32")
        per_doc_cap = max(2, int(args.n_per_class * MAX_PER_DOC_SHARE))
        n_random = int(args.n_per_class * RANDOM_SHARE)
        n_near = args.n_per_class - n_random

        for asset, vec in zip(assets, vectors):
            sims = sub @ numpy.asarray(vec, dtype="float32")
            ranked = [eligible_ids[i] for i in numpy.argsort(-sims)]
            near = spread(ranked, paras, n_near, per_doc_cap)
            pool = [p for p in eligible_ids if p not in set(near)]
            extra = rng.sample(pool, min(n_random, len(pool)))
            for pid in near:
                # A paragraph can be drawn by more than one class, which is the
                # point: several assets are routinely financed in one sentence.
                mark(pid, asset["asset_id"])
            for pid in extra:
                mark(pid)
            print(f"  {asset['asset_id']:<20} {len(near)} near + "
                  f"{len(extra)} random", flush=True)
        strat_note = (f"per-class: {n_near} nearest the asset definition "
                      f"(max {per_doc_cap}/document) + {n_random} uniform random")

    # Which questions to actually ask about each paragraph.
    #
    # Asking all eight classes about every drawn paragraph triples the work and
    # buys almost nothing: a paragraph pulled from beside the submarine-cable
    # definition is obviously not digital identity, and answering that costs a
    # person real seconds for information a classifier already has. So:
    #
    #   drawn near a definition -> ask the class that drew it, which is the
    #       informative question, plus CONTROL_ASSETS others chosen at random.
    #       Those controls are what stop the near-drawn set being pure positives
    #       for its own class and unlabelled for every other, and they catch the
    #       paragraph that finances a tower AND its backhaul fiber.
    #   drawn at random -> ask all eight. These are the representative rows, and
    #       a per-class negative rate can only be read off a complete set.
    by_id = {a["asset_id"]: a for a in assets}
    cache = {}
    units, questions = [], []
    for pid in sorted(drawn):
        meta = paras[pid]
        rec = drawn[pid]
        body = text_of(args.data, meta, cache)
        if rec["random"]:
            ask, stratum = [a["asset_id"] for a in assets], "random"
            if rec["near"]:
                stratum = "random|" + "|".join(sorted(rec["near"]))
        else:
            others = [a["asset_id"] for a in assets if a["asset_id"] not in rec["near"]]
            ask = sorted(rec["near"]) + rng.sample(
                others, min(CONTROL_ASSETS, len(others)))
            stratum = "near|" + "|".join(sorted(rec["near"]))
        units.append({"sample_id": sample_id, "unit_id": pid, "unit_type": "paragraph",
                      "stratum": stratum, "text": body,
                      "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()})
        for asset_id in ask:
            asset = by_id[asset_id]
            questions.append({
                "sample_id": sample_id, "paragraph_id": pid,
                "asset_id": asset_id, "asset_name": asset["asset_name"],
                "label": "", "project_ids": meta["project_ids"],
                "section_path": meta["section_path"], "text": body})

    os.makedirs(os.path.join(args.inputs, "labels", "samples"), exist_ok=True)
    unit_path = os.path.join(args.inputs, "labels", "samples", f"{sample_id}.csv")
    with open(unit_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SAMPLE_COLS, lineterminator="\n")
        w.writeheader(); w.writerows(units)

    work_path = os.path.join(args.inputs, "labels", f"worksheet_{sample_id}.csv")
    rng.shuffle(questions)      # so a whole class is not answered in one mood
    with open(work_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=WORKSHEET_COLS, lineterminator="\n")
        w.writeheader(); w.writerows(questions)

    draw_path = os.path.join(args.inputs, "labels", "samples.csv")
    existing = []
    if os.path.exists(draw_path):
        with open(draw_path, newline="", encoding="utf-8") as fh:
            existing = [r for r in csv.DictReader(fh) if r["sample_id"] != sample_id]
    existing.append({"sample_id": sample_id, "purpose": args.purpose,
                     "unit_type": "paragraph",
                     "drawn_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "stratification": strat_note, "seed": args.seed,
                     "n_drawn": len(units), "source_run_id": ""})
    with open(draw_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DRAW_COLS, lineterminator="\n")
        w.writeheader(); w.writerows(existing)

    docs = len({paras[p]["doc_id"] for p in drawn})
    print(f"\nsample_id:   {sample_id}")
    print(f"paragraphs:  {len(units):,} from {docs} documents")
    print(f"questions:   {len(questions):,}  "
          f"({len(questions) / max(len(units), 1):.1f} per paragraph on average)")
    print("the worksheet is shuffled, so stopping part-way still leaves a "
          "random subset across all classes")
    print(f"wrote {unit_path}\nwrote {work_path}\nwrote {draw_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
