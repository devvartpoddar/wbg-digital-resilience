"""The loader shows a measure's types as the label file says now: a type
merged into another is folded into it, a display_name renames it, and the
asset groups ride along, all without a model call."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import labels  # noqa: E402
import load_pg as L  # noqa: E402


def test_assignments_are_folded_in_place_and_best_type_follows():
    rows = [{"label_set": "cra", "label_id": "cra", "unit_id": "u1",
             "types_yes": "contractor|site|mapping", "best_type": "contractor"},
            {"label_set": "cra", "label_id": "cra", "unit_id": "u2", "types_yes": "",
             "best_type": "mapping"},
            {"label_set": "ews", "label_id": "ews", "unit_id": "u3", "types_yes": "alerts",
             "best_type": "alerts"}]
    mapping = {"cra": {("cra", "contractor"): "site", ("cra", "site"): "site",
                       ("cra", "mapping"): "mapping"}, "ews": {}}
    L.apply_type_resolution(rows, mapping)
    assert rows[0]["types_yes"] == "site|mapping" and rows[0]["best_type"] == "site"
    assert rows[1]["types_yes"] == "" and rows[1]["best_type"] == "mapping"
    assert rows[2]["types_yes"] == "alerts"


def test_type_and_group_rows_come_from_the_label_files(tmp_path, monkeypatch):
    a = tmp_path / "labels"
    a.mkdir()
    (a / "assets.csv").write_text("asset_id,asset_name,definition,active\n"
                                  "fiber,Fiber,cables,true\ntowers,Towers,masts,true\n",
                                  encoding="utf-8")
    (a / "assets_groups.csv").write_text("asset_id,group_id,group_name,active\n"
                                         "fiber,networks,Networks and connectivity,true\n"
                                         "towers,networks,Networks and connectivity,true\n",
                                         encoding="utf-8")
    (a / "cra.csv").write_text("measure_id,measure_name,definition,active\n"
                               "cra,Climate risk assessment,an assessment,true\n",
                               encoding="utf-8")
    (a / "cra_types.csv").write_text(
        "measure_id,type_id,type_name,definition,active,merge_into,display_name\n"
        "cra,site,Site-specific assessment,of one site,true,,Site assessment\n"
        "cra,contractor,Assessment required of contractors,by the contractor,true,site,\n",
        encoding="utf-8")
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(a),))
    assert L.type_rows() == [{"label_set": "cra", "label_id": "cra", "type_id": "site",
                              "type_name": "Site assessment", "definition": "of one site",
                              "merged_from": "contractor"}]
    assert L.group_rows() == [
        {"label_set": "assets", "label_id": "fiber", "group_id": "networks",
         "group_name": "Networks and connectivity"},
        {"label_set": "assets", "label_id": "towers", "group_id": "networks",
         "group_name": "Networks and connectivity"}]


def test_the_repository_groups_cover_every_active_asset_once():
    assets = {r["id"] for r in labels.load_set("assets") if r["active"] == "true"}
    groups = labels.load_groups("assets")
    assert {g["label_id"] for g in groups} == assets
    assert len(groups) == len(assets)
    assert len({g["group_id"] for g in groups}) == 6
