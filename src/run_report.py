#!/usr/bin/env python3
"""Draft the run report as a note for the project folder in the notes vault.

Reads  data/reports/{fetch,clean,audit,fetch_procurement,clean_procurement,
       audit_procurement}.txt
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
import argparse, os, re, subprocess, sys
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


def build(data, topic, today):
    fetch = read(where(data, "reports", "fetch.txt"))
    clean = read(where(data, "reports", "clean.txt"))
    audit = read(where(data, "reports", "audit.txt"))
    pfetch = read(where(data, "reports", "fetch_procurement.txt"))
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
        ("Notices", grab(pfetch, "notices rows")),
        ("Awards", grab(pfetch, "awards rows")),
    ]
    # Looked up by label, so adding a row cannot shift what the summary says.
    got = dict(rows)
    problems = []
    for name, text in (("appraisal", audit), ("procurement", paudit)):
        for check, hits, share, sev in checks(text):
            if hits:
                problems.append(f"- {name}, {sev}: {check}: {hits:,} ({share:.2f}%)")
    defects = sum(1 for p in problems if ", defect:" in p)

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
        "- `./run.sh` on the box, from `/ygg/projects/wbg-digital-resilience`.",
        "",
        "## Numbers",
        "| | Count |",
        "|---|---|",
    ] + [f"| {k} | {v} |" for k, v in rows] + [
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
