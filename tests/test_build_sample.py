"""Fixture tests for the gold-set sample builder.

The point of the fixture is that the real run happens on the box against a
20 MB corpus and a worksheet that cannot be committed, so every failure mode
has to be reachable here instead: a moved clean file, a reordered tracker
column, a slice that overlaps another, a workbook whose ids do not join.
"""
import csv, hashlib, importlib.util, os, sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "build_sample", os.path.join(ROOT, "analysis", "goldset", "build_sample.py"))
bs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bs)

openpyxl = pytest.importorskip("openpyxl")

MEASURE = ("The Project will finance the burial of fiber optic cable along "
           "one hundred and twenty kilometres of flood prone segments of the "
           "national backbone corridor near the coast.")
OTHER = ("The Recipient shall prepare and furnish to the Association interim "
         "unaudited financial reports for the Project covering the calendar "
         "semester not later than forty five days after the period ends.")
THIRD = ("Component two will support the establishment of a national computer "
         "security incident response team together with a security operations "
         "centre serving all line ministries and their agencies.")


def write_corpus(tmp, paragraphs):
    """paragraphs: [(doc_id, project_id, section, block, text)] -> data dir."""
    data = tmp / "data"
    (data / "clean").mkdir(parents=True)
    prep = data / "intermediate" / "prepared"
    prep.mkdir(parents=True)

    bodies, rows, links = {}, [], {}
    for i, (doc, proj, section, block, text) in enumerate(paragraphs):
        body = bodies.setdefault(doc, "")
        start = len(body)
        bodies[doc] = body + text + "\n\n"
        rows.append({
            "paragraph_id": f"{doc}:p{i:03d}", "doc_id": doc, "ordinal": i,
            "section_label": section, "in_scope": "true", "block_type": block,
            "char_start": start, "char_end": start + len(text),
            "raw_text_sha256": "", "clean_version": "clean-1",
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "token_count": len(text.split()),
        })
        links[doc] = proj
    for doc, body in bodies.items():
        (data / "clean" / f"{doc}.txt").write_text(body)
    with open(prep / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    with open(prep / "span_project.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["doc_id", "project_id", "link_basis"])
        for doc, proj in sorted(links.items()):
            w.writerow([doc, proj, "test"])
    return data


def write_tracker(path, entries, extra_col=False):
    """entries: [(project, excerpt, {category: value})]."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "2. Activities Review"
    cats = ["Resilient telecom", "Climate applications1"]
    hdr = (["junk", "Project ID"] + (["inserted later"] if extra_col else [])
           + ["Measures / Activities", "Adaptation"] + cats + ["Mitigation"])
    ws.append(["banner"] + [""] * (len(hdr) - 1))
    ws.append(hdr)
    for proj, text, mapping in entries:
        row = {"Project ID": proj, "Measures / Activities": text,
               "Adaptation": "Yes"}
        row.update(mapping)
        ws.append([row.get(h, "") for h in hdr])
    wb.save(path)


def args(**kw):
    base = dict(data="", tracker="", out="", seed=7, flagged=2, targeted=1,
                random_n=1, targeted_sections="", rows_rich=3, rows_random=2,
                inspect=False)
    base.update(kw)
    return type("A", (), base)()


def corpus_fixture(tmp):
    return write_corpus(tmp, [
        ("d1", "P100001", "Project Components", "narrative", MEASURE),
        ("d1", "P100001", "Fiduciary", "narrative", OTHER),
        ("d1", "P100001", "Cybersecurity arrangements", "annex", THIRD),
        ("d2", "P100002", "Project Components", "narrative", MEASURE + " Two."),
        ("d2", "P100002", "Annex 4 Climate", "annex", THIRD + " Again."),
        ("d3", "P100003", "Fiduciary", "narrative", OTHER + " More."),
    ])


def test_resolves_text_and_links_projects(tmp_path, capsys):
    data = corpus_fixture(tmp_path)
    paras = bs.load_corpus(str(data), lambda m: None)
    assert len(paras) == 6
    by_id = {p["paragraph_id"]: p for p in paras}
    assert by_id["d1:p000"]["text"] == MEASURE
    assert by_id["d1:p000"]["project_id"] == "P100001"
    assert by_id["d1:p002"]["block_type"] == "annex"


def test_moved_clean_file_aborts_rather_than_yielding_wrong_text(tmp_path):
    data = corpus_fixture(tmp_path)
    p = data / "clean" / "d1.txt"
    p.write_text("x" + p.read_text())          # shift every offset by one
    with pytest.raises(SystemExit) as e:
        bs.load_corpus(str(data), lambda m: None)
    assert "text_sha256" in str(e.value)


def test_out_of_scope_and_non_prose_are_dropped(tmp_path):
    data = write_corpus(tmp_path, [
        ("d1", "P1", "Components", "narrative", MEASURE),
        ("d1", "P1", "Table", "table", OTHER),
    ])
    rows = list(csv.DictReader(open(data / "intermediate" / "prepared" / "paragraphs.csv")))
    rows[0]["in_scope"] = "false"
    with open(data / "intermediate" / "prepared" / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    assert bs.load_corpus(str(data), lambda m: None) == []


def test_tracker_columns_are_found_by_name_not_position(tmp_path):
    a = tmp_path / "a.xlsx"; b = tmp_path / "b.xlsx"
    entries = [("P100001", MEASURE, {"Resilient telecom": "Yes"})]
    write_tracker(a, entries)
    write_tracker(b, entries, extra_col=True)
    ra = bs.read_tracker(str(a), lambda m: None)
    rb = bs.read_tracker(str(b), lambda m: None)
    assert ra == rb == [("P100001", MEASURE, ["Resilient telecom"])]


def test_tracker_categories_stop_at_the_mitigation_flag(tmp_path):
    path = tmp_path / "t.xlsx"
    write_tracker(path, [("P100001", MEASURE, {"Climate applications1": "EWS"})])
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    ws.cell(2, ws.max_column + 1, "EE telecom infra")     # a mitigation column
    ws.cell(3, ws.max_column, "Yes")
    wb.save(path)
    _, _, cats = bs.read_tracker(str(path), lambda m: None)[0]
    assert cats == ["Climate applications1"]


def test_match_finds_the_right_paragraph_and_not_the_others(tmp_path):
    data = corpus_fixture(tmp_path)
    paras = bs.load_corpus(str(data), lambda m: None)
    hits = bs.match_excerpts(
        [("P100001", MEASURE, ["Resilient telecom"])], paras, lambda m: None)
    assert set(hits) == {"d1:p000"}
    assert hits["d1:p000"] == (["Resilient telecom"], False)


def test_match_does_not_cross_project_boundaries(tmp_path):
    data = corpus_fixture(tmp_path)
    paras = bs.load_corpus(str(data), lambda m: None)
    hits = bs.match_excerpts([("P100002", MEASURE, [])], paras, lambda m: None)
    assert all(pid.startswith("d2:") for pid in hits)


def test_uncategorised_excerpts_are_flagged_for_priority(tmp_path):
    data = corpus_fixture(tmp_path)
    paras = bs.load_corpus(str(data), lambda m: None)
    hits = bs.match_excerpts([("P100001", MEASURE, [])], paras, lambda m: None)
    assert hits["d1:p000"][1] is True


def test_category_hint_maps_to_asset_and_direction():
    assert bs.hint_for(["Resilient telecom"]) == ("telecom network", "resilience_of_asset")
    assert bs.hint_for(["Climate applications1"])[1] == "digital_for_resilience"
    assert bs.hint_for(["Something nobody defined"]) == ("", "")


def test_slices_are_disjoint_and_the_run_is_deterministic(tmp_path):
    data = corpus_fixture(tmp_path)
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {"Resilient telecom": "Yes"}),
                            ("P100002", THIRD + " Again.", {})])
    outs = []
    for i in range(2):
        out = tmp_path / f"o{i}.xlsx"
        bs.build(args(data=str(data), tracker=str(tracker), out=str(out),
                      targeted_sections="cyber|climate"), lambda m: None)
        wb = openpyxl.load_workbook(out)
        rows = [[c.value for c in r] for r in wb["paragraphs"].iter_rows(min_row=2)]
        outs.append(rows)
    assert outs[0] == outs[1], "same seed must give the same sample"
    ids = [r[0] for r in outs[0]]
    assert len(ids) == len(set(ids)), "a paragraph appears in two slices"
    assert {r[4] for r in outs[0]} <= {"flagged", "targeted", "random"}


def test_workbook_ids_join_back_and_rows_carry_the_hints(tmp_path):
    data = corpus_fixture(tmp_path)
    tracker = tmp_path / "t.xlsx"
    write_tracker(tracker, [("P100001", MEASURE, {"Resilient telecom": "Yes"})])
    out = tmp_path / "o.xlsx"
    bs.build(args(data=str(data), tracker=str(tracker), out=str(out),
                  flagged=1, targeted=0, random_n=1), lambda m: None)
    wb = openpyxl.load_workbook(out)
    pids = {r[0].value for r in wb["paragraphs"].iter_rows(min_row=2)}
    assert wb["paragraphs"].max_column == 8
    rows = [[c.value for c in r] for r in wb["rows"].iter_rows(min_row=2)]
    assert rows, "no blank annotation rows were written"
    assert {r[0] for r in rows} <= pids, "a rows entry has no paragraph"
    assert [c.value for c in wb["rows"][1]] == bs.ROW_COLUMNS
    flagged = [r for r in rows if r[0] == "d1:p000"]
    assert flagged and flagged[0][6] == "telecom network"
    assert flagged[0][5] == "resilience_of_asset"
    assert all(r[1] for r in rows), "the locator preview is empty"
    assert all(r[2] in (None, "") for r in rows), "kind must start blank"


def test_blank_row_counts_follow_the_slice(tmp_path):
    data = corpus_fixture(tmp_path)
    out = tmp_path / "o.xlsx"
    bs.build(args(data=str(data), out=str(out), targeted=0, random_n=2,
                  rows_random=2), lambda m: None)
    wb = openpyxl.load_workbook(out)
    rows = [[c.value for c in r] for r in wb["rows"].iter_rows(min_row=2)]
    assert len(rows) == 4


def test_missing_tracker_skips_the_flagged_slice(tmp_path, capsys):
    data = corpus_fixture(tmp_path)
    out = tmp_path / "o.xlsx"
    bs.build(args(data=str(data), out=str(out), targeted=0, random_n=2),
             lambda m: print(m))
    assert "SKIPPED" in capsys.readouterr().out
    wb = openpyxl.load_workbook(out)
    assert {r[4].value for r in wb["paragraphs"].iter_rows(min_row=2)} == {"random"}
