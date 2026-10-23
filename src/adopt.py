"""A run folder written under another key for the same work.

A stage names its folder by a hash of what it asks. When the way that hash
is made changes (2026-10-23: the decision floors left the keys), a folder
already holding exactly the work the stage would do is reused, not rebuilt:
the stage checks it against what it would ask now (same questions, same
units, same settings; for the pair checks, the stored answers too) and, if
it matches, records it in data/<stage>/adopted.json under the new name.
Every reader resolves the new name through that file. Nothing is moved or
deleted, and a folder that does not match is never adopted.
"""
import json
import os

from paths import where


def _file(data, stage):
    return where(data, stage, "adopted.json")


def _load(data, stage):
    try:
        with open(_file(data, stage), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def resolve(data, stage, out):
    """out when it is written; else the folder adopted for its name, if
    that is written; else out."""
    if os.path.exists(os.path.join(out, "manifest.json")):
        return out
    old = _load(data, stage).get(os.path.basename(out), {}).get("folder")
    if old and os.path.exists(where(data, stage, old, "manifest.json")):
        return where(data, stage, old)
    return out


def record(data, stage, out, folder, why):
    """Adopt folder (a written run) for the name of out."""
    got = _load(data, stage)
    got[os.path.basename(out)] = {"folder": os.path.basename(folder), "why": why}
    path = _file(data, stage)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump(got, fh, indent=1, sort_keys=True)
    os.replace(path + ".part", path)
    return folder


def manifests(data, stage, prefix):
    """[(folder, manifest)] of the written runs of a stage whose names start
    with prefix, newest first."""
    import glob
    out = []
    for m in glob.glob(where(data, stage, f"{glob.escape(prefix)}*", "manifest.json")):
        try:
            with open(m, encoding="utf-8") as fh:
                out.append((os.path.dirname(m), json.load(fh)))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda x: os.path.getmtime(os.path.join(x[0], "manifest.json")),
                  reverse=True)
