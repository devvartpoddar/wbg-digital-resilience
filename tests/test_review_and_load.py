"""Unit tests for the data root, the review sheets and the Postgres loader.

Invented text throughout; no document text is ever committed. The loader test
needs a real Postgres and runs only when $WBG_PG_TEST names one.
"""
import csv
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import paths  # noqa: E402
import load_pg as L  # noqa: E402


# --- where data goes ---------------------------------------------------------

def test_env_overrides_data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("WBG_DATA", str(tmp_path))
    assert paths.data_root() == str(tmp_path)


def test_worktree_resolves_to_main_checkout(tmp_path):
    """The reason paths.py exists: a board card's worktree is deleted when the
    card completes, so data must land in the main checkout, not the worktree."""
    main = tmp_path / "repo"
    main.mkdir()
    run = lambda *a, cwd=main: subprocess.run(["git", *a], cwd=cwd, check=True,  # noqa: E731
                                              capture_output=True)
    run("init", "-q")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    wt = main / ".worktrees" / "t_1"
    run("worktree", "add", "-q", str(wt))
    assert paths.main_checkout(str(wt)) == str(main)


def test_not_a_repo_falls_back_to_itself(tmp_path):
    assert paths.main_checkout(str(tmp_path)) == str(tmp_path)


def test_convert_types_and_nulls():
    bad = {}
    assert L.convert("n_tokens", "12", bad) == 12
    assert L.convert("estimated_amount", "1,250.5", bad) == 1250.5
    assert L.convert("description", "", bad) is None
    assert L.convert("estimated_amount", "n/a", bad) is None
    assert bad == {"estimated_amount": 1}


@pytest.mark.skipif(not os.environ.get("WBG_PG_TEST"), reason="set WBG_PG_TEST to a scratch database")
def test_load_is_idempotent(tmp_path, monkeypatch, capsys):
    import psycopg
    with psycopg.connect(os.environ["WBG_PG_TEST"], autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS wbg CASCADE")
    data = tmp_path
    (data / "appraisal" / "text").mkdir(parents=True)
    (data / "raw").mkdir()
    (data / "appraisal" / "text" / "D1.txt").write_text("Alpha beta gamma. Delta epsilon.",
                                                       encoding="utf-8")
    with open(data / "raw" / "documents.csv", "w", newline="") as fh:
        fh.write("doc_id,title\nD1,Test\n")
    from clean import PARA_COLS
    with open(data / "appraisal" / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PARA_COLS)
        w.writeheader()
        for i, (a, z, n) in enumerate([(0, 17, 3), (18, 32, 2)], 1):
            w.writerow({"paragraph_id": f"D1:p{i:05d}", "doc_id": "D1", "project_ids": "P1",
                        "ordinal": i, "block": "narrative", "for_model": "true",
                        "char_start": a, "char_end": z, "n_tokens": n, "section_title": "x"})
    argv = ["load_pg.py", "--data", str(data), "--dsn", os.environ["WBG_PG_TEST"]]
    monkeypatch.setattr(sys, "argv", argv)
    L.main()
    monkeypatch.setattr(sys, "argv", argv)
    L.main()
    out = capsys.readouterr().out
    assert "paragraphs                  2 rows" in out and "paragraphs           unchanged" in out
    with psycopg.connect(os.environ["WBG_PG_TEST"]) as conn:
        texts = [r[0] for r in conn.execute("SELECT text FROM wbg.paragraphs ORDER BY 1")]
    assert texts == ["Alpha beta gamma.", "Delta epsilon."]


def test_dates_flags_and_stamps_are_typed_for_metabase():
    bad = {}
    assert L.col_type("planned_date") == "date" and L.col_type("status_as_of") == "date"
    assert L.col_type("fetched_at") == "timestamptz" and L.col_type("for_model") == "boolean"
    assert L.convert("planned_date", "2024-06-30", bad) == "2024-06-30"
    assert L.convert("planned_date", "30/06/2024", bad) is None
    assert L.convert("for_model", "true", bad) is True
    assert L.convert("for_model", "yes", bad) is None
    assert L.convert("fetched_at", "2026-10-13T14:31:08Z", bad) == "2026-10-13T14:31:08Z"
    assert L.convert("fetched_at", "yesterday", bad) is None
    assert bad == {"planned_date": 1, "for_model": 1, "fetched_at": 1}


def test_found_sets_reads_the_run_names(monkeypatch):
    monkeypatch.setattr(L.labels, "label_sets", lambda: ["assets", "cra", "ews"])
    sha = "cra-abc123def456|ews-0123456789ab|cra-assign-aaaaaaaaaaaa|9f8e7d6c5b4a"
    assert L.found_sets(sha) == ["cra", "ews"]
    assert L.found_sets(None) == []


@pytest.mark.skipif(not os.environ.get("WBG_PG_TEST"), reason="set WBG_PG_TEST to a scratch database")
def test_a_set_without_a_current_run_keeps_its_rows():
    """Replacing one set's rows, even when the table's columns changed,
    leaves every other set's rows in place (a measure being re-run must not
    vanish from the dashboards)."""
    import psycopg
    with psycopg.connect(os.environ["WBG_PG_TEST"], autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS wbg CASCADE")
        L.prepare(conn)
        cols = ["label_set", "unit_id", "text"]
        L.replace_rows(conn, "measure_findings", cols,
                       [{"label_set": "a", "unit_id": "u1", "text": "x"},
                        {"label_set": "b", "unit_id": "u2", "text": "y"}], "label_set", ["a", "b"], "s1")
        # New code adds a column: set a is replaced, set b (no current run) is carried over.
        L.replace_rows(conn, "measure_findings", cols + ["firmness"],
                       [{"label_set": "a", "unit_id": "u3", "text": "z", "firmness": "activity"}],
                       "label_set", ["a"], "s2")
        got = conn.execute("SELECT label_set, unit_id, firmness FROM wbg.measure_findings "
                           "ORDER BY 1").fetchall()
        assert got == [("a", "u3", "activity"), ("b", "u2", None)]


def test_an_evaluation_writes_its_headline_numbers(tmp_path):
    """check --evaluate keeps one row per measure and question in
    data/labels/measure_quality.csv; a later evaluation replaces it."""
    import check_units
    key = {"question_sha256": "q1", "measure_dir": "cra-abc"}
    r = lambda yes, p, w=1.0: {"yes": yes, "p": p, "w": w, "stratum": "train:near"}
    rows = [r(True, 0.9), r(True, 0.3), r(False, 0.8), r(True, 0.7)]
    check_units.write_quality(str(tmp_path), "cra", key, rows, 0.5)
    check_units.write_quality(str(tmp_path), "cra", key, rows[:2], 0.5)
    got = list(csv.DictReader(open(tmp_path / "labels" / "measure_quality.csv")))
    assert len(got) == 1 and got[0]["graded_units"] == "2"
    assert got[0]["precision"] == "1.000" and got[0]["recall"] == "0.500"
    assert got[0]["version"]                     # the measure file's version column
    assert got[0]["sheet"] == "training"


def test_the_record_of_judgement_is_read_for_postgres(tmp_path):
    """Grades, draws, layers, gates, run manifests, the spend ledger and the
    answer store each become rows of their own table."""
    import json
    import load_pg
    d = tmp_path
    (d / "labels").mkdir()
    (d / "labels" / "cra_units.csv").write_text(
        "label_set,label_id,unit_id,text_sha256,question_sha256,grade,asset,type,source,"
        "graded_on,stratum,weight\ncra,cra,u1,t1,q1,3,,,s1,2026-10-22,train:near,2.5\n")
    (d / "labels" / "cra_gate.json").write_text(json.dumps(
        {"cra": {"gate": 0.41, "lost": 0.0, "positives": 12, "question_sha256": "q1",
                 "search_run": "cra-x"}}))
    (d / "labels" / "cra_posterior.json").write_text(json.dumps(
        {"label": "cra", "question_sha256": "q1", "coef": [0.1, 1.0], "n_yes": 9, "n_no": 11,
         "settings": {"sharpness": 20}}))
    (d / "review").mkdir()
    (d / "review" / "check_units_cra_s1.key.csv").write_text(
        "row_id,label_id,unit_id,stratum,stratum_size,stratum_drawn\n1,cra,u1,train:near,10,4\n")
    (d / "measures" / "cra-abc").mkdir(parents=True)
    (d / "measures" / "cra-abc" / "manifest.json").write_text(json.dumps(
        {"label_set": "cra", "calls_this_run": 3, "cost_this_run_usd": 0.01,
         "created_at": "2026-10-22T00:00:00Z"}))
    (d / "reports").mkdir()
    (d / "reports" / "spend.csv").write_text(
        "at,stage,label_set,model,calls,cost_usd,seconds,run\n2026-10-22T00:00:00Z,measure,cra,m,3,0.01,4,cra-abc\n")
    ans = d / "classify" / "answers" / "typesafe__jev" / "ab"
    ans.mkdir(parents=True)
    (ans / "abcd.json").write_text(json.dumps({"answer": {"type": "score"}, "model": "jev-x",
                                               "cost_usd": 0.001, "call": "c1"}))
    got = {t: (key, rows) for t, cols, key, rows, _ in load_pg.judgement_tables(str(d))}
    assert got["graded_labels"][1][0]["weight"] == "2.5"
    assert got["check_draws"][1][0]["sheet"] == "check_units_cra_s1"
    assert got["skip_gates"][1][0]["gate"] == 0.41
    layer = got["posterior_layers"][1][0]
    assert (layer["label_id"], layer["graded_yes"]) == ("cra", 9)
    assert got["runs"][1][0]["stage"] == "measures" and got["runs"][1][0]["calls"] == 3
    assert got["spend"][1][0]["stage"] == "measure"
    assert got["graded_package_links"][0] is None            # no source: not loaded
    key, rows = load_pg.answer_rows(str(d))
    rows = list(rows)
    assert key and rows[0]["answer_sha256"] == "abcd" and rows[0]["model"] == "typesafe/jev"


def test_every_sentence_gets_its_asset_as_the_measure_stage_decides_it(tmp_path, monkeypatch):
    import load_pg
    import measure
    (tmp_path / "embeddings").mkdir()
    (tmp_path / "embeddings" / "sentence_index.csv").write_text(
        "unit_id,paragraph_id,project_ids\nD:p1:s1,D:p1,P1\nD:p1:s2,D:p1,P1\nD:p2:s1,D:p2,P1\n")
    monkeypatch.setattr(measure, "asset_map", lambda *a, **k: (
        {"D:p1:s1": {"towers": 0.9}, "D:p1:s2": {"towers": 0.1}, "D:p2:s1": {"fiber": 0.05}},
        "assets-abc"))
    import classify
    monkeypatch.setattr(classify, "asset_layers_digest", lambda *a: "")
    key, rows = load_pg.unit_asset_rows(str(tmp_path))
    by = {r["unit_id"]: (r["asset_id"], r["asset_level"]) for r in rows}
    assert by == {"D:p1:s1": ("towers", "unit"), "D:p1:s2": ("towers", "paragraph"),
                  "D:p2:s1": ("", "none")}
