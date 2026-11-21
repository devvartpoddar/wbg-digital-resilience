"""Tune the thresholds on the tuning set of a graded sample and report on the holdout set
(docs/method/evaluation.md). Asks nothing: the graders' answers are read from the store, the scores from the
sample's rows, so it can be run again at any time.

  tune(conn, data, seed, graders, settings) -> data/reports/evaluation_s<seed>.md and holdout_s<seed>.json,
                                               and the holdout rows of <schema>.measure_quality

Every estimate is a Horvitz-Thompson ratio: each item weighs 1 / pi, its chance of being drawn. The 90 per
cent ranges come from a bootstrap that resamples projects (the sample's clusters) with replacement and
reweights. Precision and recall are given twice: lenient (two of three graders give 2 or 3, or yes) and
strict (two of three give 3, or yes with none unsure).
"""
import json
import time
from collections import Counter

import numpy

from wbg_portfolio import common
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import graders as G
from wbg_portfolio import load_pg
from wbg_portfolio import panel
from wbg_portfolio import questions as Q
from wbg_portfolio.paths import where

BOOT = 500                  # bootstrap replicates
MIN_PER_MEASURE = 30        # holdout items a measure needs for its own figures
BALANCED_POINTS = 0.03      # the balanced point may give up this much lenient precision
NA = float("nan")
THRESHOLDS = [round(0.30 + 0.05 * i, 2) for i in range(14)]          # 0.30 .. 0.95


def _grid():
    """The one-rule-for-all family (the shapes of the earlier tuning study): the two-look rule over keep,
    low, context, grade and the digital floor (context 0 is grade only, grade 0 context only, keep 1.01 no
    free pass), and the mean shape (mean of first and context, with a grade)."""
    out = []
    for keep in (0.80, 0.85, 0.90, 0.95, 1.01):
        for low in (0.5, 0.6, 0.7):
            for ctx in (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
                for g in (0, 1, 2, 3):
                    for dig in (0.3, 0.4, 0.5, 0.6):
                        out.append({"keep": keep, "low": low, "context": ctx, "second_grade": g, "digital": dig})
    for keep in (0.85, 0.90, 1.01):
        for m in (0.6, 0.7, 0.75, 0.8, 0.85, 0.9):
            for g in (1, 2):
                out.append({"keep": keep, "low": 0.5, "context": 0.0, "second_grade": g, "digital": 0.5, "mean": m})
    return out


def current():
    """The settings as the panel reads them now."""
    b = decide.band()
    return {"keep": b["keep"], "low": b["low"], "context": b["context"], "second_grade": b["second_grade"],
            "digital": decide.digital_floor(), "strong_yes": float(panel.require("link.strong_yes")),
            "types": float(panel.require("floor.types")), "asset": float(panel.require("asset.floor")),
            "asset_multiple": bool(panel.require("asset.multiple"))}


SETTING_KEYS = {"band.keep": "keep", "band.low": "low", "band.context": "context",
                "band.second_grade": "second_grade", "digital.floor": "digital", "link.strong_yes": "strong_yes",
                "floor.types": "types", "asset.floor": "asset"}


def given(settings):
    """[name=value, ...] from the command line over the current settings, or None."""
    if not settings:
        return None
    out = current()
    for s in settings:
        k, _, v = s.partition("=")
        if k.strip() not in SETTING_KEYS:
            raise SystemExit(f"check tune: --setting {s!r}: one of {', '.join(SETTING_KEYS)}")
        out[SETTING_KEYS[k.strip()]] = float(v)
    return out


def describe(r):
    if "mean" in r:
        return f"keep {r['keep']:g}, mean(first, context) {r['mean']:g}, grade {r['second_grade']:g}, digital {r['digital']:g}"
    return (f"keep {r['keep']:g}, low {r['low']:g}, context {r['context']:g}, grade {r['second_grade']:g}, "
            f"digital {r['digital']:g}")


# ------------------------------------------------------------------ estimates

class Frame:
    """The graded rows of one kind as arrays: weight, cluster, and whatever the kind needs."""

    def __init__(self, rows, weight="weight"):
        self.rows = rows
        self.w = numpy.array([r[weight] for r in rows], dtype=float)
        names = sorted({r["project"] for r in rows})
        idx = {p: i for i, p in enumerate(names)}
        self.cl = numpy.array([idx[r["project"]] for r in rows], dtype=int)
        self.k = len(names)

    def col(self, f):
        return numpy.array([f(r) for r in self.rows], dtype=float)

    def ratio(self, num, den):
        d = float(self.w @ den)
        return float(self.w @ num) / d if d else NA

    def boot(self, num, den, seed=1, reps=BOOT):
        """The 90 per cent range of the ratio over projects resampled with replacement."""
        if not len(self.rows) or not float(self.w @ den):
            return (NA, NA)
        rng = numpy.random.default_rng(seed)
        m = rng.multinomial(self.k, [1.0 / self.k] * self.k, size=reps)          # reps x clusters
        ww = self.w * m[:, self.cl]
        d = ww @ den
        v = numpy.where(d > 0, (ww @ num) / numpy.where(d > 0, d, 1), numpy.nan)
        v = v[~numpy.isnan(v)]
        return (float(numpy.percentile(v, 5)), float(numpy.percentile(v, 95))) if len(v) else (NA, NA)

    def total(self, mask, seed=1, reps=BOOT):
        """The estimated population count of a mask, with its range."""
        est = float(self.w @ mask)
        if not len(self.rows):
            return est, (NA, NA)
        rng = numpy.random.default_rng(seed)
        m = rng.multinomial(self.k, [1.0 / self.k] * self.k, size=reps)
        v = (self.w * m[:, self.cl]) @ mask
        return est, (float(numpy.percentile(v, 5)), float(numpy.percentile(v, 95)))


def _f(x):
    return NA if x is None else float(x)


def measure_kept(f, r):
    """Kept under a rule (the band rule of decide.band_rule and the digital rule of decide.is_digital,
    vectorised; a rule with "mean" reads the mean of first and context in place of the context)."""
    first, ctx, grade, dig = f.first, f.ctx, f.grade, f.dig
    looks = ~numpy.isnan(ctx) & ~numpy.isnan(grade)
    c = numpy.nan_to_num((first + ctx) / 2 if "mean" in r else ctx, nan=-1)
    band = (first >= r["low"]) & looks & (c >= r.get("mean", r["context"])) & (numpy.nan_to_num(grade, nan=-1) >= r["second_grade"])
    committed = (first >= r["keep"]) | band
    return (committed & ~numpy.isnan(dig) & (numpy.nan_to_num(dig, nan=-1) >= r["digital"])).astype(float)


def measure_frame(rows):
    f = Frame(rows)
    f.first = f.col(lambda r: r["score_first"])
    f.ctx = f.col(lambda r: _f(r["score_context"]))
    f.grade = f.col(lambda r: _f(r["second_grade"]))
    f.dig = f.col(lambda r: _f(r["digital_p"]))
    f.right = {"lenient": f.col(lambda r: r["truth"]["lenient"]), "strict": f.col(lambda r: r["truth"]["strict"])}
    f.dig_t = f.col(lambda r: r["truth"]["digital"])
    f.cand = (f.first >= 0.3).astype(float)
    return f


def measure_figures(f, r, ranges=False):
    """Precision, and recall over two denominators (right pairs among those asked, first 0.3 or more; right
    and digital pairs), lenient and strict, for a rule; with ranges when asked."""
    kept = measure_kept(f, r)
    out = {"kept_estimate": float(f.w @ kept)}
    for how, right in f.right.items():
        figs = {"precision": (kept * right, kept), "recall_asked": (kept * right * f.cand, right * f.cand),
                "recall_digital": (kept * right * f.cand * f.dig_t, right * f.cand * f.dig_t)}
        for name, (num, den) in figs.items():
            out[f"{how}_{name}"] = f.ratio(num, den)
            if ranges:
                out[f"{how}_{name}_range"] = f.boot(num, den)
    return out


def frontier(points):
    """The rules no other beats on both lenient precision and asked recall, best precision first."""
    pts = sorted(points, key=lambda x: (-x[1]["lenient_precision"], -x[1]["lenient_recall_asked"]))
    out, best = [], -1.0
    for r, m in pts:
        if numpy.isnan(m["lenient_precision"]):
            continue
        if m["lenient_recall_asked"] > best:
            out.append((r, m))
            best = m["lenient_recall_asked"]
    return out


def operating_points(points, base, p="lenient_precision", rc="lenient_recall_asked", near=None):
    """(precision first, balanced): the most recall with precision not below the base's, and the most recall
    with precision within BALANCED_POINTS of it; ties go to the higher precision, then to the rule nearest
    `near` (the current settings)."""
    bp = base[p]

    def dist(r):
        if not near:
            return 0.0
        return sum(abs(float(v) - float(near.get(k, v))) for k, v in r.items() if k in near) + (1 if "mean" in r else 0)

    def best(floor):
        ok = [(r, m) for r, m in points if not numpy.isnan(m[p]) and m[p] >= floor - 1e-9 and not numpy.isnan(m[rc])]
        return max(ok, key=lambda x: (round(x[1][rc], 9), round(x[1][p], 9), -dist(x[0])), default=(None, None))
    return best(bp), best(bp - BALANCED_POINTS)


def threshold_sweep(fn, ts=THRESHOLDS):
    return [({"t": t}, fn(t)) for t in ts]


# ------------------------------------------------------------------ the other kinds

def strong_figures(f, t, ranges=False):
    on = (f.score >= t).astype(float)
    out = {}
    for how, right in f.right.items():
        for name, (num, den) in {"precision": (on * right, on), "recall": (on * right, right)}.items():
            out[f"{how}_{name}"] = f.ratio(num, den)
            if ranges:
                out[f"{how}_{name}_range"] = f.boot(num, den)
    return out


def types_figures(f, t, ranges=False):
    hit, pred, true, none = [], [], [], []
    for r in f.rows:
        p = {x for x, s in r["type_scores"].items() if s >= t and not x.startswith(decide.OTH)}
        tr = set(r["truth"]["lenient"])
        hit.append(len(p & tr))
        pred.append(len(p))
        true.append(len(tr))
        none.append(not p)
    hit, pred, true, none = (numpy.array(x, dtype=float) for x in (hit, pred, true, none))
    out = {"lenient_precision": f.ratio(hit, pred), "lenient_recall": f.ratio(hit, true),
           "share_no_type": f.ratio(none, numpy.ones(len(none)))}
    if ranges:
        out["lenient_precision_range"], out["lenient_recall_range"] = f.boot(hit, pred), f.boot(hit, true)
    return out


def predicted_assets(r, t, multiple):
    own = sorted(((a, p) for a, p in r["own_scores"].items() if p >= t), key=lambda x: (-x[1], x[0]))
    if own:
        return [a for a, _ in (own if multiple else own[:1])]
    return list(r["second_look_assets"] if multiple else r["second_look_assets"][:1])


def asset_figures(f, t, multiple=True, ranges=False):
    right, pred, has, wrong_none = [], [], [], []
    for r in f.rows:
        p = predicted_assets(r, t, multiple)
        a = r["truth"]["asset"]
        right.append(bool(p) and a in p)
        pred.append(bool(p))
        has.append(a not in (None, "none"))
        wrong_none.append(bool(p) and a == "none")
    right, pred, has, wrong_none = (numpy.array(x, dtype=float) for x in (right, pred, has, wrong_none))
    out = {"lenient_precision": f.ratio(right, pred), "lenient_recall": f.ratio(right * has, has),
           "asset_where_none": f.ratio(wrong_none, pred)}
    if ranges:
        out["lenient_precision_range"], out["lenient_recall_range"] = f.boot(right, pred), f.boot(right * has, has)
    return out


# ------------------------------------------------------------------ the report

def _p(x, rng=None):
    if x is None or (isinstance(x, float) and numpy.isnan(x)):
        return "-"
    s = f"{x:.2f}"
    if rng and not any(numpy.isnan(v) for v in rng):
        s += f" ({rng[0]:.2f}-{rng[1]:.2f})"
    return s


def load(conn, data, seed, graders):
    """{kind: [row with "truth"]} of the sample, and the counts of items with no truth (fewer than two
    readable graders) and left out (their measure no longer in the label files)."""
    got, gone = G.answers(conn, data, seed, graders)
    out, no_truth = {}, Counter()
    for kind, its in got.items():
        rows = []
        for item_id, r, by in its:
            t = G.truth(kind, by)
            if t is None:
                no_truth[kind] += 1
                continue
            rows.append(dict(r, truth=t, item_id=item_id))
        out[kind] = rows
    return out, no_truth, gone


def tune(conn, data, seed, graders=G.GRADERS, settings=None, log=print):
    """The sweep on the tuning set, the named settings on the holdout set, the report and the holdout
    figures. Returns the report's path."""
    rows, no_truth, gone = load(conn, data, seed, graders)
    split = lambda kind, s: [r for r in rows.get(kind, []) if r["set"] == s]
    cur, extra = current(), given(settings)
    lines = [f"# Evaluation of sample s{seed}: tuned on the tuning set, reported on the holdout set", "",
             "Graded by three models (" + ", ".join(graders) + "), not by people. Lenient: at least two of the "
             "three give 2 or 3 (or yes); strict: at least two give 3 (or yes, with none unsure). The graders are "
             "known to be generous on sentences that say the project may do something, so read the strict figure "
             "beside the lenient one. Every figure is weighted by one over the item's chance of being drawn; ranges "
             "are 90 per cent, from resampling projects. Recall counts only what the pipeline asked about.", "",
             "Items with no truth (fewer than two readable graders): "
             + (", ".join(f"{k} {v}" for k, v in sorted(no_truth.items())) or "none")
             + (f"; {gone} left out because their measure is no longer in the label files" if gone else "") + ".", ""]
    holdout = {"seed": seed, "graders": list(graders), "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # ---- measures
    tr, ho = split("measure", "tuning"), split("measure", "holdout")
    lines += ["## Measures: the band rule and the digital rule", "",
              f"Graded pairs: {len(tr)} tuning, {len(ho)} holdout.", ""]
    named, strong_named, types_named, asset_named = [], None, None, None
    if tr:
        ft = measure_frame(tr)
        pts = [(r, measure_figures(ft, r)) for r in _grid()]
        base = measure_figures(ft, cur)
        front = frontier(pts)
        pf, bal = operating_points(pts, base, near=cur)
        lines += ["### Frontier on the tuning set (in-sample: expect less on new items)", "",
                  "| Rule | Lenient precision | Lenient recall (asked) | Strict precision | Strict recall (asked) |",
                  "|---|---|---|---|---|"]
        for r, m in front[:20]:
            lines.append(f"| {describe(r)} | {_p(m['lenient_precision'])} | {_p(m['lenient_recall_asked'])} | "
                         f"{_p(m['strict_precision'])} | {_p(m['strict_recall_asked'])} |")
        lines.append("")
        named = [("current", cur), ("precision first", pf[0]), ("balanced", bal[0])] + \
            ([("given on the command line", extra)] if extra else [])
        for name, r in named[1:3]:
            log(f"check tune: {name}: {describe(r) if r else 'none found'}")
    if ho:
        fh = measure_frame(ho)
        lines += ["### On the holdout set", "",
                  "| Settings | Rule | Pairs kept (estimate) | Lenient precision | Lenient recall (asked) | "
                  "Lenient recall (right and digital) | Strict precision | Strict recall (asked) | Strict recall "
                  "(right and digital) |", "|---|---|---|---|---|---|---|---|---|"]
        holdout["measure"] = {}
        for name, r in (named or [("current", cur)] + ([("given on the command line", extra)] if extra else [])):
            if not r:
                continue
            m = measure_figures(fh, r, ranges=True)
            holdout["measure"][name] = dict(rule=r, **m)
            lines.append(f"| {name} | {describe(r)} | {m['kept_estimate']:,.0f} | "
                         + " | ".join(_p(m[k], m.get(k + '_range')) for k in (
                             "lenient_precision", "lenient_recall_asked", "lenient_recall_digital", "strict_precision",
                             "strict_recall_asked", "strict_recall_digital")) + " |")
        low = numpy.array([r["score_first"] < 0.3 for r in ho], dtype=float)
        est, rng = fh.total(low * fh.right["lenient"])
        holdout["measure_right_below_0.3"] = {"estimate": est, "range": rng}
        lines += ["", f"Right pairs (lenient) with a first score under 0.3, never asked a second look: about "
                      f"{est:,.0f} ({rng[0]:,.0f}-{rng[1]:,.0f}), from {int(low.sum())} graded pairs there.", ""]
        aim = fh.col(lambda r: r["truth"]["general_aim"])
        kept = measure_kept(fh, cur)
        lines += [f"Kept pairs the graders read as only a general aim: {_p(fh.ratio(kept * aim, kept), fh.boot(kept * aim, kept))}; "
                  f"kept pairs the graders read as not digital: "
                  f"{_p(fh.ratio(kept * (1 - fh.dig_t), kept), fh.boot(kept * (1 - fh.dig_t), kept))}.", ""]
        holdout["quality"] = quality_rows(fh, ho, cur, seed)

    # ---- the strong link
    lines += ["## The strong link (package asked about a measure)", ""]
    for s in ("tuning", "holdout"):
        rs = [r for r in split("package_measure", s) if r["score"] is not None]
        if not rs:
            continue
        f = Frame(rs)
        f.score = f.col(lambda r: r["score"])
        f.right = {h: f.col(lambda r, h=h: r["truth"][h]) for h in ("lenient", "strict")}
        if s == "tuning":
            pts = threshold_sweep(lambda t: strong_figures(f, t))
            lines += _sweep_table("link.strong_yes", pts, ("lenient_precision", "lenient_recall", "strict_precision",
                                                           "strict_recall"))
            pf, bal = operating_points(pts, strong_figures(f, cur["strong_yes"]), rc="lenient_recall", near={"t": cur["strong_yes"]})
            strong_named = [("current", cur["strong_yes"]), ("precision first", pf[0] and pf[0]["t"]),
                            ("balanced", bal[0] and bal[0]["t"])] + ([("given", extra["strong_yes"])] if extra else [])
        else:
            pairs = strong_named or [("current", cur["strong_yes"])]
            holdout["strong_link"] = {}
            lines += ["On the holdout set:", "", "| Settings | link.strong_yes | Lenient precision | Lenient recall | "
                      "Strict precision | Strict recall |", "|---|---|---|---|---|---|"]
            for name, t in pairs:
                if t is None:
                    continue
                m = strong_figures(f, t, ranges=True)
                holdout["strong_link"][name] = dict(threshold=t, **m)
                lines.append(f"| {name} | {t:g} | " + " | ".join(_p(m[k], m.get(k + "_range")) for k in (
                    "lenient_precision", "lenient_recall", "strict_precision", "strict_recall")) + " |")
            lines.append("")
    gap = [r for r in split("package_measure", "holdout") if r["score"] is None]
    if gap:
        f = Frame(gap)
        for h in ("lenient", "strict"):
            est, rng = f.total(f.col(lambda r, h=h: r["truth"][h]))
            holdout.setdefault("never_asked", {})[h] = {"estimate": est, "range": rng}
        lines += [f"Packages never asked the measure question (not among their likeliest measures): about "
                  f"{holdout['never_asked']['lenient']['estimate']:,.0f} package x measure pairs are about the measure "
                  f"(lenient; range {holdout['never_asked']['lenient']['range'][0]:,.0f}-"
                  f"{holdout['never_asked']['lenient']['range'][1]:,.0f}), "
                  f"{holdout['never_asked']['strict']['estimate']:,.0f} strict, from {len(gap)} graded holdout pairs.", ""]

    # ---- the link ladder
    ho_links = split("link", "holdout")
    if ho_links:
        lines += ["## Links by level (holdout set): share where carrying out the package delivers the commitment",
                  "", "| Level | Graded | Lenient | Strict | Lenient, not cancelled | Cumulative lenient (levels 1 to here) |",
                  "|---|---|---|---|---|---|"]
        f = Frame(ho_links)
        yes = {h: f.col(lambda r, h=h: r["truth"][h]) for h in ("lenient", "strict")}
        live = f.col(lambda r: not r["cancelled"])
        holdout["links"] = {}
        levels = sorted({r["region"] for r in ho_links})
        for lv in levels:
            at = f.col(lambda r, lv=lv: r["region"] == lv)
            upto = f.col(lambda r, lv=lv: r["region"] <= lv)
            m = {h: (f.ratio(at * yes[h], at), f.boot(at * yes[h], at)) for h in yes}
            holdout["links"][lv] = {"graded": int(at.sum()), **{h: {"share": v[0], "range": v[1]} for h, v in m.items()}}
            lines.append(f"| {lv} | {int(at.sum())} | {_p(*m['lenient'])} | {_p(*m['strict'])} | "
                         f"{_p(f.ratio(at * live * yes['lenient'], at * live))} | "
                         f"{_p(f.ratio(upto * yes['lenient'], upto))} |")
        keyed = Counter(k for r in ho_links for k in r.get("commitment_keys", []))
        lines += ["", "Levels as published (commitment_package_links.level). The linked sentences' commitments, by "
                      "the key that made them: " + (", ".join(f"{k} {v}" for k, v in sorted(keyed.items())) or "none")
                  + ".", ""]

    # ---- types
    lines += ["## Types (floor.types)", ""]
    for s in ("tuning", "holdout"):
        rs = [r for r in split("types", s)]
        if not rs:
            continue
        f = Frame(rs, weight="types_pi")
        f.w = 1.0 / f.w
        if s == "tuning":
            pts = threshold_sweep(lambda t: types_figures(f, t))
            lines += _sweep_table("floor.types", pts, ("lenient_precision", "lenient_recall", "share_no_type"),
                                  ("precision", "coverage", "share with no type"))
            pf, bal = operating_points(pts, types_figures(f, cur["types"]), rc="lenient_recall", near={"t": cur["types"]})
            types_named = [("current", cur["types"]), ("precision first", pf[0] and pf[0]["t"]),
                           ("balanced", bal[0] and bal[0]["t"])] + ([("given", extra["types"])] if extra else [])
        else:
            holdout["types"] = {}
            lines += ["On the holdout set:", "", "| Settings | floor.types | Precision | Coverage | Share with no type |",
                      "|---|---|---|---|---|"]
            for name, t in types_named or [("current", cur["types"])]:
                if t is None:
                    continue
                m = types_figures(f, t, ranges=True)
                holdout["types"][name] = dict(threshold=t, **m)
                lines.append(f"| {name} | {t:g} | {_p(m['lenient_precision'], m['lenient_precision_range'])} | "
                             f"{_p(m['lenient_recall'], m['lenient_recall_range'])} | {_p(m['share_no_type'])} |")
            lines.append("")

    # ---- assets
    lines += ["## Sentence assets (asset.floor)", ""]
    for s in ("tuning", "holdout"):
        rs = [r for r in split("asset", s) if not r["truth"]["split"]]
        if not rs:
            continue
        f = Frame(rs)
        mult = cur["asset_multiple"]
        if s == "tuning":
            pts = threshold_sweep(lambda t: asset_figures(f, t, mult))
            lines += _sweep_table("asset.floor", pts, ("lenient_precision", "lenient_recall", "asset_where_none"),
                                  ("precision", "coverage", "asset where the graders see none"))
            pf, bal = operating_points(pts, asset_figures(f, cur["asset"], mult), rc="lenient_recall", near={"t": cur["asset"]})
            asset_named = [("current", cur["asset"]), ("precision first", pf[0] and pf[0]["t"]),
                           ("balanced", bal[0] and bal[0]["t"])] + ([("given", extra["asset"])] if extra else [])
        else:
            holdout["assets"] = {}
            lines += ["On the holdout set:", "", "| Settings | asset.floor | Precision | Coverage | Asset where the "
                      "graders see none |", "|---|---|---|---|---|"]
            for name, t in asset_named or [("current", cur["asset"])]:
                if t is None:
                    continue
                m = asset_figures(f, t, mult, ranges=True)
                holdout["assets"][name] = dict(threshold=t, **m)
                lines.append(f"| {name} | {t:g} | {_p(m['lenient_precision'], m['lenient_precision_range'])} | "
                             f"{_p(m['lenient_recall'], m['lenient_recall_range'])} | {_p(m['asset_where_none'])} |")
            unspec = f.col(lambda r: r["unspecified"])
            has = f.col(lambda r: bool(r["assets"]))
            g_unspec = f.col(lambda r: r["truth"]["type"] == "unspecified")
            g_has = f.col(lambda r: r["truth"]["asset"] not in (None, "none"))
            sl = f.col(lambda r: r["level"] == "second_look")
            sl_right = f.col(lambda r: r["level"] == "second_look" and r["truth"]["asset"] in r["second_look_assets"])
            sl_none = f.col(lambda r: r["level"] == "second_look" and r["truth"]["asset"] == "none")
            holdout["assets"]["published_unspecified_share"] = f.ratio(unspec, has)
            holdout["assets"]["graders_unspecified_share"] = f.ratio(g_unspec, g_has)
            holdout["assets"]["second_look_right"] = f.ratio(sl_right, sl)
            lines += ["", f"Published asset type 'unspecified': {_p(f.ratio(unspec, has))} of sentences with an asset "
                          f"(graders: {_p(f.ratio(g_unspec, g_has))}). Assets from the second look: right "
                          f"{_p(f.ratio(sl_right, sl), f.boot(sl_right, sl))}, asset where the graders see none "
                          f"{_p(f.ratio(sl_none, sl))}, other asset {_p(f.ratio(sl - sl_right - sl_none, sl))} "
                          f"({int(sl.sum())} graded).", ""]

    report = where(data, "reports", f"evaluation_s{seed}.md")
    common.write_atomic(report, "\n".join(lines) + "\n")
    common.write_atomic(where(data, "reports", f"holdout_s{seed}.json"),
                        json.dumps(holdout, indent=1, sort_keys=True, default=str))
    if holdout.get("quality"):
        write_quality(conn, holdout["quality"])
    log(f"check tune: report -> {report}")
    return report


def _sweep_table(setting, pts, keys, heads=None):
    heads = heads or tuple(k.replace("_", " ").capitalize() for k in keys)
    out = [f"Sweep of {setting} on the tuning set:", "", f"| {setting} | " + " | ".join(heads) + " |",
           "|---" * (len(keys) + 1) + "|"]
    for r, m in pts:
        out.append(f"| {r['t']:g} | " + " | ".join(_p(m[k]) for k in keys) + " |")
    return out + [""]


# ------------------------------------------------------------------ measure_quality

def quality_rows(f, rows, cur, seed):
    """measure_quality rows (sheet holdout) for the current rule: a measure's own figures where it has
    MIN_PER_MEASURE holdout items or more, the overall figures (scope overall) otherwise."""
    kept = measure_kept(f, cur)
    right = f.right["lenient"]
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    overall = (f.ratio(kept * right, kept), f.ratio(kept * right * f.cand, right * f.cand))
    out = []
    for name in sorted({r["label_set"] for r in rows}):
        mine = numpy.array([r["label_set"] == name for r in rows], dtype=float)
        n = int(mine.sum())
        own = n >= MIN_PER_MEASURE
        p, r_ = ((f.ratio(mine * kept * right, mine * kept), f.ratio(mine * kept * right * f.cand, mine * right * f.cand))
                 if own else overall)
        try:
            labs, _ = Q.load(name)
            qsha = Q.sha(Q.graded_question(labs[0], Q.load(name)[1]))
        except SystemExit:
            continue
        out.append({"label_set": name, "version": "", "question_sha256": qsha, "sheet": "holdout",
                    "graded_units": n if own else len(rows), "precision": None if numpy.isnan(p) else round(p, 4),
                    "recall": None if numpy.isnan(r_) else round(r_, 4), "evaluated_at": at,
                    "report_path": f"data/reports/evaluation_s{seed}.md", "scope": "measure" if own else "overall"})
    return out


def write_quality(conn, rows):
    """Replace the holdout rows of measure_quality, keeping every other sheet's."""
    with conn.transaction():
        load_pg.replace_quality(conn, rows, {"holdout"}, "tune")
    conn.commit()
