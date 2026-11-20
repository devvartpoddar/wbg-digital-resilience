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
