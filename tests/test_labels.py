import os, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import labels  # noqa: E402


def _set(folder, name, rows):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.csv").write_text("x_id,x_name,definition,active\n" + rows, encoding="utf-8")


def test_sets_are_found_in_both_folders_and_a_clash_is_an_error(tmp_path):
    a, b = tmp_path / "inputs", tmp_path / "analysis"
    _set(a, "assets", "fiber,Fiber,cables,true\n")
    _set(b, "measures", "hard,Hardening,walls,true\n")
    (b / "measures_queries.csv").write_text("x_id,query_id,query,active\n", encoding="utf-8")
    assert labels.label_sets((str(a), str(b))) == ["assets", "measures"]
    assert labels.folder("measures", (str(a), str(b))) == str(b)
    _set(b, "assets", "fiber,Fiber,cables,true\n")
    with pytest.raises(SystemExit, match="both"):
        labels.label_sets((str(a), str(b)))
    with pytest.raises(SystemExit, match="no label set"):
        labels.folder("nothing", (str(a), str(b)))


def test_a_set_with_no_active_label_is_switched_off(tmp_path):
    a = tmp_path / "analysis"
    _set(a, "kept", "x,X,a measure,true\n")
    _set(a, "off", "y,Y,an old measure,false\n")
    assert labels.label_sets((str(a),)) == ["kept"]
    assert labels.label_sets((str(a),), include_inactive=True) == ["kept", "off"]


def test_load_set_keeps_inactive_labels_and_names_the_folder(tmp_path):
    a = tmp_path / "inputs"
    _set(a, "assets", "fiber,Fiber,cables  and  ducts,true\nold,Old,gone,false\n")
    rows = labels.load_set("assets", (str(a),))
    assert [(r["id"], r["active"]) for r in rows] == [("fiber", "true"), ("old", "false")]
    assert rows[0]["definition"] == "cables and ducts"


def test_the_repository_sets_are_the_assets_and_the_analysis_measures():
    sets = labels.label_sets()
    assert sets[0] == "assets"
    assert labels.folder("assets").endswith(os.path.join("inputs", "taxonomy"))
    for name in sets[1:]:
        assert labels.folder(name).endswith(os.path.join("analysis", "labels"))


def _types(folder, name, rows, extra_cols=()):
    folder.mkdir(parents=True, exist_ok=True)
    cols = "x_id,type_id,type_name,definition,active" + "".join("," + c for c in extra_cols)
    (folder / f"{name}_types.csv").write_text(cols + "\n" + rows, encoding="utf-8")


def test_a_merged_type_is_shown_as_its_target_and_a_display_name_renames_for_free(tmp_path):
    a = tmp_path / "analysis"
    _set(a, "cra", "cra,Climate risk assessment,an assessment,true\n")
    _types(a, "cra",
           "cra,site,Site-specific assessment,of one site,true,,Site assessment\n"
           "cra,contractor,Assessment required of contractors,by the contractor,true,site,\n"
           "cra,mapping,Hazard mapping,maps,true,,\n"
           "cra,old,Old type,gone,false,site,\n",
           extra_cols=("merge_into", "display_name"))
    mapping, shown = labels.type_resolution("cra", (str(a),))
    assert mapping == {("cra", "site"): "site", ("cra", "contractor"): "site",
                       ("cra", "mapping"): "mapping"}
    assert shown[("cra", "site")] == {"type_id": "site", "type_name": "Site assessment",
                                      "definition": "of one site", "merged_from": ["contractor"]}
    assert shown[("cra", "mapping")]["type_name"] == "Hazard mapping"
    assert ("cra", "contractor") not in shown
    # the wording the model was asked with is untouched by both columns
    asked = {t["type_id"]: t["type_name"] for t in labels.load_types("cra", (str(a),))}
    assert asked["site"] == "Site-specific assessment"


def test_a_merge_into_an_unknown_or_merged_type_is_refused(tmp_path):
    a = tmp_path / "analysis"
    _set(a, "cra", "cra,Climate risk assessment,an assessment,true\n")
    _types(a, "cra", "cra,site,Site,of one site,true,nowhere\n", extra_cols=("merge_into",))
    with pytest.raises(SystemExit, match="not an active type"):
        labels.type_resolution("cra", (str(a),))
    _types(a, "cra", "cra,a,A,a,true,b\ncra,b,B,b,true,c\ncra,c,C,c,true,\n",
           extra_cols=("merge_into",))
    with pytest.raises(SystemExit, match="itself merged"):
        labels.type_resolution("cra", (str(a),))


def test_definition_rows_copy_every_phrase_pattern_and_grade():
    import load_pg
    queries, patterns, scale = load_pg.definition_rows()
    sets = set(labels.label_sets())
    assert {r["label_set"] for r in scale} == sets
    assert all("{name}" not in r["description"] and len(r["file_sha256"]) == 64 for r in scale)
    assert {r["active"] for r in queries} <= {"true", "false"}
    assert all(r["label_set"] in sets and r["pattern_id"] for r in patterns)
    ews = [r for r in scale if r["label_set"] == "ews"]
    assert [r["grade"] for r in ews] == ["0", "1", "2", "3"]


def test_a_pattern_row_may_ask_for_case(tmp_path):
    import labels
    d = tmp_path / "labels"
    d.mkdir()
    (d / "m.csv").write_text("measure_id,measure_name,definition,active\nm,M,a measure,true\n")
    (d / "m_patterns.csv").write_text(
        "measure_id,pattern_id,pattern,flags,active\n"
        "m,ups,\\bUPS\\b,CASE_SENSITIVE,true\nm,gen,\\bgenerators?\\b,IGNORECASE,true\n"
        "m,old,\\bbattery\\b,,true\n")
    got = {p["pattern_id"]: p["regex"] for p in labels.load_patterns("m", dirs=(str(d),))}
    assert got["ups"].search("UPS units") and not got["ups"].search("ups and downs")
    assert got["gen"].search("Generators") and got["old"].search("BATTERY")
    (d / "m_patterns.csv").write_text("measure_id,pattern_id,pattern,flags,active\nm,x,a,MULTILINE,true\n")
    import pytest
    with pytest.raises(SystemExit):
        labels.load_patterns("m", dirs=(str(d),))
