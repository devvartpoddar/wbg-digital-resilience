"""wbg.commitment_summary, the one summary of a commitment the dashboards and the app read (the invented
dataset of tests/demo_seed.py, published with the real views)."""
import pytest

import demo_seed
from wbg_portfolio import db


@pytest.fixture
def demo(pg):
    demo_seed.publish(pg, demo_seed.Demo(seed=5, n_projects=12).build().finish_tables(), log=lambda *_: None)
    pg.commit()
    return pg


def test_asset_from_is_the_best_place_any_sentence_found_its_asset(demo):
    s = db.SCHEMA
    order = {"sentence": 0, "second_look": 1, "paragraph": 2, "none": 3}
    units = {}
    for cid, af in db.rows(demo, f"SELECT commitment_id, asset_from FROM {s}.commitment_units"):
        units.setdefault(cid, set()).add(af)
    got = dict(db.rows(demo, f"SELECT commitment_id, asset_from FROM {s}.commitment_summary"))
    assert got and set(got) == set(units)
    for cid, afs in units.items():
        assert got[cid] == min(afs, key=order.get), cid
    assert any(len(a) > 1 and "none" in a for a in units.values()), "the demo mixes a found asset and none"


def test_the_app_reads_its_commitment_figures_from_the_summary(demo):
    from test_app import _q
    from wbg_portfolio import app
    d = app.commitment_detail(_q(demo)).set_index("commitment_id")
    s = {r[0]: r[1:] for r in db.rows(demo, f"SELECT commitment_id, sentences, paragraphs, documents, linked_packages, "
                                            f"first_seen_date, next_date FROM {db.SCHEMA}.commitment_summary")}
    assert set(d.index) == set(s)
    for cid, (n, p, docs, linked, first, nxt) in s.items():
        r = d.loc[cid]
        assert (r["n_sentences"], r["n_paragraphs"], r["n_documents"], r["linked_packages"]) == (n, p, docs, linked)
        assert str(r["first_document_date"])[:10] == str(first)[:10]


def test_commitment_counts_say_whether_paragraph_groups_are_included(demo):
    from test_app import _q
    from wbg_portfolio import app
    q = _q(demo)
    m = app.measures(q)
    assert (m["commitments"] == m["keyed_commitments"] + m["paragraph_groups"]).all()
    assert m["paragraph_groups"].sum() > 0 and "graded_scope" in m.columns
    h = app.headline(app.commitments(q))
    assert h["commitments"] == h["keyed_commitments"] + h["paragraph_groups"] and h["paragraph_groups"] > 0
    proj = app.download(q, "project")
    assert (proj["commitments"] == proj["keyed_commitments"] + proj["paragraph_groups"]).all()
    notes = app.download_notes(q, "project").set_index("item")["text"]
    assert "paragraph groups included" in notes["commitments"]


def test_a_measure_page_says_when_its_precision_is_the_overall_figure(demo):
    from test_app import _q
    from wbg_portfolio import app
    s = db.SCHEMA
    demo.execute(f"INSERT INTO {s}.measure_quality (label_set, question_sha256, sheet, graded_units, precision, "
                 f"scope, evaluated_at) SELECT label_set, 'q', 'holdout', 12, 0.81, 'overall', '2030-01-01' "
                 f"FROM {s}.measure_summary LIMIT 1")
    demo.commit()
    m = app.measures(_q(demo))
    assert (m["graded_scope"] == "overall").sum() == 1
