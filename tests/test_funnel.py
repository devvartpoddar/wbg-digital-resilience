"""The funnel (src/funnel.py): gate, patterns, stored answers and feedback
rounds, the same for the asset map and a measure; scores written once."""
import db
import funnel
import questions as Q


def scores(pg, name):
    return {r[0]: r for r in db.rows(pg, f"SELECT item_id, label_id, score, route, round FROM "
                                         f"{db.SCHEMA}.scores WHERE label_set = %s ORDER BY item_id, label_id",
                                     (name,))}


def test_a_measure_goes_through_the_gate_and_its_rounds_and_writes_scores(pg, corpus, fake_model):
    out = funnel.run(pg, corpus, "cra", api_key="k", log=lambda *_: None)
    got = scores(pg, "cra")
    c = Q.Corpus(corpus)
    u0 = c.index[0]["unit_id"]
    assert got[u0][2] > 0.9 and got[u0][3] == "both" and got[u0][4] == 0    # pattern and cosine
    assert len(got) == len(c.index)                                           # a round read the rest
    assert all(r[3] == "feedback" and r[4] == 1 for u, r in got.items() if u != u0)
    assert out["labels"]["cra"]["yes"] == 1 and out["labels"]["cra"]["rounds"][0]["yes"] == 0


def test_the_asset_map_is_the_same_funnel_label_by_label(pg, corpus, fake_model):
    funnel.run(pg, corpus, "assets", api_key="k", log=lambda *_: None)
    got = db.rows(pg, f"SELECT label_id, item_id, score, route FROM {db.SCHEMA}.scores WHERE kind = 'asset'")
    c = Q.Corpus(corpus)
    by = {(l, u): (s, r) for l, u, s, r in got}
    assert by[("towers", c.index[0]["unit_id"])][0] > 0.9
    assert by[("towers", c.index[0]["unit_id"])][1] == "embedding"
    assert by[("fiber", c.index[1]["unit_id"])][0] > 0.9
    assert by[("fiber", c.index[0]["unit_id"])][0] < 0.1


def test_a_rerun_asks_nothing_and_rewrites_the_same_scores(pg, corpus, fake_model):
    funnel.run(pg, corpus, "cra", api_key="k", log=lambda *_: None)
    before = scores(pg, "cra")
    n = len(fake_model)
    funnel.run(pg, corpus, "cra", api_key="k", log=lambda *_: None)
    assert len(fake_model) == n and scores(pg, "cra") == before


def test_a_sentence_once_answered_stays_scored_whatever_the_gate(pg, corpus, fake_model, monkeypatch):
    import ask
    c = Q.Corpus(corpus)
    qs, _ = Q.graded_questions("cra")
    u3 = c.index[3]["unit_id"]
    ask.ask(pg, Q.MODEL, [(u3, c.state(u3), qs)], stage="t", api_key="k", log=lambda *_: None)
    import feedback
    monkeypatch.setattr(feedback, "settings", lambda prefix="": {"batch": 500, "min_yield": 10,
                                                                 "max_rounds": 0, "negative_weight": 0.5})
    funnel.run(pg, corpus, "cra", api_key="k", log=lambda *_: None)
    got = scores(pg, "cra")
    assert set(got) == {c.index[0]["unit_id"], u3} and got[u3][3] == "stored"


def test_a_dry_run_asks_nothing_and_writes_nothing(pg, corpus, fake_model):
    assert funnel.run(pg, corpus, "cra", dry_run=True, log=lambda *_: None) is None
    assert not fake_model and not db.exists(pg, f"{db.SCHEMA}.scores")


def test_a_failed_request_writes_nothing(pg, corpus, monkeypatch):
    import api
    monkeypatch.setattr(api, "post", lambda *a, **k: {"model": "m", "answers": {}})
    assert funnel.run(pg, corpus, "cra", api_key="k", log=lambda *_: None) is None
    assert not db.exists(pg, f"{db.SCHEMA}.scores")
