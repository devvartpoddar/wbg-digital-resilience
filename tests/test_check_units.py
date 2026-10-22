"""The grading sheets (src/check_units.py): the band draw, the combined
workbook with a tab per measure and the package links, and reading the
person's answers back, on invented rows."""
import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import check_units  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


def _units(n=200):
    return [{"unit_id": f"u{i:03d}", "sent": "true", "route": "", "grade": "0",
             "p3": f"{i / n:.3f}"} for i in range(n)]


def test_the_band_draw_takes_each_bands_quota_and_is_repeatable():
    units = _units() + [{"unit_id": "x", "sent": "false", "route": "", "grade": "", "p3": ""}]
    picked = check_units.draw_bands(units, 3, seed=2)
    got = {}
    for u, s, size, drawn in picked:
        got[s] = (size, drawn)
        lo, hi = next((b[1], b[2]) for b in check_units.BANDS if b[0] == s)
        assert lo <= float(u["p3"]) < hi
    assert got == {"p<0.15": (30, 5), "p0.15-0.4": (50, 8), "p0.4-0.6": (40, 12),
                   "p0.6-0.8": (40, 10), "p0.8+": (40, 5)}
    assert [u["unit_id"] for u, *_ in picked] == \
        [u["unit_id"] for u, *_ in check_units.draw_bands(units, 3, seed=2)]


def _tabs():
    scale = ["none", "mentioned", "planned", "committed"]
    rows = [{"row_id": i, "measure": "Backup power", "sentence": f"Sentence {i}."}
            for i in (1, 2, 3)]
    return [{"tab": "backup_power", "rows": rows, "scale": scale, "assets": ["fiber"],
             "types": [(f"type_{i}", f"Type {i}", "d" * 40) for i in range(12)],
             "measure": "backup_power"},
            {"tab": "package links", "rows": [{"row_id": 1, "commitment": "c", "package": "p"},
                                              {"row_id": 2, "commitment": "c", "package": "q"}]}]


def _fill(path, tab, col, values):
    wb = openpyxl.load_workbook(path)
    ws = wb[tab]
    head = [c.value for c in ws[1]]
    for r, v in values.items():
        ws.cell(r + 1, head.index(col) + 1, v)
    wb.save(path)


def test_the_workbook_reads_back_per_tab(tmp_path):
    path = str(tmp_path / "all.xlsx")
    check_units._write_xlsx(path, _tabs())
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames[:3] == ["how to", "lists", "backup_power"]
    assert wb["lists"].sheet_state == "hidden"
    # twelve long type ids would pass Excel's 255-character limit on an
    # inline list; the dropdowns point at the hidden sheet instead
    assert all("lists!" in dv.formula1 for dv in wb["backup_power"].data_validations.dataValidation)
    _fill(path, "backup_power", "grade", {1: 3, 3: "2"})
    _fill(path, "backup_power", "type", {1: "type_4"})
    _fill(path, "package links", "applies", {2: "Yes"})
    assert check_units.read_sheet(path, "backup_power") == {"1": (3, "", "type_4"),
                                                           "3": (2, "", "")}
    assert check_units.read_links(path) == {"2": "yes"}


def test_a_measure_is_evaluated_from_its_tab_in_the_combined_workbook(tmp_path):
    data = str(tmp_path)
    os.makedirs(tmp_path / "review")
    check_units._write_xlsx(check_units.all_path(data, 2), _tabs())
    with pytest.raises(SystemExit):           # no key file: not drawn at this seed
        check_units.graded_sheet("backup_power", data, 2)
    (tmp_path / "review" / "check_units_backup_power_s2.key.csv").write_text("row_id\n")
    _fill(check_units.all_path(data, 2), "backup_power", "grade", {2: 1})
    kpath, graded, source = check_units.graded_sheet("backup_power", data, 2)
    assert graded == {"2": (1, "", "")} and source == "check_units_backup_power_s2"


def test_link_answers_are_stored_by_text_and_scored_by_threshold(tmp_path, monkeypatch):
    data = str(tmp_path)
    os.makedirs(tmp_path / "review")
    check_units._write_xlsx(check_units.all_path(data, 2), _tabs())
    key = [{"row_id": i, "label_set": "backup_power", "unit_id": f"u{i}", "package_id": f"k{i}",
            "text_sha256": f"t{i}", "package_sha256": f"p{i}", "question_sha256": "q",
            "probability": p, "stratum": s, "stratum_size": 10, "stratum_drawn": 1,
            "link_run": "link-abc"}
           for i, p, s in ((1, "0.95", "p0.9+"), (2, "0.4", "p0.3-0.5"))]
    with open(tmp_path / "review" / "check_links_s2.key.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=check_units.LINK_KEY_COLS)
        w.writeheader()
        w.writerows(key)
    _fill(check_units.all_path(data, 2), "package links", "applies", {1: "yes", 2: "no"})
    out, n = check_units.evaluate_links(data, 2)
    assert n == 2
    stored = list(csv.DictReader(open(tmp_path / "labels" / "package_links.csv")))
    assert [(r["text_sha256"], r["applies"]) for r in stored] == [("t1", "yes"), ("t2", "no")]
    report = open(out).read()
    assert "| 0.3 | 0.50" in report and "| 0.9 | 1.00" in report


def test_the_floor_draw_sits_around_the_measures_commit_floor():
    bands = check_units.floor_bands(0.3)
    assert [b[0] for b in bands] == ["p0.00-0.10", "p0.10-0.20", "p0.20-0.30", "p0.30-0.40",
                                     "p0.40-0.55", "p0.55-1.00"]
    assert sum(b[3] for b in bands) == 20
    assert any(lo <= 0.3 < hi for _, lo, hi, _ in bands)
    assert sum(b[3] for b in check_units.floor_bands(None)) == 21


def test_the_tuning_draw_is_the_floor_draw_at_about_28_rows():
    for floor in (0.3, 0.5, 0.6):
        bands = check_units.floor_bands(floor, check_units.TUNE_SCALE)
        assert [b[0] for b in bands] == [b[0] for b in check_units.floor_bands(floor)]
        assert sum(b[3] for b in bands) == 28
    assert sum(b[3] for b in check_units.floor_bands(None, check_units.TUNE_SCALE)) == 29


def test_only_grades_given_under_the_current_question_are_read(tmp_path):
    import check_units
    lab = tmp_path / "labels"
    lab.mkdir()
    rows = [dict(label_set="cra", label_id="cra", unit_id="u1", text_sha256="t1",
                 question_sha256="old", grade="3", graded_on="2026-10-01", weight="4"),
            dict(label_set="cra", label_id="cra", unit_id="u2", text_sha256="t2",
                 question_sha256="new", grade="1", graded_on="2026-10-02", weight="2.5")]
    with open(lab / "cra_units.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=check_units.LABEL_COLS, restval="")
        w.writeheader()
        w.writerows(rows)
    assert check_units.graded_labels("cra", str(tmp_path), "new") == {"u2": (1, "", 2.5)}
    assert set(check_units.graded_labels("cra", str(tmp_path))) == {"u1", "u2"}


def test_training_draws_near_the_floor_and_evaluation_draws_by_the_decision():
    import check_units
    units = [{"unit_id": f"u{i:03d}", "sent": "true", "p_committed_calibrated": f"{i / 200:.3f}"}
             for i in range(200)]
    train = check_units.draw(units, 0.5, 1, "train")
    near = [u for u, s, *_ in train if s == "train:near"]
    assert len(near) == 30 and all(abs(float(u["p_committed_calibrated"]) - 0.5) <= 0.2
                                   for u in near)
    ev = check_units.draw(units, 0.5, 1, "eval")
    by = {}
    for u, s, size, drawn in ev:
        by[s] = (size, drawn)
    assert by == {"eval:committed": (100, 60), "eval:not_committed": (100, 30)}
    assert check_units.draw(units, 0.5, 1, "eval") == ev            # repeatable


def test_evaluation_rows_never_fit_a_layer_or_a_gate(tmp_path):
    import check_units
    lab = tmp_path / "labels"
    lab.mkdir()
    rows = [dict(label_set="cra", label_id="cra", unit_id="u1", text_sha256="t1",
                 question_sha256="q", grade="3", stratum="train:near", weight="2"),
            dict(label_set="cra", label_id="cra", unit_id="u2", text_sha256="t2",
                 question_sha256="q", grade="3", stratum="eval:committed", weight="5")]
    with open(lab / "cra_units.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=check_units.LABEL_COLS, restval="")
        w.writeheader()
        w.writerows(rows)
    assert set(check_units.graded_labels("cra", str(tmp_path), "q", fitting=True)) == {"u1"}
    assert set(check_units.graded_labels("cra", str(tmp_path), "q")) == {"u1", "u2"}
