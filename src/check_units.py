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
import argparse, csv, datetime, hashlib, json, math, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
import measure  # noqa: E402

CELL_MAX = 32_000   # Excel's limit on a cell's characters


def cell_text(value):
    """Excel refuses control characters. A form feed is a page break in the
    raw file, which the reader wants to see, so it gets a visible marker."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    text = str(value if value is not None else "").replace("\f", "\n[page break]\n")
    return ILLEGAL_CHARACTERS_RE.sub("", text)[:CELL_MAX]

PER = {"3": 30, "2": 15, "1": 20, "0": 20, "feedback": 10}
ELUSION_PER = {"near": 25, "far": 25}
ELUSION_BAND = 0.10      # "near": best cosine within this much below the pool threshold
KEY_COLS = ["row_id", "label_id", "unit_id", "paragraph_id", "text_sha256", "question_sha256",
            "stratum", "stratum_size", "stratum_drawn", "measure_dir"]
SHEET_COLS = [("row_id", 8), ("measure", 20), ("measure means", 40), ("project", 10),
              ("section", 20), ("component", 18), ("sentence", 70), ("paragraph", 70),
              ("grade", 8), ("asset", 14), ("type", 18), ("note", 24)]
LABEL_COLS = ["label_set", "label_id", "unit_id", "text_sha256", "question_sha256", "grade",
              "asset", "type", "source", "graded_on"]
THRESHOLDS = [0.15, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
# The band design (--design bands): drawn by the model's probability of the
# top grade, densest where a cut-off would be chosen, so the floor can be
# read off the graded sheet. Each band is (stratum, low, high, rows).
BANDS = [("p<0.15", 0.0, 0.15, 5), ("p0.15-0.4", 0.15, 0.4, 8), ("p0.4-0.6", 0.4, 0.6, 12),
         ("p0.6-0.8", 0.6, 0.8, 10), ("p0.8+", 0.8, 1.01, 5)]
# The floor design (--design floor): about 20 units a measure in bands around
# its commit floor (labels.commit_floor), to check a chosen floor; a measure
# without one is drawn in BANDS at about half the rows.
FLOOR_STEPS = [(-1.0, -0.2, 2), (-0.2, -0.1, 3), (-0.1, 0.0, 5), (0.0, 0.1, 5), (0.1, 0.25, 3),
               (0.25, 1.0, 2)]


def floor_bands(floor, scale=1.0):
    """[(stratum, low, high, rows)] around a commit floor, or BANDS halved;
    `scale` multiplies every band's rows (the tuning draw: 1.4, about 28)."""
    rows = lambda n: int(n * scale + 0.5)
    if floor is None:
        return [(s, lo, hi, rows((n + 1) // 2)) for s, lo, hi, n in BANDS]
    out = []
    for a, b, n in FLOOR_STEPS:
        lo, hi = max(0.0, floor + a), min(1.01, floor + b)
        if hi > lo:
            out.append((f"p{lo:.2f}-{min(hi, 1.0):.2f}", lo, hi, rows(n)))
    return out


TUNE_SCALE = 1.4   # the tuning draw: the floor bands at 1.4 times (about 28), links over the whole range


LINK_FLOOR_BANDS = [("p0.5-0.6", 0.5, 0.6, 8), ("p0.6-0.7", 0.6, 0.7, 8), ("p0.7-0.8", 0.7, 0.8, 8),
                    ("p0.8+", 0.8, 1.01, 6)]
LINK_BANDS = [("p<0.3", 0.0, 0.3, 10), ("p0.3-0.5", 0.3, 0.5, 15), ("p0.5-0.7", 0.5, 0.7, 15),
              ("p0.7-0.9", 0.7, 0.9, 15), ("p0.9+", 0.9, 1.01, 15)]
HOW_TO = [
    "Each measure tab: read the sentence (with its paragraph, section and component) and enter a "
    "grade on the measure's scale (tab 'grades'). Asset and type are optional (tab 'types' "
    "defines the types). Leave a row blank to skip it.",
    "Rows were drawn by the model's probability that the sentence is a commitment, more of them "
    "near where a cut-off would fall; the model's answer is not shown.",
    "Tab 'package links': does carrying out the package deliver or apply the commitment? "
    "Enter yes, no or unsure.",
    "Do not reorder, insert or delete rows or columns: row_id joins each row to its key file.",
]
ALL = "all"              # ./wbg check all: every measure, one workbook, plus the package links
LINK_KEY_COLS = ["row_id", "label_set", "unit_id", "package_id", "text_sha256", "package_sha256",
                 "question_sha256", "probability", "stratum", "stratum_size", "stratum_drawn",
                 "link_run"]
LINK_SHEET_COLS = [("row_id", 8), ("project", 10), ("measure", 20), ("commitment", 70),
                   ("commitment component", 14), ("package", 60), ("package component", 18),
                   ("applies", 10), ("note", 24)]
LINK_LABEL_COLS = ["label_set", "unit_id", "package_id", "text_sha256", "package_sha256",
                   "question_sha256", "applies", "source", "graded_on"]


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


def draw_bands(units, top, seed, bands=BANDS):
    """The units the model read, by band of its probability of the top
    grade: (unit, stratum, stratum size, rows drawn)."""
    rng = random.Random(seed)
    strata = {}
    for u in units:
        if u["sent"] != "true" or u.get(f"p{top}", "") == "":
            continue
        p = float(u[f"p{top}"])
        s = next((b[0] for b in bands if b[1] <= p < b[2]), None)
        if s is not None:
            strata.setdefault(s, []).append(u)
    picked = []
    for s, _, _, per in bands:
        pool = sorted(strata.get(s, []), key=lambda u: u["unit_id"])
        n = min(per, len(pool))
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


def set_types(name, data):
    """[(type_id, name, definition)] a measure's committed units can be: its
    list and the OTH types the gap step proposed, as the dashboards show them."""
    import load_pg
    return [(t["type_id"], t["type_name"], t["definition"]) for t in load_pg.type_rows(data)
            if t["label_set"] == name]


def sheet_rows(name, data, seed=1, per=PER, elusion=False, design="grade", **kw):
    """(key rows, sheet rows, scale, asset ids) of one measure's draw."""
    folder = measure.current_run(name, data, **kw)
    if not folder:
        raise SystemExit(f"check: no measure run for {name} as it stands; run ./wbg measure {name}")
    run = measure.Run(name, data, build=False, log=lambda *_: None, **kw)
    run.load_corpus()
    qsha = classify.question_shas(run.qs)
    with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
        units = list(csv.DictReader(fh))
    if elusion:
        picked = draw_elusion(run, units, ELUSION_PER, seed)
    elif design == "bands":
        picked = draw_bands(units, len(run.scale) - 1, seed)
    elif design in ("floor", "tune"):
        import labels
        picked = draw_bands(units, len(run.scale) - 1, seed,
                            floor_bands(labels.commit_floor(name),
                                        TUNE_SCALE if design == "tune" else 1.0))
    else:
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
    return key, sheet, run.scale, [a["id"] for a in run.assets]


def _write_key(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def build(name, data, seed=1, per=PER, elusion=False, design="grade", **kw):
    if name == ALL:
        return build_all(data, seed, design=design, **kw)
    key, sheet, scale, assets = sheet_rows(name, data, seed, per, elusion, design, **kw)
    base = where(data, "review", f"check_units_{name}{'_elusion' if elusion else ''}_s{seed}")
    if os.path.exists(base + ".xlsx"):
        raise SystemExit(f"check: {base}.xlsx exists and may hold grades; use another --seed")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    _write_key(base + ".key.csv", KEY_COLS, key)
    _write_xlsx(base + ".xlsx", [{"tab": "grade these", "rows": sheet, "scale": scale,
                                  "assets": assets, "types": set_types(name, data),
                                  "measure": name}])
    return base + ".xlsx", len(sheet)


def all_path(data, seed):
    return where(data, "review", f"check_units_{ALL}_s{seed}.xlsx")


def build_all(data, seed, design="bands", **kw):
    """One workbook for a person to grade: a tab per measure (drawn by
    `design`), and a tab of package links. Each tab has its own key file,
    check_units_<set>_s<seed>.key.csv and check_links_s<seed>.key.csv, so
    `--evaluate` reads each measure as it reads a sheet of its own."""
    import labels
    xlsx = all_path(data, seed)
    if os.path.exists(xlsx):
        raise SystemExit(f"check: {xlsx} exists and may hold grades; use another --seed")
    tabs, keys = [], []
    for name in labels.label_sets():
        if name == measure.ASSET_SET:
            continue
        if not measure.current_run(name, data, **kw):
            print(f"check: {name}: no current measure run; left out")
            continue
        key, sheet, scale, assets = sheet_rows(name, data, seed, design=design, **kw)
        base = where(data, "review", f"check_units_{name}_s{seed}")
        if os.path.exists(base + ".xlsx") or os.path.exists(base + ".key.csv"):
            raise SystemExit(f"check: {base} exists; use another --seed")
        tabs.append({"tab": name[:31], "rows": sheet, "scale": scale, "assets": assets,
                     "types": set_types(name, data), "measure": name})
        keys.append((base + ".key.csv", KEY_COLS, key))
    lkey, lsheet = link_rows(data, seed, kw.get("projects"),
                             LINK_FLOOR_BANDS if design == "floor" else LINK_BANDS)
    if lsheet:
        tabs.append({"tab": "package links", "rows": lsheet})
        keys.append((where(data, "review", f"check_links_s{seed}.key.csv"), LINK_KEY_COLS, lkey))
    os.makedirs(os.path.dirname(xlsx), exist_ok=True)
    for path, cols, rows in keys:
        _write_key(path, cols, rows)
    _write_xlsx(xlsx, tabs)
    return xlsx, sum(len(t["rows"]) for t in tabs)


def link_question_sha():
    import link_packages
    return hashlib.sha256(json.dumps(link_packages.PAIR_QUESTION, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def link_rows(data, seed, projects=None, bands=LINK_BANDS):
    """(key rows, sheet rows) of the package links drawn by band of the
    link's probability, from the current link run; ([], []) without one."""
    import link_packages
    folder = link_packages.current_run(data, projects=projects)
    if not folder:
        print("check: no current link run; the package links tab is left out")
        return [], []
    with open(os.path.join(folder, "commitment_packages.csv"), newline="", encoding="utf-8") as fh:
        pairs = list(csv.DictReader(fh))
    units = {(name, u["unit_id"]): u
             for name, (_, us) in link_packages.committed_units(data, projects).items() for u in us}
    packages = {p["package_id"]: p for p in link_packages.load_packages(data, projects)}
    names = {}
    import labels
    for name in {p["label_set"] for p in pairs}:
        try:
            names[name] = labels.load_set(name)[0]["name"]
        except (SystemExit, IndexError):
            names[name] = name
    pairs = [p for p in pairs if (p["label_set"], p["unit_id"]) in units
             and p["package_id"] in packages and p.get("probability", "") != ""]
    picked = draw_links(pairs, seed, bands)
    random.Random(seed).shuffle(picked)
    qsha = link_question_sha()
    key, sheet = [], []
    for i, (p, stratum, size, drawn) in enumerate(picked, 1):
        u, pk = units[(p["label_set"], p["unit_id"])], packages[p["package_id"]]
        state = link_packages.pair_state(u, pk)
        key.append({"row_id": i, "label_set": p["label_set"], "unit_id": p["unit_id"],
                    "package_id": p["package_id"],
                    "text_sha256": hashlib.sha256(u["text"].encode("utf-8")).hexdigest(),
                    "package_sha256": pk["sha"], "question_sha256": qsha,
                    "probability": p["probability"], "stratum": stratum, "stratum_size": size,
                    "stratum_drawn": drawn, "link_run": os.path.basename(folder)})
        sheet.append({"row_id": i, "project": p["project_id"],
                      "measure": names.get(p["label_set"], p["label_set"]),
                      "commitment": u["text"],
                      "commitment component": state.get("commitment_component", ""),
                      "package": pk["text"], "package component": pk["component"]})
    return key, sheet


def draw_links(pairs, seed, bands=LINK_BANDS):
    """Commitment and package pairs by band of the link probability:
    (pair, stratum, stratum size, rows drawn)."""
    rng = random.Random(seed)
    strata = {}
    for p in pairs:
        x = float(p["probability"])
        s = next((b[0] for b in bands if b[1] <= x < b[2]), None)
        if s is not None:
            strata.setdefault(s, []).append(p)
    picked = []
    for s, _, _, per in bands:
        pool = sorted(strata.get(s, []), key=lambda p: (p["label_set"], p["unit_id"], p["package_id"]))
        n = min(per, len(pool))
        for p in rng.sample(pool, n):
            picked.append((p, s, len(pool), n))
    return picked


def _write_xlsx(path, tabs):
    """tabs: [{tab, rows, and for a measure: scale, assets, types, measure}].
    A measure tab has dropdowns for grade, asset and type; the package links
    tab one for applies. The dropdown lists sit on a hidden sheet (a list
    written into the validation itself is capped at 255 characters)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    wb.active.title = "how to"
    for line in HOW_TO:
        wb.active.append([line])
    wb.active.column_dimensions["A"].width = 120
    lists = wb.create_sheet("lists")
    lists.sheet_state = "hidden"
    ncol = [0]

    def listed(values):
        ncol[0] += 1
        c = lists.cell(1, ncol[0]).column_letter
        for i, v in enumerate(values, 1):
            lists.cell(i, ncol[0], v)
        return f"lists!${c}$1:${c}${len(values)}"

    wrap = Alignment(wrap_text=True, vertical="top")
    for t in tabs:
        spec = LINK_SHEET_COLS if "scale" not in t else SHEET_COLS
        ws = wb.create_sheet(t["tab"])
        cols = [c for c, _ in spec]
        ws.append(cols)
        for r in t["rows"]:
            ws.append([cell_text(r.get(c)) for c in cols])
        for i, (_, width) in enumerate(spec, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = width
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = wrap
        n = len(t["rows"]) + 1
        if "scale" in t:
            drops = [("grade", [str(i) for i in range(len(t["scale"]))]),
                     ("asset", t["assets"] + ["none", "other"]),
                     ("type", [x[0] for x in t["types"]] + ["none", "other"])]
        else:
            drops = [("applies", ["yes", "no", "unsure"])]
        for col, values in drops:
            dv = DataValidation(type="list", formula1=listed(values), allow_blank=True)
            ws.add_data_validation(dv)
            c = ws.cell(1, cols.index(col) + 1).column_letter
            dv.add(f"{c}2:{c}{n}")
        ws.freeze_panes = "A2"
    measures = [t for t in tabs if "scale" in t]
    s = wb.create_sheet("grades")
    s.append(["measure", "grade", "meaning ({name} is the measure)"])
    for t in measures:
        for i, line in enumerate(t["scale"]):
            s.append([t["measure"], i, line])
    s.column_dimensions["A"].width = 26
    s.column_dimensions["C"].width = 120
    if any(t["types"] for t in measures):
        s = wb.create_sheet("types")
        s.append(["measure", "type", "name", "definition"])
        for t in measures:
            for tid, nm, d in t["types"]:
                s.append([t["measure"], tid, nm, cell_text(d)])
        for c, w in zip("ABCD", (26, 30, 30, 100)):
            s.column_dimensions[c].width = w
    wb.save(path)


def _sheet_values(xlsx, tab):
    from openpyxl import load_workbook
    ws = load_workbook(xlsx, read_only=True)[tab]
    rows = ws.iter_rows(values_only=True)
    head = list(next(rows))
    return head, rows


def _clean(v):
    return str(v).strip() if v is not None else ""


def read_sheet(xlsx, tab="grade these"):
    """{row_id: (grade, asset, type)} for the rows a person graded."""
    head, rows = _sheet_values(xlsx, tab)
    gi, ai, ri = head.index("grade"), head.index("asset"), head.index("row_id")
    ti = head.index("type") if "type" in head else None
    out = {}
    for r in rows:
        if _clean(r[gi]) == "":
            continue
        out[str(r[ri])] = (int(float(_clean(r[gi]))), _clean(r[ai]),
                           _clean(r[ti]) if ti is not None else "")
    return out


def read_links(xlsx, tab="package links"):
    """{row_id: yes|no|unsure} for the links a person answered."""
    head, rows = _sheet_values(xlsx, tab)
    ai, ri = head.index("applies"), head.index("row_id")
    return {str(r[ri]): _clean(r[ai]).lower() for r in rows if _clean(r[ai])}


def graded_sheet(name, data, seed, elusion=False):
    """(key path, {row_id: (grade, asset, type)}, sheet name) of a measure's
    sheet: its own workbook, else its tab in the combined one."""
    base = where(data, "review", f"check_units_{name}{'_elusion' if elusion else ''}_s{seed}")
    if os.path.exists(base + ".xlsx"):
        return base + ".key.csv", read_sheet(base + ".xlsx"), os.path.basename(base)
    xlsx = all_path(data, seed)
    if not elusion and os.path.exists(xlsx) and os.path.exists(base + ".key.csv"):
        return base + ".key.csv", read_sheet(xlsx, name[:31]), os.path.basename(base)
    raise SystemExit(f"check: no sheet for {name} at seed {seed} ({base}.xlsx or {xlsx})")


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


QUALITY_COLS = ["label_set", "version", "question_sha256", "run_id", "graded_units",
                "precision", "recall", "evaluated_at", "report_path"]


def write_quality(data, name, key, rows, top):
    """The headline numbers of an evaluation, one row per measure and exact
    question in data/labels/measure_quality.csv (loaded as
    wbg.measure_quality): precision and recall of the model's most likely
    grade at the top of the scale against the person's grade."""
    tp = sum(1 for r in rows if r["model_grade"] >= top and r["grade"] >= top)
    pp = sum(1 for r in rows if r["model_grade"] >= top)
    ap = sum(1 for r in rows if r["grade"] >= top)
    path = where(data, "labels", "measure_quality.csv")
    current = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            current = {(r["label_set"], r["question_sha256"]): r for r in csv.DictReader(fh)}
    import labels
    version = next((lab.get("version") or "" for lab in labels.load_set(name)), "")
    current[(name, key["question_sha256"])] = {
        "label_set": name, "version": version, "question_sha256": key["question_sha256"],
        "run_id": key["measure_dir"], "graded_units": len(rows),
        "precision": f"{tp / pp:.3f}" if pp else "", "recall": f"{tp / ap:.3f}" if ap else "",
        "evaluated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report_path": f"reports/evaluate_units_{name}.md"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=QUALITY_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(current.values(), key=lambda r: (r["label_set"], r["evaluated_at"])))
    os.replace(path + ".part", path)


def calibration_path(name, data):
    return where(data, "labels", f"{name}_calibration.json")


def store_labels(name, data, key, graded, source):
    """The person's grades as labels in data/labels/<set>_units.csv, keyed by
    text and question, never by position; a later grade of the same text
    under the same question replaces an earlier one."""
    lpath = where(data, "labels", f"{name}_units.csv")
    current = {}
    if os.path.exists(lpath):
        with open(lpath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                current[(r["label_id"], r["text_sha256"], r["question_sha256"])] = r
    today = datetime.date.today().isoformat()
    for k in key:
        if k["row_id"] in graded:
            g, a, t = graded[k["row_id"]]
            current[(k["label_id"], k["text_sha256"], k["question_sha256"])] = {
                "label_set": name, "label_id": k["label_id"], "unit_id": k["unit_id"],
                "text_sha256": k["text_sha256"], "question_sha256": k["question_sha256"],
                "grade": g, "asset": a, "type": t, "source": source, "graded_on": today}
    os.makedirs(os.path.dirname(lpath), exist_ok=True)
    with open(lpath + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LABEL_COLS, lineterminator="\n", restval="")
        w.writeheader()
        w.writerows(sorted(current.values(), key=lambda r: (r["label_id"], r["text_sha256"])))
    os.replace(lpath + ".part", lpath)


def assigned_types(name, data):
    """{unit_id: best type} of the set's current type assignment, or {}."""
    import kinds
    folder = kinds.current_assign(name, data)
    if not folder:
        return {}
    with open(os.path.join(folder, "assignments.csv"), newline="", encoding="utf-8") as fh:
        return {r["unit_id"]: r.get("best_type") or "none" for r in csv.DictReader(fh)}


def evaluate(name, data, seed=1, elusion=False, **kw):
    if name == ALL:
        return evaluate_all(data, seed, **kw)
    if elusion:
        return evaluate_elusion(name, data, seed, **kw)
    kpath, graded, source = graded_sheet(name, data, seed)
    with open(kpath, newline="", encoding="utf-8") as fh:
        key = list(csv.DictReader(fh))
    if not graded:
        raise SystemExit(f"check: no grade entered in {source}")
    store_labels(name, data, key, graded, source)
    # measure the run the sheet was drawn from
    mdir = where(data, "measures", key[0]["measure_dir"])
    with open(os.path.join(mdir, "units.csv"), newline="", encoding="utf-8") as fh:
        units = {u["unit_id"]: u for u in csv.DictReader(fh)}
    with open(os.path.join(mdir, "manifest.json"), encoding="utf-8") as fh:
        scale = json.load(fh)["scale"]
    top = len(scale) - 1
    rows = []
    for k in key:
        if k["row_id"] not in graded:
            continue
        u = units[k["unit_id"]]
        g, a, t = graded[k["row_id"]]
        rows.append({"grade": g, "asset": a, "type": t, "unit_id": k["unit_id"], "w": int(k["stratum_size"]) / int(k["stratum_drawn"]),
                     "p": [float(u[f"p{i}"]) for i in range(len(scale))],
                     "model_grade": int(u["grade"]), "link": u.get("asset_link", ""),
                     "stratum": k["stratum"]})
    lines = [f"# {name}: the decision model against {len(rows)} graded units", "",
             f"Drawn from {key[0]['measure_dir']} by stratum "
             f"({', '.join(sorted({k['stratum'] for k in key}))}); weighted "
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
    withtype = [r for r in rows if r["type"] and r["grade"] >= top]
    if withtype:
        best = assigned_types(name, data)
        typed = [r for r in withtype if r["unit_id"] in best]
        agree = sum(1 for r in typed if r["type"] == best[r["unit_id"]])
        lines += [f"## Type on committed units the person also gave a type: {agree} of "
                  f"{len(typed)} agree with the assigned type ({len(withtype) - len(typed)} "
                  f"not committed by the model, so not typed)", ""]
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
    today = datetime.date.today().isoformat()
    cal = {"label_set": name, "measure_dir": key[0]["measure_dir"], "graded_rows": len(rows),
           "graded_on": today, "question_sha256": key[0]["question_sha256"], "grades": {}}
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
    write_quality(data, name, key[0], rows, top)
    cpath = calibration_path(name, data)
    with open(cpath + ".part", "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=1, sort_keys=True)
    os.replace(cpath + ".part", cpath)
    out = where(data, "reports", f"evaluate_units_{name}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out, len(rows)


def evaluate_all(data, seed, **kw):
    """Every measure tab of the combined workbook a person graded, then its
    package links. Returns (reports written, graded rows)."""
    import labels
    xlsx = all_path(data, seed)
    if not os.path.exists(xlsx):
        raise SystemExit(f"check: {xlsx} not found; draw it with ./wbg check all --seed {seed}")
    outs, n = [], 0
    for name in labels.label_sets():
        if not os.path.exists(where(data, "review", f"check_units_{name}_s{seed}.key.csv")):
            continue
        try:
            out, k = evaluate(name, data, seed, **kw)
        except SystemExit as exc:
            print(f"  {name}: {exc}")
            continue
        outs.append(out)
        n += k
    if os.path.exists(where(data, "review", f"check_links_s{seed}.key.csv")):
        try:
            out, k = evaluate_links(data, seed)
            outs.append(out)
            n += k
        except SystemExit as exc:
            print(f"  package links: {exc}")
    return ", ".join(outs) or "nothing", n


def evaluate_links(data, seed):
    """The person's answers on the package links tab: stored as labels in
    data/labels/package_links.csv (keyed by both texts and the question), and
    the precision of the link at each probability threshold, weighted back
    to the bands, in data/reports/evaluate_links.md."""
    kpath = where(data, "review", f"check_links_s{seed}.key.csv")
    with open(kpath, newline="", encoding="utf-8") as fh:
        key = list(csv.DictReader(fh))
    answered = read_links(all_path(data, seed))
    if not answered:
        raise SystemExit("no link answered")
    lpath = where(data, "labels", "package_links.csv")
    current = {}
    if os.path.exists(lpath):
        with open(lpath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                current[(r["text_sha256"], r["package_sha256"], r["question_sha256"])] = r
    today = datetime.date.today().isoformat()
    for k in key:
        if k["row_id"] in answered:
            current[(k["text_sha256"], k["package_sha256"], k["question_sha256"])] = {
                **{c: k[c] for c in ("label_set", "unit_id", "package_id", "text_sha256",
                                      "package_sha256", "question_sha256")},
                "applies": answered[k["row_id"]], "source": os.path.basename(kpath)[:-8],
                "graded_on": today}
    os.makedirs(os.path.dirname(lpath), exist_ok=True)
    with open(lpath + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LINK_LABEL_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(current.values(), key=lambda r: (r["text_sha256"], r["package_sha256"])))
    os.replace(lpath + ".part", lpath)
    rows = [{"p": float(k["probability"]), "yes": answered[k["row_id"]] == "yes",
             "w": int(k["stratum_size"]) / int(k["stratum_drawn"]), "stratum": k["stratum"]}
            for k in key if answered.get(k["row_id"]) in ("yes", "no")]
    unsure = sum(1 for v in answered.values() if v not in ("yes", "no"))
    f = lambda x: "-" if x is None else f"{x:.2f}"
    lines = [f"# Package links: the decision model against {len(rows)} answered pairs", "",
             f"Drawn from {key[0]['link_run']} by band of the link probability; {unsure} "
             f"answered unsure are left out. Weighted figures use band size over rows drawn; "
             f"intervals are 95 per cent Wilson on the raw counts. Recall is among the pairs "
             f"the link asked, not every package.", "",
             "| link kept at P >= | precision (95% CI) | recall (95% CI) | weighted precision | "
             "weighted recall | n kept |", "|---|---|---|---|---|---|"]
    for t in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        tp = sum(1 for r in rows if r["p"] >= t and r["yes"])
        pp = sum(1 for r in rows if r["p"] >= t)
        ap = sum(1 for r in rows if r["yes"])
        wtp = sum(r["w"] for r in rows if r["p"] >= t and r["yes"])
        wpp = sum(r["w"] for r in rows if r["p"] >= t)
        wap = sum(r["w"] for r in rows if r["yes"])
        pl, ph = wilson(tp, pp)
        rl, rh = wilson(tp, ap)
        lines.append(f"| {t} | {f(tp / pp if pp else None)} ({f(pl)}-{f(ph)}) | "
                     f"{f(tp / ap if ap else None)} ({f(rl)}-{f(rh)}) | "
                     f"{f(wtp / wpp if wpp else None)} | {f(wtp / wap if wap else None)} | {pp} |")
    lines += ["", "## By band: pairs answered, share yes", ""]
    for s in sorted({r["stratum"] for r in rows}):
        rs = [r for r in rows if r["stratum"] == s]
        lines.append(f"- {s}: {len(rs)} answered, {sum(r['yes'] for r in rs)} yes")
    out = where(data, "reports", "evaluate_links.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out, len(answered)


def evaluate_elusion(name, data, seed=1, **kw):
    """What the pool missed: the elusion sheet's grades, weighted back to
    their strata."""
    kpath, graded, source = graded_sheet(name, data, seed, elusion=True)
    with open(kpath, newline="", encoding="utf-8") as fh:
        key = list(csv.DictReader(fh))
    if not graded:
        raise SystemExit(f"check: no grade entered in {source}")
    store_labels(name, data, key, graded, source)
    mdir = where(data, "measures", key[0]["measure_dir"])
    with open(os.path.join(mdir, "manifest.json"), encoding="utf-8") as fh:
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
    ap.add_argument("--design", default=None, choices=("grade", "bands", "floor", "tune"),
                    help="draw by the model's grade (30/15/20/20 and 10 feedback), by band "
                         "of its probability of the top grade (5/8/12/10/5; the default for all), "
                         "around each measure's commit floor (about 20, links around 0.7), "
                         "or to tune every threshold (about 28 around each floor, 70 links "
                         "over the whole range)")
    ap.add_argument("--context", default="sentence", choices=measure.CONTEXTS)
    ap.add_argument("--gate", default="none", choices=measure.GATES)
    ap.add_argument("--asset-min", type=float, default=0.0)
    ap.add_argument("--projects", default="", help="comma-separated project ids: the run's scope")
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    kw = dict(context=args.context, gate=args.gate, asset_min=args.asset_min,
              projects=classify.scope(args.projects))
    if args.evaluate:
        out, n = evaluate(args.label_set, args.data, args.seed, elusion=args.elusion, **kw)
        print(f"{n} graded rows; wrote {out}")
    else:
        design = args.design or ("bands" if args.label_set == ALL else "grade")
        path, n = build(args.label_set, args.data, args.seed, elusion=args.elusion,
                        design=design, **kw)
        print(f"wrote {path}: {n} rows to grade")
    return 0


if __name__ == "__main__":
    sys.exit(main())
