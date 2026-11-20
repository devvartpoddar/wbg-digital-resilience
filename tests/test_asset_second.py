"""The second look at the assets and their kinds (src/wbg_portfolio/second.py):
asset_second for a committed, digital sentence the first pass gave no asset,
asset_subtype for one it did. No model is called: the fake decision and chat models."""
import json


from helpers import QUIET, count, first_pass
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import funnel
from wbg_portfolio import labels
from wbg_portfolio import load_pg
from wbg_portfolio import questions as Q
from wbg_portfolio import second

NAMES = ["cra"]


def ready(pg, corpus, fake_chat):
    """Both sentences committed and digital; the fibre sentence has no first-pass asset."""
    c = first_pass(pg, corpus, 0.95, units=(0, 1))
    assert funnel.run(pg, corpus, "assets", corpus=c, api_key="k", **QUIET) is not None
    second.digital(pg, corpus, c, NAMES, api_key="k", **QUIET)
    u0, u1 = c.index[0]["unit_id"], c.index[1]["unit_id"]
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'asset' AND item_id = %s", (u1,))
    pg.commit()
    return c, u0, u1


def test_the_subtypes_file_is_read_and_checked(corpus):
    sub = labels.load_subtypes("assets")
    assert set(sub) == {"towers", "fiber"} and sub["fiber"][-1]["id"] == "unspecified"
    assert [r["id"] for r in sub["towers"]] == ["mast", "unspecified"]


def test_the_real_taxonomy_has_four_assets_each_with_kinds_and_no_cybersecurity():
    real = labels.LABEL_DIRS[0]
    assert real.endswith("taxonomy")
    assets = [a["id"] for a in labels.load_set("assets", taxonomy=real)]
    sub = labels.load_subtypes("assets", taxonomy=real)
    assert assets == ["connectivity", "hosting", "platforms", "monitoring_control"] and set(sub) == set(assets)
    assert all(rows[-1]["id"] == "unspecified" for rows in sub.values())
    assert "cybersecurity" not in assets and not any("cyber" in r["id"] for rows in sub.values() for r in rows)


def test_the_second_look_asks_only_sentences_with_no_first_pass_asset(pg, corpus, fake_chat, pipeline_on):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: '{"assets": [{"asset": "fiber", "subtype": "backbone"}]}'
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1
    assert len(fake_chat.calls) == 1
    prompt = fake_chat.prompts()[0]
    assert "fibre backbone" in prompt and "[context]" in prompt and "[section]" in prompt
    assert "- towers:" in prompt and "- fiber:" in prompt and "kind backbone" in prompt and "kind mast" in prompt
    row = db.one(pg, f"SELECT item_id, label_id, score, answer FROM {db.SCHEMA}.scores WHERE kind = 'asset_second'")
    assert row[0] == u1 and row[1] == "second" and float(row[2]) == 1 and row[3]["labels"] == ["fiber/backbone"]
    # a rerun asks nothing
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1 and len(fake_chat.calls) == 1


def test_it_asks_again_only_for_what_is_missing(pg, corpus, fake_chat, pipeline_on):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: '{"assets": []}'
    second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_chat.calls) == 1
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'asset' AND item_id = %s", (u0,))
    pg.commit()
    second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_chat.calls) == 2 and u0 in fake_chat.prompts()[1] or "tower" in fake_chat.prompts()[1]
    assert count(pg, "asset_second") == 2


def test_an_asset_or_kind_the_files_do_not_have_is_discarded_and_counted(pg, corpus, fake_chat, pipeline_on):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: json.dumps({"assets": [{"asset": "cybersecurity", "subtype": "x"},
                                                       {"asset": "fiber", "subtype": "invented"}]})
    logged = []
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", log=logged.append) == 1
    answer = db.one(pg, f"SELECT answer FROM {db.SCHEMA}.scores WHERE kind = 'asset_second'")[0]
    assert answer["labels"] == ["fiber/"] and answer["discarded"] == 1
    assert any("discarded 1 unknown or repeated asset id(s) and 1 unknown kind(s)" in line for line in logged)
    assert decide.asset_second_looks(pg) == {u1: [("fiber", "")]}


def test_a_repeated_asset_counts_once_and_none_is_a_valid_answer(pg, corpus, fake_chat, pipeline_on):
    kept, la, lk = second.valid_assets(["fiber/backbone", "fiber/unspecified", "towers/mast"],
                                       *Q.asset_catalogue())
    assert kept == [("fiber", "backbone"), ("towers", "mast")] and (la, lk) == (1, 0)
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: '{"assets": []}'
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1
    assert decide.asset_second_looks(pg) == {}


def test_more_than_two_assets_or_a_malformed_reply_is_never_a_yes(pg, corpus, fake_chat, pipeline_on):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: json.dumps({"assets": [{"asset": "fiber"}, {"asset": "towers"}, {"asset": "x"}]})
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 0
    assert count(pg, "asset_second") == 0 and decide.asset_second_looks(pg) == {}
    fake_chat.reply = lambda p: "fiber"
    second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert count(pg, "asset_second") == 0


def test_the_kind_is_asked_only_for_sentences_with_a_first_pass_asset(pg, corpus, fake_chat, pipeline_on):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    assert second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1
    assert len(fake_chat.calls) == 1 and "kind mast" not in fake_chat.prompts()[0] and "- mast:" in fake_chat.prompts()[0]
    assert decide.asset_kinds(pg) == {(u0, "towers"): "mast"}
    second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_chat.calls) == 1
    # a kind that is not one of the asset's is not stored
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'asset_subtype'")
    pg.execute(f"DELETE FROM {db.STORE}.answers WHERE model = 'anthropic/claude-haiku-5.5'")
    pg.commit()
    fake_chat.reply = lambda p: '{"answer": "backbone"}'
    second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert decide.asset_kinds(pg) == {}


def test_nothing_is_asked_while_the_band_rule_is_off_or_on_a_dry_run(pg, corpus, fake_chat, pipeline_on, monkeypatch):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    assert second.asset_second(pg, corpus, c, NAMES, dry_run=True, **QUIET) is None
    assert second.asset_subtype(pg, corpus, c, NAMES, dry_run=True, **QUIET) is None
    assert not fake_chat.calls
    from wbg_portfolio import panel
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "band.on": False})
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 0
    assert second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET) == 0
    assert not fake_chat.calls


def test_the_levels_and_kinds_reach_unit_assets(pg, corpus, fake_chat, fake_model, pipeline_on):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: ('{"assets": [{"asset": "fiber", "subtype": "backbone"}]}'
                                 if '"assets": [{"asset"' in p else '{"answer": "mast"}')
    second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET)
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    rows = {(u, a): (st, lvl, p) for u, a, st, lvl, p in db.rows(
        pg, f"SELECT unit_id, asset_id, subtype_id, asset_level, probability FROM {db.SCHEMA}.unit_assets "
            f"WHERE asset_id <> ''")}
    assert rows[(u0, "towers")][:2] == ("mast", "unit") and rows[(u0, "towers")][2] is not None
    assert rows[(u1, "fiber")] == ("backbone", "second_look", None)
    af = {u: lvl for u, lvl in db.rows(pg, f"SELECT unit_id, linked_asset_level FROM {db.SCHEMA}.measure_findings")}
    assert af[u1] == "second_look"
    # the second look's asset counts on the dashboards at the level the panel allows
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.commitment_units WHERE unit_id = %s AND asset_id = 'fiber' "
                      f"AND asset_from = 'second_look'", (u1,))[0] == 1
