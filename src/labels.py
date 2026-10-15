"""Where label sets live, and how to read one.

A label set is two or three CSV files, kept by people and only read here:

  <set>.csv           one row per label: <x>_id, <x>_name, definition, active
                      (and any other columns: version, seed_ids, note)
  <set>_queries.csv   search phrases per label: <x>_id, query_id, query, active
  <set>_scale.csv     the grades the classifier asks for: grade, description

Two folders hold them, with one difference between them:

  inputs/taxonomy/    the pipeline's own sets (the assets). Changed rarely
                      and deliberately: a change invalidates every label a
                      person gave under the old wording (AGENTS.md rule 3).
  analysis/labels/    analysis sets (the measures, and anything tried). Added
                      and revised freely; every run keys its output on the
                      set's exact wording, so trying a new set costs only the
                      phrases it adds and never disturbs another set's results.

A set's name is its file name without .csv. The same name in both folders is
an error, not a precedence rule.
"""
import csv
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABEL_DIRS = (os.path.join(ROOT, "inputs", "taxonomy"), os.path.join(ROOT, "analysis", "labels"))
_SUFFIXES = ("_queries", "_scale")


def _is_set(path):
    name = os.path.basename(path)[:-4]
    return not name.endswith(_SUFFIXES)


def label_sets(dirs=LABEL_DIRS):
    """Every label set's name, inputs first, then analysis, each sorted."""
    out, seen = [], {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.csv"))):
            if not _is_set(path):
                continue
            name = os.path.basename(path)[:-4]
            if name in seen:
                raise SystemExit(f"labels: {name!r} is in both {seen[name]} and {d}; "
                                 f"rename one")
            seen[name] = d
            out.append(name)
    return out


def folder(name, dirs=LABEL_DIRS):
    """The folder holding label set `name`, or a clear error."""
    found = [d for d in dirs if os.path.exists(os.path.join(d, f"{name}.csv"))]
    if len(found) > 1:
        raise SystemExit(f"labels: {name!r} is in more than one folder: {', '.join(found)}")
    if not found:
        raise SystemExit(f"labels: no label set {name!r} in "
                         + " or ".join(os.path.relpath(d, ROOT) for d in dirs))
    return found[0]


def rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def is_active(r):
    return (r.get("active") or "true").strip().lower() == "true"


def load_set(name, dirs=LABEL_DIRS, taxonomy=None):
    """Every label of a set, active or not: [{id, name, definition, active,
    version, folder}], in file order. `taxonomy` names the folder directly
    (tests)."""
    d = taxonomy or folder(name, dirs)
    path = os.path.join(d, f"{name}.csv")
    out = []
    for r in rows(path):
        if not out:
            id_col = next((c for c in r if c.endswith("_id")), None)
            name_col = next((c for c in r if c.endswith("_name")), None)
            if not id_col or not name_col or "definition" not in r:
                raise SystemExit(f"labels: {path} needs an *_id, a *_name and a definition "
                                 f"column")
        out.append({"id": r[id_col].strip(), "name": r[name_col].strip(),
                    "definition": " ".join(r["definition"].split()),
                    "active": str(is_active(r)).lower(), "version": r.get("version", ""),
                    "folder": os.path.relpath(d, ROOT)})
    if not out:
        raise SystemExit(f"labels: {path} has no rows")
    return out
