"""A paid answer is never lost, never paid for twice and never blocks a run
(src/wbg_portfolio/ask.py, chat.py, api.py): each answer is committed as it arrives, a batch with a bad
answer keeps its good ones, a refused item is skipped, an empty reply is retried, repeated states are
asked once."""
import json

import pytest

from wbg_portfolio import api
from wbg_portfolio import ask
from wbg_portfolio import chat
from wbg_portfolio import db
from wbg_portfolio import questions as Q

from test_ask import GQ, items

QUIET = dict(log=lambda *_: None)


def seen_by_another_connection(sql):
    """What a second connection (a crashed process's successor) finds committed."""
    other = db.connect()
    try:
        return other.execute(sql).fetchone()[0]
    finally:
        other.close()


def good(b):
    return {bq: {"type": "score", "probabilities": {"0": 0.1, "1": 0.1, "2": 0.1, "3": 0.7}}
            for bq in b["questions"]}


# ---------------------------------------------------------------- A1 durability

def test_each_decision_answer_is_committed_when_it_arrives(pg, monkeypatch):
    n, at_crash = [0], []

    def post(path, b, key, **kw):
        n[0] += 1
        if n[0] == 4:                     # the process is killed here: what is committed is all that is left
            at_crash.append(seen_by_another_connection(f"SELECT count(*) FROM {db.STORE}.answers"))
            raise KeyboardInterrupt
        return {"model": "m", "usage": {"cost": 0.01}, "answers": good(b)}
    monkeypatch.setattr(api, "post", post)
    with pytest.raises(KeyboardInterrupt):
        ask.ask(pg, "m", items(8), stage="t", api_key="k", batch=0, workers=1, **QUIET)
    assert at_crash == [3]


def test_each_chat_answer_and_each_unreadable_reply_is_committed_when_it_arrives(pg, fake_chat, monkeypatch):
    q = Q.policy_question()
    its = [(f"u{i}", {"sentence": f"s{i}"}, q) for i in range(6)]
    seen = []
    inner = api.post

    def post(path, b, key, **kw):
        seen.append((seen_by_another_connection(f"SELECT count(*) FROM {db.STORE}.answers"),
                     seen_by_another_connection(f"SELECT count(*) FROM {db.STORE}.chat_failures")))
        return inner(path, b, key, **kw)
    monkeypatch.setattr(api, "post", post)
    fake_chat.reply = lambda p: "not json" if "s1" in p else '{"answer": "investment"}'
    chat.ask(pg, its, stage="t", api_key="k", workers=1, **QUIET)
    assert seen[-1] == (4, 1)             # before the last request, five were already committed


# ------------------------------------------------- A2 a batch with one bad answer keeps the good ones

def test_a_batch_with_one_incomplete_answer_keeps_the_good_ones_and_records_the_cost_once(pg, monkeypatch):
    def post(path, b, key, **kw):
        out = good(b)
        if len(b["questions"]) > 1:
            out[sorted(out)[2]] = {"type": "score", "probabilities": {"0": 1}}      # one malformed
        return {"model": "m", "usage": {"cost": 0.05}, "answers": out}
    monkeypatch.setattr(api, "post", post)
    got, st = ask.ask(pg, "m", items(5), stage="t", api_key="k", batch=10, **QUIET)
    assert len(got) == 4 and len(st["failures"]) == 1
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 4
    calls, cost = db.one(pg, f"SELECT calls, cost_usd FROM {db.STORE}.spend")
    assert calls == 1 and float(cost) == pytest.approx(0.05)
    # the next run asks only the bad one
    sent = []
    monkeypatch.setattr(api, "post", lambda p, b, k, **kw: sent.append(b) or
                        {"model": "m", "usage": {"cost": 0.01}, "answers": good(b)})
    got, st = ask.ask(pg, "m", items(5), stage="t", api_key="k", batch=10, **QUIET)
    assert len(got) == 5 and len(sent) == 1 and len(sent[0]["questions"]) == 1


# ------------------------------------------------- A3 a refused item is skipped, a refused request stops

def refuse_poison(monkeypatch, poison, status=400):
    sent = []

    def post(path, b, key, **kw):
        sent.append(b)
        text = json.dumps(b["state"])
        if poison in text:
            raise api.Refused(f"HTTP {status} flagged", status=status)
        return {"model": "m", "usage": {"cost": 0.01}, "answers": good(b)}
    monkeypatch.setattr(api, "post", post)
    return sent


def test_one_refused_item_is_skipped_and_the_rest_of_its_batch_is_stored(pg, monkeypatch):
    sent = refuse_poison(monkeypatch, "road 5")
    got, st = ask.ask(pg, "m", items(8), stage="t", api_key="k", batch=10, **QUIET)
    assert sorted(got) == sorted(f"u{i}" for i in range(8) if i != 5)
    assert st["refused"] == [("u5", "cra")] and st["failures"] == []
    assert ask.refused_items(st) == {"u5"}
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 7
    assert (("t", "u5", "cra")) in ask.REFUSED
    # a rerun does not ask the refused item again, and stops nothing
    sent.clear()
    got, st = ask.ask(pg, "m", items(8), stage="t", api_key="k", batch=10, **QUIET)
    assert st["refused"] == [("u5", "cra")] and len(got) == 7 and not sent


def test_a_lone_refused_item_among_other_requests_is_skipped(pg, monkeypatch):
    refuse_poison(monkeypatch, "road 3")
    got, st = ask.ask(pg, "m", items(6), stage="t", api_key="k", batch=0, workers=1, **QUIET)
    assert "u3" not in got and len(got) == 5 and ask.refused_items(st) == {"u3"}


@pytest.mark.parametrize("status", [401, 402])
def test_a_bad_key_or_no_credit_stops_the_run(pg, monkeypatch, status):
    refuse_poison(monkeypatch, "", status=status)
    with pytest.raises(api.Refused):
        ask.ask(pg, "m", items(4), stage="t", api_key="k", batch=10, **QUIET)


def test_a_refusal_of_everything_is_a_refusal_of_the_request_and_stops_the_run(pg, monkeypatch):
    refuse_poison(monkeypatch, "", status=400)             # a bad model: nothing is ever answered
    with pytest.raises(api.Refused):
        ask.ask(pg, "m", items(6), stage="t", api_key="k", batch=0, workers=1, **QUIET)
    with pytest.raises(api.Refused):
        ask.ask(pg, "m", items(6), stage="t", api_key="k", batch=10, **QUIET)


def test_a_refused_chat_item_is_skipped_and_one_refused_everywhere_stops(pg, fake_chat, monkeypatch):
    q = Q.policy_question()
    its = [(f"u{i}", {"sentence": f"s{i}"}, q) for i in range(5)]
    inner = api.post

    def post(path, b, key, **kw):
        if "s2" in b["messages"][0]["content"]:
            raise api.Refused("HTTP 400 flagged", status=400)
        return inner(path, b, key, **kw)
    monkeypatch.setattr(api, "post", post)
    got, st = chat.ask(pg, its, stage="t", api_key="k", workers=1, **QUIET)
    assert sorted(got) == ["u0", "u1", "u3", "u4"] and st["refused"] == ["u2"]
    monkeypatch.setattr(api, "post", lambda *a, **k: (_ for _ in ()).throw(api.Refused("HTTP 404", status=404)))
    with pytest.raises(api.Refused):
        chat.ask(pg, [(f"v{i}", {"sentence": f"x{i}"}, q) for i in range(5)], stage="t", api_key="k",
                 workers=1, **QUIET)


# ------------------------------------------------- A4 an empty reply is not an attempt

def test_an_empty_reply_is_retried_and_never_counts_toward_max_attempts(pg, fake_chat):
    q = Q.policy_question()
    its = [("u0", {"sentence": "s0"}, q)]
    replies = ["", "  ", '{"answer": "policy"}']
    fake_chat.reply = lambda p: replies.pop(0)
    got, st = chat.ask(pg, its, stage="t", api_key="k", **QUIET)       # empty, empty, then readable
    assert got["u0"]["labels"] == ["policy"] and st["failures"] == [] and len(fake_chat.calls) == 3
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.chat_failures")[0] == 0


def test_a_provider_that_stays_empty_fails_the_request_for_ever_without_giving_up(pg, fake_chat):
    q = Q.policy_question()
    its = [("u0", {"sentence": "s0"}, q)]
    fake_chat.reply = lambda p: ""
    for _ in range(4):                                                  # far more runs than max_attempts
        got, st = chat.ask(pg, its, stage="t", api_key="k", **QUIET)
        assert got == {} and len(st["failures"]) == 1 and st["gave_up"] == 0 and st["to_ask"] == 1
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.chat_failures")[0] == 0
    fake_chat.reply = lambda p: '{"answer": "policy"}'
    got, st = chat.ask(pg, its, stage="t", api_key="k", **QUIET)
    assert "u0" in got


def test_a_reply_with_content_that_cannot_be_read_still_counts(pg, fake_chat):
    q = Q.policy_question()
    fake_chat.reply = lambda p: "I cannot say"
    for _ in range(3):
        chat.ask(pg, [("u0", {"sentence": "s0"}, q)], stage="t", api_key="k", **QUIET)
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.chat_failures")[0] == 2     # then given up


# ------------------------------------------------- A5 identical states are asked once

def test_identical_states_in_one_call_are_asked_once_and_each_item_still_answered(pg, fake_model):
    its = [(f"p{i}", {"package": "Supply of towers" if i % 2 else "Audit"}, {"cra": GQ}) for i in range(6)]
    got, st = ask.ask(pg, "m", its, stage="t", api_key="k", batch=10, **QUIET)
    assert sorted(got) == [f"p{i}" for i in range(6)]
    assert sum(len(b["questions"]) for b in fake_model) == 2
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 2


def test_a_refused_sentence_does_not_stop_the_funnel_and_is_left_unscored(pg, corpus, fake_model, monkeypatch):
    from wbg_portfolio import funnel
    from wbg_portfolio import panel
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "floor.ask": 0.5})
    inner = api.post

    def post(path, b, key, **kw):
        if "The road will be widened" in json.dumps(b["state"]):
            raise api.Refused("HTTP 403 flagged", status=403)
        return inner(path, b, key, **kw)
    monkeypatch.setattr(api, "post", post)
    out = funnel.run(pg, corpus, "cra", api_key="k", log=lambda *_: None)
    assert out is not None
    c = Q.Corpus(corpus)
    scored = {r[0] for r in db.rows(pg, f"SELECT item_id FROM {db.SCHEMA}.scores WHERE label_set = 'cra'")}
    road = next(r["unit_id"] for r in c.index if "road" in c.texts[r["unit_id"]])
    assert road not in scored and len(scored) == len(c.index) - 1
