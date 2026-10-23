"""The tests' fake decision models answer one item a request; the batched
form (src/answers.py BATCH) has tests of its own (tests/test_answers_batch.py)."""
import os

os.environ.setdefault("WBG_ASK_BATCH", "0")

# The tests were written against the panel's starting values; v1.0 moved the
# floors and turned the probability layer off (inputs/config/control_panel.csv).
# Each test run reads the live panel with those starting values put back, so a
# tuned number never breaks a test. A test that writes its own panel
# (WBG_PANEL) still wins.
import csv
import tempfile

STARTING = {"posterior.on": "true", "floor.default": "0.5", "floor.types": "0.5",
            "floor.pair_check": "0.5", "asset.floor": "0.3", "asset.decay": "0.4",
            "asset.multiple": "true", "link.asset_yes": "0.5"}


def _test_panel():
    live = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "inputs", "config", "control_panel.csv")
    with open(live, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if r["setting"] in STARTING:
            r["value"] = STARTING[r["setting"]]
    fd, path = tempfile.mkstemp(prefix="wbg-panel-", suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


os.environ.setdefault("WBG_PANEL", _test_panel())
