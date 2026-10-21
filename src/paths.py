"""Where the data lives.

One rule: data goes in the MAIN checkout's data/ folder, never in a worktree's.

On the box the main checkout is /ygg/projects/wbg-digital-resilience, and every
board card works in /ygg/projects/wbg-digital-resilience/.worktrees/<task-id>.
That worktree is deleted when the card completes. A script that wrote to
"data/ next to the code" would write into the worktree and the data would go
with it. So the data root is found through git: the shared .git directory sits
in the main checkout, whichever worktree the code is run from.

Every file has one home inside data/, named in LAYOUT below; scripts ask
`where(data, name)` rather than building paths themselves, so the layout is
written down once:

  data/raw/          what was fetched, exactly as published
       pdf/            appraisal documents (PDF)
       pdf_text/       each PDF's text exactly as read, before cleaning
       text/           the Bank's text renditions (no longer fetched; kept)
       plans/ notices/ awards/   procurement plans and interface responses
       projects/       each project's record from the projects interface
       documents.csv   the document list; fetch_log.csv the procurement
                       listings, notices and awards requests
  data/appraisal/    what the appraisal stages make
       text/           cleaned text, one file per document
       cache/          per-document reads kept to skip unchanged work
       paragraphs.csv sentences.csv rejected.csv components.csv
       document_parts.csv
  data/procurement/  packages, notices, awards: raw and cleaned tables
  data/projects/     projects.csv: one row per project (src/projects.py)
  data/embeddings/   the vector cache, sentence_emb.npy, sentence_index.csv
  data/classify/     every decision-model answer (answers/) and one folder per
                     label set and version of the asset map (src/classify.py)
  data/labels/       grades a person gave (src/check_units.py) and
                     the hand-labelled activities workbook (src/evaluate_activities.py)
  data/releases/     one folder per data release, and index.csv (src/release.py)
  data/reports/      every stage's report, the run notes for the vault, and
                     fetch_status/ (what each fetcher fully read)
  data/review/       spreadsheets for reading the output by eye
  data/runs/         a selection's exported results, when asked for (src/wbg.py)
  data/archive/      tables an earlier version wrote and nothing reads (kept, never deleted)
  data/search/       a label set's scores over the sentences, one folder per
                     label set and version (src/search.py)
  data/measures/     a measure's pool graded by the decision model, with
                     the asset map's columns, one folder per label set and
                     version (src/measure.py)
  data/types/        a measure's types: stored generative-model proposals and
                     the decision model's assignments, one folder per label
                     set and version (src/kinds.py)
  data/links/        which asset each package buys and which commitments it
                     would carry out, one folder per version
                     (src/link_packages.py)

Order of precedence for the data root:
  1. $WBG_DATA, if set
  2. <main checkout>/data, found with `git rev-parse --git-common-dir`
  3. <this checkout>/data, when git is unavailable
"""
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main_checkout(repo=REPO):
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return repo
    if not out or os.path.basename(out) != ".git":
        return repo
    return os.path.dirname(out)


def data_root():
    env = os.environ.get("WBG_DATA", "").strip()
    if env:
        return os.path.abspath(env)
    return os.path.join(main_checkout(), "data")


LAYOUT = {
    "pdf": "raw/pdf",
    "rendition": "raw/text",
    "pdf_text": "raw/pdf_text",
    "plans": "raw/plans",
    "notices_json": "raw/notices",
    "awards_json": "raw/awards",
    "projects_json": "raw/projects",
    "projects": "projects/projects.csv",
    "documents": "raw/documents.csv",
    "fetch_log": "raw/fetch_log.csv",
    "text": "appraisal/text",
    "cache": "appraisal/cache",
    "appraisal": "appraisal",
    "procurement": "procurement",
    "embeddings": "embeddings",
    "reports": "reports",
    "review": "review",
    "runs": "runs",
    "search": "search",
    "measures": "measures",
    "types": "types",
    "links": "links",
    "checks": "checks",
    "classify": "classify",
    "labels": "labels",
    "requested_labels": "requested_labels",
    "releases": "releases",
    "fetch_status": "reports/fetch_status",
    "archive": "archive",
}


REMEMBER_MIN_BYTES = 1 << 20


def file_sha256(path, remember=True):
    """The SHA-256 of a file, remembered beside it: <path>.sha256 holds the
    size, the modification time and the digest, and is trusted while both
    match. Every stage keys its runs on the big files (the 1.8 GB sentence
    matrix, the paragraphs table), and hashing them again on every call was
    minutes of each run on this hardware; a stale sidecar can only name a
    digest for bytes that have since changed, and the size and mtime check
    rules that out. A file under REMEMBER_MIN_BYTES is hashed afresh and
    gets no sidecar: it costs microseconds, and the hand-kept inputs
    (inputs/, analysis/labels/) are small and must not gain files beside
    them on every run."""
    import hashlib
    st = os.stat(path)
    side = path + ".sha256"
    remember = remember and st.st_size >= REMEMBER_MIN_BYTES
    if remember:
        try:
            with open(side, encoding="utf-8") as fh:
                size, mtime, sha = fh.read().split()
            if int(size) == st.st_size and int(mtime) == st.st_mtime_ns and len(sha) == 64:
                return sha
        except (OSError, ValueError):
            pass
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    sha = h.hexdigest()
    if not remember:
        return sha
    try:
        with open(side + ".part", "w", encoding="utf-8") as fh:
            fh.write(f"{st.st_size} {st.st_mtime_ns} {sha}\n")
        os.replace(side + ".part", side)
    except OSError:
        pass          # a read-only copy still hashes, it just does not remember
    return sha


def where(data, name, *parts):
    """The path of a named place in the data folder (see LAYOUT)."""
    return os.path.join(data, LAYOUT[name], *parts)


def write_fetch_status(data, kind, ok, failed):
    """Record which projects a fetch fully read (ok) and which it did not, in
    reports/fetch_status/<kind>.json. src/wbg.py counts a project as fetched
    only when it is in ok, so a failed one is tried again on the next run."""
    import json
    path = where(data, "fetch_status", f"{kind}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump({"ok": sorted(ok), "failed": sorted(failed)}, fh, indent=1)
    os.replace(path + ".part", path)


# The layout before everything had a home: (old, new), relative to data/. A
# directory entry moves its files one by one; a glob takes what it matches.
_MOVES = [
    ("raw/*.txt", "raw/text/"),
    ("api_fetch_log.csv", "raw/fetch_log.csv"),
    ("documents.csv", "raw/documents.csv"),
    ("clean/*.txt", "appraisal/text/"),
    ("clean/*.json", "appraisal/cache/"),
    ("paragraphs.csv", "appraisal/paragraphs.csv"),
    ("sentences.csv", "appraisal/sentences.csv"),
    ("rejected.csv", "appraisal/rejected.csv"),
    ("components.csv", "appraisal/components.csv"),
    ("intermediate/procurement/*.csv", "procurement/"),
    ("intermediate/procurement/fetch_procurement_report.txt", "reports/fetch_procurement.txt"),
    ("intermediate/procurement/clean_procurement_report.txt", "reports/clean_procurement.txt"),
    ("intermediate/procurement/audit_procurement_report.txt", "reports/audit_procurement.txt"),
    ("intermediate/procurement/v6_link_test.txt", "reports/v6_link_test.txt"),
    ("fetch_report.txt", "reports/fetch.txt"),
    ("clean_report.txt", "reports/clean.txt"),
    ("audit_report.txt", "reports/audit.txt"),
    ("components_report.txt", "reports/components.txt"),
    ("review_sample.txt", "review/review_sample.txt"),
    # Tables an earlier version wrote and nothing reads any more. Never
    # deleted (AGENTS.md rule 1): moved aside, out of every stage's way.
    ("appraisal/document_projects.csv", "archive/"),
    ("procurement/package_changes.csv", "archive/"),
    ("review/review_sample.txt", "archive/"),
]


def migrate(data, dry_run=False):
    """Move files from the old layout into LAYOUT. Move only: nothing is
    deleted or overwritten, a file whose new home is taken stays where it is
    and is reported, and running it twice does nothing the second time.
    Directories the moves leave empty are removed."""
    import glob
    moved, clashes = [], []
    for old, new in _MOVES:
        for src in sorted(glob.glob(os.path.join(data, old))):
            if not os.path.isfile(src):
                continue
            dst = os.path.join(data, new)
            if new.endswith("/"):
                dst = os.path.join(dst, os.path.basename(src))
            if os.path.exists(dst):
                clashes.append((src, dst))
                continue
            moved.append((src, dst))
            if not dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
    if not dry_run:
        for d in ("clean", "intermediate/procurement", "intermediate"):
            path = os.path.join(data, d)
            if os.path.isdir(path) and not os.listdir(path):
                os.rmdir(path)
    return moved, clashes


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Move data/ into its current layout.")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    moved, clashes = migrate(a.data, a.dry_run)
    print(f"{'would move' if a.dry_run else 'moved'} {len(moved)} files")
    for src, dst in clashes:
        print(f"left in place (new home taken): {src} -> {dst}")
    loose = [f for f in sorted(os.listdir(a.data))
             if f not in {p.split("/")[0] for p in LAYOUT.values()}]
    if loose:
        print("not in the layout: " + ", ".join(loose))
