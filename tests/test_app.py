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
    base = app.projects(q)
    assert "P1" in base["project_id"].tolist() and base["place"].notna().all()
    assert app.projects(q, cohort="no such set").empty
    s = app.sentences(q, df["commitment_id"])
    assert len(s) >= 1 and set(s["commitment_id"]) == set(df["commitment_id"])
    assert app.sentences(q, []).empty
    t = app.tally(df, "measure_name", unit="projects", base=base)
    assert t["n"].tolist() == [1] and t["of"].tolist() == [len(base)]


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


def test_tally_counts_a_project_once_and_takes_its_share_of_the_right_base():
    df = pd.DataFrame({"project_id": ["A", "A", "A", "B"], "measure_name": ["m1", "m1", "m2", "m1"],
                       "region": ["North", "North", "North", "South"]})
    base = pd.DataFrame({"project_id": ["A", "B", "C", "D"], "region": ["North", "South", "South", "South"]})
    c = app.tally(df, "measure_name")
    assert c[["measure_name", "n"]].values.tolist() == [["m1", 3], ["m2", 1]] and c["share"].isna().all()
    p = app.tally(df, "measure_name", unit="projects", base=base)
    assert p[["measure_name", "n", "of"]].values.tolist() == [["m1", 2, 4], ["m2", 1, 4]]
    assert p["share"].tolist() == [0.5, 0.25]
    # Grouped by something about the project, the base is the projects sharing it.
    r = app.tally(df, "region", unit="projects", base=base).set_index("region")
    assert r.loc["North", ["n", "of"]].tolist() == [1, 1] and r.loc["South", ["n", "of"]].tolist() == [1, 3]
    both = app.tally(df, "measure_name", "region", unit="projects", base=base)
    assert both[both["region"] == "South"]["of"].tolist() == [3]
    assert app.tally(df.iloc[0:0], "measure_name", unit="projects", base=base).empty
