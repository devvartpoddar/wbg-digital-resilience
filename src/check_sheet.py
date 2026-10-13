#!/usr/bin/env python3
"""A sheet of paragraphs for a person to grade, so the search and the
classifier can be measured and their cut-offs chosen (src/evaluate.py).

For each label, paragraphs are drawn from every grade the classifier gave
(its most likely grade), plus paragraphs the search scored high but the
classifier put at the lowest grade, where the two disagree. Drawing from every
stratum, not at random, puts enough of the rare high grades in front of the
person; the key file records each stratum's size so evaluate.py can weight
the sample back to the whole corpus.

The person sees the passage, its context and the label, never the model's
answer, so the grade is their own.

Reads  data/classify/<set>-<version>/  (the newest), data/search/<set>-<version>/
       (the newest, if any), data/appraisal/, inputs/taxonomy/<set>.csv, <set>_scale.csv
Writes data/review/check_<set>_s<seed>.xlsx       the sheet: fill the grade column
       data/review/check_<set>_s<seed>.key.csv    what was drawn and from where

  python3 src/check_sheet.py assets             5 per stratum, seed 1
  python3 src/check_sheet.py assets --per 8 --seed 2
"""
import argparse, csv, glob, os, random, sys

import numpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
from review_sheets import cell_text  # noqa: E402
from search import TAXONOMY, _rows  # noqa: E402

# A paragraph the search places in this top share for a label counts as a
# search hit when looking for disagreements. Only the sampling uses it.
SEARCH_TOP = 0.05
KEY_COLS = ["row_id", "label_id", "paragraph_id", "text_sha256", "stratum", "stratum_size",
            "stratum_drawn", "search_best", "classify_dir", "search_dir"]
SHEET_COLS = [("row_id", 8), ("label", 22), ("label means", 40), ("paragraph_id", 18),
              ("project", 10), ("section", 22), ("component", 18), ("passage", 90),
              ("grade", 8), ("note", 30)]


def newest(data, kind, name):
    """The most recently written run folder for the label set, or None."""
    runs = [d for d in glob.glob(where(data, kind, f"{name}-*"))
            if os.path.exists(os.path.join(d, "manifest.json"))]
    return max(runs, key=os.path.getmtime) if runs else None


def search_best(folder, data):
    """{(label_id, paragraph_id): best cosine over the label's queries and the
    paragraph's units}, or {} without a search run."""
    if not folder:
        return {}
    scores = numpy.load(os.path.join(folder, "scores.npy"), mmap_mode="r")
    queries = _rows(os.path.join(folder, "queries.csv"))
    index = _rows(where(data, "embeddings", "sentence_index.csv"))
    out = {}
    for lab in sorted({q["label_id"] for q in queries}):
        cols = [i for i, q in enumerate(queries) if q["label_id"] == lab]
        best = numpy.asarray(scores[:, cols]).max(axis=1)
        for r, s in zip(index, best):
            k = (lab, r["paragraph_id"])
            if s > out.get(k, -2.0):
                out[k] = float(s)
    return out


def draw(scores, best, per, seed):
    """[(row, stratum, stratum_size)] per label: `per` paragraphs from each
    classifier grade, and `per` the search ranks high at the lowest grade."""
    rng = random.Random(seed)
    by_label = {}
    for r in scores:
        by_label.setdefault(r["label_id"], []).append(r)
    picked = []
    for lab in sorted(by_label):
        rows = sorted(by_label[lab], key=lambda r: r["paragraph_id"])
        ranked = sorted((best[(lab, r["paragraph_id"])] for r in rows
                         if (lab, r["paragraph_id"]) in best), reverse=True)
        cut = ranked[int(len(ranked) * SEARCH_TOP)] if ranked else None
        strata = {}
        for r in rows:
            s = f"grade {r['grade']}"
            if r["grade"] == "0" and cut is not None and \
                    best.get((lab, r["paragraph_id"]), -2) >= cut:
                s = "grade 0, search high"
            strata.setdefault(s, []).append(r)
        for s in sorted(strata):
            pool = strata[s]
            for r in rng.sample(pool, min(per, len(pool))):
                picked.append((r, s, len(pool), min(per, len(pool))))
    return picked


def build(name, data, per=5, seed=1, taxonomy=TAXONOMY):
    cdir = newest(data, "classify", name)
    if not cdir:
        raise SystemExit(f"check_sheet: no classification of {name} yet - run "
                         f"./wbg classify {name} first")
    sdir = newest(data, "search", name)
    labels, scale = classify.load_labels(name, taxonomy)
    lab = {x["id"]: x for x in labels}
    scores = _rows(os.path.join(cdir, "scores.csv"))
    best = search_best(sdir, data)
    paras = {p["row"]["paragraph_id"]: p for p in classify.paragraphs(data)}
    picked = draw(scores, best, per, seed)
    rng = random.Random(seed)
    rng.shuffle(picked)                # mixed order, so strata are not guessable
    key, sheet = [], []
    for i, (r, stratum, size, drawn) in enumerate(picked, 1):
        p = paras[r["paragraph_id"]]
        key.append({"row_id": i, "label_id": r["label_id"], "paragraph_id": r["paragraph_id"],
                    "text_sha256": p["row"]["text_sha256"], "stratum": stratum,
                    "stratum_size": size, "stratum_drawn": drawn,
                    "search_best": best.get((r["label_id"], r["paragraph_id"]), ""),
                    "classify_dir": os.path.basename(cdir),
                    "search_dir": os.path.basename(sdir) if sdir else ""})
        L = lab[r["label_id"]]
        sheet.append({"row_id": i, "label": L["name"], "label means": L["definition"],
                      "paragraph_id": r["paragraph_id"], "project": r["project_ids"],
                      "section": p["state"].get("section", ""),
                      "component": p["state"].get("component", ""),
                      "passage": p["state"]["passage"]})
    base = where(data, "review", f"check_{name}_s{seed}")
    if os.path.exists(base + ".xlsx"):
        raise SystemExit(f"check_sheet: {base}.xlsx exists and may hold grades; use "
                         f"another --seed")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    with open(base + ".key.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=KEY_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(key)
    _write_xlsx(base + ".xlsx", sheet, scale)
    return base + ".xlsx", len(sheet)


def _write_xlsx(path, rows, scale):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "grade these"
    ws.append([c for c, _ in SHEET_COLS])
    for r in rows:
        ws.append([cell_text(r.get(c)) for c, _ in SHEET_COLS])
    for i, (_, width) in enumerate(SHEET_COLS, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = width
    for cell in ws[1]:
        cell.font = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrap
    col = ws.cell(1, [c for c, _ in SHEET_COLS].index("grade") + 1).column_letter
    dv = DataValidation(type="list", formula1='"' + ",".join(str(i) for i in range(len(scale)))
                        + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{col}2:{col}{len(rows) + 1}")
    ws.freeze_panes = "A2"
    g = wb.create_sheet("grades")
    g.append(["grade", "meaning ({name} is the label in that row)"])
    for i, s in enumerate(scale):
        g.append([i, s])
    g.column_dimensions["B"].width = 120
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--per", type=int, default=5, help="paragraphs per label and stratum")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    path, n = build(args.label_set, args.data, args.per, args.seed)
    print(f"wrote {path}: {n} rows to grade")
    return 0


if __name__ == "__main__":
    sys.exit(main())
