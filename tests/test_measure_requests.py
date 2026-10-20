"""The dashboard's measure queue (src/measure_requests.py), on a scratch
database and a fake stage runner. Invented text only."""
import csv
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import measure_requests as R  # noqa: E402

GOOD = {"request_id": 1, "label_set": "solar_backup", "measure_name": "Solar backup power",
        "definition": "A commitment to supply solar power with storage as backup for the sites "
                      "this project finances.",
        "phrases": "solar panels at each site\nbattery storage for towers\nsolar hybrid power",
        "patterns": r"\bsolar\b", "cohort_set": None}


def test_a_request_is_checked_before_anything_runs():
    assert R.check(GOOD, set()) == ""
    assert "must match" in R.check(dict(GOOD, label_set="Solar!"), set())
    assert "exists already" in R.check(GOOD, {"solar_backup"})
    assert "three search phrases" in R.check(dict(GOOD, phrases="one\ntwo"), set())
    assert "does not compile" in R.check(dict(GOOD, patterns="(unclosed"), set())


def test_the_files_have_the_shape_analysis_labels_keeps(tmp_path):
    R.write_files(GOOD, str(tmp_path))
    names = sorted(os.listdir(tmp_path))
    assert names == ["solar_backup.csv", "solar_backup_patterns.csv",
                     "solar_backup_queries.csv", "solar_backup_scale.csv"]
    q = list(csv.DictReader(open(tmp_path / "solar_backup_queries.csv")))
    assert [r["query"] for r in q] == GOOD["phrases"].splitlines()
    assert "{name}" in open(tmp_path / "solar_backup_scale.csv").read()


def test_the_spend_cap_stops_before_asking(monkeypatch, tmp_path):
    import answers
    import classify
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "0.000001")
    monkeypatch.setattr(answers, "_spent_here", [0.0])
    q = {"q": {"type": "noul", "instructions": "Is it about towers?",
               "criteria": {"true": "yes", "false": "no"}}}
    monkeypatch.setattr(classify, "decide", lambda *a, **k: pytest.fail("asked over the cap"))
    with pytest.raises(answers.CapReached):
        answers.ask("m", [({"passage": "x" * 2000}, q)], str(tmp_path), api_key="k")


@pytest.mark.skipif(not os.environ.get("WBG_PG_TEST"), reason="set WBG_PG_TEST to a scratch database")
def test_a_queued_request_runs_its_stages_once(monkeypatch, tmp_path):
    import psycopg
    monkeypatch.setenv("WBG_PG", os.environ["WBG_PG_TEST"])
    monkeypatch.setenv("WBG_DATA", str(tmp_path))
    with psycopg.connect(os.environ["WBG_PG_TEST"], autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {R.SCHEMA} CASCADE")
        R.ensure_schema(conn)
        conn.execute(f"INSERT INTO {R.TABLE} (label_set, measure_name, definition, phrases, "
                     f"patterns) VALUES (%s, %s, %s, %s, %s)",
                     (GOOD["label_set"], GOOD["measure_name"], GOOD["definition"],
                      GOOD["phrases"], GOOD["patterns"]))
        conn.execute(f"INSERT INTO {R.TABLE} (label_set, measure_name, definition, phrases) "
                     f"VALUES ('Bad Name', 'x', 'x', 'x')")
        ran = []

        def runner(cmd, env, **kw):
            ran.append(os.path.basename(cmd[1]))
            assert float(env["WBG_SPEND_CAP_USD"]) <= R.REQUEST_CAP_USD
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        monkeypatch.setattr(R, "committed", lambda *a: 7)
        assert R.run_one(conn, str(tmp_path), log=lambda *_: None, runner=runner) == "done"
        assert ran == ["search.py", "measure.py", "kinds.py", "kinds.py", "store.py"]
        assert os.path.exists(tmp_path / "requested_labels" / "solar_backup.csv")
        assert R.run_one(conn, str(tmp_path), log=lambda *_: None, runner=runner) == "failed"
        assert R.run_one(conn, str(tmp_path), log=lambda *_: None, runner=runner) is None
        got = conn.execute(f"SELECT label_set, status, committed_found FROM {R.TABLE} "
                           f"ORDER BY request_id").fetchall()
        assert got == [("solar_backup", "done", 7), ("Bad Name", "failed", None)]
