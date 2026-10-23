"""What the grades say each tunable setting should be (docs/method/decisions.md).

Run by ./wbg check all --evaluate after the grades are stored; writes
data/reports/tune.md. It changes nothing: a person reads it and sets the
control panel. Every figure is out of sample where the setting is fitted
(folds by whole project, the same folds as the layer's own check) and
weighted by design weight, each measure's weights scaled to its graded rows
so no measure outweighs the rest by the size of its strata.

Sections:

  layer     posterior.sharpness, near_copy, ridge, centroid and pool on a
            grid, by log loss (lower is better; the probability's own
            accuracy), mean AUC (ranking) and precision and recall at each
            measure's floor.
  score     which reading of the model's four grade probabilities ranks the
            graded sentences best: P(2)+P(3) (read.grades), P(3), or the
            expected grade.
  asset     asset.floor, asset.paragraph_floor and asset.decay on the
            sentences a person gave an asset (in sample: the asset layers are fitted on the same
            grades).
  not yet   the settings these grades cannot set, and the sheet each needs.
"""
import itertools
import math
import os

import panel
import posterior
from paths import where

GRID = {"sharpness": [5.0, 10.0, 20.0, 40.0, 80.0],
        "near_copy": ["off", 0.97, 0.95],
        "ridge": [0.25, 1.0, 4.0],
        "centroid": [True, False],
        "pool": [False, True]}
FLOORS = [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6]
DECAYS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def wauc(ps, ys, ws):
    """Weighted area under the ROC curve (ties count half); None without
    both kinds."""
    pos = [(p, w) for p, y, w in zip(ps, ys, ws) if y]
    neg = [(p, w) for p, y, w in zip(ps, ys, ws) if not y]
    if not pos or not neg:
        return None
    num = sum(wp * wn * ((p > n) + 0.5 * (p == n)) for p, wp in pos for n, wn in neg)
    return num / (sum(w for _, w in pos) * sum(w for _, w in neg))


def _scaled(rows):
    """Each row's design weight scaled so the measure's weights sum to its
    rows."""
    tot = sum(r[2] for r in rows) or 1.0
    return [r[2] * len(rows) / tot for r in rows]


def score_table(blocks):
    """The three readings of the model's grade probabilities, by weighted
    AUC per measure."""
    lines = ["## Which reading of the model's answer ranks best", "",
             "Weighted AUC on the graded sentences (1 = every yes above every no; 0.5 = chance).",
             "", "| measure | rows | P(2)+P(3) | P(3) | expected grade |", "|---|---|---|---|---|"]
    f = lambda x: "-" if x is None else f"{x:.3f}"
    means = {k: [] for k in ("p23", "p3", "eg")}
    for name, (rows, _, _, _, probs) in sorted(blocks.items()):
        ys, ws = [r[1] for r in rows], [r[2] for r in rows]
        got = {"p23": [probs[r[0]][2] + probs[r[0]][3] for r in rows],
               "p3": [probs[r[0]][3] for r in rows],
               "eg": [sum(g * p for g, p in enumerate(probs[r[0]])) for r in rows]}
        a = {k: wauc(v, ys, ws) for k, v in got.items()}
        for k, v in a.items():
            if v is not None:
                means[k].append(v)
        lines.append(f"| {name} | {len(rows)} | {f(a['p23'])} | {f(a['p3'])} | {f(a['eg'])} |")
    m = {k: (sum(v) / len(v) if v else None) for k, v in means.items()}
    lines += [f"| mean | | {f(m['p23'])} | {f(m['p3'])} | {f(m['eg'])} |", "",
              "The panel reads P(2)+P(3) (read.grades). A difference under about 0.02 is within "
              "the noise of a few dozen rows a measure.", ""]
    return lines


def layer_grid(blocks, log=print):
    """Every combination of GRID, out of sample. Returns report lines."""
    base = posterior.settings()
    four = {n: b[:4] for n, b in blocks.items()}
    combos = list(itertools.product(*GRID.values()))
    results = []
    for i, combo in enumerate(combos):
        s = dict(base, **dict(zip(GRID, combo)))
        s["near_copy"] = posterior._near(s["near_copy"])
        oof = posterior.out_of_fold_all(four, s)
        ll, n, aucs, pp = 0.0, 0.0, [], []
        for name, (rows, *_rest) in blocks.items():
            ws = _scaled(rows)
            ps = [oof[name].get(r[0], 0.5) for r in rows]
            ys = [r[1] for r in rows]
            for p, y, w in zip(ps, ys, ws):
                p = min(max(p, 1e-6), 1 - 1e-6)
                ll -= w * (math.log(p) if y else math.log(1 - p))
                n += w
            a = wauc(ps, ys, ws)
            if a is not None:
                aucs.append(a)
            fl = panel.floor(name)
            pp += [(p >= fl, y, w) for p, y, w in zip(ps, ys, ws)]
        kept = sum(w for k, _, w in pp if k)
        tp = sum(w for k, y, w in pp if k and y)
        pos = sum(w for _, y, w in pp if y)
        results.append((ll / n if n else None, combo,
                        sum(aucs) / len(aucs) if aucs else None,
                        tp / kept if kept else None, tp / pos if pos else None))
        if (i + 1) % 30 == 0:
            log(f"  tune: {i + 1} of {len(combos)} layer settings checked")
    results.sort(key=lambda r: (r[0] is None, r[0]))
    now = tuple(panel.get(f"posterior.{k}", "") for k in GRID)
    f = lambda x: "-" if x is None else f"{x:.3f}"
    lines = ["## Probability layer settings", "",
             f"{len(combos)} combinations, every one out of sample (folds by project). Log loss is "
             "the main figure: it scores the probability itself, which the floor then reads. "
             "Precision and recall are at each measure's floor, pooled over measures.", "",
             "| rank | sharpness | near_copy | ridge | centroid | pool | log loss | mean AUC "
             "| precision | recall |", "|---|---|---|---|---|---|---|---|---|---|"]
    raw = _raw_logloss(blocks)
    for k, (ll, combo, a, p, r) in enumerate(results, 1):
        mark = " (panel)" if tuple(map(_norm, combo)) == tuple(map(_norm, now)) else ""
        if k <= 15 or mark:
            lines.append(f"| {k}{mark} | " + " | ".join(str(c) for c in combo) +
                         f" | {f(ll)} | {f(a)} | {f(p)} | {f(r)} |")
    lines += ["", f"- the model's score alone: log loss {f(raw)}", "",
              "Read it: take the top row unless a simpler one (pool, no centroid, a middle "
              "sharpness) is within about 0.01 of it; the differences between neighbouring rows "
              "are mostly noise at this many grades. Set the posterior.* lines, then evaluate "
              "again to refit.", ""]
    return lines


def _norm(v):
    """A setting as compared with the panel: 20 and 20.0 alike, 'Off' and 'off'."""
    if isinstance(v, bool):
        return str(v).lower()
    try:
        return repr(float(v))
    except (TypeError, ValueError):
        return str(v).strip().lower()


def _raw_logloss(blocks):
    ll, n = 0.0, 0.0
    for rows, score, *_ in blocks.values():
        for r, w in zip(rows, _scaled(rows)):
            p = min(max(score[r[0]], 1e-6), 1 - 1e-6)
            ll -= w * (math.log(p) if r[1] else math.log(1 - p))
            n += w
    return ll / n if n else None


def asset_grid(data):
    """asset.floor x asset.decay against the assets people gave the
    sentences they graded. Returns report lines."""
    import csv
    import check_units
    import labels
    import measure
    vector_of, projects_of = posterior.lookups(data)
    import classify
    # the map the grades were drawn against: refitting the asset layers
    # renames the next map (its feedback rounds read them)
    amap, run = measure.asset_map(data, None, vector_of, projects_of,
                                  folder=classify.last_run("assets", data))
    lines = ["## Asset floor, paragraph floor and decay", ""]
    if not amap:
        return lines + ["- no sentence asset map yet", ""]
    with open(where(data, "embeddings", "sentence_index.csv"), newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    before, seen = {}, {}
    for r in index:
        prior = seen.setdefault(r["paragraph_id"], [])
        before[r["unit_id"]] = list(reversed(prior))
        prior.append(r["unit_id"])
    by_text = {check_units.text_key(t): u for u, t in measure.unit_texts(data, index).items()}
    named = {}
    for name in labels.label_sets():
        for u, (_, asset, w) in check_units.graded_labels(name, data, fitting=True,
                                                         units_by_text=by_text).items():
            given = set(asset.split("|")) - {"", "other"}
            if given and u in before:
                named[u] = (given, w)
    real = {u: (a - {"none"}, w) for u, (a, w) in named.items() if a - {"none"}}
    none = {u: v for u, v in named.items() if v[0] == {"none"}}
    if not real:
        return lines + ["- no graded sentence names an asset yet", ""]
    lines += [f"On {len(real)} graded sentences a person gave an asset and {len(none)} where "
              "they said none, weighted, at the panel's asset.multiple. Agree = the person's "
              "assets are among the pipeline's (any one of them); other assets given = assets the "
              "pipeline gives a sentence that the person did not, per sentence; "
              "on 'none' = the pipeline gives an asset at the "
              "sentence's own level where the person said the sentence names none (an asset "
              "from the paragraph is not counted against it). "
              "In sample: the asset layers were fitted on the same grades.", "",
              "| rank | floor | paragraph floor | decay | agree | other assets given | on 'none' |",
              "|---|---|---|---|---|---|---|"]
    rw = sum(w for _, w in real.values())
    nw = sum(w for _, w in none.values()) or 1.0
    now = (float(panel.get("asset.floor", 0.3)),
           float(panel.get("asset.paragraph_floor", panel.get("asset.floor", 0.3))),
           float(panel.get("asset.decay", 0.4)))
    results = []
    for fl in FLOORS:
        onnone = sum(w for u, (_, w) in none.items()
                     if measure.sentence_assets(amap.get(u, {}), [], 0.0, fl)[0])
        for pf in FLOORS:
            for d in DECAYS:
                if d == 0 and pf != fl:
                    continue                 # no paragraph rule: its floor reads nothing
                agree = given = extra = 0.0
                for u, (a, w) in real.items():
                    win = {x for x, _ in measure.sentence_assets(
                        amap.get(u, {}), [amap.get(x, {}) for x in before[u]], d, fl, pf)[0]}
                    given += w if win else 0.0
                    agree += w if a & win else 0.0
                    extra += w * len(win - a)
                results.append((agree / rw, -(onnone / nw), (fl, pf, d), extra / rw, onnone / nw))
    results.sort(key=lambda r: (-r[0], -r[1], r[2]))
    for k, (ag, _, (fl, pf, d), ex, on) in enumerate(results, 1):
        mark = " (panel)" if (fl, pf, d) == now else ""
        if k <= 20 or mark:
            lines.append(f"| {k}{mark} | {fl} | {pf} | {d} | {ag:.2f} | {ex:.2f} | {on:.2f} |")
    return lines + ["", "Read it: the highest agree with a low 'on none'; decay 0 is the "
                    "sentence alone.", ""]


NOT_YET = [
    ("floor.types, floor.pair_check, link.asset_yes, "
     "dashboard.package_link_min_probability", "data/reports/evaluate_after_measures.md, from "
     "the type column and the pair checks, package assets and package links tabs "
     "(src/judged.py)"),
    ("link.asset_top, link.measure_top", "the yes answers at the last rank asked "
     "(data/reports/link_packages.txt): many there means ask deeper"),
    ("measure.gate_cosine, asset.gate_cosine", "starting values only: the skip rule fits each "
     "label's gate from its graded yes; the elusion sheet (./wbg check <set> --elusion) checks "
     "below it"),
    ("feedback.*, asset_feedback.*", "the elusion sheet: what the rounds miss shows below the "
     "gate"),
    ("dashboard.near_copy_share, text.shingle_words", "a person reading the near-identical "
     "sentences the view flags"),
    ("parser and sentence rules (src/pdf_layout.py, src/units.py, src/sentences.py)",
     "not from grades: the parser's regression tests and the corpus-quality report"),
]


def report(data, log=print):
    """Write data/reports/tune.md; returns its path."""
    import check_units
    blocks = {n: b for n, b in check_units.layer_blocks(data).items() if b[0]}
    lines = ["# What the grades say each setting should be", "",
             f"Measures with graded sentences under their current question: {len(blocks)}. "
             "Nothing here changes the panel.", ""]
    if blocks:
        lines += score_table(blocks)
        lines += layer_grid(blocks, log)
    else:
        lines += ["- no measure has graded sentences under its current question yet", ""]
    try:
        lines += asset_grid(data)
    except SystemExit as exc:
        lines += ["## Asset floor and decay", "", f"- {exc}", ""]
    lines += ["## Settings set elsewhere", ""]
    lines += [f"- {k}: {v}" for k, v in NOT_YET] + [""]
    out = where(data, "reports", "tune.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out
