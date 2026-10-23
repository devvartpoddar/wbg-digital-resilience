"""The run archive (src/archive_runs.py) and the runs ledger it feeds, on an
invented store."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import archive_runs  # noqa: E402
import load_pg  # noqa: E402


def _run(path, label_set="cra"):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "manifest.json"), "w") as fh:
        json.dump({"label_set": label_set}, fh)


def test_a_failed_lookup_moves_nothing(tmp_path, monkeypatch, capsys):
    data = str(tmp_path)
    _run(os.path.join(data, "measures", "cra-aaaaaaaaaaaa"))
    monkeypatch.setattr(archive_runs, "current_names",
                        lambda d, s: ({k: set() for k in archive_runs.STAGES},
                                      ["measures cra scope=None: boom"]))
    monkeypatch.setattr(sys, "argv", ["archive_runs.py", "--data", data])
    assert archive_runs.main() == 1
    assert os.path.isdir(os.path.join(data, "measures", "cra-aaaaaaaaaaaa"))   # not moved
    assert "nothing is moved" in capsys.readouterr().out


def test_a_run_both_archived_and_live_is_one_row(tmp_path):
    data = str(tmp_path)
    _run(os.path.join(data, "measures", "cra-aaaaaaaaaaaa"))
    _run(os.path.join(data, "archive", "runs-2026-10-22", "measures", "cra-aaaaaaaaaaaa"))
    _run(os.path.join(data, "archive", "runs-2026-10-22", "measures", "cra-bbbbbbbbbbbb"))
    tables = {t[0]: t for t in load_pg.judgement_tables(data)}
    runs = tables["runs"][3]
    assert sorted(r["run_id"] for r in runs) == ["cra-aaaaaaaaaaaa", "cra-bbbbbbbbbbbb"]
