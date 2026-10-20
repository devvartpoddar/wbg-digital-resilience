"""The few helpers every stage needs, defined once: reading and writing a
CSV table, the checksum of a file, and a small content-addressed JSON
store (one file per SHA-256, written aside and renamed, so a reader never
sees a torn write)."""
import csv
import json
import os

from paths import file_sha256  # noqa: F401  (re-exported: one checksum for every stage)


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
