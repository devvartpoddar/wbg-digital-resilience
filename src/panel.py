"""The control panel: every threshold a person tunes, in one file.

inputs/config/control_panel.csv holds one row per setting (setting, value,
stage, what_it_does, how_to_tune). It is kept by people and only read here
(AGENTS.md rule 3); every stage asks this module for its numbers instead of
holding its own. A setting's value is part of the run key of every stage
that reads it, so a changed number gives a new run, rebuilt from the stored
answers where no question changed.

  panel.get("asset.gate_cosine")      0.2
  panel.floor("cra")                  floor.cra if written, else floor.default
  panel.grades()                      (2, 3): the grades a score adds up

WBG_PANEL points at another file (tests).
"""
import csv, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "inputs", "config", "control_panel.csv")
_cache = {}


def path():
    return os.environ.get("WBG_PANEL") or PATH


def _parse(v):
    v = (v or "").strip()
    if v == "":
        return None
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def settings():
    """{setting: value} as written (numbers and true/false parsed; blank = None)."""
    p = path()
    stamp = os.path.getmtime(p) if os.path.exists(p) else None
    if _cache.get("key") != (p, stamp):
        out = {}
        if stamp is not None:
            with open(p, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    k = (r.get("setting") or "").strip()
                    if k:
                        out[k] = _parse(r.get("value"))
        _cache.update(key=(p, stamp), values=out)
    return _cache["values"]


def get(key, default=None):
    """A setting's value, or default when the panel does not write it."""
    v = settings().get(key)
    return default if v is None else v


def floor(measure):
    """The probability at or above which a sentence counts for the measure:
    floor.<measure> if written, else floor.default (None if neither is)."""
    v = get(f"floor.{measure}")
    return float(v) if v is not None else (None if get("floor.default") is None
                                            else float(get("floor.default")))


def grades():
    """The grades a sentence's score adds up (read.grades, '2|3')."""
    return tuple(int(g) for g in str(get("read.grades", "2|3")).split("|") if g.strip())


def items(prefix):
    """{setting: value} for every setting starting with prefix (for a run key)."""
    return {k: v for k, v in sorted(settings().items()) if k.startswith(prefix)}


def sql_value(key):
    """A setting as a SQL literal, for {{panel:key}} in sql/views."""
    v = get(key)
    if v is None:
        raise SystemExit(f"panel: {key} is not set in {path()}")
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if "|" in str(v):
        return "ARRAY[" + ", ".join("'" + s.replace("'", "''") + "'" for s in str(v).split("|")) + "]::text[]"
    return "'" + str(v).replace("'", "''") + "'"
