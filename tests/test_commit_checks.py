"""The commitment checks (src/commit_checks.py): pair answers for units two
measures both commit, and each committed unit's scope, from a fake decision
model on invented committed units."""
import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import classify  # noqa: E402
import commit_checks  # noqa: E402
import kinds  # noqa: E402
import labels  # noqa: E402
import measure  # noqa: E402

COMMITTED = {
    "backup_power": [("u1", "Solar systems with batteries will power the off-grid towers."),
                     ("u2", "Generators will keep the data centre running in outages.")],
    "renewable_energy": [("u1", "Solar systems with batteries will power the off-grid towers."),
                         ("u3", "All sites will be powered by renewable energy.")],
}


def _csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def store(tmp_path, monkeypatch):
    lab = tmp_path / "labels"
    for name, b in (("backup_power", "The project provides electricity when the main supply fails."),
                    ("renewable_energy", "The project supplies renewable electricity.")):
        _csv(str(lab / f"{name}.csv"), ["measure_id", "measure_name", "definition", "boundary",
                                        "active"],
             [{"measure_id": name, "measure_name": name.replace("_", " ").title(),
               "definition": "a definition", "boundary": b, "active": "true"}])
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(lab),))

    def fake_committed(name, data, **kw):
        return f"{name}-run1", {name: [{"unit_id": u, "text": t} for u, t in COMMITTED[name]]}
    monkeypatch.setattr(kinds, "committed_units", fake_committed)
    calls = []

    def fake_decide(body, **kw):
        calls.append(body)
        s = body["state"]["sentence"]
        out = {}
        for qid, q in body["questions"].items():
            opts = list(q["criteria"])
            if qid == "pair":
                pick = "only_b" if "off-grid" in s else "both"
            else:
                pick = "rule" if s.startswith("All") else "investment"
            out[qid] = {"type": "choice", "choice": pick,
                        "probabilities": {o: (0.8 if o == pick else 0.2 / (len(opts) - 1))
                                          for o in opts}}
        return {"model": measure.MODEL, "usage": {"cost": 0.00001}, "answers": out}
    monkeypatch.setattr(classify, "decide", fake_decide)
    return str(tmp_path / "data"), calls


def test_a_unit_two_measures_commit_gets_one_pair_answer_and_every_unit_a_scope(store):
    data, calls = store
    out = commit_checks.run(data, api_key="k", log=lambda *_: None, workers=1)
    pairs = list(csv.DictReader(open(os.path.join(out, "pairs.csv"))))
    assert [(p["unit_id"], p["set_a"], p["set_b"], p["decision"]) for p in pairs] == [
        ("u1", "backup_power", "renewable_energy", "only_b")]
    q = next(c for c in calls if "pair" in c["questions"])["questions"]["pair"]
    assert q["type"] == "choice" and set(q["criteria"]) == set(commit_checks.PAIR_OPTIONS)
    assert "main supply fails" in q["instructions"] and "renewable electricity" in q["instructions"]
    scope = {r["unit_id"]: r["scope"] for r in csv.DictReader(open(os.path.join(out, "scope.csv")))}
    assert scope == {"u1": "investment", "u2": "investment", "u3": "rule"}


def test_a_rerun_asks_nothing_and_finds_the_same_folder(store):
    data, calls = store
    out = commit_checks.run(data, api_key="k", log=lambda *_: None, workers=1)
    n = len(calls)
    assert commit_checks.current_run(data) == out
    assert commit_checks.run(data, api_key="k", log=lambda *_: None, workers=1) == out
    assert len(calls) == n


def test_a_dry_run_asks_nothing(store):
    data, calls = store
    assert commit_checks.run(data, dry_run=True, log=lambda *_: None) is None
    assert calls == []
