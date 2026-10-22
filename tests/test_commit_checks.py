"""The commitment checks (src/commit_checks.py): pair answers for units two
measures both commit, from a fake decision model on invented committed
units. No scope question since 2026-10-22."""
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
        _csv(str(lab / f"{name}_scale.csv"), ["grade", "description"],
             [{"grade": g, "description": f"grade {g}"} for g in range(4)])
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(lab),))

    def fake_committed(name, data, **kw):
        return f"{name}-run1", {name: [{"unit_id": u, "text": t} for u, t in COMMITTED[name]]}
    monkeypatch.setattr(kinds, "committed_units", fake_committed)
    calls = []

    def fake_decide(body, **kw):
        calls.append(body)
        s = body["state"]["sentence"]
        out = {}
        for qid in body["questions"]:
            # off-grid solar is renewable energy, not backup power (side a)
            yes = not (qid == "keep_a" and "off-grid" in s)
            out[qid] = {"type": "score", "score": 3 if yes else 0, "confidence": 0.9,
                        "probabilities": {"0": 0.05, "1": 0.05, "2": 0.0, "3": 0.9} if yes else {"0": 0.95, "1": 0.0, "2": 0.0, "3": 0.05}}
        return {"model": measure.MODEL, "usage": {"cost": 0.00001}, "answers": out}
    monkeypatch.setattr(classify, "decide", fake_decide)
    return str(tmp_path / "data"), calls


def test_a_unit_two_measures_commit_gets_one_pair_answer_and_nothing_else_is_asked(store):
    data, calls = store
    out = commit_checks.run(data, api_key="k", log=lambda *_: None, workers=1)
    pairs = list(csv.DictReader(open(os.path.join(out, "pairs.csv"))))
    assert [(p["unit_id"], p["set_a"], p["set_b"], p["decision"]) for p in pairs] == [
        ("u1", "backup_power", "renewable_energy", "only_b")]
    # With no pair layer, each side's posterior is the model's score.
    p = pairs[0]
    assert p["p_keep_a"] == p["score_a"] == "0.050" and p["p_keep_b"] == "0.900"
    qs = next(c for c in calls if "keep_a" in c["questions"])["questions"]
    assert all(q["type"] == "score" for q in qs.values())
    # each side is the measure's own question, the other's boundary beside it
    assert "renewable electricity" in qs["keep_a"]["instructions"]
    assert "main supply fails" in qs["keep_b"]["instructions"]
    assert all(set(c["questions"]) == {"keep_a", "keep_b"} for c in calls)
    assert not os.path.exists(os.path.join(out, "scope.csv"))
    # The prior inputs: each measure's probability for the unit, known before the pair is asked.
    assert pairs[0]["prior_probability_a"] == "0.0000" and "prior_phrase_cosine_b" in pairs[0]


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
