"""Where the data lives: the MAIN checkout's data/ folder, never a worktree's
(a board card's worktree is deleted when the card completes).

The data root is $WBG_DATA if set, else <main checkout>/data (found with
`git rev-parse --git-common-dir`), else <this checkout>/data. Every file has one
home, named in LAYOUT; scripts ask where(data, name).
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
    "prior": "prior",
    "classify": "classify",
    "labels": "labels",
    "requested_labels": "requested_labels",
    "scratch": "scratch",
    "releases": "releases",
    "fetch_status": "reports/fetch_status",
    "archive": "archive",
}


REMEMBER_MIN_BYTES = 1 << 20


def file_sha256(path, remember=True):
    """The SHA-256 of a file, streamed. With remember, a file of at least
    REMEMBER_MIN_BYTES keeps <path>.sha256 (size, mtime, digest) beside it, trusted
    while size and mtime match, so big files are not hashed again on every call;
    small files (the hand-kept inputs among them) never gain a sidecar."""
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
