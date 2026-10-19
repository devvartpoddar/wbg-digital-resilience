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
                                        and the calibration data/labels/cra_calibration.json
  ./wbg check cra --elusion             draw units the funnel never read, from just below
                                        the pool threshold and from far below it:
                                        data/review/check_units_cra_elusion_s1.xlsx
  ./wbg check cra --elusion --evaluate  what the pool missed, with intervals

What the report says, and does not: precision and recall of "committed"
(and of "at least planned") against the person's grades, over the graded
pool (candidates and feedback units), with 95 per cent Wilson intervals on
the raw counts and stratum-weighted point estimates. The calibration is a
Platt scaling (a logistic fit of the person's grade on the model's
log-odds) with the Brier score before and after; the measure stage reads it
and adds p_committed_calibrated to every unit. Recall outside the pool is
the elusion sheet's job: a random draw of the units the funnel never read,
in two strata (near: within ELUSION_BAND below the pool threshold; far: the
rest), weighted back to their strata, which is how a technology-assisted
review is signed off.
"""
import argparse, csv, datetime, hashlib, math, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
import measure  # noqa: E402
from review_sheets import cell_text  # noqa: E402

PER = {"3": 30, "2": 15, "1": 20, "0": 20, "feedback": 10}
ELUSION_PER = {"near": 25, "far": 25}
ELUSION_BAND = 0.10      # "near": best cosine within this much below the pool threshold
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


def draw_elusion(run, units, per, seed):
    """Units the funnel never read, by stratum of their best cosine over the
    measure's phrases: near (within ELUSION_BAND below the pool threshold)
    and far (the rest). Returns rows shaped like the run's own."""
    import search
    rng = random.Random(seed)
    seen = {u["unit_id"] for u in units}
    folder, _ = search.run_folder(run.name, run.data)
    best = {h["unit_id"]: float(h["score"])
            for h in search.hits(folder, run.data, labels=[run.labels[0]["id"]])}
    floor = run.settings["min_cosine"] or 1.0
    strata = {"near": [], "far": []}
    for r in run.index:
        uid = r["unit_id"]
        if uid in seen or uid not in best:
            continue
        strata["near" if best[uid] >= floor - ELUSION_BAND else "far"].append(r)
    picked = []
    for s in sorted(strata):
        pool = sorted(strata[s], key=lambda r: r["unit_id"])
        n = min(per.get(s, 0), len(pool))
        for r in rng.sample(pool, n):
            row = run.base_row(r, "elusion", "", "", f"{best[r['unit_id']]:.6f}")
            picked.append((row, s, len(pool), n))
    return picked


def build(name, data, seed=1, per=PER, elusion=False, **kw):
    folder = measure.current_run(name, data, **kw)
    if not folder:
        raise SystemExit(f"check: no measure run for {name} as it stands; run ./wbg measure {name}")
    run = measure.Run(name, data, build=False, log=lambda *_: None, **kw)
    qsha = classify.question_shas(run.qs)
    with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
        units = list(csv.DictReader(fh))
    picked = draw_elusion(run, units, ELUSION_PER, seed) if elusion else draw(units, per, seed)
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
    base = where(data, "review", f"check_units_{name}{'_elusion' if elusion else ''}_s{seed}")
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


def platt(p, y):
    """Platt scaling: (a, b) of sigmoid(a * logit(p) + b) fitted to the 0/1
    grades y by damped Newton steps on the log-likelihood, with the usual
    smoothed targets so a perfectly separated sample still fits. Returns
    None with fewer than two of each class."""
    import numpy
    p = numpy.clip(numpy.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    y = numpy.asarray(y, dtype=float)
    n1, n0 = y.sum(), len(y) - y.sum()
    if n1 < 2 or n0 < 2:
        return None
    t = numpy.where(y > 0, (n1 + 1) / (n1 + 2), 1 / (n0 + 2))
    x = numpy.log(p / (1 - p))

    def loss(a, b):
        z = a * x + b
        return float((numpy.logaddexp(0, z) - t * z).sum())
    a, b = 1.0, 0.0
    for _ in range(200):
        z = 1 / (1 + numpy.exp(-numpy.clip(a * x + b, -30, 30)))
        g = numpy.array([((z - t) * x).sum(), (z - t).sum()])
        w = z * (1 - z)
        h = numpy.array([[(w * x * x).sum(), (w * x).sum()], [(w * x).sum(), w.sum()]])
        h += numpy.eye(2) * 1e-6
        step = numpy.linalg.solve(h, g)
        base, scale = loss(a, b), 1.0
        while scale > 1e-4 and loss(a - scale * step[0], b - scale * step[1]) > base:
            scale /= 2
        a, b = a - scale * step[0], b - scale * step[1]
        if abs(scale * step).max() < 1e-8:
            break
    return float(a), float(b)


def calibrated(p, ab):
    import math
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    z = max(-30.0, min(30.0, ab[0] * math.log(p / (1 - p)) + ab[1]))
    return 1 / (1 + math.exp(-z))


def calibration_path(name, data):
    return where(data, "labels", f"{name}_calibration.json")


def evaluate(name, data, seed=1, elusion=False, **kw):
    if elusion:
        return evaluate_elusion(name, data, seed, **kw)
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
    # Platt scaling of P(grade >= k) on the person's grades, with the Brier
    # score before and after; stored for the measure stage to apply.
    import json
    cal = {"label_set": name, "measure_dir": key[0]["measure_dir"], "graded_rows": len(rows),
           "graded_on": today, "grades": {}}
    lines += ["## Calibration (Platt scaling of P(grade >= k), Brier score before -> after)", ""]
    for k in (top, top - 1):
        ps = [sum(r["p"][k:]) for r in rows]
        ys = [1 if r["grade"] >= k else 0 for r in rows]
        ab = platt(ps, ys)
        if ab is None:
            lines.append(f"- grade >= {k}: too few of one class to fit")
            continue
        raw = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)
        after = sum((calibrated(p, ab) - y) ** 2 for p, y in zip(ps, ys)) / len(ps)
        cal["grades"][str(k)] = {"a": ab[0], "b": ab[1], "brier_raw": raw,
                                 "brier_calibrated": after, "n": len(ps)}
        lines.append(f"- grade >= {k}: a = {ab[0]:.3f}, b = {ab[1]:.3f}; Brier {raw:.3f} -> "
                     f"{after:.3f} (fitted and scored on the same {len(ps)} rows: an "
                     f"in-sample figure)")
    lines.append("")
    cpath = calibration_path(name, data)
    with open(cpath + ".part", "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=1, sort_keys=True)
    os.replace(cpath + ".part", cpath)
    out = where(data, "reports", f"evaluate_units_{name}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out, len(rows)


def evaluate_elusion(name, data, seed=1, **kw):
    """What the pool missed: the elusion sheet's grades, weighted back to
    their strata."""
    base = where(data, "review", f"check_units_{name}_elusion_s{seed}")
    with open(base + ".key.csv", newline="", encoding="utf-8") as fh:
        key = list(csv.DictReader(fh))
    graded = read_sheet(base + ".xlsx")
    if not graded:
        raise SystemExit(f"check: no grade entered in {base}.xlsx")
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
    mdir = where(data, "measures", key[0]["measure_dir"])
    with open(os.path.join(mdir, "manifest.json"), encoding="utf-8") as fh:
        import json
        man = json.load(fh)
    top = len(man["scale"]) - 1
    with open(os.path.join(mdir, "units.csv"), newline="", encoding="utf-8") as fh:
        in_pool = sum(1 for u in csv.DictReader(fh) if u["sent"] == "true" and u["grade"] == str(top))
    lines = [f"# {name}: what the pool missed, from {len(graded)} graded units it never read", "",
             f"Drawn from the units outside {key[0]['measure_dir']} (pool: cosine >= "
             f"{man['min_cosine']}), in two strata: near (within {ELUSION_BAND} below the "
             f"threshold) and far (the rest). Each stratum's count is weighted by its size "
             f"over rows drawn; intervals are 95 per cent Wilson on the raw counts.", "",
             "| stratum | units | graded | committed | planned or more | committed share (95% CI) "
             "| estimated committed units in stratum |", "|---|---|---|---|---|---|---|"]
    est_total = 0.0
    for s in sorted({k["stratum"] for k in key}):
        ks = [k for k in key if k["stratum"] == s and k["row_id"] in graded]
        if not ks:
            continue
        size, n = int(ks[0]["stratum_size"]), len(ks)
        c = sum(1 for k in ks if graded[k["row_id"]][0] >= top)
        pl = sum(1 for k in ks if graded[k["row_id"]][0] >= top - 1)
        lo, hi = wilson(c, n)
        est = size * c / n
        est_total += est
        lines.append(f"| {s} | {size:,} | {n} | {c} | {pl} | {c / n:.2f} ({lo:.2f}-{hi:.2f}) | "
                     f"{est:,.0f} (up to {size * hi:,.0f}) |")
    lines += ["", f"The model found {in_pool} committed units inside the pool (its own grade, not "
                  f"a person's). The strata estimate {est_total:,.0f} committed units outside it, "
                  f"so the pool's recall of the corpus is about "
                  f"{in_pool / (in_pool + est_total):.0%} if the model's count inside is right; "
                  f"a zero count in a stratum bounds it only by the interval's upper end.", ""]
    out = where(data, "reports", f"evaluate_elusion_{name}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out, len(graded)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--elusion", action="store_true",
                    help="units the funnel never read, from just below and far below the pool")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--context", default="sentence", choices=measure.CONTEXTS)
    ap.add_argument("--gate", default="none", choices=measure.GATES)
    ap.add_argument("--asset-min", type=float, default=0.0)
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    kw = dict(context=args.context, gate=args.gate, asset_min=args.asset_min)
    if args.evaluate:
        out, n = evaluate(args.label_set, args.data, args.seed, elusion=args.elusion, **kw)
        print(f"{n} graded rows; wrote {out}")
    else:
        path, n = build(args.label_set, args.data, args.seed, elusion=args.elusion, **kw)
        print(f"wrote {path}: {n} rows to grade")
    return 0


if __name__ == "__main__":
    sys.exit(main())
