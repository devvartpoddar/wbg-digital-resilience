"""$WBG_STORE: a run can write its scores into one schema and read its stored
answers from another (src/wbg_portfolio/db.py)."""
import os
import subprocess
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _names(**env):
    base = {k: v for k, v in os.environ.items() if k not in ("WBG_SCHEMA", "WBG_STORE")}
    out = subprocess.run([sys.executable, "-c", "from wbg_portfolio import db; print(db.SCHEMA, db.STORE)"],
                         env={**base, "PYTHONPATH": SRC, **env}, capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_the_store_is_derived_from_the_schema_unless_named():
    assert _names() == ["wbg", "wbg_store"]
    assert _names(WBG_SCHEMA="wbg_v2") == ["wbg_v2", "wbg_v2_store"]
    assert _names(WBG_SCHEMA="wbg_v2", WBG_STORE="wbg_store") == ["wbg_v2", "wbg_store"]
