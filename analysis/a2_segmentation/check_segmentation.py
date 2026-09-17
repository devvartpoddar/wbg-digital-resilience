#!/usr/bin/env python3
"""A2 - the acceptance check for clause segmentation.

Reads  analysis/a2_segmentation/check_set.csv   hand counts, no text
       data/intermediate/prepared/clauses.csv
       data/segment_report.txt                  (for the distribution and tally)
Writes analysis/a2_segmentation/report.txt

  python3 analysis/a2_segmentation/check_segmentation.py

The check set is twenty paragraphs that plainly state several measures each,
twenty of them read by hand and counted by hand. Selection was by reading, not
by how many clauses the splitter produced - ranking candidates on the splitter's
own output would make the check circular. The strata are the messiest text in
the corpus: bulleted component lists, annex activity lists, project-component
narrative and results-framework prose.

WHAT COUNTS AS ONE MEASURE. A measure is a technique, not a specification.
"Underground" and "aerial" deployment are two measures; "weather-proofing the
ducts, poles, switches and sockets" is one, because it is one technique applied
to a list of objects. Three of these paragraphs are project development
objective statements rather than commitments, and for those the counted unit is
a distinct stated objective. `basis` in check_set.csv records what was counted
in each case so a reviewer can disagree with a specific row rather than with
the whole exercise.

ACCEPTANCE. Clause count >= hand count - 1 on at least 16 of 20 paragraphs.
A failure is a result, not an error: it is recorded with the numbers and the
probe carries on.
"""
import argparse, csv, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "analysis", "a2_segmentation")
PASS_RATIO = 16
MIN_N = 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    with open(os.path.join(args.out, "check_set.csv"), newline="", encoding="utf-8") as fh:
        check = list(csv.DictReader(fh))

    by_para = {}
    with open(os.path.join(args.data, "intermediate", "prepared", "clauses.csv"),
              newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            by_para.setdefault(r["paragraph_id"], []).append(r["split_rule"])

    rows = []
    for c in check:
        pid = c["paragraph_id"]
        hand = int(c["hand_measure_count"])
        rules = by_para.get(pid, [])
        n = len(rules)
        rows.append({"pid": pid, "stratum": c["stratum"], "hand": hand, "clauses": n,
                     "ok": n >= hand - 1, "rules": rules, "basis": c["basis"]})

    n_ok = sum(1 for r in rows if r["ok"])
    verdict = "PASS" if (n_ok >= PASS_RATIO and len(rows) >= MIN_N) else "FAIL"

    dist, tally = read_stage_report(os.path.join(args.data, "segment_report.txt"))

    out = []
    out.append("A2 - clause segmentation: acceptance check")
    out.append("=" * 78)
    out.append("")
    out.append("ASSUMPTIONS")
    out.append("-" * 78)
    out.append("""
1. SCOPE CONFLICT, DELIBERATE. methodology Stage 4 says segmentation "runs only
   on paragraphs where at least one asset fired", and business-rule 31 says
   clauses rows exist only for such paragraphs. This probe segments every
   paragraph whose block_type is narrative or annex - 17,719 paragraphs - as the
   task specifies, because A3's pool is defined that way and an asset gate
   would silently drop fiber prose (the fiber classifier fires on ~1% of prose).
   CONSEQUENCE: clauses.csv currently violates business-rule 31. That rule and
   the pool definition in methodology Stage 4 have to be reconciled before this
   table is consumed downstream. Numbers that move if the gate is restored:
   every count in this report, and the whole A3 pool.

2. `block_type` and `section_label` do not exist in paragraphs.csv. The table on
   disk carries `block` (values narrative, annex, heading, table, footnote,
   frontmatter, template:*) and `section_title`/`section_path`. business-rules
   A1.10 says the column is `block_type` with values prose/heading/bullet/
   table_cell/footnote. segment.py reads either name, so it keeps working when
   the schema and the table are reconciled; the divergence is reported, not
   fixed.

3. `clean_version` is not stamped by src/clean.py on documents.csv or
   paragraphs.csv, so there is nothing to inherit. segment.py writes "clean-1",
   the first version of the cleaning rules, so business-rule 14 has a value to
   check against. IF THE CLEANING STAGE INTENDS A DIFFERENT VALUE, every clause
   row is stamped wrong - a stamp correction, not a boundary change.

4. A clause is accepted only if it contains at least one letter. Without this a
   numbered list's "1." becomes a one-character clause. The effect is small
   (see the count below) but it is a judgement, and it is cheaper than leaving
   132k rows with list markers among them.

5. Clause offsets are relative to the parent paragraph, per business-rule 23.
   The absolute position is paragraph.char_start + clause.char_start. A clause
   is trimmed of leading and trailing tokens carrying no alphanumeric character,
   so a bullet glyph does not open a clause and a trailing comma does not close
   one. A trailing ")" is trimmed with them, which is cosmetic.

6. Hand counts are one person's reading. `basis` in check_set.csv records what
   was counted per row. Where a row is arguable, the argument is about one
   paragraph, not about the method.

7. Two check-set paragraphs are results-framework objective statements rather
   than commitments. They are included because the task asks for
   results-framework text; for those rows the counted unit is a distinct stated
   objective.

8. The splitter over-splits rather than under-splits, deliberately. On this
   check set the median margin is +3 clauses and the worst case is
   32108178:p00207 at 14 clauses against 4 measures. Over-splitting is the
   cheap direction: an extra clause costs one row in clause_measure, while a
   missed boundary merges two measures into one unit and loses a measure from
   discovery permanently. IF THE INTENT WAS a unit close to one measure each,
   this splitter is too aggressive and the `verb_subtree` condition is the one
   to narrow - it produces 30.2% of all clauses on its own.
""")

    out.append("RESULT")
    out.append("-" * 78)
    out.append(f"check set        {len(rows)} paragraphs")
    out.append(f"criterion        clause count >= hand count - 1 on >= {PASS_RATIO} of "
               f"{MIN_N}")
    out.append(f"met              {n_ok} of {len(rows)}")
    out.append(f"VERDICT          {verdict}")
    short = [r for r in rows if not r["ok"]]
    if short:
        out.append("")
        out.append("paragraphs below the criterion:")
        for r in short:
            out.append(f"  {r['pid']}  hand={r['hand']}  clauses={r['clauses']}")
    else:
        out.append("")
        out.append("no paragraph fell below the criterion.")

    out.append("")
    out.append("Per paragraph")
    out.append("-" * 78)
    out.append(f"{'paragraph_id':<24}{'stratum':<20}{'hand':>5}{'clauses':>9}"
               f"{'margin':>8}  rules")
    for r in sorted(rows, key=lambda x: (x["clauses"] - x["hand"])):
        rules = ",".join(sorted(set(r["rules"]))) or "-"
        out.append(f"{r['pid']:<24}{r['stratum']:<20}{r['hand']:>5}{r['clauses']:>9}"
                   f"{r['clauses'] - r['hand']:>8}  {rules[:38]}")

    out.append("")
    out.append("The worked example")
    out.append("-" * 78)
    ex = next((r for r in rows if r["pid"] == "34338772:p00680"), None)
    if ex:
        out.append(f"34338772:p00680 (Angola, ANNEX 6) produced {ex['clauses']} clauses "
                   f"against {ex['hand']} measures counted by hand.")
        out.append("The task required at least four. It yields "
                   f"{'at least four' if ex['clauses'] >= 4 else 'FEWER THAN FOUR'}.")
        for i, rule in enumerate(ex["rules"], 1):
            out.append(f"  c{i:02d}  {rule}")

    out.append("")
    out.append("The known limit, restated")
    out.append("-" * 78)
    out.append("A coordinated object noun phrase still bundles. One clause of")
    out.append("34338772:p00680 holds two measures at once: the deploy verb governs a")
    out.append("weather-resistant cable and then a list of passive plant - ducts, poles,")
    out.append("switches, sockets, appliances - under a second verb, and the splitter")
    out.append("leaves them together. That is intended: clause_measure keys on")
    out.append("clause_id + measure_id and is multi-label by design. Noun-phrase")
    out.append("splitting was not added.")
    out.append("")
    out.append("NO CLAUSE TEXT IS QUOTED IN THIS REPORT. No document text is committed")
    out.append("in this repository. The clauses are resolved from data/clean/ at read")
    out.append("time, and the offsets in clauses.csv are what makes that possible.")

    out.append("")
    out.append("Clause-count distribution (whole prose corpus)")
    out.append("-" * 78)
    out.extend(dist)
    out.append("")
    out.append("split_rule tally (whole prose corpus)")
    out.append("-" * 78)
    out.extend(tally)

    body = "\n".join(out)
    path = os.path.join(args.out, "report.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    print(body)
    print(f"\nwrote {path}")
    return 0


def read_stage_report(path):
    """The distribution and tally live in the stage report; surface them here so
    the probe report answers everything the task asked it to."""
    if not os.path.exists(path):
        return ["(data/segment_report.txt not found - run src/segment.py first)"], []
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    dist, tally, mode = [], [], None
    for line in lines:
        if line.startswith("clause count per paragraph"):
            mode = "dist"
        elif line.startswith("split_rule tally"):
            mode = "tally"
        elif line.startswith("clauses per segmented paragraph") or \
                line.startswith("NO TEXT IS WRITTEN"):
            mode = None
        elif mode == "dist" and line.startswith("  "):
            dist.append(line)
        elif mode == "tally" and line.startswith("  "):
            tally.append(line)
    return dist, tally


if __name__ == "__main__":
    sys.exit(main())