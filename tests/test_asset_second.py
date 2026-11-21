"""The second look at the assets and their types (src/wbg_portfolio/second.py):
asset_second for a committed, digital sentence the first pass gave no asset,
asset_subtype for every asset that counts. No model is called: the fake decision and chat models."""
import json

import pytest

from helpers import QUIET, count, first_pass
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import funnel
from wbg_portfolio import labels
from wbg_portfolio import load_pg
from wbg_portfolio import questions as Q
from wbg_portfolio import second

NAMES = ["cra"]


@pytest.fixture
def only_where_none(monkeypatch):
    """asset.ask_high at asset.floor: the second look is asked only of a sentence with no first-pass asset."""
    from wbg_portfolio import panel
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "asset.ask_high": live()["asset.floor"]})


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


def test_the_real_taxonomy_has_four_assets_each_with_types_and_no_cybersecurity():
    real = labels.LABEL_DIRS[0]
    assert real.endswith("taxonomy")
    assets = [a["id"] for a in labels.load_set("assets", taxonomy=real)]
    sub = labels.load_subtypes("assets", taxonomy=real)
    assert assets == ["connectivity", "hosting", "platforms", "monitoring_control"] and set(sub) == set(assets)
    assert all(rows[-1]["id"] == "unspecified" for rows in sub.values())
    assert "cybersecurity" not in assets and not any("cyber" in r["id"] for rows in sub.values() for r in rows)


FIBRE = '{"labels": ["fiber"]}'


def test_the_second_look_asks_only_sentences_with_no_first_pass_asset(pg, corpus, fake_chat, only_where_none):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: FIBRE
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1
    assert len(fake_chat.calls) == 1
    prompt = fake_chat.prompts()[0]
    assert "fibre backbone" in prompt and "[context]" in prompt and "[section]" in prompt
    assert "- towers: Mobile networks: masts" in prompt and "- fiber:" in prompt and Q.ASSET_RULE in prompt
    assert "backbone" not in prompt.split("[section]")[0] and "kind" not in prompt.lower()   # the type is asked after
    row = db.one(pg, f"SELECT item_id, label_id, score, answer FROM {db.SCHEMA}.scores WHERE kind = 'asset_second'")
    assert row[0] == u1 and row[1] == "second" and float(row[2]) == 1 and row[3]["labels"] == ["fiber"]
    # a rerun asks nothing
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1 and len(fake_chat.calls) == 1


def test_it_asks_again_only_for_what_is_missing(pg, corpus, fake_chat, only_where_none):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: '{"labels": []}'
    second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_chat.calls) == 1
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'asset' AND item_id = %s", (u0,))
    pg.commit()
    second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_chat.calls) == 2 and u0 in fake_chat.prompts()[1] or "tower" in fake_chat.prompts()[1]
    assert count(pg, "asset_second") == 2


def test_an_asset_the_files_do_not_have_is_unreadable_and_never_stored(pg, corpus, fake_chat, only_where_none):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: json.dumps({"labels": ["cybersecurity", "fiber"]})
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 0
    assert count(pg, "asset_second") == 0 and decide.asset_second_looks(pg) == {}


def test_none_is_a_valid_answer_and_an_old_asset_type_answer_reads_as_its_asset(pg, corpus, fake_chat, only_where_none):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: '{"labels": []}'
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1
    assert decide.asset_second_looks(pg) == {}
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET answer = %s WHERE kind = 'asset_second'",
               (json.dumps({"labels": ["fiber/backbone", "fiber/unspecified"]}),))
    pg.commit()
    assert decide.asset_second_looks(pg) == {u1: ["fiber"]}


def test_a_malformed_reply_is_never_a_yes_and_a_repeated_id_is_read_once(pg, corpus, fake_chat):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: "fiber"
    second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert count(pg, "asset_second") == 0 and decide.asset_second_looks(pg) == {}
    fake_chat.reply = lambda p: json.dumps({"labels": ["Fiber", "towers", "fiber"]})
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 2
    assert set(map(tuple, decide.asset_second_looks(pg).values())) == {("fiber", "towers")}


def test_the_type_is_asked_for_every_asset_that_counts_whichever_look_found_it(pg, corpus, fake_chat, only_where_none):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    assert second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1      # the first pass's asset
    prompt = fake_chat.prompts()[0]
    assert "Which type of Mobile networks is the sentence in the 'sentence' field about?" in prompt
    assert "- mast: Masts\n- unspecified: not stated" in prompt and "tall masts" not in prompt
    assert decide.asset_types(pg) == {(u0, "towers"): "mast"}
    second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_chat.calls) == 1
    # the second look finds fibre for the other sentence: its type is asked too
    fake_chat.reply = lambda p: FIBRE if '"labels"' in p else '{"answer": "backbone"}'
    second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET) == 2
    assert decide.asset_types(pg) == {(u0, "towers"): "mast", (u1, "fiber"): "backbone"}
    # a type that is not one of the asset's is not stored
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'asset_subtype'")
    pg.execute(f"DELETE FROM {db.STORE}.answers WHERE model = 'anthropic/claude-haiku-5.5'")
    pg.commit()
    fake_chat.reply = lambda p: FIBRE if '"labels"' in p else '{"answer": "mast"}'
    second.asset_subtype(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert decide.asset_types(pg) == {(u0, "towers"): "mast"}


def test_nothing_is_asked_on_a_dry_run(pg, corpus, fake_chat):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    assert second.asset_second(pg, corpus, c, NAMES, dry_run=True, **QUIET) is None
    assert second.asset_subtype(pg, corpus, c, NAMES, dry_run=True, **QUIET) is None
    assert not fake_chat.calls


def test_the_levels_and_kinds_reach_unit_assets(pg, corpus, fake_chat, fake_model):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: (FIBRE if '"labels"' in p else
                                 '{"answer": "backbone"}' if "backbone" in p else '{"answer": "mast"}')
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
    # the chat model's asset is published beside a first-pass one, so agreement can be measured
    agree = dict(db.rows(pg, f"SELECT unit_id, second_look_asset_id FROM {db.SCHEMA}.unit_assets "
                             f"WHERE asset_id <> ''"))
    assert agree == {u0: "fiber", u1: "fiber"}
    # a type is readable by name: its table, and beside the ids in the asset map
    names = {(a, k): n for a, k, n in db.rows(
        pg, f"SELECT asset_id, subtype_id, subtype_name FROM {db.SCHEMA}.asset_subtypes")}
    assert names[("fiber", "backbone")] and names[("towers", "mast")]
    mapped = {(u, a): n for u, a, n in db.rows(
        pg, f"SELECT unit_id, asset_id, subtype_name FROM {db.SCHEMA}.asset_map")}
    assert mapped[(u1, "fiber")] == names[("fiber", "backbone")]
    assert {f for (f,) in db.rows(pg, f"SELECT flag_id FROM {db.SCHEMA}.flags")} == {"digital", "policy"}
    af = {u: lvl for u, lvl in db.rows(pg, f"SELECT unit_id, linked_asset_level FROM {db.SCHEMA}.measure_findings")}
    assert af[u1] == "second_look"
    # the second look's asset counts on the dashboards at the level the panel allows
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.commitment_units WHERE unit_id = %s AND asset_id = 'fiber' "
                      f"AND asset_from = 'second_look'", (u1,))[0] == 1


def test_the_asset_second_look_is_asked_also_under_asset_ask_high_and_never_changes_the_decision(
        pg, corpus, fake_chat, fake_model, monkeypatch):
    c, u0, u1 = ready(pg, corpus, fake_chat)
    fake_chat.reply = lambda p: FIBRE
    from wbg_portfolio import panel
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "asset.ask_high": 0.8})   # only where none at the floor
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "asset.ask_high": 1.01})  # every committed digital one
    assert second.asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 2
    assert len(fake_chat.calls) == 2                                                    # u1 was not asked again
    own = decide.asset_map(pg, c)
    assert decide.unit_assets(c, own, decide.asset_second_looks(pg))[u0][1][0][0] == "towers"   # first pass wins
    # moving asset.floor up at publish asks nothing: the chat model's opinion is already there
    n = len(fake_chat.calls)
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "asset.floor": 0.99})
    lvl = decide.unit_assets(c, own, decide.asset_second_looks(pg))
    assert lvl[u0][1] == [("fiber", None)] and lvl[u0][2] == "second_look" and len(fake_chat.calls) == n
