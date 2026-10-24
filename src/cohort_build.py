#!/usr/bin/env python3
"""List the World Bank operations a cohort could hold, from the projects
interface, by approval fiscal year and lending instrument, so a person can
decide the cohort (inputs/config/cohort.csv is hand-kept and never written
here: this writes a candidate list next to the reports).

  python3 src/cohort_build.py --from-fy 2020                       # IPF, FY2020 to now
  python3 src/cohort_build.py --from-fy 2020 --to-fy 2026 --instrument all
  python3 src/cohort_build.py --from-fy 2020 --count                # totals only, no file

Writes data/reports/cohort_candidates_fy<from>-<to>.csv in cohort.csv's
columns (project_id, country_code, region, approval_fy, practice, included,
exclusion_reason, cohort_version) plus project_name, lending_instrument,
status and commitment_usd_millions for choosing. Every row is included=true;
a person edits and copies the rows they want into inputs/config/cohort.csv.
Only board-approved operations are listed (a pipeline project has no
appraisal document), and the interface's own counts are reported so a page
that failed is visible.
"""
import argparse, datetime, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_procurement import fetch_pages  # noqa: E402
from fetch_projects import PROJECTS  # noqa: E402
from paths import data_root, where  # noqa: E402
from common import write_csv  # noqa: E402

ROWS = 200
FIELDS = ("id", "project_name", "approvalfy", "boardapprovaldate", "status", "regionname",
          "countrycode", "countryshortname", "lendinginstr", "totalcommamt",
          "project_gp_info")
INSTRUMENTS = {"ipf": ("Investment Project Financing",),
               "ipf_pforr": ("Investment Project Financing", "Program-for-Results Financing"),
               "all": ()}


def listing(from_fy, to_fy, instruments, delay=0.3, log=print):
    """([record], {(fy, instrument): (total, listed)}) for every operation
    approved in the years, by instrument. A listing that comes back shorter
    than the interface's own total stops the run (fetch_pages)."""
    out, totals = {}, {}
    for fy in range(from_fy, to_fy + 1):
        for inst in (instruments or (None,)):
            params = {"approvalfy": str(fy), "fl": ",".join(FIELDS)}
            if inst:
                params["lendinginstr"] = inst
            what = f"FY{fy} {inst or 'all instruments'}"
            try:
                recs, total = fetch_pages(PROJECTS, params,
                                          lambda d: list((d.get("projects") or {}).values()),
                                          what=what, expected=("id",), page=ROWS, delay=delay)
            except RuntimeError as exc:
                raise SystemExit(f"cohort_build: {exc}") from None
            for r in recs:
                if r.get("boardapprovaldate"):
                    out.setdefault(r["id"], r)
            totals[(fy, inst or "all")] = (total or 0, len(recs))
            log(f"  {what}: {len(recs)} of {total or 0} listed")
    return list(out.values()), totals


def rows_for(recs, version):
    def practice(r):
        info = r.get("project_gp_info") or ""
        return info if isinstance(info, str) else ""
    out = []
    for r in sorted(recs, key=lambda r: (r.get("approvalfy", ""), r["id"])):
        amt = (r.get("totalcommamt") or "").replace(",", "")
        out.append({"project_id": r["id"], "country_code": r.get("countrycode", ""),
                    "region": r.get("regionname", ""), "approval_fy": r.get("approvalfy", ""),
                    "practice": practice(r), "included": "true", "exclusion_reason": "",
                    "cohort_version": version, "project_name": r.get("project_name", ""),
                    "lending_instrument": r.get("lendinginstr", ""),
                    "status": r.get("status", ""),
                    "commitment_usd_millions": f"{float(amt) / 1e6:.1f}" if amt else ""})
    return out


COLS = ["project_id", "country_code", "region", "approval_fy", "practice", "included",
        "exclusion_reason", "cohort_version", "project_name", "lending_instrument", "status",
        "commitment_usd_millions"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-fy", type=int, required=True)
    ap.add_argument("--to-fy", type=int, default=int(datetime.date.today().year) + 1)
    ap.add_argument("--instrument", choices=sorted(INSTRUMENTS), default="ipf")
    ap.add_argument("--count", action="store_true", help="totals only; write nothing")
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    recs, totals = listing(args.from_fy, args.to_fy, INSTRUMENTS[args.instrument])
    print(f"{len(recs)} board-approved operations, FY{args.from_fy} to FY{args.to_fy}, "
          f"{args.instrument}")
    if args.count:
        return 0
    rows = rows_for(recs, datetime.date.today().isoformat())
    out = where(args.data, "reports", f"cohort_candidates_fy{args.from_fy}-{args.to_fy}.csv")
    write_csv(out, COLS, rows)
    print(f"wrote {out}; copy the rows you want into inputs/config/cohort.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
