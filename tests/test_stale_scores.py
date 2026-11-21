"""A score stored under an earlier wording of its question is never read as an answer to the current
one (src/wbg_portfolio/decide.py, scores): it is left out, counted and logged, and read as not asked yet.
The fake models only."""
import pytest

from helpers import QUIET, first_pass
from wbg_portfolio import checks
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import funnel
from wbg_portfolio import questions as Q
from wbg_portfolio import second

OLD_Q = {"type": "score", "instructions": "An earlier wording of the question", "criteria": Q.FIT_SCALE}
YES = {"type": "score", "probabilities": {"0": 0.0, "1": 0.1, "2": 0.2, "3": 0.7}}


def _old_row(kind, label_set, label_id, item, answer=YES):
    return checks._row(kind, label_set, label_id, item, {"sentence": "x"}, OLD_Q, answer, score=0.95)


@pytest.mark.parametrize("kind,label_set,label_id,read", [
    ("digital", "digital", "digital", lambda pg: decide.digital_scores(pg)),
    ("asset", "assets", "towers", lambda pg: dict(decide.scores(pg, "asset") and {r[2]: r for r in decide.scores(pg, "asset")})),
    ("package_asset", "assets", "towers", lambda pg: decide.package_asset_posteriors(pg)),
    ("package_asset_second", "assets", "towers", lambda pg: decide.package_asset_sets(pg)),
    ("package_asset_subtype", "assets", "towers", lambda pg: decide.package_asset_types(pg)),
    ("package_measure", "cra", "cra", lambda pg: decide.package_measure_scores(pg)),
    ("asset_second", "assets", "second", lambda pg: decide.asset_second_looks(pg)),
    ("asset_subtype", "assets", "towers", lambda pg: decide.asset_types(pg)),
    ("policy", "policy", "policy", lambda pg: decide.instruments(pg)),
])
def test_an_old_question_row_is_never_read_as_an_answer(pg, kind, label_set, label_id, read):
    answer = dict(YES, labels=["mast"], grade=1) if "subtype" in kind or kind in ("policy", "asset_second") else YES
    checks.write_scores(pg, kind, label_set, [_old_row(kind, label_set, label_id, "item-1", answer)])
    logged = []
    assert decide.scores(pg, kind, log=logged.append) == []
    assert not read(pg)
    assert decide.IGNORED[(kind, "")] == 1
    assert any("earlier wording" in line and kind in line for line in logged)


def test_the_current_question_is_read(pg):
    q = Q.digital_question()
    checks.write_scores(pg, "digital", "digital",
                        [checks._row("digital", "digital", "digital", "u1", {"sentence": "x"}, q, YES, score=0.9)])
    assert decide.digital_scores(pg) == {"u1": 0.9}
    assert decide.IGNORED[("digital", "")] == 0


def test_publish_after_a_stopped_step_does_not_mix_old_and_new_answers(pg, corpus, fake_model):
    """The digital step asked under a new wording stopped part way: the sentence still holding only
    an old-wording digital row is not digital (not asked yet), never digital on the old answer."""
    c = first_pass(pg, corpus, 0.95, units=(0, 1))
    funnel.run(pg, corpus, "assets", corpus=c, api_key="k", **QUIET)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None, views=False)
    before = {u: d for u, d in db.rows(pg, f"SELECT unit_id, is_digital FROM {db.SCHEMA}.measure_findings")}
    assert any(before.values())
    stale = sorted(u for u, d in before.items() if d)[0]
    # the row of that sentence is now the old wording's (what a stopped re-ask leaves behind)
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET question_sha = %s WHERE kind = 'digital' AND item_id = %s",
               (Q.sha(OLD_Q), stale))
    pg.commit()
    logged = []
    decide.publish(pg, corpus, corpus=c, log=logged.append, views=False)
    after = {u: (d, k) for u, d, k in db.rows(
        pg, f"SELECT unit_id, is_digital, is_committed FROM {db.SCHEMA}.measure_findings")}
    assert after[stale] == (False, False)
    assert db.one(pg, f"SELECT digital_p FROM {db.SCHEMA}.measure_findings WHERE unit_id = %s", (stale,))[0] is None
    assert any("no digital answer yet" in line for line in logged)
    assert all(after[u][0] == d for u, d in before.items() if u != stale)
