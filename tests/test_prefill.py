"""Fixture tests for the laptop-side prefill step.

This script is the one piece that ever sees the portfolio tracker, and it runs
on the annotator's machine where nobody is watching it. So the fixture has to
cover the ways it could quietly ruin a day's work: overwriting a cell that was
already filled in, writing a suggestion onto the wrong paragraph, or crashing
after saving a half-written file.
"""
import importlib.util, os, sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("build_sample", os.path.join(ROOT, "analysis", "goldset", "build_sample.py"))
pf = _load("prefill", os.path.join(ROOT, "analysis", "goldset", "prefill.py"))
openpyxl = pytest.importorskip("openpyxl")
from test_build_sample import MEASURE, THIRD, write_tracker  # noqa: E402


def make_workbook(path, paragraphs, rows_per=2):
    """paragraphs: [(pid, project, text)]."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "paragraphs"
    ws.append(["paragraph_id", "project_id", "doc_id", "section_label",
               "slice", "tokens", "read", "text"])
    for pid, proj, text in paragraphs:
        ws.append([pid, proj, "d", "sec", "targeted", len(text.split()), "", text])
    rs = wb.create_sheet("rows")
    rs.append(bs.ROW_COLUMNS)
    for pid, _, text in paragraphs:
        for _ in range(rows_per):
            rs.append([pid, text[:40], "", "", "", "", "", "", "", ""])
    wb.save(path)
    return path


def test_fills_asset_and_direction_on_the_matching_paragraph_only(tmp_path):
    book = make_workbook(tmp_path / "w.xlsx", [
        ("p1", "P100001", MEASURE),
        ("p2", "P100001", THIRD),
    ])
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {"Resilient telecom": "Yes"})])
    pf.main.__globals__["sys"].argv = [
        "prefill", "--workbook", str(book), "--tracker", str(tracker)]
    assert pf.main() == 0
    rs = openpyxl.load_workbook(book)["rows"]
    got = {(r[0].value, r[6].value, r[5].value) for r in rs.iter_rows(min_row=2)}
    assert ("p1", "telecom network", "resilience_of_asset") in got
    assert all(pid != "p2" or (asset in (None, "") and d in (None, ""))
               for pid, asset, d in got)


def test_never_overwrites_a_cell_the_annotator_filled(tmp_path):
    book = make_workbook(tmp_path / "w.xlsx", [("p1", "P100001", MEASURE)])
    wb = openpyxl.load_workbook(book)
    rs = wb["rows"]
    c_asset = bs.ROW_COLUMNS.index("asset") + 1
    rs.cell(2, c_asset, "submarine landing station")     # annotator's own answer
    wb.save(book)
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {"Resilient telecom": "Yes"})])
    pf.main.__globals__["sys"].argv = [
        "prefill", "--workbook", str(book), "--tracker", str(tracker)]
    pf.main()
    rs = openpyxl.load_workbook(book)["rows"]
    assert rs.cell(2, c_asset).value == "submarine landing station"
    assert rs.cell(3, c_asset).value == "telecom network"


def test_is_idempotent(tmp_path):
    book = make_workbook(tmp_path / "w.xlsx", [("p1", "P100001", MEASURE)])
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {"Resilient telecom": "Yes"})])
    argv = ["prefill", "--workbook", str(book), "--tracker", str(tracker)]
    pf.main.__globals__["sys"].argv = argv
    pf.main()
    first = [[c.value for c in r] for r in openpyxl.load_workbook(book)["rows"].iter_rows()]
    pf.main.__globals__["sys"].argv = argv
    pf.main()
    second = [[c.value for c in r] for r in openpyxl.load_workbook(book)["rows"].iter_rows()]
    assert first == second


def test_dry_run_writes_nothing(tmp_path):
    book = make_workbook(tmp_path / "w.xlsx", [("p1", "P100001", MEASURE)])
    before = (tmp_path / "w.xlsx").read_bytes()
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {"Resilient telecom": "Yes"})])
    pf.main.__globals__["sys"].argv = [
        "prefill", "--workbook", str(book), "--tracker", str(tracker), "--dry-run"]
    pf.main()
    assert (tmp_path / "w.xlsx").read_bytes() == before
    assert not (tmp_path / "w.xlsx.bak").exists()


def test_keeps_a_backup_before_the_first_write(tmp_path):
    book = make_workbook(tmp_path / "w.xlsx", [("p1", "P100001", MEASURE)])
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {"Resilient telecom": "Yes"})])
    pf.main.__globals__["sys"].argv = [
        "prefill", "--workbook", str(book), "--tracker", str(tracker)]
    pf.main()
    assert (tmp_path / "w.xlsx.bak").exists()


def test_match_does_not_cross_projects(tmp_path):
    book = make_workbook(tmp_path / "w.xlsx", [("p1", "P999999", MEASURE)])
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {"Resilient telecom": "Yes"})])
    pf.main.__globals__["sys"].argv = [
        "prefill", "--workbook", str(book), "--tracker", str(tracker)]
    pf.main()
    rs = openpyxl.load_workbook(book)["rows"]
    c_asset = bs.ROW_COLUMNS.index("asset") + 1
    assert rs.cell(2, c_asset).value in (None, "")


def test_refuses_a_workbook_that_is_not_ours(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "something else"
    path = tmp_path / "w.xlsx"
    wb.save(path)
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P1", MEASURE, {})])
    pf.main.__globals__["sys"].argv = [
        "prefill", "--workbook", str(path), "--tracker", str(tracker)]
    with pytest.raises(SystemExit) as e:
        pf.main()
    assert "sheet" in str(e.value)


def test_reports_categories_it_has_no_hint_for(tmp_path, capsys):
    book = make_workbook(tmp_path / "w.xlsx", [("p1", "P100001", MEASURE)])
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {})])
    wb = openpyxl.load_workbook(tracker)
    ws = wb.active
    hdr = [c.value for c in ws[2]]
    ws.cell(3, hdr.index("Resilient telecom") + 1, "")
    ws.cell(2, ws.max_column + 1, "Mitigation")      # keep the boundary present
    wb.save(tracker)
    pf.main.__globals__["sys"].argv = [
        "prefill", "--workbook", str(book), "--tracker", str(tracker)]
    pf.main()
    out = capsys.readouterr().out
    assert "workbook paragraphs the tracker had already caught" in out
