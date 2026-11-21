"""A sentence that passes the measure rule but is not digital is never counted as kept: not in
measure_findings.is_committed, not in a commitment (commitment_members) or a link, not in the views that
hold kept sentences, not in the readers (commitments, sentences, commitment_detail,
commitment_sentences_detail, packages_detail, families, measures, about) or the downloads (sentence,
commitment, project, package), and not in the evaluation sampler (the invented dataset of
tests/demo_seed.py, published with the real views)."""
import pytest

import demo_seed
from test_app import _q
from wbg_portfolio import app
from wbg_portfolio import db
from wbg_portfolio import sampling


@pytest.fixture
def demo(pg):
    demo_seed.publish(pg, demo_seed.Demo(seed=5, n_projects=12).build().finish_tables(), log=lambda *_: None)
    pg.commit()
    return pg


def test_a_not_digital_sentence_is_never_kept_anywhere(demo):
    pg, q = demo, _q(demo)
    S = db.SCHEMA
    nd = set(db.rows(pg, f"SELECT label_set, unit_id FROM {S}.measure_findings "
                         f"WHERE passes_measure_rule AND NOT is_digital"))
    assert nd, "the demo has sentences that pass the rule and are not digital"
    units = {u for _, u in nd}
    # the table: is_committed means kept
    assert db.one(pg, f"SELECT count(*) FROM {S}.measure_findings WHERE is_committed AND NOT is_digital")[0] == 0
    assert db.one(pg, f"SELECT count(*) FROM {S}.measure_findings WHERE is_committed AND NOT passes_measure_rule")[0] == 0
    assert {(m, u) for m, u in db.rows(pg, f"SELECT label_set, unit_id FROM {S}.not_digital_findings")} == nd
    # every table and view that holds kept sentences
    for rel in ("commitment_members", "commitment_units", "commitment_package_links",
                "commitment_package_links_in_context", "commitment_sentences"):
        got = {u for (u,) in db.rows(pg, f"SELECT DISTINCT unit_id FROM {S}.{rel}")}
        assert not got & units, rel
    kept_mf = {u for (u,) in db.rows(pg, f"SELECT unit_id FROM {S}.measure_findings_in_context WHERE is_committed")}
    assert not kept_mf & units
    overlap = {u for (u,) in db.rows(pg, f"SELECT unit_id FROM {S}.unit_wording_overlap")}
    assert not overlap & units
    # the reader and the downloads
    sents = app.commitment_sentences_detail(q, None)
    assert len(sents) and not set(sents["unit_id"]) & units
    assert not set(app.download(q, "sentence")["sentence_id"]) & units
    c = app.commitments(q)
    assert not set(app.sentences(q, c["commitment_id"])["unit_id"]) & units
    # every reader and download of commitments and packages holds only commitments made of kept sentences
    d = app.commitment_detail(q)
    assert set(d["commitment_id"]) == set(c["commitment_id"])
    assert set(app.download(q, "commitment")["commitment_id"]) == set(d["commitment_id"])
    assert app.download(q, "project")["commitments"].sum() == len(d)
    assert app.families(q).drop_duplicates("label_set")["measure_commitments"].sum() == len(d)
    pk = app.packages_detail(q)
    served = {x for ids in pk["commitment_ids"] for x in ids.split("; ") if x}
    assert served and served <= set(d["commitment_id"])
    assert set(app.download(q, "package")["package_id"]) == set(pk["package_id"])
    linked = {u for (u,) in db.rows(pg, f"SELECT DISTINCT unit_id FROM {S}.commitment_package_links")}
    assert not linked & units
    measures = app.measures(q).set_index("label_set")["commitments"]
    assert measures.sum() == len(c)
    at = app.about(q)["measures"]
    # the About page's funnel: the step before the digital rule is the measure rule alone, and kept is
    # what is also digital, so the digital drop shows
    assert at["at_threshold"] == db.one(pg, f"SELECT count(DISTINCT unit_id) FROM {S}.measure_findings "
                                            f"WHERE passes_measure_rule")[0]
    assert at["kept"] < at["at_threshold"]
    # the evaluation sampler: kept is committed and digital, and the not-digital pairs are never kept
    mf, ua, _ = sampling.published(pg)
    m = {(x["label_set"], x["unit_id"]): x for x in sampling.measure_units(mf, {})}
    assert all(not m[k]["kept"] for k in nd)
    assert not {x["unit_id"] for x in sampling.asset_units(mf, ua, {})} & units
