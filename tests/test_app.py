"""What the Shiny app reads (src/wbg_portfolio/app.py), end to end on the
invented corpus: every function runs against the views a publish builds."""
import pandas as pd

from wbg_portfolio import app
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import load_pg

from test_decide import run_all


def _q(pg):
    def q(sql, params=None):
        cur = pg.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    return q


def _published(pg, corpus):
    c = run_all(pg, corpus)
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    return _q(pg)


def test_the_app_reads_commitments_with_stage_place_and_evidence(pg, corpus, fake_model):
    q = _published(pg, corpus)
    df = app.commitments(q)
    assert list(df[["project_id", "label_set", "asset_id", "type_id"]].itertuples(index=False, name=None)) == \
        [("P1", "cra", "towers", "site")]
    assert df["stage"].iloc[0] in app.STAGES and df["place"].iloc[0]
    assert app.commitments(q, cohort="no such set").empty
    ev = app.evidence(q, df["commitment_id"])
    assert len(ev) == 1 and ev["text"].iloc[0] and ev["commitment_id"].iloc[0] == df["commitment_id"].iloc[0]
    assert app.evidence(q, []).empty
    h = app.headline(df)
    assert h["commitments"] == 1 and h["projects"] == 1 and h["measures"] == 1
    b = app.board(df)
    assert b["project_id"].tolist() == ["P1"] and b["commitments"].tolist() == [1]
    assert app.count(df, "measure_name")["commitments"].tolist() == [1]


def test_the_app_reads_measures_types_assets_and_a_project(pg, corpus, fake_model):
    q = _published(pg, corpus)
    m = app.measures(q)
    assert "cra" in m["label_set"].tolist()
    assert set(app.measure_types(q, "cra")["type_id"]) >= {"site"}
    assert {"towers", "fiber"} <= set(app.assets(q)["asset_id"])
    p = app.project(q, "P1")
    assert p["header"]["project_id"].tolist() == ["P1"]
    assert p["packages"]["package_id"].tolist() == ["P1-1"] and p["packages"]["stage"].iloc[0] in app.STAGES
    assert {"assets", "components", "documents"} <= set(p)


def test_the_frame_helpers_take_an_empty_frame():
    empty = pd.DataFrame(columns=["commitment_id", "project_id", "project_name", "place", "stage", "type_name",
                                  "label_set", "country_code", "is_regional"])
    assert app.headline(empty)["commitments"] == 0
    assert app.board(empty).empty and app.count(empty, "stage").empty
    assert db.SCHEMA                                                                # the store under test
