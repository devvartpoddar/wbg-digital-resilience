"""The checks (src/checks.py) and the one decision (src/decide.py), end to
end on the invented corpus: funnel, types, pairs, links, publish, views."""
import db
import checks
import decide
import funnel
import load_pg
import questions as Q


def run_all(pg, corpus, log=lambda *_: None):
    c = Q.Corpus(corpus)
    funnel.run(pg, corpus, "assets", corpus=c, api_key="k", log=log)
    funnel.run(pg, corpus, "cra", corpus=c, api_key="k", log=log)
    checks.types(pg, corpus, c, "cra", api_key="k", log=log)
    checks.pairs(pg, corpus, c, ["cra"], api_key="k", log=log)
    checks.links(pg, corpus, c, ["cra"], api_key="k", log=log)
    return c


def test_types_are_asked_of_committed_units_with_the_sentence_state(pg, corpus, fake_model):
    c = run_all(pg, corpus)
    u0 = c.index[0]["unit_id"]
    rows = db.rows(pg, f"SELECT label_id, item_id, score FROM {db.SCHEMA}.scores WHERE kind = 'type'")
    assert {(l, u) for l, u, _ in rows} == {("site", u0), ("route", u0)}
    assert max(float(s) for l, _, s in rows if l == "site") >= 0.89 > min(float(s) for l, _, s in rows if l == "route")
    typed = [b for b in fake_model if any(q.startswith("Does Site") for q in
                                          (x["instructions"] for x in b["questions"].values()))]
    assert typed and typed[0]["state"]["section"] == "Project Components"     # the same state as the measure


def test_packages_get_their_best_assets_and_commitments_their_packages(pg, corpus, fake_model):
    run_all(pg, corpus)
    pa = db.rows(pg, f"SELECT item_id, label_id, score, rank FROM {db.SCHEMA}.scores WHERE kind = 'package_asset'")
    assert {(p, a) for p, a, _, _ in pa} == {("P1-1", "towers"), ("P1-1", "fiber"), ("P1-2", "towers"), ("P1-2", "fiber")}
    assert float(next(s for p, a, s, _ in pa if (p, a) == ("P1-1", "towers"))) >= 0.89
    links = db.rows(pg, f"SELECT item_id, route, score FROM {db.SCHEMA}.scores WHERE kind = 'link'")
    assert {r for _, r, _ in links} == {"asset", "measure"}
    assert any(i.endswith("|P1-1") and float(s) >= 0.89 for i, _, s in links)


def test_publish_writes_the_decision_tables_and_the_views_build(pg, corpus, fake_model):
    c = run_all(pg, corpus)
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    f = db.rows(pg, f"SELECT unit_id, is_committed, posterior_probability, linked_asset_id, type_ids_above_threshold "
                    f"FROM {db.SCHEMA}.measure_findings ORDER BY unit_id")
    u0 = c.index[0]["unit_id"]
    assert [r[0] for r in f] == sorted(c.index[i]["unit_id"] for i in range(len(c.index)))
    row = next(r for r in f if r[0] == u0)
    assert row[1] is True and float(row[2]) > 0.85 and row[3] == "towers" and row[4] == ["site"]
    assert all(r[1] is False for r in f if r[0] != u0)
    ua = db.rows(pg, f"SELECT unit_id, asset_id, asset_level FROM {db.SCHEMA}.unit_assets WHERE asset_level = 'unit'")
    assert set(ua) == {(u0, "towers", "unit"), (c.index[1]["unit_id"], "fiber", "unit")}
    com = db.rows(pg, f"SELECT project_id, label_set, asset_id, type_id FROM {db.SCHEMA}.commitments")
    assert com == [("P1", "cra", "towers", "site")]
    pk = db.rows(pg, f"SELECT package_id FROM {db.SCHEMA}.commitment_package_status")
    assert pk == [("P1-1",)]


def test_a_moved_floor_is_a_publish_not_a_run(pg, corpus, fake_model, monkeypatch):
    c = run_all(pg, corpus)
    n = len(fake_model)
    import panel
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "floor.default": 0.99})
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None, views=False)
    assert len(fake_model) == n
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.measure_findings WHERE is_committed")[0] == 0
