"""The scope of every paid question is the cohort file's included rows
(questions.Corpus.in_scope, checks.load_packages)."""
from wbg_portfolio import checks
from wbg_portfolio import db
from wbg_portfolio import funnel
from wbg_portfolio import questions as Q
from wbg_portfolio import second


def cohort(path, **flags):
    ids = ["P1"] + list(flags)
    path.write_text("project_id,country_code,region,approval_fy,practice,included,exclusion_reason,"
                    "cohort_set,cohort_version\n" +
                    "".join(f"{i},KE,Africa,2023,,{'true' if flags.get(i, True) else 'false'},,digital,t\n"
                            for i in ids), encoding="utf-8")


def test_an_excluded_project_is_never_asked_and_gets_no_scores_or_packages(pg, corpus, fake_model, cohort_file):
    cohort(cohort_file, P1=False)
    c = Q.Corpus(corpus)
    lines = []
    assert not any(c.in_scope(r["unit_id"], None) for r in c.index)
    assert "0 sentence(s) in scope" in c.scope_note() and "left out" in c.scope_note()
    out = funnel.run(pg, corpus, "cra", corpus=c, api_key="k", log=lines.append)
    assert fake_model == [] and db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores")[0] == 0
    assert out["calls"] == 0
    assert second._in_scope(c, [r["unit_id"] for r in c.index], None)[0] == []
    assert checks.load_packages(corpus) == []
    assert checks.package_assets(pg, corpus, c, api_key="k", log=lines.append) is None and fake_model == []


def test_a_document_shared_with_an_included_project_stays_in_scope(pg, corpus, fake_model, cohort_file):
    cohort(cohort_file, P1=False, P2=True)
    c = Q.Corpus(corpus)
    for r in c.index:
        r["project_ids"] = "P1|P2"
    assert all(c.in_scope(r["unit_id"], None) for r in c.index)
    assert not any(c.in_scope(r["unit_id"], ["P1"]) for r in c.index) is False
    funnel.run(pg, corpus, "cra", corpus=c, api_key="k", log=lambda *_: None)
    assert fake_model and db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores")[0] >= 1
    assert [p["package_id"] for p in checks.load_packages(corpus)] == []     # the packages are P1's alone


def test_an_unscoped_run_asks_every_included_sentence_and_publishes(pg, corpus, fake_model, fake_chat):
    from test_decide import run_all
    from wbg_portfolio import decide
    c = run_all(pg, corpus)
    n = db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE label_set = 'cra' AND kind = 'measure'")[0]
    assert n >= 1
    assert c.scope_note().startswith("scope: 4 sentence(s) in scope") and "0 left out" in c.scope_note()
    from wbg_portfolio import load_pg
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.measure_findings")[0] > 0
