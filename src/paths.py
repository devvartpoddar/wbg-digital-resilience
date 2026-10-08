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
       text/           the Bank's text renditions of the same documents
       plans/ notices/ awards/   procurement plans and interface responses
       documents.csv   the document list; fetch_log.csv every request made
  data/appraisal/    what the appraisal stages make
       text/           cleaned text, one file per document
       cache/          per-document reads kept to skip unchanged work
       paragraphs.csv sentences.csv rejected.csv components.csv
  data/procurement/  packages, notices, awards: raw and cleaned tables
  data/reports/      every stage's report, and the run notes for the vault
  data/review/       spreadsheets for reading the output by eye

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
    "plans": "raw/plans",
    "notices_json": "raw/notices",
    "awards_json": "raw/awards",
    "documents": "raw/documents.csv",
    "fetch_log": "raw/fetch_log.csv",
    "text": "appraisal/text",
    "cache": "appraisal/cache",
    "appraisal": "appraisal",
    "procurement": "procurement",
    "embeddings": "embeddings",
    "reports": "reports",
    "review": "review",
}


def where(data, name, *parts):
    """The path of a named place in the data folder (see LAYOUT)."""
    return os.path.join(data, LAYOUT[name], *parts)


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
