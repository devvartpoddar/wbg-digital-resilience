"""Package assets with a second look, the strong package-measure question, the
asset-level links and the table they publish (src/wbg_portfolio/second.py,
checks.py, decide.py). The fake decision and chat models only."""

import pytest

from helpers import QUIET, count, first_pass
from wbg_portfolio import checks
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import funnel
from wbg_portfolio import load_pg
from wbg_portfolio import second

NAMES = ["cra"]


@pytest.fixture
def asked_where_it_decides(monkeypatch):
    """The package asset second look asked only for first-pass P(yes) in [link.asset_band_low, link.asset_yes)."""
    from wbg_portfolio import panel
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "link.asset_ask_low": live()["link.asset_band_low"],
                                                    "link.asset_ask_high": live()["link.asset_yes"]})


def ready(pg, corpus):
    c = first_pass(pg, corpus, 0.95, units=(0, 1))
    funnel.run(pg, corpus, "assets", corpus=c, api_key="k", **QUIET)
    second.digital(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert checks.package_assets(pg, corpus, c, api_key="k", **QUIET) is not None
    return c


def set_first_pass(pg, package, asset, score):
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = %s WHERE kind = 'package_asset' AND item_id = %s "
               f"AND label_id = %s", (score, package, asset))
    pg.commit()


def test_no_commitment_to_package_question_is_asked(pg, corpus, fake_model):
    ready(pg, corpus)
    assert count(pg, "package_asset") == 4 and count(pg, "link") == 0
    assert not any("carrying out" in json_q for b in fake_model for json_q in
                   (q["instructions"] for q in b["questions"].values()))


def test_a_package_asset_in_the_band_needs_the_chat_models_yes(pg, corpus, fake_chat, asked_where_it_decides):
    c = ready(pg, corpus)
    set_first_pass(pg, "P1-1", "fiber", 0.4)
    assert decide.package_asset_sets(pg) == {"P1-1": {"towers"}}
    assert second.package_asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1
    assert len(fake_chat.calls) == 1                                    # only the pair in the band
    prompt = fake_chat.prompts()[0]
    assert "Supply of towers" in prompt and "[category]\nGoods" in prompt and "Fibre networks" in prompt
    assert decide.package_asset_sets(pg) == {"P1-1": {"towers", "fiber"}}
    second.package_asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_chat.calls) == 1                                    # a rerun asks nothing
    # a no, an unreadable reply and a missing look are never a yes
    for reply in ('{"answer": "no"}', "yes please"):
        pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'package_asset_second'")
        pg.execute(f"DELETE FROM {db.STORE}.answers WHERE model = 'anthropic/claude-haiku-5.5'")
        pg.commit()
        fake_chat.reply = lambda p, r=reply: r
        second.package_asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
        assert decide.package_asset_sets(pg) == {"P1-1": {"towers"}}


def test_the_package_pair_above_the_yes_line_is_asked_and_moving_the_line_up_asks_nothing(
        pg, corpus, fake_chat, monkeypatch):
    from wbg_portfolio import panel
    c = ready(pg, corpus)
    set_first_pass(pg, "P1-1", "fiber", 0.8)                            # above link.asset_yes (0.67)
    fake_chat.reply = lambda p: '{"answer": "no"}'
    assert second.package_asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) == 2
    assert decide.package_asset_sets(pg) == {"P1-1": {"towers", "fiber"}}      # a stored no changes nothing at 0.8
    n = len(fake_chat.calls)
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "link.asset_yes": 0.9})
    assert decide.package_asset_sets(pg) == {"P1-1": {"towers"}}               # now in the band: the chat no counts
    fake_chat.reply = lambda p: '{"answer": "yes"}'
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'package_asset_second' AND label_id = 'fiber'")
    pg.execute(f"DELETE FROM {db.STORE}.answers WHERE model = 'anthropic/claude-haiku-5.5'")
    pg.commit()
    second.package_asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert decide.package_asset_sets(pg) == {"P1-1": {"towers", "fiber"}}
    assert len(fake_chat.calls) > n                                     # (that last run re-asked: the answers were deleted)
    n = len(fake_chat.calls)
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "link.asset_yes": 0.95})
    decide.package_asset_sets(pg)
    assert second.package_asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET) is not None
    assert len(fake_chat.calls) == n                                    # a decision line moved: nothing asked


def test_a_dry_run_asks_nothing(pg, corpus, fake_chat):
    c = ready(pg, corpus)
    set_first_pass(pg, "P1-1", "fiber", 0.4)
    assert second.package_asset_second(pg, corpus, c, NAMES, dry_run=True, **QUIET) is None
    assert not fake_chat.calls and decide.package_asset_sets(pg) == {"P1-1": {"towers"}}


def test_the_strong_question_is_asked_of_every_package_with_or_without_an_asset(pg, corpus, fake_model):
    c = ready(pg, corpus)
    assert decide.package_asset_sets(pg) == {"P1-1": {"towers"}}          # the roads package has no asset
    n = len(fake_model)
    assert checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET) == 2
    asked = [b for b in fake_model[n:]]
    assert sorted(b["state"]["package"] for b in asked) == ["Consulting on roads", "Supply of towers"]
    q = list(asked[0]["questions"].values())[0]["instructions"]
    assert q.startswith("Does Hazard assessment apply to the procurement package in the 'package' field?")
    assert "named or clearly implied in the package's own description" in q
    assert decide.package_measure_scores(pg)[("cra", "P1-1")] >= 0.7 > decide.package_measure_scores(pg)[("cra", "P1-2")]
    n = len(fake_model)
    checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_model) == n                                          # stored: nothing asked again
    assert checks.package_measure(pg, corpus, c, NAMES, dry_run=True, **QUIET) is None and len(fake_model) == n


def test_a_package_is_asked_only_its_likeliest_measures_and_what_is_stored_stays(pg, corpus, fake_model, monkeypatch):
    from wbg_portfolio import panel
    c = ready(pg, corpus)
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "link.package_measure_top": 0})
    n = len(fake_model)
    assert checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET) == 0 and len(fake_model) == n
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "link.package_measure_top": 1})
    assert checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET) == 2
    n = len(fake_model)
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "link.package_measure_top": 0})
    assert checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET) == 2      # kept from the store
    assert len(fake_model) == n


def test_a_package_with_no_asset_can_have_a_strong_link(pg, corpus, fake_model):
    c = ready(pg, corpus)
    u0 = c.index[0]["unit_id"]
    checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET)
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = 0.9 WHERE kind = 'package_measure' AND item_id = 'P1-2'")
    pg.commit()
    publish(pg, corpus, c)
    assert "P1-2" not in decide.package_asset_sets(pg)
    assert links(pg)[(u0, "P1-2")][0] == "strong" and not links(pg)[(u0, "P1-2")][1]


def test_a_measure_with_no_committed_digital_sentence_in_the_project_is_not_asked(pg, corpus, fake_model):
    c = first_pass(pg, corpus, 0.95, units=(0,))
    funnel.run(pg, corpus, "assets", corpus=c, api_key="k", **QUIET)
    checks.package_assets(pg, corpus, c, api_key="k", **QUIET)         # digital never asked: not digital
    n = len(fake_model)
    assert checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET) == 0
    assert len(fake_model) == n


def publish(pg, corpus, c):
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)


def links(pg):
    return {(u, p): (s, sh, sp) for u, p, s, sh, sp in db.rows(
        pg, f"SELECT unit_id, package_id, strength, shared_asset_ids, strong_p FROM {db.SCHEMA}.commitment_package_links")}


def test_strong_and_asset_links_are_one_row_per_pair(pg, corpus, fake_chat):
    c = ready(pg, corpus)
    u0, u1 = c.index[0]["unit_id"], c.index[1]["unit_id"]
    set_first_pass(pg, "P1-1", "fiber", 0.4)
    second.package_asset_second(pg, corpus, c, NAMES, api_key="k", **QUIET)
    checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET)
    publish(pg, corpus, c)
    got = links(pg)
    # strong and sharing an asset: one row, strong, shared assets filled
    assert got[(u0, "P1-1")][0] == "strong" and got[(u0, "P1-1")][1] == ["towers"]
    assert got[(u1, "P1-1")][0] == "strong" and got[(u1, "P1-1")][1] == ["fiber"]
    assert float(got[(u0, "P1-1")][2]) >= 0.7 and set(got) == {(u0, "P1-1"), (u1, "P1-1")}
    assert db.one(pg, f"SELECT package_category FROM {db.SCHEMA}.commitment_package_links LIMIT 1")[0] == "Goods"
    v = db.rows(pg, f"SELECT unit_id, strength, package_description FROM {db.SCHEMA}.commitment_package_links_in_context")
    assert {(u, s) for u, s, _ in v} == {(u0, "strong"), (u1, "strong")}
    # below link.strong_yes the same pairs are asset-level links: the label says only "same asset"
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = 0.5 WHERE kind = 'package_measure'")
    pg.commit()
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    got = links(pg)
    assert got[(u0, "P1-1")][0] == "asset" and got[(u1, "P1-1")][0] == "asset"
    assert float(got[(u0, "P1-1")][2]) == 0.5
    # without the chat model's yes on the band, the fibre sentence shares nothing with the package
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'package_asset_second'")
    pg.commit()
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    assert set(links(pg)) == {(u0, "P1-1")}


def test_the_views_the_app_reads_carry_both_kinds_of_link(pg, corpus, fake_chat):
    """commitment_package_status (read by app.packages and app.coming_up) is fed by the links: strength
    strong with the strong-link probability, or asset with none."""
    c = ready(pg, corpus)
    checks.package_measure(pg, corpus, c, NAMES, api_key="k", **QUIET)
    publish(pg, corpus, c)
    q = f"SELECT package_id, strength, link_probability, units_linked FROM {db.SCHEMA}.commitment_package_status"
    got = db.rows(pg, q)
    assert {(p, s, n) for p, s, _, n in got} == {("P1-1", "strong", 1)} and len(got) == 2   # a commitment each
    assert all(float(pr) >= 0.7 for _, _, pr, _ in got)
    link_floor, pair_floor = db.one(pg, f"SELECT package_link_min_probability, pair_check_floor "
                                        f"FROM {db.SCHEMA}.dashboard_settings")
    assert float(link_floor) == 0.7 and pair_floor is None
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = 0.5 WHERE kind = 'package_measure'")
    pg.commit()
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    assert db.rows(pg, q) == [("P1-1", "asset", None, 1)]      # the tower sentence shares an asset; the fibre one does not
    assert db.one(pg, f"SELECT measures_linked, commitments_linked, best_commitment_probability "
                      f"FROM {db.SCHEMA}.packages_with_commitments WHERE package_id = 'P1-1'") == (["cra"], 1, None)
    assert db.one(pg, f"SELECT count(*), min(strength) FROM {db.SCHEMA}.commitment_packages_in_context") == (1, "asset")
