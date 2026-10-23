"""The grading sheets (src/check_units.py): the workbook, and reading the
person's answers back (including a tab of a workbook written before the
combined sheets), on invented rows."""
import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import check_units  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


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


def test_a_grade_follows_its_sentence_when_a_re_parse_moves_the_ids(tmp_path):
    import check_units
    lab = tmp_path / "labels"
    lab.mkdir()
    key = check_units.text_key("The project will finance backup generators.")
    with open(lab / "cra_units.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=check_units.LABEL_COLS, restval="")
        w.writeheader()
        w.writerow(dict(label_set="cra", label_id="cra", unit_id="D:p00007:s002",
                        text_sha256=key, question_sha256="q", grade="3"))
        w.writerow(dict(label_set="cra", label_id="cra", unit_id="D:p00008:s001",
                        text_sha256="gone", question_sha256="q", grade="0"))
    got = check_units.graded_labels("cra", str(tmp_path), "q", units_by_text={key: "D:p00009:s002"})
    assert got == {"D:p00009:s002": (3, "", 1.0)}                # moved; the vanished one dropped


def test_a_character_excel_refuses_never_reaches_the_workbook(tmp_path):
    """One U+FFFF inside a Chad PAD paragraph killed a whole `./wbg check all`
    draw at write time - "All strings must be XML compatible" - because
    openpyxl's own ILLEGAL_CHARACTERS_RE covers the C0 controls but not the
    noncharacters U+FFFE and U+FFFF. cell_text is the display boundary and
    strips XML 1.0's whole disallowed set, so the sheet opens and can be
    graded."""
    assert check_units.cell_text("before\uffffafter") == "beforeafter"
    assert check_units.cell_text("a\ufffeb\x00c\x0bd\ud800e") == "abcde"
    assert check_units.cell_text("a\fb") == "a\n[page break]\nb"   # the marker survives
    assert check_units.cell_text("a\tb\nc\rd") == "a\tb\nc\rd"     # tab, newline, CR survive
    assert check_units.cell_text("Résumé 数字") == "Résumé 数字"    # ordinary text is untouched
    tabs = _tabs()
    tabs[0]["rows"][0]["sentence"] = "before\uffffafter"
    tabs[0]["assets"] = ["fib\x00er"]
    path = str(tmp_path / "refused.xlsx")
    check_units._write_xlsx(path, tabs)                            # raised before the fix
    wb = openpyxl.load_workbook(path)
    seen = [c.value for row in wb["backup_power"].iter_rows()
            for c in row if isinstance(c.value, str)]
    assert "beforeafter" in seen
    assert not any("\uffff" in v or "\x00" in v for v in seen)
