"""The spend cap (src/wbg_portfolio/api.py, ask.py, chat.py): a chat step is estimated from the ledger's
measured cost per request where there is one, else from the upper bound, and the log says which; the
cost of requests in flight when the cap is reached still reaches the ledger; a package description's
embedding is in the ledger and under the cap. The fake models only."""
import threading
import time

import pytest

from wbg_portfolio import api
from wbg_portfolio import ask
from wbg_portfolio import chat
from wbg_portfolio import db
from wbg_portfolio import questions as Q

QUIET = dict(log=lambda *_: None)
CQ = Q.chat_question("Is it?", "yesno")


def _items(n):
    return [(f"u{i}", {"sentence": f"Sentence {i}"}, CQ) for i in range(n)]


def _ledger(pg, stage, model, calls, cost):
    pg.execute(f"INSERT INTO {db.STORE}.spend (at, stage, label_set, model, calls, cost_usd, seconds) "
               f"VALUES (now() - interval '1 day', %s, '', %s, %s, %s, 1)", (stage, model, calls, cost))
    pg.commit()


def test_a_chat_step_is_estimated_from_its_measured_cost(pg):
    mdl = chat.model()
    _, st = chat.ask(pg, _items(10), stage="policy", api_key="k", dry_run=True, **QUIET)
    assert "upper bound" in st["estimate_basis"] and "upper bound" in chat.estimate_line("policy", st)
    upper = st["estimate_usd"]
    _ledger(pg, "policy", mdl, 100, 0.002)                     # US$0.00002 a request, measured
    _, st = chat.ask(pg, _items(10), stage="policy", api_key="k", dry_run=True, **QUIET)
    assert st["estimate_usd"] == pytest.approx(10 * 0.00002) and st["estimate_usd"] < upper
    assert "measured" in st["estimate_basis"] and "step policy" in chat.estimate_line("policy", st)
    # another step of the same model with no ledger of its own reads the model's average
    _, st = chat.ask(pg, _items(10), stage="asset_second", api_key="k", dry_run=True, **QUIET)
    assert "model " + mdl in st["estimate_basis"]


def test_a_measured_cost_does_not_stop_a_step_the_upper_bound_would(pg, fake_chat, monkeypatch):
    mdl = chat.model()
    _ledger(pg, "policy", mdl, 100, 0.002)
    monkeypatch.setenv("WBG_SPEND_SINCE", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "0.003")           # under the upper bound of 10 items
    got, st = chat.ask(pg, _items(10), stage="policy", api_key="k", **QUIET)
    assert len(got) == 10


def test_requests_in_flight_when_the_cap_is_reached_reach_the_ledger(pg, monkeypatch):
    monkeypatch.setenv("WBG_SPEND_SINCE", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "0.5")
    gate = threading.Event()

    def one(i):
        if i == 0:
            return 1.0                                          # the first to finish passes the cap
        gate.wait(5)
        return 0.25
    stats = {"estimate_usd": 0.0, "failures": []}
    timer = threading.Timer(0.3, gate.set)
    timer.start()
    with pytest.raises(api.CapReached):
        ask.run_requests(pg, "m", [(i,) for i in range(4)], one, stats, stage="t", label_set="", api_key="k",
                         workers=4, **QUIET)
    total = db.one(pg, f"SELECT sum(cost_usd), sum(calls) FROM {db.STORE}.spend WHERE stage = 't'")
    assert float(total[0]) == pytest.approx(1.75) and total[1] == 4


def test_a_package_description_embedding_is_in_the_ledger_and_under_the_cap(pg, corpus, monkeypatch):
    from wbg_portfolio import checks
    from wbg_portfolio import embed
    packages = [dict(p, text="A description never embedded", sha="f" * 64) for p in checks.load_packages(corpus)[:1]]
    monkeypatch.setenv("WBG_SPEND_SINCE", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "0")
    with pytest.raises(api.CapReached):
        checks.package_vectors(pg, corpus, packages, "k", lambda *_: None)

    def fake_batch(n, total, batch, unique, root, dim, **kw):
        for s in batch:
            embed.write_cached(root, s, b"\0" * 4 * dim)
        return 1, 1000, len(batch), 0, {}, "m", []
    monkeypatch.setattr(embed, "_embed_batch", fake_batch)
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "1")
    checks.package_vectors(pg, corpus, packages, "k", lambda *_: None)
    assert db.one(pg, f"SELECT sum(calls) FROM {db.STORE}.spend WHERE stage = 'link:embed'")[0] == 1
