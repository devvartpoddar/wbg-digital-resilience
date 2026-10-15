#!/usr/bin/env python3
"""Data releases: which data, made by which code, from which inputs.

A release is a snapshot of the store's outputs with everything needed to say
where they came from:

  code      the git commit that made them, and whether the checkout had
            uncommitted changes to the code or the hand-kept inputs
  inputs    SHA-256 of the cohort and of every taxonomy file; the PDF
            reader's version; when each project was last fetched
  tables    every output table: rows and SHA-256
  runs      the search and classification runs present, by version

Its id is a hash of the tables and the inputs, so the same data is the same
release however often it is cut; the commit that first made it is recorded. A new
release keeps a compressed copy of the cleaned tables (about 10 MB), so any
two releases can be compared row by row later; the raw files are never
overwritten anyway, and the embedding and model caches are content-addressed.

Reads  data/ (the outputs below), inputs/, data/reports/stages.json
Writes data/releases/<id>/manifest.json, data/releases/<id>/tables.tar.gz,
       data/releases/index.csv            one row per release, oldest first

  python3 src/release.py            cut a release if the data changed; print it
  python3 src/release.py --status   the current release, the code it was made
                                    with, and whether that code is current
"""
import argparse, csv, glob, hashlib, json, os, subprocess, sys, tarfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import labels  # noqa: E402

# The tables a release covers: everything a reader or Postgres sees.
TABLES = [
    ("projects", ("projects",)),
    ("documents", ("documents",)),
    ("paragraphs", ("appraisal", "paragraphs.csv")),
    ("sentences", ("appraisal", "sentences.csv")),
    ("rejected", ("appraisal", "rejected.csv")),
    ("document_parts", ("appraisal", "document_parts.csv")),
    ("components", ("appraisal", "components.csv")),
    ("packages", ("procurement", "packages.csv")),
    ("superseded_packages", ("procurement", "superseded_packages.csv")),
    ("notices", ("procurement", "notices.csv")),
    ("awards", ("procurement", "awards.csv")),
    ("sentence_index", ("embeddings", "sentence_index.csv")),
]
INDEX_COLS = ["release_id", "created_at", "commit", "code_dirty", "paragraphs", "packages",
              "notices", "awards", "sentences_embedded", "search_runs", "classify_runs"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rows(path):
    with open(path, "rb") as fh:
        return max(sum(1 for _ in fh) - 1, 0)


def _git(*args):
    try:
        out = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True,
                             timeout=20)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _is_ancestor(commit, of):
    try:
        return subprocess.run(["git", "-C", ROOT, "merge-base", "--is-ancestor", commit, of],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def code_state():
    """The commit the code is at, and whether the code or the hand-kept
    inputs differ from it."""
    return {"commit": _git("rev-parse", "HEAD"),
            "dirty": bool(_git("status", "--porcelain", "--", "src", "inputs", "sql",
                               "run.sh", "wbg"))}


def _library():
    try:
        from importlib.metadata import version
        return {"pdfplumber": version("pdfplumber")}
    except Exception:
        return {"pdfplumber": "unknown"}


def _fetched(data):
    """For each fetch kind, how many projects and the latest fetch date."""
    try:
        with open(where(data, "reports", "stages.json"), encoding="utf-8") as fh:
            fetched = json.load(fh).get("fetched", {})
    except (OSError, ValueError):
        return {}
    return {k: {"projects": len(v), "latest": max(v.values()) if v else ""}
            for k, v in sorted(fetched.items())}


def _runs(data, kind):
    return sorted(os.path.basename(d) for d in glob.glob(where(data, kind, "*-*"))
                  if os.path.exists(os.path.join(d, "manifest.json"))
                  and not os.path.basename(d).startswith("smoke_"))


def manifest(data):
    tables = {}
    for name, spec in TABLES:
        path = where(data, *spec)
        if os.path.exists(path):
            tables[name] = {"rows": _rows(path), "sha256": sha256_file(path)}
    inputs = {"cohort_sha256": sha256_file(os.path.join(ROOT, "inputs", "config",
                                                        "cohort.csv")),
              "taxonomy": {os.path.relpath(p, ROOT): sha256_file(p) for d in labels.LABEL_DIRS
                           for p in sorted(glob.glob(os.path.join(d, "*.csv")))},
              "libraries": _library()}
    code = code_state()
    runs = {"search": _runs(data, "search"), "classify": _runs(data, "classify")}
    # The data's identity: the same tables from the same inputs are the same
    # release whatever commit is checked out now, and whatever library
    # version is installed now (that is recorded, not identifying); the commit
    # that first made it is recorded when it is cut.
    identity = {k: v for k, v in inputs.items() if k != "libraries"}
    ident = hashlib.sha256(json.dumps([tables, identity, runs],
                                      sort_keys=True).encode()).hexdigest()[:10]
    return {"release_id": f"r-{ident}", "code": code, "inputs": inputs,
            "fetched": _fetched(data), "tables": tables, "runs": runs}


def cut(data, log=print):
    """The current release id, writing the release if it is new."""
    m = manifest(data)
    folder = where(data, "releases", m["release_id"])
    # Known only when both its manifest and its index row exist: an
    # interrupted cut is finished on the next one, not taken as done.
    if os.path.exists(os.path.join(folder, "manifest.json")) and \
            any(r["release_id"] == m["release_id"] for r in releases(data)):
        log(f"release {m['release_id']}: unchanged")
        return m["release_id"]
    m["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    os.makedirs(folder, exist_ok=True)
    with tarfile.open(os.path.join(folder, "tables.tar.gz.part"), "w:gz") as tar:
        for name, spec in TABLES:
            path = where(data, *spec)
            if os.path.exists(path):
                tar.add(path, arcname=f"{name}.csv")
    os.replace(os.path.join(folder, "tables.tar.gz.part"), os.path.join(folder, "tables.tar.gz"))
    with open(os.path.join(folder, "manifest.json.part"), "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1, sort_keys=True)
    os.replace(os.path.join(folder, "manifest.json.part"), os.path.join(folder, "manifest.json"))
    index = where(data, "releases", "index.csv")
    new = not os.path.exists(index)
    t = m["tables"]
    with open(index, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX_COLS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow({"release_id": m["release_id"], "created_at": m["created_at"],
                    "commit": m["code"]["commit"][:12],
                    "code_dirty": str(m["code"]["dirty"]).lower(),
                    **{k: t.get(k, {}).get("rows", "") for k in
                       ("paragraphs", "packages", "notices", "awards")},
                    "sentences_embedded": t.get("sentence_index", {}).get("rows", ""),
                    "search_runs": "|".join(m["runs"]["search"]),
                    "classify_runs": "|".join(m["runs"]["classify"])})
    log(f"release {m['release_id']}: new ({m['created_at']}, commit "
        f"{m['code']['commit'][:12]}{', with uncommitted changes' if m['code']['dirty'] else ''})")
    return m["release_id"]


def releases(data):
    path = where(data, "releases", "index.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def status(data):
    """Lines saying what data is current, from which code, and whether the
    code or the data is behind."""
    m = manifest(data)
    known = {r["release_id"]: r for r in releases(data)}
    head = m["code"]["commit"]
    remote = _git("rev-parse", "origin/main")
    # origin/main as last fetched: this does not fetch, so the line is as
    # fresh as the last git fetch or pull.
    if not remote or remote == head:
        against = "" if not remote else " = origin/main (as last fetched)"
    elif _is_ancestor(head, remote):
        against = f" - behind origin/main {remote[:12]} (as last fetched): run git pull"
    else:
        against = f" - not on origin/main {remote[:12]} (as last fetched)"
    lines = [f"code:     {head[:12]}" + (" (uncommitted changes)" if m["code"]["dirty"] else "")
             + against]
    if m["release_id"] in known:
        r = known[m["release_id"]]
        lines.append(f"data:     release {r['release_id']} cut {r['created_at']} "
                     f"with commit {r['commit']}")
    else:
        lines.append(f"data:     the store differs from every release ({m['release_id']} "
                     f"would be new): run ./wbg release")
    try:
        import wbg
        stale = wbg.stale_stages(data)
    except Exception as exc:                          # noqa: BLE001
        stale = [f"(could not check: {exc})"]
    lines.append("stages:   " + ("all up to date with this code" if not stale else
                                 f"out of date: {', '.join(stale)} - run ./wbg prepare"))
    for kind, f in m["fetched"].items():
        lines.append(f"fetched:  {kind:<12} {f['projects']} projects, latest {f['latest']}")
    lines.append(f"runs:     search {', '.join(m['runs']['search']) or 'none'}; "
                 f"classify {', '.join(m['runs']['classify']) or 'none'}")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        print("\n".join(status(args.data)))
    else:
        cut(args.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
