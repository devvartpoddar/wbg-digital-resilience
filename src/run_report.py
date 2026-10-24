#!/usr/bin/env python3
"""Draft the run report as a note for the project folder in the notes vault.

Reads  data/reports/{fetch,clean,audit,fetch_procurement,clean_procurement,
       audit_procurement}.txt, the last run's stage times (run_stages.jsonl, as
       ./wbg run writes them), and from Postgres the spend of that run's window
       (wbg_store.spend) and the evaluation's precision and recall per measure
       (wbg.measure_quality). A missing database leaves those sections empty
       rather than failing the note.
Writes data/reports/{date} {topic}.md

Reports do not belong in the repository. This writes a Markdown note in the
shape of Projects/WBG Digital Resilience/_template.md, into data/ (never
committed). The agent that ran the pipeline then copies it into the vault with
the notes tools - Projects/WBG Digital Resilience/Reports/ - and adds a line to
that folder's README index. The pipeline itself never writes outside its own
folder.

The note carries numbers only. "Decisions needed" is left for whoever read the
output, because a script cannot know what is surprising.

  python3 src/run_report.py --topic "Cleaning run"
"""
import argparse, json, os, re, subprocess, sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402

CHECK_ROW = re.compile(r"^(.+?)\s{2,}(\d[\d,]*)\s+([\d.]+)%\s+(defect|soft)\s*$")


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def grab(text, label):
    m = re.search(rf"^\s*{re.escape(label)}\s*:?\s*([\d,]+)", text, re.M)
    return m.group(1) if m else "?"


def checks(text):
    """(name, hits, share, severity) for every audit check row."""
    out = []
    for line in text.splitlines():
        m = CHECK_ROW.match(line.rstrip())
        if m:
            out.append((m.group(1).strip(), int(m.group(2).replace(",", "")),
                        float(m.group(3)), m.group(4)))
    return out


def commit():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


SPEND_GROUP = (("asset:", "classify"), ("types:", "measure"), ("pairs", "measure"),
               ("link:", "link"))


def runs(data):
    """Every run ./wbg run recorded, oldest first; empty when it has written
    nothing yet."""
    path = where(data, "reports", "run_stages.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [line for line in fh.read().splitlines() if line.strip()]
    except OSError:
        return []
    out = []
    for line in rows:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def last_run(data):
    got = runs(data)
    return got[-1] if got else None


def earlier(run, all_runs, keep=5):
    """(started, total seconds, stages) for the runs before the one reported, so
    a note written after a repeat run still shows what the earlier ones cost.
    Oldest first; the tail is kept."""
    out = []
    for r in all_runs:
        if r is run:
            break
        secs = r.get("seconds") or {}
        out.append((r.get("started", "?"), sum(float(v) for v in secs.values()),
                    ", ".join(secs)))
    return sorted(out)[-keep:]


def spend_by_stage(run):
    """(stage, calls, US$) from the spend ledger, for the run's own window. The
    ledger records one row per step (types:<set>, pairs, link: pairs); a step is
    folded into the pipeline stage that asked it."""
    if not run:
        return []
    sql = ("SELECT stage, coalesce(count(*), 0), coalesce(sum(calls), 0), coalesce(sum(cost_usd), 0) "
           "FROM wbg_store.spend WHERE at >= %s AND at <= %s GROUP BY stage")
    rows = _query(sql, (run["started"], run["ended"]))
    got = {}
    for stage, _steps, calls, cost in rows or []:
        group = next((g for pre, g in SPEND_GROUP if stage.startswith(pre)), stage.split(":")[0])
        c, k = got.get(group, (0, 0.0))
        got[group] = (c + int(calls), k + float(cost))
    return [(g, c, k) for g, (c, k) in got.items()]


def quality():
    """(sheet, rows) from wbg.measure_quality: precision and recall per measure
    at its floor. The evaluation sheet is the figure to quote; a training sheet
    is an upper estimate."""
    rows = _query("SELECT label_set, sheet, graded_units, precision, recall FROM wbg.measure_quality "
                  "ORDER BY sheet, label_set")
    if not rows:
        return None, []
    sheet = "evaluation" if any(r[1] == "evaluation" for r in rows) else rows[0][1]
    return sheet, [r for r in rows if r[1] == sheet]


def _query(sql, params=()):
    try:
        import psycopg
        with psycopg.connect("postgresql:///work") as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [r for r in cur.fetchall()]
    except Exception:                                  # noqa: BLE001  no Postgres here
        return None


def build(data, topic, today):
    fetch = read(where(data, "reports", "fetch.txt"))
    clean = read(where(data, "reports", "clean.txt"))
    audit = read(where(data, "reports", "audit.txt"))
    pclean = read(where(data, "reports", "clean_procurement.txt"))
    paudit = read(where(data, "reports", "audit_procurement.txt"))

    rows = [
        ("Appraisal documents", grab(fetch, "documents written")),
        ("  cleaned (read from the PDF)", grab(clean, "documents cleaned")),
        ("  not cleaned", grab(clean, "documents not cleaned")),
        ("Paragraphs kept", grab(clean, "paragraphs kept")),
        ("  given to the model", grab(clean, "given to the model (for_model)")),
        ("Paragraphs dropped", grab(clean, "paragraphs dropped")),
        ("Sentences", grab(clean, "sentences")),
        ("Procurement packages", grab(pclean, "packages (one row each)")),
        ("Notices", grab(pclean, "notices rows")),
        ("Awards", grab(pclean, "awards rows")),
    ]
    # Looked up by label, so adding a row cannot shift what the summary says.
    got = dict(rows)
    problems = []
    for name, text in (("appraisal", audit), ("procurement", paudit)):
        for check, hits, share, sev in checks(text):
            if hits:
                problems.append(f"- {name}, {sev}: {check}: {hits:,} ({share:.2f}%)")
    defects = sum(1 for p in problems if ", defect:" in p)

    all_runs = runs(data)
    run = all_runs[-1] if all_runs else None
    timing = (run or {}).get("seconds") or {}
    spend = {g: (c, k) for g, c, k in spend_by_stage(run)}
    sheet, qual = quality()
    stages = [s for s in ("fetch", "prepare", "embed", "search", "classify", "measure", "link",
                          "publish", "audit") if s in timing]

    lines = [
        "---",
        f"date: {today}",
        "type: report",
        "project: wbg-digital-resilience",
        "workstream: infra-resilience",
        f"run: {today}",
        f"commit: {commit()}",
        "tags: [report, project/wbg-digital-resilience, ws/infra-resilience]",
        "---",
        "",
        f"# {topic}",
        "",
        "## Summary",
        f"- {got['Appraisal documents']} appraisal documents, {got['Paragraphs kept']} "
        f"paragraphs ({got['  given to the model']} given to the model) and "
        f"{got['Sentences']} sentences; {got['Procurement packages']} procurement packages.",
        f"- {defects} defect-level audit checks with hits"
        + (" - see Problems found." if defects else "."),
        "",
        "## What ran",
        "- The pipeline (`./wbg run`, which `./run.sh` runs), from the main checkout.",
        "",
        "## Numbers",
        "| | Count |",
        "|---|---|",
    ] + [f"| {k} | {v} |" for k, v in rows] + [
        "",
        "## Time and spend by stage",
    ] + ([f"- The last run started {run['started']} and ended {run['ended']}"
          + ("; it stopped on a failed stage." if run.get("ended_early") else ".")]
         if run else ["- No run has written `data/reports/run_stages.jsonl` yet."]) + (
        ["", "| Stage | Wall clock | Model calls | US$ |", "|---|---|---|---|"]
        + [f"| {s} | {float(timing[s]):.0f} s | {spend.get(s, (0, 0))[0]:,} | "
           f"{spend.get(s, (0, 0))[1]:.2f} |" for s in stages]
        + [f"| all | {sum(float(timing[s]) for s in stages):.0f} s | "
           f"{sum(spend.get(s, (0, 0))[0] for s in stages):,} | "
           f"{sum(spend.get(s, (0, 0))[1] for s in stages):.2f} |"]
        if stages else ["- The run recorded no stage time."]) + (
        ["- Earlier runs recorded: "
         + "; ".join(f"{s} ({t:.0f} s: {st})" for s, t, st in earlier(run, all_runs)) + "."]
        if earlier(run, all_runs) else []) + [
        "",
        "## Evaluation: precision and recall at the floor",
    ] + ([f"- Sheet: {sheet}. "
          + ("An evaluation draw fits nothing: these are the figures to quote."
             if sheet == "evaluation" else
             "A training sheet holds the hard cases and fits the layers, so these are upper estimates.")
          , "", "| Measure | Graded sentences | Precision | Recall |", "|---|---|---|---|"]
         + [f"| {r[0]} | {int(r[2]):,} | {float(r[3]):.2f} | {float(r[4]):.2f} |" for r in qual]
         if qual else ["- No evaluation has been run (`./wbg check evaluate`)."]) + [
        "",
        "## Problems found",
    ] + (problems or ["- None: every audit check is at zero."]) + [
        "",
        "## Decisions needed",
        "- (for the reader to fill in)",
        "",
        "## Files",
        "- Review sheets: `data/review/` in the project folder.",
        "- Full reports: `data/reports/*.txt`.",
        "- Postgres: database `work`, schema `wbg`.",
        "",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--topic", default="Pipeline run")
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()
    out_dir = where(args.data, "reports")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, f"{args.date} {args.topic}.md")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(build(args.data, args.topic, args.date))
    print(f"wrote {dest}")
    print("next: copy it to the notes vault at "
          f"Projects/WBG Digital Resilience/Reports/{os.path.basename(dest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
