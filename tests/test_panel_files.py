"""The one control panel file: each setting once, every setting read in the code written, and
every description says what the setting does now."""
import csv
import os
import re

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inputs", "config")
SRC = os.path.join(os.path.dirname(CONFIG), "..", "src", "wbg_portfolio")


def shipped():
    with open(os.path.join(CONFIG, "control_panel.csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_there_is_one_panel_file_and_each_setting_is_written_once():
    assert sorted(f for f in os.listdir(CONFIG) if f.startswith("control_panel")) == ["control_panel.csv"]
    names = [r["setting"] for r in shipped()]
    assert len(names) == len(set(names))
    assert all(r["value"].strip() != "" and r["what_it_does"].strip() for r in shipped())


def test_every_setting_the_code_reads_is_written():
    written = {r["setting"] for r in shipped()}
    read = set()
    for name in os.listdir(SRC):
        if name.endswith(".py"):
            with open(os.path.join(SRC, name), encoding="utf-8") as fh:
                read |= set(re.findall(r"""(?:panel\.(?:get|require)|setting)\(\s*["']([a-z_]+\.[a-z_.]+)["']""",
                                       fh.read()))
    assert read, "the pattern found no setting"
    assert read <= written, sorted(read - written)


def test_the_retired_settings_are_gone_and_no_description_speaks_of_a_before_and_after():
    written = {r["setting"] for r in shipped()}
    gone = {"band.on", "digital.on", "link.strengths", "policy.on", "activities.on", "floor.pair_check",
            "floor.cyber_security", "floor.public_institutions", "link.measure_top",
            "dashboard.apply_measure_pair_checks", "dashboard.package_link_min_probability"}
    assert not gone & written, sorted(gone & written)
    bad = re.compile(r"\bv2\b|\bnew\b|\bold\b|retired|when on|as before", re.I)
    for r in shipped():
        assert not bad.search(r["what_it_does"]), r["setting"]
