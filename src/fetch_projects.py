#!/usr/bin/env python3
"""Fetch each cohort project's record from the World Bank projects interface.

Reads  inputs/config/cohort.csv  (hand-maintained; included=true rows only)
Writes data/raw/projects/{project_id}.json   the record, the fields in FIELDS
       data/reports/fetch_projects.txt       what was found and what was not
       data/reports/fetch_status/projects.json  which requests succeeded

Only the fields in FIELDS are requested. The interface also publishes the
team's names and e-mail addresses; they are not requested and never stored.
A record that changes between runs keeps its old copy beside the new one
(fetch_procurement.store_keeping_old). src/projects.py builds the table.
"""
import argparse, json, os, sys, time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where, write_fetch_status  # noqa: E402
from fetch import ROOT, get, read_cohort  # noqa: E402
from fetch_procurement import store_keeping_old  # noqa: E402

PROJECTS = "https://search.worldbank.org/api/v2/projects"

# What a project is, where, when, how large, and which Global Practice leads
# it (project_gp_info). No team or contact fields.
FIELDS = ("id", "project_name", "status", "boardapprovaldate", "approvalfy",
          "closingdate", "regionname", "countrycode", "countryshortname",
          "lendinginstr", "curr_total_commitment", "project_gp_info", "sector1",
          "sector2", "sector3")


def fetch_record(project_id):
    """The project's record, or {} when the interface has none."""
    q = urllib.parse.urlencode({"format": "json", "id": project_id,
                                "fl": ",".join(FIELDS)})
    data = get(f"{PROJECTS}?{q}", timeout=60).json()
    rec = (data.get("projects") or {}).get(project_id) or {}
    return {k: rec[k] for k in FIELDS if k in rec}


def fetch(project_ids, data, delay=0.3):
    """Fetch and store each project's record. Returns (found, missing, failed)."""
    found, missing, failed = [], [], []
    for pid in project_ids:
        try:
            rec = fetch_record(pid)
        except Exception as exc:
            failed.append((pid, str(exc)))
            continue
        # An empty record is stored too: "the interface has no such project"
        # is an answer, and projects.py reports it.
        blob = (json.dumps(rec, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode()
        store_keeping_old(where(data, "projects_json", f"{pid}.json"), blob)
        (found if rec else missing).append(pid)
        time.sleep(delay)
    return found, missing, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=os.path.join(ROOT, "inputs/config/cohort.csv"))
    ap.add_argument("--out", default=data_root())
    ap.add_argument("--projects", default="",
                    help="comma-separated project ids: fetch only these")
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    ids = [r["project_id"] for r in read_cohort(args.cohort)]
    if args.projects:
        wanted = {p.strip() for p in args.projects.split(",") if p.strip()}
        ids = [p for p in ids if p in wanted]
    found, missing, failed = fetch(ids, args.out, args.delay)
    write_fetch_status(args.out, "projects", found + missing, [p for p, _ in failed])

    report = where(args.out, "reports", "fetch_projects.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"projects asked for: {len(ids)}\n")
        fh.write(f"records found:      {len(found)}\n")
        fh.write(f"no record:          {len(missing)}\n")
        for p in missing:
            fh.write(f"  {p}\n")
        fh.write(f"request failed:     {len(failed)}\n")
        for p, err in failed:
            fh.write(f"  {p}\t{err}\n")
    print(f"projects: {len(found)} found, {len(missing)} with no record, "
          f"{len(failed)} failed", flush=True)
    # Failures are reported and retried on the next run, never fatal: one
    # project's failed request must not stop the others' stages.
    return 0


if __name__ == "__main__":
    sys.exit(main())
