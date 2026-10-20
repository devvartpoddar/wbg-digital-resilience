"""Where label sets live, and how to read one.

A label set is two or three CSV files, kept by people and only read here:

  <set>.csv           one row per label: <x>_id, <x>_name, definition, active
                      (and any other columns: version, seed_ids, note)
  <set>_queries.csv   search phrases per label: <x>_id, query_id, query, active
  <set>_scale.csv     the grades the classifier asks for: grade, description
  <set>_patterns.csv  word patterns per label (optional): <x>_id, pattern_id,
                      pattern, active. A regular expression, matched without
                      regard to case; write whole words with \b
                      (r"\bunderground\b"), or "ducts?" also matches
                      "conducted"; an acronym that must keep its case goes in
                      (?-i:...), or "UPS" also matches "start-ups". The candidates stage takes every unit a
                      pattern matches, whatever its embedding score.
  <set>_types.csv     the types of a label (optional): <x>_id, type_id,
                      type_name, definition, pattern, active, and two
                      presentation columns a person may add at any time:
                      merge_into (another type_id of the same label: this
                      type's yes answers count as that type's, and nothing
                      is asked again) and display_name (shown instead of
                      type_name, which stays the wording the model was asked
                      with). A unit is of every type whose pattern it matches
                      ("underground", "aerial").
  <set>_groups.csv    how the set's labels are grouped for reading (optional):
                      <x>_id, group_id, group_name, active. A presentation
                      layer only: the labels keep their ids and answers, the
                      views add the group beside them. A label in two active
                      groups is an error; a label in none has no group.

Two folders hold them, with one difference between them:

  inputs/taxonomy/    the pipeline's own sets (the assets). Changed rarely
                      and deliberately: a change invalidates every label a
                      person gave under the old wording (AGENTS.md rule 3).
  analysis/labels/    analysis sets (the measures, and anything tried). Added
                      and revised freely; every run keys its output on the
                      set's exact wording, so trying a new set costs only the
                      phrases it adds and never disturbs another set's results.

A third folder, data/requested_labels/ under the data root, holds the
measures someone asked for from the dashboard (src/measure_requests.py
writes them; nothing else does). It is read after the two above, sits outside
git, and a set graduates from it when a person copies its files into
analysis/labels/ and deletes them here.

A set's name is its file name without .csv. The same name in two folders is
an error, not a precedence rule.
"""
import csv
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABEL_DIRS = (os.path.join(ROOT, "inputs", "taxonomy"), os.path.join(ROOT, "analysis", "labels"))
_SUFFIXES = ("_queries", "_scale", "_patterns", "_types", "_groups")


def requested_dir():
    """data/requested_labels/ under the data root: measures asked for from
    the dashboard (src/measure_requests.py)."""
    from paths import data_root, where
    return where(data_root(), "requested_labels")


def _dirs(dirs):
    """The folders to read: those given, else the two kept by people and,
    when it exists, the requested folder."""
    if dirs:
        return dirs
    req = requested_dir()
    return tuple(LABEL_DIRS) + ((req,) if os.path.isdir(req) else ())


def _is_set(path):
    name = os.path.basename(path)[:-4]
    return not name.endswith(_SUFFIXES)


def label_sets(dirs=None, include_inactive=False):
    """Every label set's name, inputs first, then analysis, then requested,
    each sorted. A set whose labels are all inactive is switched off: it is
    left out (not run, and its rows leave Postgres at the next load, as a
    removed set's do), unless `include_inactive`. Its stored answers stay,
    so switching it back on asks nothing again."""
    dirs = _dirs(dirs)
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
            if include_inactive or any(is_active(r) for r in rows(path)):
                out.append(name)
    return out


def folder(name, dirs=None):
    """The folder holding label set `name`, or a clear error. The folders are
    read at call time, so a test may point LABEL_DIRS elsewhere."""
    dirs = _dirs(dirs)
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


def load_set(name, dirs=None, taxonomy=None):
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


def _optional(name, suffix, dirs, taxonomy):
    d = taxonomy or folder(name, dirs)
    path = os.path.join(d, f"{name}{suffix}.csv")
    if not os.path.exists(path) and suffix == "_types":
        # No list written by a person: the decision model's own proposal,
        # which the types stage writes to data/types/<set>_types.csv, is
        # the list. A file in analysis/labels/ overrides it whenever one
        # is written; nothing waits on a person.
        from paths import data_root, where
        proposed = where(data_root(), "types", f"{name}_types.csv")
        if os.path.exists(proposed):
            return proposed, rows(proposed)
    return path, (rows(path) if os.path.exists(path) else [])


def _id_col(r, path):
    col = next((c for c in r if c.endswith("_id") and c not in ("pattern_id", "type_id")), None)
    if not col:
        raise SystemExit(f"labels: {path} needs the label's *_id column")
    return col


def load_patterns(name, dirs=None, taxonomy=None):
    """Active word patterns: [{label_id, pattern_id, pattern, regex}]. A
    pattern that does not compile is an error, not a silent skip."""
    path, found = _optional(name, "_patterns", dirs, taxonomy)
    out = []
    for r in found:
        if not is_active(r):
            continue
        try:
            rx = re.compile(r["pattern"], re.I)
        except re.error as e:
            raise SystemExit(f"labels: {path}: pattern {r['pattern_id']!r} does not "
                             f"compile: {e}")
        out.append({"label_id": r[_id_col(r, path)].strip(), "pattern_id": r["pattern_id"].strip(),
                    "pattern": r["pattern"], "regex": rx})
    return out


def load_types(name, dirs=None, taxonomy=None):
    """Active types: [{label_id, type_id, type_name, definition, pattern,
    regex}]. A type approved from the decision model's proposals
    (src/kinds.py) has a definition and usually no pattern; its regex is
    then None and the candidates stage does not tag by it."""
    path, found = _optional(name, "_types", dirs, taxonomy)
    out = []
    for r in found:
        if not is_active(r):
            continue
        pattern = (r.get("pattern") or "").strip()
        rx = None
        if pattern:
            try:
                rx = re.compile(pattern, re.I)
            except re.error as e:
                raise SystemExit(f"labels: {path}: type {r['type_id']!r} does not compile: {e}")
        out.append({"label_id": r[_id_col(r, path)].strip(), "type_id": r["type_id"].strip(),
                    "type_name": r.get("type_name", "").strip(),
                    "definition": " ".join((r.get("definition") or "").split()),
                    "pattern": pattern, "regex": rx,
                    "merge_into": (r.get("merge_into") or "").strip(),
                    "display_name": " ".join((r.get("display_name") or "").split())})
    return out


def type_resolution(name, dirs=None, taxonomy=None):
    """How a set's types are shown: ({(label_id, type_id): shown type_id},
    {(label_id, shown type_id): {type_id, type_name, definition,
    merged_from}}). A type with merge_into is shown as the type it names:
    its yes answers count as that type's and no question is asked again. A
    type's display_name, when given, is the name shown; type_name stays the
    wording the model was asked with, so renaming costs nothing either. A
    merge target must be an active type of the same label that is not
    itself merged away."""
    types = load_types(name, dirs, taxonomy)
    by_key = {(t["label_id"], t["type_id"]): t for t in types}
    mapping, shown = {}, {}
    for t in types:
        target = t["merge_into"] or t["type_id"]
        tkey = (t["label_id"], target)
        if tkey not in by_key:
            raise SystemExit(f"labels: {name}_types.csv: {t['type_id']!r} merges into "
                             f"{target!r}, which is not an active type of {t['label_id']!r}")
        if by_key[tkey]["merge_into"]:
            raise SystemExit(f"labels: {name}_types.csv: {t['type_id']!r} merges into "
                             f"{target!r}, which is itself merged into "
                             f"{by_key[tkey]['merge_into']!r}; name the final type")
        mapping[(t["label_id"], t["type_id"])] = target
    for t in types:
        if t["merge_into"]:
            continue
        shown[(t["label_id"], t["type_id"])] = {
            "type_id": t["type_id"], "type_name": t["display_name"] or t["type_name"],
            "definition": t["definition"],
            "merged_from": [s["type_id"] for s in types
                            if s["label_id"] == t["label_id"] and s["merge_into"] == t["type_id"]]}
    return mapping, shown


def load_groups(name, dirs=None, taxonomy=None):
    """The set's reading groups (<set>_groups.csv), active rows only:
    [{label_id, group_id, group_name}]. A label in two groups is an error."""
    path, found = _optional(name, "_groups", dirs, taxonomy)
    out, seen = [], {}
    for r in found:
        if not is_active(r):
            continue
        lid = r[_id_col(r, path)].strip()
        gid = (r.get("group_id") or "").strip()
        if not gid:
            raise SystemExit(f"labels: {path}: {lid!r} has no group_id")
        if lid in seen and seen[lid] != gid:
            raise SystemExit(f"labels: {path}: {lid!r} is in two groups, {seen[lid]!r} and "
                             f"{gid!r}")
        seen[lid] = gid
        out.append({"label_id": lid, "group_id": gid,
                    "group_name": " ".join((r.get("group_name") or gid).split())})
    return out
