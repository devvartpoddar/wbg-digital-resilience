#!/usr/bin/env python3
"""Measure the funnel against a hand-labelled workbook of activities: for a
theme column, what each level (candidates, asset gate, decision model) keeps
of the activities a person flagged, and what it wrongly keeps of those a
person flagged as not adaptation.

The workbook (data/labels/activities.xlsx, never in git: it holds document
text) has one row per activity: a project id, a stretch of appraisal text
pasted by hand, and a flag per theme column. Labels are per activity, not per
sentence, and carry no firmness grade, so this measures whether the funnel
finds a positive somewhere in the activity's text, not which sentence or how
firm. The text was pasted from older document versions, so each activity is
matched to the current paragraphs by shared word shingles, and the match
rate is reported first: an unmatched activity cannot be measured.

Reads  data/labels/activities.xlsx
       data/appraisal/paragraphs.csv, data/appraisal/text/
       data/measures/<set>-<version>/units.csv     the current measure run
Writes data/reports/evaluate_<set>_<column>.txt
       data/reports/evaluate_<set>_<column>.csv    one row per activity with
                                                   what each level found

  ./wbg evaluate cra --column "Data center: site-specific climate risk assessment or site selection"
  ./wbg evaluate cra --column "Resilient telecom" --text-regex "risk assessment|hazard map"
"""
import argparse, csv, os, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import measure  # noqa: E402

WORKBOOK = "activities.xlsx"           # under data/labels/
SHINGLE = 8           # words per shingle
MIN_SHINGLES = 3      # a paragraph matches an activity when it shares at least this many
NEGATIVE_COLUMN = "Adaptation"
NEGATIVE_VALUE = "no"
ID_COLUMN = "Project ID"
TEXT_COLUMN = "Measures / Activities"
THRESHOLDS = measure.REPORT_THRESHOLDS
WORD = re.compile(r"[a-z0-9]+")


def shingles(text):
    w = WORD.findall((text or "").lower())
    return {" ".join(w[i:i + SHINGLE]) for i in range(max(len(w) - SHINGLE + 1, 0))}


def read_workbook(path):
    """[{row, project_id, text, flags}] from the first sheet."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(it)]
    out = []
    for n, r in enumerate(it, 2):
        row = dict(zip(header, r))
        pid = str(row.get(ID_COLUMN) or "").strip().split()[0] if row.get(ID_COLUMN) else ""
        text = row.get(TEXT_COLUMN)
        if not pid or not text:
            continue
        out.append({"row": n, "project_id": pid, "text": str(text),
                    "flags": {k: ("" if v is None else str(v).strip()) for k, v in row.items()}})
    return header, out


def match_paragraphs(activities, data):
    """{activity row: [paragraph_id]}: the project's paragraphs sharing at
    least MIN_SHINGLES word shingles with the activity's text."""
    by_project = defaultdict(list)
    with open(where(data, "appraisal", "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        for p in csv.DictReader(fh):
            if p.get("for_model") != "true":
                continue
            for pid in p["project_ids"].split("|"):
                by_project[pid].append(p)
    texts = {}
    out = {}
    for a in activities:
        want = shingles(a["text"])
        hits = []
        for p in by_project.get(a["project_id"], []):
            doc = measure._text(texts, data, p["doc_id"])
            body = doc[int(p["char_start"]):int(p["char_end"])]
            n = len(want & shingles(body))
            if n >= MIN_SHINGLES:
                hits.append((n, p["paragraph_id"]))
        out[a["row"]] = [pid for _, pid in sorted(hits, reverse=True)]
    return out


def evaluate(name, data, column, text_regex=None, log=print, **kw):
    folder = measure.current_run(name, data, **kw)
    if not folder:
        raise SystemExit(f"evaluate: no measure run for {name} as it stands; run ./wbg measure "
                         f"{name} first")
    header, activities = read_workbook(where(data, "labels", WORKBOOK))
    if column not in header:
        raise SystemExit(f"evaluate: no column {column!r}; columns: {header}")
    rx = re.compile(text_regex, re.I) if text_regex else None
    matched = match_paragraphs(activities, data)
    units = defaultdict(list)
    with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
        for u in csv.DictReader(fh):
            units[u["paragraph_id"]].append(u)
    top_grade = max(int(u["grade"]) for us in units.values() for u in us if u["grade"] != "")
    rows = []
    for a in activities:
        flag = a["flags"].get(column, "")
        positive = bool(flag) and (rx.search(a["text"]) is not None if rx else True)
        negative = a["flags"].get(NEGATIVE_COLUMN, "").lower() == NEGATIVE_VALUE
        paras = matched[a["row"]]
        mine = [u for p in paras for u in units.get(p, [])]
        sent = [u for u in mine if u["sent"] == "true"]
        committed = [u for u in sent if int(u["grade"]) == top_grade]
        gate_unit = {t: any(u["asset_p_unit"] and float(u["asset_p_unit"]) >= t for u in committed)
                     for t in THRESHOLDS}
        gate_para = {t: any(u["asset_p_para"] and float(u["asset_p_para"]) >= t for u in committed)
                     for t in THRESHOLDS}
        gate_comp = {t: any(u["asset_p_comp"] and float(u["asset_p_comp"]) >= t for u in committed)
                     for t in THRESHOLDS}
        rows.append({"row": a["row"], "project_id": a["project_id"], "flag": flag,
                     "positive": positive, "negative": negative, "paragraphs": len(paras),
                     "candidates": len(mine), "graded": len(sent), "committed": len(committed),
                     "best_expected": max((float(u["expected"]) for u in sent), default=""),
                     **{f"gate_unit_{t}": gate_unit[t] for t in THRESHOLDS},
                     **{f"gate_para_{t}": gate_para[t] for t in THRESHOLDS},
                     **{f"gate_comp_{t}": gate_comp[t] for t in THRESHOLDS},
                     "snippet": " ".join(a["text"].split())[:160]})
    slug = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")[:40]
    base = where(data, "reports", f"evaluate_{name}_{slug}_{kw.get('context', 'sentence')}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    with open(base + ".csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    pos = [r for r in rows if r["positive"]]
    neg = [r for r in rows if r["negative"]]
    pos_m = [r for r in pos if r["paragraphs"]]
    neg_m = [r for r in neg if r["paragraphs"]]

    def share(rs, key):
        return f"{sum(1 for r in rs if r[key]) / len(rs):.0%} ({sum(1 for r in rs if r[key])}/{len(rs)})" \
            if rs else "n/a"
    lines = [f"{name} against {WORKBOOK} column {column!r}"
             + (f" and text /{text_regex}/" if text_regex else ""),
             f"  measure run {os.path.basename(folder)}",
             f"  activities: {len(rows)}; matched to a current paragraph: "
             f"{sum(1 for r in rows if r['paragraphs'])} ({MIN_SHINGLES}+ shared {SHINGLE}-word shingles)",
             f"  flagged positive: {len(pos)}, matched {len(pos_m)}; flagged '{NEGATIVE_COLUMN}' = "
             f"{NEGATIVE_VALUE}: {len(neg)}, matched {len(neg_m)}",
             "",
             "  recall on matched positives, by funnel level (an activity counts when any of",
             "  its paragraphs has a unit that passes the level):",
             f"    1 candidate (pattern or embedding top-N):     {share(pos_m, 'candidates')}",
             f"    2 graded by the decision model:               {share(pos_m, 'graded')}",
             f"    3 grade {top_grade} (committed):                      {share(pos_m, 'committed')}",
             "    3 + asset gate on the unit answer, at threshold " + ", ".join(
                 f"{t}: {share(pos_m, f'gate_unit_{t}')}" for t in THRESHOLDS),
             "    3 + asset gate on the paragraph, at threshold " + ", ".join(
                 f"{t}: {share(pos_m, f'gate_para_{t}')}" for t in THRESHOLDS),
             "    3 + asset gate on the component, at threshold " + ", ".join(
                 f"{t}: {share(pos_m, f'gate_comp_{t}')}" for t in THRESHOLDS),
             "",
             f"  false positives among matched negatives (grade {top_grade} somewhere in the activity):",
             f"    {share(neg_m, 'committed')}; with unit gate "
             + ", ".join(f"{t}: {share(neg_m, f'gate_unit_{t}')}" for t in THRESHOLDS),
             "",
             "  positives not found at level 3 (row, project, snippet):"]
    for r in pos_m:
        if not r["committed"]:
            lines.append(f"    {r['row']:>4} {r['project_id']} cand={r['candidates']} "
                         f"graded={r['graded']} best={r['best_expected']}  {r['snippet'][:100]}")
    lines.append("  positives with no matched paragraph: "
                 + ", ".join(str(r["row"]) for r in pos if not r["paragraphs"]))
    with open(base + ".txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    for line in lines:
        log(line)
    return base


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("--column", required=True, help="the workbook's theme column")
    ap.add_argument("--text-regex", help="a positive must also match this in its text")
    ap.add_argument("--context", default="sentence", choices=measure.CONTEXTS)
    ap.add_argument("--gate", default="none", choices=measure.GATES)
    ap.add_argument("--asset-min", type=float, default=0.0)
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    evaluate(args.label_set, args.data, args.column, args.text_regex, context=args.context,
             gate=args.gate, asset_min=args.asset_min)
    return 0


if __name__ == "__main__":
    sys.exit(main())
