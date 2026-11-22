"""Shared by the tests of the second looks, the link strengths and the commitments."""
from wbg_portfolio import db
from wbg_portfolio import funnel
from wbg_portfolio import questions as Q

QUIET = dict(log=lambda *_: None)


def first_pass(pg, corpus, score=0.95, units=(0,), name="cra"):
    """`name` scored by the funnel, then the first-pass score of the chosen units set by hand."""
    c = Q.Corpus(corpus)
    funnel.run(pg, corpus, name, corpus=c, api_key="k", **QUIET)
    template = db.rows(pg, f"SELECT item_id FROM {db.SCHEMA}.scores WHERE kind = 'measure' AND label_set = %s",
                       (name,))[0][0]
    for i in units:
        u = c.index[i]["unit_id"]
        if u != template:
            pg.execute(f"INSERT INTO {db.SCHEMA}.scores SELECT kind, label_set, label_id, %s, state_sha, "
                       f"question_sha, score, answer, served, route, round, cosine, rank FROM {db.SCHEMA}.scores "
                       f"WHERE item_id = %s AND kind = 'measure'", (u, template))
        pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = %s WHERE item_id = %s AND kind = 'measure'", (score, u))
    pg.commit()
    return c


def count(pg, kind):
    return db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE kind = %s", (kind,))[0]


def looks(pg, corpus, c, names):
    """The digital question, then each measure's two second looks: what the band rule needs before a
    first-pass score commits a sentence."""
    import conftest
    from wbg_portfolio import second
    model = conftest.LIVE["model"]       # the fibre sentence and the road read as the measure with the paragraph
    model.also_yes = tuple(model.also_yes) + ("fibre backbone", "road will be widened")
    second.digital(pg, corpus, c, list(names), api_key="k", **QUIET)
    for n in names:
        second.measure_second(pg, corpus, c, n, api_key="k", **QUIET)
    del model[:], conftest.LIVE["chat"].calls[:]      # the setup's own requests are not what a test counts
