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
import review_sheets as RS  # noqa: E402
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


# --- finding a paragraph in the raw file --------------------------------------

RAW = ("Some heading\n\n"
       "  12. The ministry will build shel-\n"
       "  ters on raised plinths above the flood line.\n"
       "\fPage 4 of 9\n"
       "  The works cost 3.2 million.\n"
       "Next paragraph here.\n")


def test_locates_across_hyphen_and_page_header():
    clean = ("12. The ministry will build shelters on raised plinths above the "
             "flood line. The works cost 3.2 million.")
    got = RS.locate_raw(clean, RAW, RS.letters(RAW))
    assert got.startswith("  12. The ministry")        # whole first line
    assert "Page 4 of 9" in got                        # what cleaning removed is shown
    assert got.endswith("3.2 million.")                # trailing figure kept
    assert "Next paragraph" not in got


def test_unlocatable_paragraph_is_none():
    assert RS.locate_raw("Nothing like this appears anywhere in it at all, honestly.",
                         RAW, RS.letters(RAW)) is None


def test_cell_text_marks_page_breaks_and_drops_control_chars():
    assert RS.cell_text("a\fb\x01c") == "a\n[page break]\nbc"


def test_existing_sheet_is_not_overwritten(tmp_path, monkeypatch, capsys):
    data = tmp_path
    (data / "review").mkdir()
    keep = data / "review" / "paragraphs_s1.xlsx"
    keep.write_bytes(b"notes")
    (data / "procurement").mkdir(parents=True)
    with open(data / "procurement" / "packages.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["package_id", "project_id", "description"])
        w.writeheader()
        w.writerow({"package_id": "K1", "project_id": "P1", "description": "x"})
    monkeypatch.setattr(sys, "argv", ["review_sheets.py", "--data", str(data)])
    RS.main()
    assert keep.read_bytes() == b"notes"
    assert (data / "review" / "packages_s1.xlsx").exists()


# --- loading -----------------------------------------------------------------

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
    with open(data / "appraisal" / "paragraphs.csv", "w", newline="") as fh:
        fh.write("paragraph_id,doc_id,char_start,char_end,n_tokens\n"
                 "D1:p00001,D1,0,17,3\nD1:p00002,D1,18,32,2\n")
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
