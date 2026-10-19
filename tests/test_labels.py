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
