"""Helpers every stage needs: CSV tables, checksums of text and files, and a small
content-addressed JSON store written aside and renamed.
"""
import csv
import hashlib
import json
import os

from paths import file_sha256  # noqa: F401  (re-exported: one checksum for every stage)


def sha_text(s):
    """The SHA-256 hex digest of a string (as UTF-8) or of bytes; None is ''."""
    if not isinstance(s, (bytes, bytearray)):
        s = (s or "").encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def read_csv(path):
    """The rows of a CSV table as dicts; an absent file is an empty table."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, cols, rows):
    """Write rows under cols, atomically; columns a row lacks are blank,
    columns it has beyond cols are left out."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def json_path(root, sha):
    return os.path.join(root, sha[:2], sha + ".json")


def read_json(root, sha):
    """The stored object under this key, or None."""
    try:
        with open(json_path(root, sha), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_json(root, sha, obj):
    path = json_path(root, sha)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, sort_keys=True)
    os.replace(path + ".part", path)
