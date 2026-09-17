#!/usr/bin/env python3
"""A3 - build the naming worksheet.

Reads  analysis/a3_clusters/clusters_*.csv        one row per cluster, no text
       analysis/a3_clusters/reference_measures.tsv
       analysis/a3_clusters/pool.csv
       data/clean/{doc_id}.txt                   (exemplar text, resolved at read)
Writes analysis/a3_clusters/naming_worksheet.xlsx  GITIGNORED - it carries clause text

  python3 analysis/a3_clusters/a3_worksheet.py

WHY AN XLSX AND NOT A CSV. The deliverable is a worksheet a domain expert who has
never seen this repository can fill in. That means the evidence has to sit beside
the decision: the cluster, how big it is, how many projects it reaches, what its
five most central clauses actually say, which words make it distinctive, and
which reference measure it is closest to - then five empty columns for the
verdict.

THE WORKSHEET CARRIES CLAUSE TEXT, so it is not committed. No document text is
committed anywhere in this repository; the one committed place is
inputs/labels/samples/. The file's location on disk is named in the PR body.

THE BLANK COLUMNS ARE BLANK. Nothing in this pipeline may name a measure
automatically - that would put a generative model in the decision path, which is
rule GV-10. The nearest reference entry is EVIDENCE for a person, not an answer.
"""
import argparse, csv, os, sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

DECISION_COLS = ["measure_name", "family", "direction", "decision", "merge_into"]
EVIDENCE_COLS = ["run", "cluster_id", "size", "n_projects", "n_documents",
                 "nearest_reference", "cosine", "top_terms"]
EXEMPLAR_COLS = [f"exemplar_{i}" for i in range(1, 6)]

HEAD_FILL = PatternFill("solid", fgColor="1F3864")
BLANK_FILL = PatternFill("solid", fgColor="FFF2CC")


def load_runs(out_dir):
    runs = []
    for name in sorted(os.listdir(out_dir)):
        if name.startswith("clusters_") and name.endswith(".csv"):
            key = name[len("clusters_"):-len(".csv")]
            with open(os.path.join(out_dir, name), newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            if rows:
                runs.append((key, rows))
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", default=HERE)
    args = ap.parse_args()

    runs = load_runs(args.out)
    if not runs:
        raise SystemExit("a3_worksheet: no clusters_*.csv found - run a3_cluster.py")
    with open(os.path.join(args.out, "pool.csv"), newline="", encoding="utf-8") as fh:
        pool = {r["clause_id"]: r for r in csv.DictReader(fh)}
    refs = []
    with open(os.path.join(args.out, "reference_measures.tsv"), newline="",
              encoding="utf-8") as fh:
        refs = list(csv.DictReader(fh, delimiter="\t"))

    cache = {}

    def text_of(clause_id):
        r = pool.get(clause_id)
        if not r:
            return "(clause not in the pool)"
        did = r["doc_id"]
        if did not in cache:
            with open(os.path.join(args.data, "clean", f"{did}.txt"),
                      encoding="utf-8") as fh:
                cache[did] = fh.read()
        return " ".join(cache[did][int(r["char_start"]):int(r["char_end"])].split())

    wb = Workbook()
    read_me = wb.active
    read_me.title = "read_me"
    fill_readme(read_me, runs, refs, len(pool))

    for key, rows in runs:
        ws = wb.create_sheet(title=key[:31])
        write_sheet(ws, key, rows, text_of)

    ws = wb.create_sheet(title="reference_measures")
    ws.append(["measure_name", "direction", "definition"])
    for r in refs:
        ws.append([r["measure_name"], r["direction"], r["definition"]])
    style_header(ws, 1)
    widths(ws, [60, 22, 90])

    path = os.path.join(args.out, "naming_worksheet.xlsx")
    wb.save(path)
    total = sum(len(rows) for _k, rows in runs)
    print(f"wrote {path}")
    print(f"  {len(runs)} runs, {total:,} cluster rows, {len(refs)} reference measures")
    return 0


def fill_readme(ws, runs, refs, n_pool):
    ws.column_dimensions["A"].width = 118
    lines = [
        ("Naming worksheet - measure discovery by clustering", True),
        ("", False),
        (f"Pool: {n_pool:,} clauses from every paragraph typed narrative or annex. "
         "No asset gate.", False),
        (f"Reference list: {len(refs)} hand-written measures, "
         f"{sum(1 for r in refs if r['direction'] == 'resilience_of_asset')} "
         "resilience_of_asset and "
         f"{sum(1 for r in refs if r['direction'] == 'digital_for_resilience')} "
         "digital_for_resilience.", False),
        ("", False),
        ("HOW TO READ A ROW", True),
        ("  size              clauses in the cluster", False),
        ("  n_projects        how many documents contribute. Regional documents "
         "serve several projects.", False),
        ("  exemplar_1..5     the five clauses nearest the cluster centre - read "
         "these first", False),
        ("  top_terms         the words that occur most more often inside this "
         "cluster than across the pool", False),
        ("  nearest_reference the reference measure this cluster is closest to, "
         "with its cosine", False),
        ("", False),
        ("WHAT TO FILL IN - five columns, all blank, all yours", True),
        ("  measure_name  the measure this cluster is. If it is a measure already "
         "on the reference sheet, copy that name EXACTLY - the join is on the "
         "name.", False),
        ("  family        the family it belongs to, by shared vocabulary rather "
         "than intervention type. Two measures belong together if a reader could "
         "confuse their text.", False),
        ("  direction     resilience_of_asset or digital_for_resilience", False),
        ("  decision      accept  this cluster is a measure, name it above", False),
        ("                merge   it belongs with another cluster - say which in "
         "merge_into", False),
        ("                reject  it is not a measure at all (boilerplate, a "
         "heading, a list of objects)", False),
        ("  merge_into    the cluster_id this one should be merged into, when "
         "decision is merge", False),
        ("", False),
        ("WHY THE CLUSTER COUNT IS HIGH", True),
        ("  The splitter over-clusters on purpose. Splitting a measure after it "
         "has been named is a forbidden redefinition, so a cluster holding half a "
         "measure is cheap and a cluster holding two measures is expensive. Merge "
         "up; never split down.", False),
        ("", False),
        ("ONE SHEET PER RUN", True),
        ("  Every run is on its own sheet, so the same clauses can be seen under "
         "the raw representation and under the asset-projected one. Naming one "
         "sheet is enough to make the method usable; the others are there for the "
         "comparison.", False),
        ("", False),
        ("THE WORKSHEET CARRIES CLAUSE TEXT AND IS NOT COMMITTED. No document "
         "text is committed anywhere in this repository.", False),
    ]
    for i, (text, bold) in enumerate(lines, start=1):
        c = ws.cell(row=i, column=1, value=text)
        c.font = Font(bold=bold, size=12 if bold and i == 1 else 10)
        c.alignment = Alignment(wrap_text=False, vertical="top")


def write_sheet(ws, key, rows, text_of):
    header = EVIDENCE_COLS + EXEMPLAR_COLS + DECISION_COLS
    ws.append(header)
    for r in rows:
        exemplars = r["exemplar_clause_ids"].split("|")[:5]
        vals = [key, r["cluster_id"], int(r["size"]), int(r["n_projects"]),
                int(r["n_documents"]), r["nearest_reference"], float(r["cosine"]),
                r["top_terms"]]
        vals += [text_of(c) for c in exemplars]
        vals += [""] * len(DECISION_COLS)
        ws.append(vals)
    style_header(ws, len(header))
    widths(ws, [16, 22, 8, 10, 11, 46, 9, 40] + [70] * 5 + [46, 22, 22, 12, 22])
    for row in ws.iter_rows(min_row=2, min_col=len(EVIDENCE_COLS) + len(EXEMPLAR_COLS) + 1,
                            max_col=len(header)):
        for c in row:
            c.fill = BLANK_FILL
    ws.freeze_panes = "C2"


def style_header(ws, ncols):
    for i in range(1, ncols + 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(vertical="center")


def widths(ws, w):
    for i, width in enumerate(w, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


if __name__ == "__main__":
    sys.exit(main())