"""The gap step of checks.types (types.gaps on): the sentences no approved
type reaches are shown to the generative model once, which proposes OTH types. No model is called:
the fake decision and chat models."""
import json
import os

import pytest

from helpers import QUIET, first_pass, looks
from wbg_portfolio import api
from wbg_portfolio import chat
from wbg_portfolio import checks
from wbg_portfolio import db
from wbg_portfolio import panel
from wbg_portfolio import second

PROPOSAL = json.dumps({"types": [{"type_id": "backup", "type_name": "Off-site backup", "definition": "Copies kept elsewhere.",
                                  "examples": [1]}]})


@pytest.fixture
def gaps(monkeypatch, fake_chat):
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "types.gaps": True, "types.gap_min_units": 1,
                                                    "floor.types": 1.01, "floor.ask": 0.99})
    fake_chat.reply = lambda p: PROPOSAL if "None of these types fits" in p else '{"answer": "yes", "grade": 3}'
    return fake_chat


def ready(pg, corpus):
    c = first_pass(pg, corpus, 0.95, units=(0, 1))
    looks(pg, corpus, c, ["cra"])
    return c


def gen_calls(chat):
    return [b for b in chat.calls if "None of these types fits" in b["messages"][0]["content"]]


def test_the_gap_step_reads_the_committed_digital_sentences_and_proposes_oth_types(pg, corpus, gaps, fake_model):
    c = ready(pg, corpus)
    logged = []
    assert checks.types(pg, corpus, c, "cra", api_key="k", log=logged.append) is not None
    call = gen_calls(gaps)
    assert len(call) == 1
    body = call[0]
    assert body["max_tokens"] == chat.GENERATE_REQUEST["max_tokens"] and body["temperature"] == 0
    listed = body["messages"][0]["content"]
    for u in checks.committed_at_ask(pg, c, "cra"):
        assert " ".join(c.texts[u].split()) in listed
    path = checks.oth_path(corpus, "cra")
    assert [t["type_id"] for t in checks.oth_types(corpus, "cra")] == ["OTH.backup"] and os.path.exists(path)
    line = [m for m in logged if "OTH type" in m][0]
    assert "units no listed type reaches" in line and "1 OTH type(s) proposed" in line
    # the call is in the ledger, counted like a chat question
    assert db.one(pg, f"SELECT sum(calls) FROM {db.STORE}.spend WHERE stage = 'types:generate'")[0] == 1


def test_a_stored_proposal_is_not_asked_again(pg, corpus, gaps, fake_model):
    c = ready(pg, corpus)
    checks.types(pg, corpus, c, "cra", api_key="k", **QUIET)
    checks.types(pg, corpus, c, "cra", api_key="k", **QUIET)
    assert len(gen_calls(gaps)) == 1


def test_a_dry_run_asks_nothing(pg, corpus, gaps, fake_model):
    c = ready(pg, corpus)
    assert checks.types(pg, corpus, c, "cra", api_key="k", dry_run=True, **QUIET) is None
    assert not gen_calls(gaps)


def test_the_spend_cap_stops_the_proposal_before_it_is_asked(pg, corpus, gaps, fake_model, monkeypatch):
    c = ready(pg, corpus)
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "0.000001")
    monkeypatch.setenv("WBG_SPEND_SINCE", "2999-01-01")
    with pytest.raises(api.CapReached):
        checks.types(pg, corpus, c, "cra", api_key="k", **QUIET)
    assert not gen_calls(gaps)


@pytest.mark.parametrize("reply", ["no json at all", '{"types": [{"type_id": "x"}]}', '{"other": 1}'])
def test_a_reply_that_cannot_be_read_proposes_nothing(pg, corpus, gaps, fake_model, reply):
    c = ready(pg, corpus)
    gaps.reply = lambda p: reply if "None of these types fits" in p else '{"answer": "yes", "grade": 3}'
    logged = []
    assert checks.types(pg, corpus, c, "cra", api_key="k", log=logged.append) is not None
    assert checks.oth_types(corpus, "cra") == []
    assert any("not usable" in m for m in logged)


def test_generate_is_asked_through_the_chat_asker_and_reads_an_answer_stored_before(pg, fake_chat):
    from wbg_portfolio import ask
    from wbg_portfolio import questions as Q
    mdl = chat.model()
    state, q = {"prompt": "Propose types."}, {"type": "generate", "model": mdl}
    ask._store(pg, mdl, [(Q.sha(state), Q.sha(q), {"type": "generate", "text": "stored before"}, "m", 0.0, "c", None)],
               {Q.sha(state): state}, {Q.sha(q): q})
    assert checks.generate(pg, "Propose types.", "k", False) == "stored before" and not fake_chat.calls
    # a new prompt is asked once, with its own request size, and an empty reply is asked again
    replies = iter(["", "the proposal"])
    fake_chat.reply = lambda p: next(replies)
    assert checks.generate(pg, "Propose other types.", "k", False) == "the proposal"
    assert len(fake_chat.calls) == 2 and fake_chat.calls[0]["max_tokens"] == chat.GENERATE_REQUEST["max_tokens"]
    assert fake_chat.calls[0]["messages"][-1]["content"] == "Propose other types."
    assert checks.generate(pg, "Propose other types.", "k", False) == "the proposal" and len(fake_chat.calls) == 2
