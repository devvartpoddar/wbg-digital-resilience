#!/usr/bin/env python3
"""Build the projects table: one row per cohort project.

Reads  data/raw/projects/{project_id}.json   (src/fetch_projects.py)
       data/raw/documents.csv               (owner_unit, from src/fetch.py)
       inputs/config/cohort.csv
Writes data/projects/projects.csv
       data/reports/projects.txt

Two sources, each named in the columns it fills:

  projects interface   name, status, approval and closing dates, region,
                       country, lending instrument, commitment, the Global
                       Practice(s) and the top sectors
  appraisal documents  managing_unit: the owner the documents interface
                       records for the project's most recent appraisal
                       document that records one, with that document and its
                       date. It is usually a practice unit ("Digital Dev -
                       AFR EAST/SOUTH (IDD04)"), sometimes a country office
                       or a regional vice presidency's office; it is printed
                       as recorded, never interpreted.

Neither source publishes the vice presidency as a field, so there is no such
column. A project the interface has no record of keeps its cohort row with
in_projects_interface=false.
"""
import argparse, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
from fetch import ROOT, read_cohort  # noqa: E402
from dates import iso_date  # noqa: E402
from common import read_csv, write_csv  # noqa: E402

COLS = ["project_id", "cohort_set", "in_projects_interface", "project_name", "status",
        "approval_date", "approval_fy", "closing_date", "region", "country_code",
        "country_name", "lending_instrument", "commitment_usd_m", "commitment_v2_usd_m",
        "commitment_v3_usd_m", "record_source", "practice",
        "practice_code", "sectors", "managing_unit", "unit_code", "unit_doc_id",
        "unit_as_of"]

_GP_CODE = re.compile(r"<GP_PRACTICE_CODE>([^<]*)</GP_PRACTICE_CODE>")
_GP_NAME = re.compile(r"<GP_PRACTICE_NAME><!\[CDATA\[(.*?)\]\]")
# A unit code in brackets ("... (IDD04)"), or a bare code opening the text
# ("IDD02 - Digital Dev ...", "LCC3C"). Three-letter region names are not codes.
_UNIT_IN_BRACKETS = re.compile(r"\(([A-Z][A-Z0-9]{3,5})\)")
_UNIT_LEADING = re.compile(r"^([A-Z][A-Z0-9]{3,5})\b")
_NOT_A_UNIT = {"", "N/A", "NA"}


def _unique(seq):
    return list(dict.fromkeys(x for x in seq if x))


def practices(gp_info):
    """(names, codes) of the Global Practice(s), in the order given."""
    return (_unique(n.strip() for n in _GP_NAME.findall(gp_info or "")),
            _unique(c.strip() for c in _GP_CODE.findall(gp_info or "")))


def sectors(rec):
    out = []
    for k in ("sector1", "sector2", "sector3"):
        s = rec.get(k) or {}
        if isinstance(s, dict) and s.get("Name"):
            out.append(f"{s['Name']} ({s.get('Percent', '')}%)")
    return "; ".join(out)


def unit_codes(owner):
    """Every unit code an owner string names, joined with '|'."""
    codes = []
    for part in owner.split(";"):
        part = part.strip()
        codes += _UNIT_IN_BRACKETS.findall(part) or _UNIT_LEADING.findall(part)
    return "|".join(_unique(codes))


def managing_unit(docs):
    """(owner, doc_id, disclosure_date) of the most recent document whose owner
    is recorded, or blanks."""
    named = [d for d in docs if (d.get("owner_unit") or "").strip() not in _NOT_A_UNIT]
    if not named:
        return "", "", ""
    d = max(named, key=lambda d: (d["disclosure_date"], d["doc_id"]))
    return d["owner_unit"].strip(), d["doc_id"], d["disclosure_date"]


def fiscal_year(approval):
    """The World Bank fiscal year (1 July to 30 June, named by the year it
    ends) of an approval date, for a record that gives no approvalfy."""
    iso = iso_date(approval, "mdy")
    if not iso:
        return ""
    year, month = int(iso[:4]), int(iso[5:7])
    return str(year + 1 if month >= 7 else year)


def project_row(pid, rec, docs, cohort_set=""):
    """One projects.csv row from the interface record and the project's documents."""
    fetched, rec = rec is not None, rec or {}
    names, codes = practices(rec.get("project_gp_info"))
    owner, doc_id, as_of = managing_unit(docs)
    country = rec.get("countrycode") or []
    return {
        "project_id": pid,
        "cohort_set": cohort_set,
        # true, false (the interface has no record), or blank (not fetched)
        # A record holding nothing but its own id is no record: the
        # interface lists the id of a dropped project and nothing else.
        "in_projects_interface": str(bool(set(rec or {}) - {"id"})).lower()
        if fetched else "",
        "project_name": " ".join((rec.get("project_name") or "").split()),
        "status": rec.get("status") or "",
        "approval_date": iso_date(rec.get("boardapprovaldate"), "mdy"),
        "approval_fy": rec.get("approvalfy") or fiscal_year(rec.get("boardapprovaldate")),
        "closing_date": iso_date(rec.get("closingdate"), "mdy"),
        "region": rec.get("regionname") or "",
        "country_code": "|".join(country) if isinstance(country, list) else country,
        "country_name": rec.get("countryshortname") or "",
        "lending_instrument": rec.get("lendinginstr") or "",
        "commitment_usd_m": rec.get("curr_total_commitment") or "",
        "commitment_v2_usd_m": rec.get("commitment_v2") or "",
        "commitment_v3_usd_m": rec.get("commitment_v3") or "",
        "record_source": rec.get("record_source") or ("v2" if set(rec) - {"id"} else ""),
        "practice": "|".join(names),
        "practice_code": "|".join(codes),
        "sectors": sectors(rec),
        "managing_unit": owner,
        "unit_code": unit_codes(owner),
        "unit_doc_id": doc_id,
        "unit_as_of": as_of,
    }


def build(project_ids, data, sets=None):
    """sets: {project_id: cohort_set} from the cohort file."""
    sets = sets or {}
    docs = {}
    for d in read_csv(where(data, "documents")):
        for p in d["project_ids"].split("|"):
            docs.setdefault(p, []).append(d)
    rows = []
    for pid in sorted(project_ids):
        try:
            with open(where(data, "projects_json", f"{pid}.json"), encoding="utf-8") as fh:
                rec = json.load(fh)
        except FileNotFoundError:
            rec = None
        rows.append(project_row(pid, rec, docs.get(pid, []), sets.get(pid, "")))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=os.path.join(ROOT, "inputs/config/cohort.csv"))
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()

    cohort_rows = read_cohort(args.cohort)
    rows = build([r["project_id"] for r in cohort_rows], args.data,
                 {r["project_id"]: r.get("cohort_set", "") for r in cohort_rows})
    out = where(args.data, "projects")
    write_csv(out, COLS, rows)

    def count(col, value=None):
        return sum(1 for r in rows if (r[col] == value if value else r[col]))
    lines = [f"projects: {len(rows)}",
             f"  in the projects interface: {count('in_projects_interface', 'true')}",
             f"  with a practice:           {count('practice')}",
             f"  with a managing unit:      {count('managing_unit')}",
             f"  with a unit code:          {count('unit_code')}",
             f"  with a closing date:       {count('closing_date')}",
             "", "not in the projects interface:"]
    lines += [f"  {r['project_id']}" for r in rows if r["in_projects_interface"] == "false"]
    lines += ["", "not fetched yet (or the request failed):"]
    lines += [f"  {r['project_id']}" for r in rows if not r["in_projects_interface"]]
    report = where(args.data, "reports", "projects.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:6]))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
