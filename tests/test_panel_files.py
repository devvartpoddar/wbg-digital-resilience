"""The two control panel files cannot drift, and the shipped one behaves as the live
pipeline did: every new switch off, every old setting at its old value."""
import csv
import os

from wbg_portfolio import panel

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inputs", "config")
# the settings whose value the new behaviour changes: (shipped file, v2 file)
DIFFERENCES = {
    "band.on": ("false", "true"), "digital.on": ("false", "true"), "link.strengths": ("false", "true"),
    "policy.on": ("false", "true"), "activities.on": ("false", "true"), "floor.types": ("0.7", "0.6"),
    "asset.multiple": ("false", "true"), "dashboard.apply_measure_pair_checks": ("true", "false"),
    "dashboard.commitment_link_levels": ("unit|none", "unit|second_look|none"),
    "types.gaps": ("off", "on"),
}


def read(name):
    with open(os.path.join(CONFIG, name), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return rows, {r["setting"]: r["value"] for r in rows}


def test_the_two_files_have_the_same_settings_and_differ_only_in_the_new_behaviour():
    a_rows, a = read("control_panel.csv")
    b_rows, b = read("control_panel_v2.csv")
    assert [r["setting"] for r in a_rows] == [r["setting"] for r in b_rows]
    assert len(a) == len(a_rows), "a setting is written twice"
    assert {k for k in a if a[k] != b[k]} == set(DIFFERENCES)
    for k, (old, new) in DIFFERENCES.items():
        assert (a[k], b[k]) == (old, new), k
    # the other columns (stage, what it does) are the same too
    assert [{**r, "value": ""} for r in a_rows] == [{**r, "value": ""} for r in b_rows]


def test_the_shipped_file_keeps_the_live_values_of_the_settings_that_existed(monkeypatch):
    monkeypatch.setenv("WBG_PANEL", os.path.join(CONFIG, "control_panel.csv"))
    assert panel.get("floor.types") == 0.7 and panel.get("asset.multiple") is False
    assert panel.get("dashboard.apply_measure_pair_checks") is True
    assert panel.get("dashboard.commitment_link_levels") == "unit|none"
    for k in ("band.on", "digital.on", "link.strengths", "policy.on", "activities.on"):
        assert panel.require(k) is False, k


def test_a_relative_panel_path_is_read_from_the_repository_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WBG_PANEL", "inputs/config/control_panel_v2.csv")
    assert panel.path() == os.path.join(panel.ROOT, "inputs", "config", "control_panel_v2.csv")
    assert panel.require("band.on") is True and panel.get("floor.types") == 0.6
