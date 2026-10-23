#!/usr/bin/env python3
"""The measure stage of ./wbg run in one process: every measure through the
funnel together (src/measure.py, measure_many: the corpus read once, one ask
and one ranking pass a round for all of them), then each measure's types
proposed and assigned (src/kinds.py), then the pair checks across the
measures (src/commit_checks.py). One process instead of three a measure, so
the sentence index, the paragraphs, the texts and the answer store are read
once. Writes no Postgres rows: ./wbg run publishes once at the end.

  python src/measure_stage.py cra ews ... [--workers N] [--projects P1,P2] [--dry-run]

Exits 1 when a measure failed (named in the output); the stage resumes on
the next run from the answers stored.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root  # noqa: E402
import classify  # noqa: E402
import measure  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_sets", nargs="+")
    ap.add_argument("--workers", type=int, default=classify.WORKERS,
                    help="requests at once, over every measure")
    ap.add_argument("--projects", default="", help="comma-separated project ids: the run's scope")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    import embed
    import kinds
    key = "" if args.dry_run else embed.read_key("")
    projects = classify.scope(args.projects)
    outs = measure.measure_many(args.label_sets, args.data, api_key=key, dry_run=args.dry_run,
                                workers=args.workers, projects=projects)
    failed = [] if args.dry_run else [n for n in args.label_sets if not outs.get(n)]
    for name in args.label_sets:
        if name in failed:
            continue
        print(f"== types {name}", flush=True)
        try:
            # a dry run's steps return nothing: each gives its estimate
            ok = kinds.propose(name, args.data, api_key=key, dry_run=args.dry_run,
                               projects=projects)
            if ok or args.dry_run:
                ok = kinds.assign(name, args.data, api_key=key, dry_run=args.dry_run,
                                  workers=args.workers, projects=projects)
        except SystemExit as exc:
            print(f"  {name}: {exc}", flush=True)
            ok = False
        if not ok and not args.dry_run:
            failed.append(name)
    # The checks span every measure: a unit two measures commit.
    if not failed:
        import commit_checks
        print("== checks", flush=True)
        if not (commit_checks.run(args.data, api_key=key, projects=projects,
                                  dry_run=args.dry_run, workers=args.workers) or args.dry_run):
            failed.append("checks")
    if failed:
        print(f"measure stage: failed for {', '.join(failed)}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
