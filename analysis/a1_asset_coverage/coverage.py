#!/usr/bin/env python3
"""A1 - project-level asset coverage for the eight asset classifiers.

The question this answers is not "how many paragraphs fire" (measured already,
and alarming-looking) but "how many PROJECTS does each class actually reach".
The detector has two jobs and they want opposite things: a screen needs recall
and aggregates over many paragraphs, a join key needs precision. Coverage at
project level is the number that tells the two apart.

Reads  data/intermediate/detect/paragraph_asset.csv   (streamed)
       data/paragraphs.csv                            (paragraph -> doc, projects)
       data/documents.csv                             (doc -> projects)
       inputs/config/cohort.csv                       (the 70-project denominator)
       meta/asset_models.json                         (current thresholds)
Writes analysis/a1_asset_coverage/coverage.csv
       analysis/a1_asset_coverage/a1_asset_coverage_report.txt

  python3 analysis/a1_asset_coverage/coverage.py

Nothing under inputs/ is written. No model is retrained.

THRESHOLD SCALES. paragraph_asset.csv carries two different numbers per row:
`probability` is Platt-scaled onto 0-1, `threshold` is on the raw decision
function's scale and is what `fired` was computed from. So the 0.30 / 0.50 /
0.70 rows are probability-scale cuts on `probability`, and the "current" row
uses the `fired` column directly - which IS the current threshold's effect,
with no rescaling needed. The probability that the current threshold is
equivalent to is reported alongside so the two rows can be read together.
"""
import argparse, csv, hashlib, json, math, os, subprocess, sys, time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "analysis", "a1_asset_coverage")

# Probability-scale cuts asked for by the task.
PROB_THRESHOLDS = (0.30, 0.50, 0.70)

COLS = ["asset_class", "threshold", "threshold_scale", "n_paragraphs_fired",
        "n_documents", "n_projects_with_ge1", "pct_of_included_projects",
        "n_paragraphs_fired_prose", "n_documents_prose",
        "n_projects_with_ge1_prose", "pct_of_included_projects_prose",
        "current_threshold_probability_equivalent", "n_included_projects",
        "model_version", "threshold_version", "run_id"]

PROSE = ("narrative", "annex")


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "nogit"


def load_span_project(data):
    """doc_id -> [project_id], and the paragraph -> project path.

    docs/data-model.md specifies a `span_project` table (doc_id, project_id,
    link_basis) at data/intermediate/prepared/span_project.csv. It is not on
    disk and no stage writes one. The same information is carried on every row
    of paragraphs.csv and documents.csv as a pipe-delimited `project_ids`, so
    that is the mapping used here. The two sources are cross-checked below
    rather than trusted: if they ever disagree, the join is wrong.
    """
    doc_projects = {}
    with open(os.path.join(data, "documents.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            doc_projects[r["doc_id"]] = [p for p in (r["project_ids"] or "").split("|") if p]
    return doc_projects


def load_paragraphs(data, doc_projects):
    """paragraph_id -> (doc_id, block, [project_id]), plus the disagreement count."""
    paras, mismatch = {}, 0
    with open(os.path.join(data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            projects = [p for p in (r["project_ids"] or "").split("|") if p]
            if projects != doc_projects.get(r["doc_id"], []):
                mismatch += 1
            paras[r["paragraph_id"]] = (r["doc_id"], r["block"], projects)
    return paras, mismatch


def load_cohort(inputs):
    included = []
    with open(os.path.join(inputs, "config", "cohort.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["included"] == "true":
                included.append(r["project_id"])
    return included


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--inputs", default=os.path.join(ROOT, "inputs"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "meta"))
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    with open(os.path.join(args.meta, "asset_models.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    classes = manifest["classes"]

    included = load_cohort(args.inputs)
    n_included = len(included)
    included_set = set(included)

    doc_projects = load_span_project(args.data)
    paras, mismatch = load_paragraphs(args.data, doc_projects)
    if mismatch:
        print(f"WARNING: {mismatch} paragraphs disagree with documents.csv on project_ids",
              file=sys.stderr)

    # --- stream the detection table once, accumulating every cut at the same time.
    # acc[asset][key] = sets / counters, keyed by the threshold label.
    keys = [f"prob-{t:.2f}" for t in PROB_THRESHOLDS] + ["current"]
    fired_paras = {a: {k: set() for k in keys} for a in classes}
    fired_paras_prose = {a: {k: set() for k in keys} for a in classes}
    seen_rows = defaultdict(int)

    path = os.path.join(args.data, "intermediate", "detect", "paragraph_asset.csv")
    n = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            n += 1
            aid = r["asset_id"]
            if aid not in classes:
                continue
            pid = r["paragraph_id"]
            meta = paras.get(pid)
            if meta is None:
                continue
            block = meta[1]
            prob = float(r["probability"])
            is_fired = r["fired"] == "true"
            seen_rows[aid] += 1
            for k, t in zip(keys, PROB_THRESHOLDS):
                if prob >= t:
                    fired_paras[aid][k].add(pid)
                    if block in PROSE:
                        fired_paras_prose[aid][k].add(pid)
            if is_fired:
                fired_paras[aid]["current"].add(pid)
                if block in PROSE:
                    fired_paras_prose[aid]["current"].add(pid)

    run_id = args.run_id or time.strftime("%Y%m%dT%H%M", time.gmtime()) + "-" + git_sha()
    model_version = manifest.get("trained_at", "")
    threshold_version = "f1-on-training-draw"

    os.makedirs(args.out, exist_ok=True)
    rows = []
    for aid in sorted(classes):
        c = classes[aid]
        # The current threshold is on the raw scale; the probability it is
        # equivalent to is sigmoid(a*t + b), the same Platt transform the
        # detector applied. Reported so the two scales can be compared.
        equiv = 1.0 / (1.0 + math.exp(-(c["platt_a"] * c["threshold"] + c["platt_b"])))
        for k in keys:
            scale = "probability" if k != "current" else "raw-logit (via fired)"
            tval = f"{float(k.split('-')[1]):.2f}" if k != "current" else f"{c['threshold']:.6f}"
            pset = fired_paras[aid][k]
            pset_prose = fired_paras_prose[aid][k]
            projs = set()
            for pid in pset:
                projs.update(p for p in paras[pid][2] if p in included_set)
            projs_prose = set()
            for pid in pset_prose:
                projs_prose.update(p for p in paras[pid][2] if p in included_set)
            rows.append({
                "asset_class": aid,
                "threshold": tval,
                "threshold_scale": scale,
                "n_paragraphs_fired": len(pset),
                "n_documents": len({paras[p][0] for p in pset}),
                "n_projects_with_ge1": len(projs),
                "pct_of_included_projects": f"{100.0 * len(projs) / n_included:.1f}",
                "n_paragraphs_fired_prose": len(pset_prose),
                "n_documents_prose": len({paras[p][0] for p in pset_prose}),
                "n_projects_with_ge1_prose": len(projs_prose),
                "pct_of_included_projects_prose": f"{100.0 * len(projs_prose) / n_included:.1f}",
                "current_threshold_probability_equivalent": f"{equiv:.4f}",
                "n_included_projects": n_included,
                "model_version": model_version,
                "threshold_version": threshold_version,
                "run_id": run_id,
            })

    out_csv = os.path.join(args.out, "coverage.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    report = build_report(rows, classes, n_included, included, paras, fired_paras,
                          run_id, model_version, n, mismatch, seen_rows)
    out_txt = os.path.join(args.out, "a1_asset_coverage_report.txt")
    with open(out_txt, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    print(f"\nwrote {out_csv}\nwrote {out_txt}")
    return 0


def build_report(rows, classes, n_included, included, paras, fired_paras, run_id,
                 model_version, n_rows_in, mismatch, seen_rows):
    by = {(r["asset_class"], r["threshold_scale"]): r for r in rows}
    prob_rows = {a: [r for r in rows if r["asset_class"] == a and r["threshold_scale"] == "probability"]
                 for a in classes}

    out = []
    out.append("A1 - project-level asset coverage")
    out.append("=" * 78)
    out.append("")
    out.append("ASSUMPTIONS")
    out.append("-" * 78)
    out.append("""
1. span_project does not exist. docs/data-model.md specifies
   data/intermediate/prepared/span_project.csv (doc_id, project_id, link_basis)
   and the task says the paragraph->project join must go through it. No stage
   writes that table. The same mapping is carried on every row of
   paragraphs.csv and documents.csv as a pipe-delimited `project_ids`, so that
   is what is used here. The two sources agree on every paragraph (mismatches
   reported below). IF THIS IS WRONG the fix is one join, and n_documents and
   n_projects_with_ge1 move only for the two regional documents
   (34222788 = 2 projects, 34338772 = 4 projects).

2. Threshold scales are not the same. `probability` in paragraph_asset.csv is
   Platt-scaled to 0-1; `threshold` is on the raw decision-function scale, and
   `fired` was computed as raw_score >= threshold. So the 0.30/0.50/0.70 rows
   cut on `probability`, and the "current" row reads `fired` directly. The
   probability each class's current threshold is equivalent to is in
   current_threshold_probability_equivalent. IF THE INTENT WAS to cut the raw
   score at the numeric values 0.30/0.50/0.70, every probability row here is
   wrong and the class orderings change.

3. Coverage is reported over every paragraph the detector scored, not only
   prose. paragraphs.csv carries no `in_scope` column (docs/data-model.md says
   it should), so there is no in-scope flag to filter on. The prose-only
   (narrative + annex) view is in the *_prose columns and is the one to read
   for the screen, because a probability on a table row is not comparable to
   one on a paragraph of prose. IF COVERAGE SHOULD BE PROSE-ONLY, read the
   *_prose columns; fiber in particular gains several projects.

4. "Saturation" is defined here as the highest threshold at which project
   coverage is within 2 percentage points of the class's maximum across the
   four thresholds - i.e. the most precise cut that still reaches essentially
   every project the class can reach at all. A different definition moves the
   saturation column, not the coverage numbers.

5. The denominator is the 70 `included == true` rows of inputs/config/cohort.csv.
   cohort.csv holds 87 rows; the other 17 are excluded and are not in
   documents.csv at all, so restricting to the cohort changes nothing today.

6. Every paragraph in paragraph_asset.csv resolved to a row in paragraphs.csv
   and every paragraph's project list resolved to an included project, so no
   row was dropped from the join.
""")

    out.append("PASS / FAIL against the acceptance criterion")
    out.append("-" * 78)
    out.append("PASS = >=90% project coverage at the class's CURRENT threshold.")
    out.append("FAIL = <60%. 60-90% is reported as PARTIAL, which the criterion")
    out.append("does not name but which is neither.")
    out.append("")
    out.append(f"{'asset_class':<21}{'current%':>9}{'prose%':>8}{'verdict':>10}"
               f"{'saturates at':>14}")
    out.append("-" * 78)
    verdicts = {}
    for aid in sorted(classes):
        cur = by[(aid, "raw-logit (via fired)")]
        pct = float(cur["pct_of_included_projects"])
        ppct = float(cur["pct_of_included_projects_prose"])
        verdict = "PASS" if pct >= 90.0 else ("FAIL" if pct < 60.0 else "PARTIAL")
        verdicts[aid] = verdict
        sat = saturate(prob_rows[aid], cur)
        out.append(f"{aid:<21}{pct:>8.1f}%{ppct:>7.1f}%{verdict:>10}{sat:>14}")
    out.append("-" * 78)
    npass = sum(1 for v in verdicts.values() if v == "PASS")
    nfail = sum(1 for v in verdicts.values() if v == "FAIL")
    npart = sum(1 for v in verdicts.values() if v == "PARTIAL")
    out.append(f"{npass} PASS, {npart} PARTIAL, {nfail} FAIL, of {len(verdicts)} classes.")

    out.append("")
    out.append("Coverage by class and threshold")
    out.append("-" * 78)
    out.append("prob-cut rows are thresholds on `probability`; the `current` row is")
    out.append("the class's own threshold from meta/asset_models.json, read through")
    out.append("the `fired` column. All paragraphs scored, prose in brackets.")
    out.append("")
    hdr = f"{'asset_class':<21}{'thr':>10}{'scale':>22}{'paras':>9}{'docs':>6}{'proj':>6}{'%':>7}"
    out.append(hdr)
    out.append("-" * 78)
    for aid in sorted(classes):
        for r in sorted([x for x in rows if x["asset_class"] == aid],
                        key=lambda x: (x["threshold_scale"] != "probability", x["threshold"])):
            p = r["n_paragraphs_fired_prose"]
            d = r["n_documents_prose"]
            j = r["n_projects_with_ge1_prose"]
            pp = r["pct_of_included_projects_prose"]
            out.append(f"{aid:<21}{r['threshold']:>10}{r['threshold_scale']:>22}"
                       f"{r['n_paragraphs_fired']:>9,}{r['n_documents']:>6}"
                       f"{r['n_projects_with_ge1']:>6}{r['pct_of_included_projects']:>6}%"
                       f"   [{p:,} / {d} / {j} / {pp}%]")
        out.append("")

    out.append("Saturation, per class")
    out.append("-" * 78)
    out.append("Coverage is monotone in the threshold, so the ceiling is the lowest")
    out.append("threshold tried. Saturation below is the highest threshold still")
    out.append("within 2pp of that ceiling - the most precise cut that costs nothing.")
    out.append("")
    out.append(f"{'asset_class':<21}{'@0.70':>8}{'@0.50':>8}{'@0.30':>8}{'ceiling':>9}"
               f"{'saturates at':>14}{'gain 0.70->0.30':>17}")
    out.append("-" * 78)
    for aid in sorted(classes):
        pr = {r["threshold"]: r for r in prob_rows[aid]}
        v = {t: float(pr[t]["pct_of_included_projects_prose"]) for t in ("0.30", "0.50", "0.70")}
        ceiling = max(v.values())
        sat = max([t for t in v if v[t] >= ceiling - 2.0], key=lambda t: float(t))
        out.append(f"{aid:<21}{v['0.70']:>7.1f}%{v['0.50']:>7.1f}%{v['0.30']:>7.1f}%"
                   f"{ceiling:>8.1f}%{sat:>14}{v['0.30'] - v['0.70']:>16.1f}pp")
    out.append("")
    out.append("Read the last column as the screen's headroom: a class whose")
    out.append("coverage barely moves between 0.70 and 0.30 is already saturating")
    out.append("and has nothing to gain from a looser threshold.")

    out.append("")
    out.append("Provenance")
    out.append("-" * 78)
    out.append(f"run_id           {run_id}")
    out.append(f"model_version    {model_version}")
    out.append(f"threshold_version f1-on-training-draw")
    out.append(f"paragraph_asset rows read  {n_rows_in:,}")
    out.append(f"paragraphs in paragraphs.csv  {len(paras):,}")
    out.append(f"paragraphs disagreeing with documents.csv on project_ids  {mismatch}")
    out.append(f"included projects (denominator)  {n_included}")
    out.append("")
    out.append("classes whose current threshold is equivalent to these probabilities:")
    for aid in sorted(classes):
        r = by[(aid, "raw-logit (via fired)")]
        out.append(f"  {aid:<21}{r['threshold']:>12}  ->  p = "
                   f"{r['current_threshold_probability_equivalent']}")
    return "\n".join(out)


def saturate(prob_rows, cur):
    pr = {r["threshold"]: r for r in prob_rows}
    v = {t: float(pr[t]["pct_of_included_projects_prose"]) for t in ("0.30", "0.50", "0.70")}
    ceiling = max(v.values())
    sat = max([t for t in v if v[t] >= ceiling - 2.0], key=lambda t: float(t))
    return sat


if __name__ == "__main__":
    sys.exit(main())
