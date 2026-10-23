#!/usr/bin/env python3
"""Move the run folders no stage would name now into one dated archive folder.

A run folder is `data/<stage>/<name>-<12 hex>` with a `manifest.json`: the
folder a stage wrote for a label set and version. Its name is a SHA-256 of
the stage's inputs, so a folder is **superseded** when those inputs have
moved on: the stage names a different folder now, nothing read this one
again, and it is out of view without being deleted (AGENTS.md rule 1).
`data/archive/` is where the pipeline keeps what an earlier version wrote
and nothing reads (`src/paths.py`).

Stays where it is:

- `data/classify/answers/` — the answer store, what costs money;
- `data/classify/current/` — the stages' markers, and `data/types/cache/`
  with `data/types/*.csv` — the generative model's stored proposals and a
  person's approved types;
- `data/embeddings/` — the vector cache; the PDFs and text under
  `data/raw/`; `data/labels/`; `data/releases/`; `data/reports/`,
  `data/review/`, `data/prior/`;
- every run folder a stage would name now, over the whole cohort and over
  each scope given with `--project` (a project smoke run's folders are
  current for that scope, so they are kept).

    python3 src/archive_runs.py --dry-run
    python3 src/archive_runs.py --project P180000

Nothing here is a model call, and nothing under `data/` is deleted: the
dry run prints every line first.
"""
import argparse
import datetime
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where                     # noqa: E402
import labels                                          # noqa: E402
import search                                          # noqa: E402
import classify                                        # noqa: E402
import measure                                         # noqa: E402
import kinds                                           # noqa: E402
import commit_checks                                   # noqa: E402
import link_packages                                   # noqa: E402

# The stages that write run folders, and the older ones whose folders
# nothing in the layout reads any more (src/paths.py reports them as "not
# in the layout"): for those, no folder is current.
STAGES = ("search", "classify", "measures", "types", "checks", "links")
EXTRA = ("candidates", "commitment")
# Entries inside those folders that are not run folders, kept where they are.
KEEP_ENTRIES = {("classify", "answers"), ("classify", "current"), ("types", "cache")}


def current_names(data, scopes):
    """{stage: {folder name}} of the run folders the stages would name now,
    over every scope in scopes (None is the whole cohort)."""
    keep = {s: set() for s in STAGES}

    def add(stage, folder):
        if folder:
            keep[stage].add(os.path.basename(folder))

    for projects in scopes:
        for name in labels.label_sets():
            try:
                add("search", search.run_folder(name, data)[0])
            except Exception as exc:                   # noqa: BLE001
                print(f"  search {name}: {exc}")
            for fn, stage in ((lambda: classify.current_run(name, data, projects=projects),
                               "classify"),
                              (lambda: measure.current_run(name, data, projects=projects),
                               "measures"),
                              (lambda: kinds.current_assign(name, data, projects=projects),
                               "types")):
                try:
                    add(stage, fn())
                except Exception as exc:               # noqa: BLE001
                    print(f"  {stage} {name} scope={projects}: {exc}")
        try:
            add("checks", commit_checks.current_run(data, projects=projects))
        except Exception as exc:                       # noqa: BLE001
            print(f"  checks scope={projects}: {exc}")
        try:
            add("links", link_packages.current_run(data, projects=projects))
        except Exception as exc:                       # noqa: BLE001
            print(f"  links scope={projects}: {exc}")
    return keep


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="the archive folder's name: data/archive/runs-<date>")
    ap.add_argument("--project", action="append", default=[],
                    help="a scope whose current run folders are kept as well "
                         "(the scope a smoke run was made over)")
    ap.add_argument("--keep-after", type=float, default=0.0,
                    help="keep every run folder changed at or after this epoch")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    data = args.data
    scopes = [None] + [sorted(classify.scope(p) or []) for p in args.project]
    keep = current_names(data, scopes)
    dest = os.path.join(data, "archive", f"runs-{args.date}")
    log, moved = [], []
    for stage in STAGES + EXTRA:
        # The older stages are not in the layout (src/paths.py holds the
        # places that are): their folders sit directly under data/.
        root = where(data, stage) if stage in STAGES else os.path.join(data, stage)
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            path = os.path.join(root, entry)
            if (stage, entry) in KEEP_ENTRIES or not os.path.isdir(path):
                continue
            if not os.path.exists(os.path.join(path, "manifest.json")):
                log.append(f"  kept (not a run folder): {stage}/{entry}")
                continue
            if entry in keep.get(stage, ()):
                log.append(f"  kept (current): {stage}/{entry}")
                continue
            if args.keep_after and os.path.getmtime(path) >= args.keep_after:
                log.append(f"  kept (written by the run being kept): {stage}/{entry}")
                continue
            target = os.path.join(dest, stage, entry)
            log.append(f"  move {stage}/{entry} -> {os.path.relpath(target, data)}")
            moved.append(f"{stage}/{entry}")
            if not args.dry_run:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    raise SystemExit(f"archive already holds {target}")
                shutil.move(path, target)
    print("\n".join(log))
    print(f"{'would move' if args.dry_run else 'moved'} {len(moved)} run folder(s) "
          f"into {os.path.relpath(dest, data)}")
    if moved and not args.dry_run:
        with open(os.path.join(dest, "WHAT-IS-HERE.md"), "w", encoding="utf-8") as fh:
            fh.write(
                f"# Superseded run folders, archived {args.date}\n\n"
                "Run folders under `data/<stage>/` whose inputs have moved on: a stage would\n"
                "not name them again, so nothing reads them. They are kept, never deleted\n"
                "(AGENTS.md rule 1); the answer store, the record tables in Postgres and\n"
                "`data/labels/` were not touched.\n\n"
                "What stayed where it was: `data/classify/answers/` (the answer store),\n"
                "`data/classify/current/` (the stages' markers), `data/types/cache/` and\n"
                "`data/types/*.csv`, `data/embeddings/`, the PDFs and text under `data/raw/`,\n"
                "`data/labels/`, `data/releases/` and the run folder each stage names now.\n\n"
                "Moved here:\n\n" + "\n".join(f"- `{m}`" for m in moved) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
