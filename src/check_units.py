#!/usr/bin/env python3
"""A sheet of a measure's units for a person to grade, and the measurement
of the decision model against those grades: the precision and recall that
can be quoted, with their confidence intervals.

Draws from the current measure run (src/measure.py) by stratum of the
model's grade (3, 2, 1, 0) plus the feedback units, so the rare high grades
are seen and every figure can be weighted back to the graded pool. The
person sees the sentence with its lead-in, the paragraph around it, its
section and component, and the measure with its definition; never the
model's answer. They enter a grade on the set's scale and, if they wish, the
asset the sentence is about.

  ./wbg check cra                       draw: data/review/check_units_cra_s1.xlsx (+ .key.csv)
  ./wbg check cra --evaluate            read the grades back, store them as labels
                                        (data/labels/cra_units.csv, keyed by text and
                                        question hash), write data/reports/evaluate_units_cra.md

What the report says, and does not: precision and recall of "committed"
(and of "at least planned") against the person's grades, over the graded
pool (candidates and feedback units), with 95 per cent Wilson intervals on
the raw counts and stratum-weighted point estimates. Recall outside the pool
(units the search never reached) is not measured here; the rank-yield curve
in the measure report bounds it.
"""
import argparse, csv, datetime, hashlib, math, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
import measure  # noqa: E402
from review_sheets import cell_text  # noqa: E402

PER = {"3": 30, "2": 15, "1": 20, "0": 20, "feedback": 10}
KEY_COLS = ["row_id", "label_id", "unit_id", "paragraph_id", "text_sha256", "question_sha256",
            "stratum", "stratum_size", "stratum_drawn", "measure_dir"]
SHEET_COLS = [("row_id", 8), ("measure", 20), ("measure means", 40), ("project", 10),
              ("section", 20), ("component", 18), ("sentence", 70), ("paragraph", 70),
              ("grade", 8), ("asset", 14), ("note", 24)]
LABEL_COLS = ["label_set", "label_id", "unit_id", "text_sha256", "question_sha256", "grade",
              "asset", "source", "graded_on"]
THRESHOLDS = [0.3, 0.5, 0.7, 0.9]


def wilson(k, n, z=1.96):
    if not n:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def draw(units, per, seed):
    rng = random.Random(seed)
    strata = {}
    for u in units:
        if u["sent"] != "true":
            continue
        s = "feedback" if u["route"] == "feedback" else u["grade"]
        strata.setdefault(s, []).append(u)
    picked = []
    for s in sorted(strata):
        pool = sorted(strata[s], key=lambda u: u["unit_id"])
        n = min(per.get(s, 0), len(pool))
        for u in rng.sample(pool, n):
            picked.append((u, s, len(pool), n))
    return picked


def build(name, data, seed=1, per=PER, **kw):
    folder = measure.current_run(name, data, **kw)
    if not folder:
        raise SystemExit(f"check: no measure run for {name} as it stands; run ./wbg measure {name}")
    run = measure.Run(name, data, build=False, log=lambda *_: None, **kw)
    qsha = classify.question_shas(run.qs)
    with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
        units = list(csv.DictReader(fh))
    picked = draw(units, per, seed)
    rng = random.Random(seed)
    rng.shuffle(picked)
    idx = {r["unit_id"]: r for r in run.index}
    key, sheet, texts = [], [], {}
    lab = run.labels[0]
    for i, (u, stratum, size, drawn) in enumerate(picked, 1):
        para = run.paras[u["paragraph_id"]]
        doc = measure._text(texts, data, u["doc_id"])
        state = measure.state_for(idx[u["unit_id"]], para, u["text"],
                                  doc[int(para["char_start"]):int(para["char_end"])],
                                  run.names, "paragraph")
        key.append({"row_id": i, "label_id": u["label_id"], "unit_id": u["unit_id"],
                    "paragraph_id": u["paragraph_id"],
                    "text_sha256": hashlib.sha256(u["text"].encode("utf-8")).hexdigest(),
                    "question_sha256": qsha[u["label_id"]], "stratum": stratum,
                    "stratum_size": size, "stratum_drawn": drawn,
                    "measure_dir": os.path.basename(folder)})
        sheet.append({"row_id": i, "measure": lab["name"], "measure means": lab["definition"],
                      "project": u["project_ids"], "section": state.get("section", ""),
                      "component": state.get("component", ""), "sentence": u["text"],
                      "paragraph": state.get("paragraph", "")})
    base = where(data, "review", f"check_units_{name}_s{seed}")
    if os.path.exists(base + ".xlsx"):
        raise SystemExit(f"check: {base}.xlsx exists and may hold grades; use another --seed")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    with open(base + ".key.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=KEY_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(key)
    _write_xlsx(base + ".xlsx", sheet, run.scale, [a["id"] for a in run.assets])
    return base + ".xlsx", len(sheet)


def _write_xlsx(path, rows, scale, assets):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = "grade these"
    cols = [c for c, _ in SHEET_COLS]
    ws.append(cols)
    for r in rows:
        ws.append([cell_text(r.get(c)) for c in cols])
    for i, (_, width) in enumerate(SHEET_COLS, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = width
    for cell in ws[1]:
        cell.font = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrap
    g = ws.cell(1, cols.index("grade") + 1).column_letter
    dv = DataValidation(type="list", formula1='"' + ",".join(str(i) for i in range(len(scale)))
                        + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{g}2:{g}{len(rows) + 1}")
    a = ws.cell(1, cols.index("asset") + 1).column_letter
    dva = DataValidation(type="list", formula1='"' + ",".join(assets + ["none", "other"]) + '"',
                         allow_blank=True)
    ws.add_data_validation(dva)
    dva.add(f"{a}2:{a}{len(rows) + 1}")
    ws.freeze_panes = "A2"
    s = wb.create_sheet("grades")
    s.append(["grade", "meaning ({name} is the measure)"])
    for i, t in enumerate(scale):
        s.append([i, t])
    s.column_dimensions["B"].width = 120
    wb.save(path)


def read_sheet(xlsx):
    """{row_id: (grade, asset)} for the rows a person graded."""
    from openpyxl import load_workbook
    ws = load_workbook(xlsx, read_only=True)["grade these"]
    rows = ws.iter_rows(values_only=True)
    head = list(next(rows))
    gi, ai, ri = head.index("grade"), head.index("asset"), head.index("row_id")
    out = {}
    for r in rows:
        if r[gi] is None or str(r[gi]).strip() == "":
            continue
        out[str(r[ri])] = (int(float(str(r[gi]).strip())), (str(r[ai]).strip() if r[ai] else ""))
    return out


def evaluate(name, data, seed=1, **kw):
    base = where(data, "review", f"check_units_{name}_s{seed}")
    with open(base + ".key.csv", newline="", encoding="utf-8") as fh:
        key = list(csv.DictReader(fh))
    graded = read_sheet(base + ".xlsx")
    if not graded:
        raise SystemExit(f"check: no grade entered in {base}.xlsx")
    # store the labels, keyed by text and question, never by position
    lpath = where(data, "labels", f"{name}_units.csv")
    current = {}
    if os.path.exists(lpath):
        with open(lpath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                current[(r["label_id"], r["text_sha256"], r["question_sha256"])] = r
    today = datetime.date.today().isoformat()
    for k in key:
        if k["row_id"] in graded:
            g, a = graded[k["row_id"]]
            current[(k["label_id"], k["text_sha256"], k["question_sha256"])] = {
                "label_set": name, "label_id": k["label_id"], "unit_id": k["unit_id"],
                "text_sha256": k["text_sha256"], "question_sha256": k["question_sha256"],
                "grade": g, "asset": a, "source": os.path.basename(base), "graded_on": today}
    os.makedirs(os.path.dirname(lpath), exist_ok=True)
    with open(lpath + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LABEL_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(current.values(), key=lambda r: (r["label_id"], r["text_sha256"])))
    os.replace(lpath + ".part", lpath)
    # measure the run the sheet was drawn from
    mdir = where(data, "measures", key[0]["measure_dir"])
    with open(os.path.join(mdir, "units.csv"), newline="", encoding="utf-8") as fh:
        units = {u["unit_id"]: u for u in csv.DictReader(fh)}
    with open(os.path.join(mdir, "manifest.json"), encoding="utf-8") as fh:
        import json
        scale = json.load(fh)["scale"]
    top = len(scale) - 1
    rows = []
    for k in key:
        if k["row_id"] not in graded:
            continue
        u = units[k["unit_id"]]
        g, a = graded[k["row_id"]]
        rows.append({"grade": g, "asset": a, "w": int(k["stratum_size"]) / int(k["stratum_drawn"]),
                     "p": [float(u[f"p{i}"]) for i in range(len(scale))],
                     "model_grade": int(u["grade"]), "link": u.get("asset_link", ""),
                     "stratum": k["stratum"]})
    lines = [f"# {name}: the decision model against {len(rows)} graded units", "",
             f"Drawn from {key[0]['measure_dir']} by stratum of the model's grade; weighted "
             f"point estimates use stratum size over rows drawn; intervals are 95 per cent "
             f"Wilson on the raw counts. Recall is within the graded pool (candidates and "
             f"feedback units), not the corpus.", ""]
    for k in (top, top - 1):
        pos = [r for r in rows if r["grade"] >= k]
        lines += [f"## Positive = person graded {k} or above ({len(pos)} of {len(rows)} rows)", "",
                  "| model says | precision (95% CI) | recall (95% CI) | weighted precision | "
                  "weighted recall | n predicted |", "|---|---|---|---|---|---|"]
        for label, pred in [(f"most likely grade >= {k}", lambda r, k=k: r["model_grade"] >= k)] + \
                [(f"P(grade >= {k}) >= {t}", lambda r, t=t, k=k: sum(r["p"][k:]) >= t)
                 for t in THRESHOLDS]:
            tp = sum(1 for r in rows if pred(r) and r["grade"] >= k)
            pp = sum(1 for r in rows if pred(r))
            ap = len(pos)
            wtp = sum(r["w"] for r in rows if pred(r) and r["grade"] >= k)
            wpp = sum(r["w"] for r in rows if pred(r))
            wap = sum(r["w"] for r in pos)
            pl, ph = wilson(tp, pp)
            rl, rh = wilson(tp, ap)
            f = lambda x: "-" if x is None else f"{x:.2f}"
            lines.append(f"| {label} | {f(tp / pp if pp else None)} ({f(pl)}-{f(ph)}) | "
                         f"{f(tp / ap if ap else None)} ({f(rl)}-{f(rh)}) | "
                         f"{f(wtp / wpp if wpp else None)} | {f(wtp / wap if wap else None)} | {pp} |")
        lines.append("")
    withasset = [r for r in rows if r["asset"] and r["grade"] >= top]
    if withasset:
        agree = sum(1 for r in withasset if r["asset"] == r["link"])
        lines += [f"## Asset link on committed units the person also gave an asset: "
                  f"{agree} of {len(withasset)} agree with the pipeline's link", ""]
    by = {}
    for r in rows:
        by.setdefault(r["stratum"], []).append(r)
    lines += ["## By stratum: rows, person's mean grade", ""]
    for s, rs in sorted(by.items()):
        lines.append(f"- model {s}: {len(rs)} rows, person's mean grade "
                     f"{sum(r['grade'] for r in rs) / len(rs):.2f}, graded committed "
                     f"{sum(1 for r in rs if r['grade'] >= top)}")
    out = where(data, "reports", f"evaluate_units_{name}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out, len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--context", default="sentence", choices=measure.CONTEXTS)
    ap.add_argument("--gate", default="none", choices=measure.GATES)
    ap.add_argument("--asset-min", type=float, default=0.0)
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    kw = dict(context=args.context, gate=args.gate, asset_min=args.asset_min)
    if args.evaluate:
        out, n = evaluate(args.label_set, args.data, args.seed, **kw)
        print(f"{n} graded rows; wrote {out}")
    else:
        path, n = build(args.label_set, args.data, args.seed, **kw)
        print(f"wrote {path}: {n} rows to grade")
    return 0


if __name__ == "__main__":
    sys.exit(main())
