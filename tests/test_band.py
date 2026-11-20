"""The chat model's asker, the context state, the band rule, the digital and
policy questions (src/wbg_portfolio/chat.py, second.py, decide.py), end to end on
the invented corpus with the fake decision and chat models. No model is called."""
import json

import pytest

from wbg_portfolio import chat
from wbg_portfolio import checks
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import funnel
from wbg_portfolio import load_pg
from wbg_portfolio import panel
from wbg_portfolio import questions as Q
from wbg_portfolio import second

QUIET = dict(log=lambda *_: None)
GRADE = chat.question("Grade it.", "grade", maximum=3)
CHOICE = chat.question("Which?", "choice", ["investment", "policy"])


@pytest.fixture
def band_on(monkeypatch):
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "band.on": True, "digital.on": True,
                                                    "floor.ask": 0.99})


# ------------------------------------------------------------ the chat asker

@pytest.mark.parametrize("text,reply,expect", [
    ('{"grade": 2}', {"form": "grade", "max": 3}, {"grade": 2, "labels": None}),
    ('```json\n{"grade": 0}\n```', {"form": "grade", "max": 3}, {"grade": 0, "labels": None}),
    ('{"answer": "Yes"}', {"form": "yesno"}, {"grade": 1, "labels": ["yes"]}),
    ('{"answer": "policy"}', {"form": "choice", "options": ["investment", "policy"]},
     {"grade": None, "labels": ["policy"]}),
    ('{"labels": ["a"]}', {"form": "labels", "options": ["a", "b"], "max": 2}, {"grade": None, "labels": ["a"]}),
    ('{"labels": []}', {"form": "labels", "options": ["a", "b"], "max": 2}, {"grade": None, "labels": []}),
])
def test_a_reply_is_read_only_when_it_is_exactly_the_json_asked_for(text, reply, expect):
    assert chat.parse(text, reply) == expect


@pytest.mark.parametrize("text,reply", [
    ("The grade is 2", {"form": "grade", "max": 3}),
    ('{"grade": 4}', {"form": "grade", "max": 3}),
    ('{"grade": true}', {"form": "grade", "max": 3}),
    ('{"grade": "2"}', {"form": "grade", "max": 3}),
    ('Sure: {"grade": 2}', {"form": "grade", "max": 3}),
    ('{"answer": "maybe"}', {"form": "yesno"}),
    ('{"answer": "other"}', {"form": "choice", "options": ["investment", "policy"]}),
    ('{"labels": ["c"]}', {"form": "labels", "options": ["a", "b"], "max": 2}),
    ('{"labels": ["a", "b", "a"]}', {"form": "labels", "options": ["a", "b"], "max": 2}),
    ("", {"form": "grade", "max": 3}),
])
def test_anything_else_is_not_read(text, reply):
    with pytest.raises(ValueError):
        chat.parse(text, reply)


def test_what_is_asked_is_stored_under_the_chat_model_and_a_rerun_asks_nothing(pg, fake_chat):
    items = [("a", {"sentence": "one"}, GRADE), ("b", {"sentence": "two"}, GRADE)]
    got, st = chat.ask(pg, items, stage="t", api_key="k", **QUIET)
    assert st["calls"] == 2 and len(fake_chat.calls) == 2 and got["a"]["grade"] == 3
    assert fake_chat.calls[0]["model"] == "anthropic/claude-haiku-5.5" and fake_chat.calls[0]["temperature"] == 0
    row = db.one(pg, f"SELECT model, answer, served FROM {db.STORE}.answers LIMIT 1")
    assert row[0] == "anthropic/claude-haiku-5.5" and row[1]["raw"] == '{"grade": 3}' and row[2] == "chat-served"
    assert db.one(pg, f"SELECT calls FROM {db.STORE}.spend")[0] == 2
    got2, st2 = chat.ask(pg, items, stage="t", api_key="k", **QUIET)
    assert st2["to_ask"] == 0 and len(fake_chat.calls) == 2 and got2 == got


def test_a_dry_run_estimates_and_asks_nothing(pg, fake_chat):
    got, st = chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", dry_run=True)
    assert got == {} and st["to_ask"] == 1 and st["estimate_usd"] > 0 and not fake_chat.calls


def test_an_unreadable_reply_is_counted_stored_as_a_failure_and_asked_again(pg, fake_chat):
    fake_chat.reply = lambda prompt: "Yes, I think so."
    got, st = chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert got == {} and st["unparsed"] == 1
    stored = db.one(pg, f"SELECT answer FROM {db.STORE}.answers")[0]
    assert stored["failed"] is True and stored["grade"] is None and stored["raw"] == "Yes, I think so."
    fake_chat.reply = lambda prompt: '{"grade": 1}'
    got, st = chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert got["a"]["grade"] == 1 and st["unparsed"] == 0 and len(fake_chat.calls) == 2
    assert db.one(pg, f"SELECT count(*), bool_or(answer->>'failed' = 'true') FROM {db.STORE}.answers")[0] == 1
    assert chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)[1]["to_ask"] == 0


def test_a_request_that_fails_is_counted_and_nothing_is_invented(pg, fake_chat, monkeypatch):
    from wbg_portfolio import api

    def down(path, b, key, **kw):
        raise api.Transport("HTTP 503")
    monkeypatch.setattr(api, "post", down)
    got, st = chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert got == {} and len(st["failures"]) == 1


def test_the_spend_cap_stops_a_chat_step_before_it_asks(pg, fake_chat, monkeypatch):
    from wbg_portfolio import api
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "0.0000001")
    with pytest.raises(api.CapReached):
        chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert not fake_chat.calls


# ------------------------------------------------------------ the context state

def test_the_context_state_adds_the_paragraph_and_the_plain_state_is_unchanged(corpus, monkeypatch):
    c = Q.Corpus(corpus)
    u = c.index[0]["unit_id"]
    plain = c.state(u)
    assert "context" not in plain and "paragraph" not in plain
    ctx = c.context_state(u)
    assert {k: v for k, v in ctx.items() if k != "context"} == plain
    assert ctx["context"].startswith("The project will finance a hazard assessment") and "Floods" in ctx["context"]
    assert c.chat_state(u) == plain
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "second.context_chars": 10})
    assert c.context_state(u)["context"] == "The projec"


def test_a_context_question_says_only_the_sentence_is_judged():
    q = Q.graded_question({"id": "x", "name": "X", "definition": "d"}, ["a", "b"])
    cq = Q.in_context(q)
    assert cq["instructions"].startswith("Only the sentence in the 'sentence' field is judged")
    assert cq["instructions"].endswith(q["instructions"]) and cq["criteria"] == q["criteria"]
    assert Q.sha(cq) != Q.sha(q)


# ------------------------------------------------------------ the band rule

B = {"on": True, "low": 0.5, "keep": 0.9, "context": 0.7, "second_grade": 2}


@pytest.mark.parametrize("post,ctx,second,expect", [
    (0.95, None, None, (True, "score", False)),
    (0.90, 0.1, 0, (True, "score", False)),
    (0.60, 0.8, 2, (True, "second_look", False)),
    (0.60, 0.8, 3, (True, "second_look", False)),
    (0.60, 0.6, 3, (False, "", False)),
    (0.60, 0.8, 1, (False, "", False)),
    (0.60, None, 3, (False, "", True)),          # a missing second look is never a yes
    (0.60, 0.8, None, (False, "", True)),
    (0.60, None, None, (False, "", True)),
    (0.49, 0.9, 3, (False, "", False)),
])
def test_the_band_rule(post, ctx, second, expect):
    assert decide.band_rule(post, ctx, second, B) == expect


# ------------------------------------------------------------ the steps

def first_pass(pg, corpus, score=0.6, units=(0,)):
    """cra scored by the funnel, then the first-pass score of the chosen units set by hand."""
    c = Q.Corpus(corpus)
    funnel.run(pg, corpus, "cra", corpus=c, api_key="k", **QUIET)
    base = db.rows(pg, f"SELECT item_id FROM {db.SCHEMA}.scores WHERE kind = 'measure' AND label_set = 'cra'")
    template = base[0][0]
    for i in units:
        u = c.index[i]["unit_id"]
        if u != template:
            pg.execute(f"INSERT INTO {db.SCHEMA}.scores SELECT kind, label_set, label_id, %s, state_sha, "
                       f"question_sha, score, answer, served, route, round, cosine, rank FROM {db.SCHEMA}.scores "
                       f"WHERE item_id = %s AND kind = 'measure'", (u, template))
        pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = %s WHERE item_id = %s AND kind = 'measure'", (score, u))
    pg.commit()
    return c


def committed(pg, c, name="cra"):
    return {u: (r["committed"], r["decided_by"], r["waiting"]) for u, r in decide.measure_units(pg, c, name).items()}


def test_a_pair_in_the_band_is_committed_only_when_both_looks_agree(pg, corpus, fake_chat, band_on):
    c = first_pass(pg, corpus, 0.6)
    u0 = c.index[0]["unit_id"]
    assert committed(pg, c)[u0] == (False, "", True)          # no second look yet: waiting, not committed
    assert second.measure(pg, corpus, c, "cra", api_key="k", **QUIET) == 1
    assert committed(pg, c)[u0] == (True, "second_look", False)
    row = decide.measure_units(pg, c, "cra")[u0]
    assert row["score_first"] == pytest.approx(0.6) and row["score_context"] >= 0.7 and row["second_grade"] == 3
    ask = fake_chat.prompts()[0]
    assert "[sentence]" in ask and "[section]" in ask and "[context]" not in ask      # no paragraph for the chat grade


def test_the_chat_model_saying_no_keeps_the_pair_out(pg, corpus, fake_chat, band_on):
    c = first_pass(pg, corpus, 0.6)
    fake_chat.reply = lambda prompt: '{"grade": 1}'
    second.measure(pg, corpus, c, "cra", api_key="k", **QUIET)
    assert committed(pg, c)[c.index[0]["unit_id"]] == (False, "", False)


def test_an_unreadable_second_look_is_never_a_yes(pg, corpus, fake_chat, band_on):
    c = first_pass(pg, corpus, 0.6)
    fake_chat.reply = lambda prompt: "grade 3"
    assert second.measure(pg, corpus, c, "cra", api_key="k", **QUIET) is not None
    assert committed(pg, c)[c.index[0]["unit_id"]] == (False, "", True)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE kind = 'measure_second'")[0] == 0


def test_a_score_at_the_keep_level_needs_no_second_look(pg, corpus, fake_chat, fake_model, band_on):
    c = first_pass(pg, corpus, 0.95)
    n = len(fake_model)
    assert second.measure(pg, corpus, c, "cra", api_key="k", **QUIET) == 0
    assert len(fake_model) == n and not fake_chat.calls
    assert committed(pg, c)[c.index[0]["unit_id"]] == (True, "score", False)


def test_a_rerun_with_everything_stored_asks_nothing(pg, corpus, fake_chat, fake_model, band_on):
    c = first_pass(pg, corpus, 0.6, units=(0, 1))
    second.measure(pg, corpus, c, "cra", api_key="k", **QUIET)
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    assert n_chat == 2
    second.measure(pg, corpus, c, "cra", api_key="k", **QUIET)
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE kind = 'measure_ctx'")[0] == 2


def test_a_dry_run_and_a_scope_with_no_such_project_ask_nothing(pg, corpus, fake_chat, fake_model, band_on):
    c = first_pass(pg, corpus, 0.6)
    n = len(fake_model)
    assert second.measure(pg, corpus, c, "cra", dry_run=True, **QUIET) is None
    assert second.measure(pg, corpus, c, "cra", api_key="k", projects=["P9"], **QUIET) == 0
    assert len(fake_model) == n and not fake_chat.calls
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE kind = 'measure_ctx'")[0] == 0


def test_with_the_band_off_the_second_look_does_nothing(pg, corpus, fake_chat, fake_model):
    c = first_pass(pg, corpus, 0.6)
    n = len(fake_model)
    assert second.measure(pg, corpus, c, "cra", api_key="k", **QUIET) == 0
    assert second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET) == 0
    assert len(fake_model) == n and not fake_chat.calls
    # the floor of the tests is 0.5, so 0.6 is committed by the floor alone
    assert committed(pg, c)[c.index[0]["unit_id"]] == (True, "score", False)


def test_the_pair_stage_asks_nothing_under_the_band_rule(pg, corpus, fake_model, band_on):
    c = first_pass(pg, corpus, 0.95)
    n = len(fake_model)
    assert checks.pairs(pg, corpus, c, ["cra"], api_key="k", **QUIET) == 0
    assert len(fake_model) == n


def test_later_stages_ask_about_the_sentences_committed_under_the_band_rule(pg, corpus, fake_chat, fake_model, band_on):
    c = first_pass(pg, corpus, 0.6)
    assert checks.committed_at_ask(pg, c, "cra") == []                # in the band, no second look: not asked
    second.measure(pg, corpus, c, "cra", api_key="k", **QUIET)
    assert checks.committed_at_ask(pg, c, "cra") == [c.index[0]["unit_id"]]
    assert checks.types(pg, corpus, c, "cra", api_key="k", **QUIET) == 2
    # the same sentence below the band is not asked: floor.ask no longer decides
    c = first_pass(pg, corpus, 0.6, units=())
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = 0.8 WHERE kind = 'measure'")
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind IN ('measure_ctx', 'measure_second')")
    pg.commit()
    assert checks.committed_at_ask(pg, c, "cra") == []


def test_digital_is_asked_once_per_committed_sentence_and_a_rerun_asks_nothing(pg, corpus, fake_chat, fake_model, band_on):
    c = first_pass(pg, corpus, 0.95, units=(0, 2))
    u0, u1 = c.index[0]["unit_id"], c.index[2]["unit_id"]
    assert second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET) == 2
    asked = [b for b in fake_model if "digital" in json.dumps(b["questions"]).lower()]
    assert len(asked) == 2 and "context" in asked[0]["state"]
    assert "Only the sentence in the 'sentence' field is judged" in list(asked[0]["questions"].values())[0]["instructions"]
    dig = decide.digital_scores(pg)
    assert dig[u0] >= 0.5 > dig[u1]
    assert (decide.is_digital(dig.get(u0)), decide.is_digital(dig.get(u1)), decide.is_digital(None)) == (True, False, False)
    assert decide.committed_digital_units(pg, c, ["cra"]) == [u0]
    n = len(fake_model)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    assert len(fake_model) == n


def test_policy_is_the_chat_models_reading_and_a_rerun_asks_nothing(pg, corpus, fake_chat, band_on):
    c = first_pass(pg, corpus, 0.95, units=(0, 2))
    u0, u1 = c.index[0]["unit_id"], c.index[2]["unit_id"]
    fake_chat.reply = lambda prompt: ('{"answer": "policy"}' if "road" in prompt else '{"answer": "investment"}')
    assert second.policy(pg, corpus, c, ["cra"], api_key="k", **QUIET) == 2
    assert decide.instruments(pg) == {u0: "investment", u1: "policy"}
    assert "[context]" in fake_chat.prompts()[0] and "technical assistance to draft a regulation" in fake_chat.prompts()[0]
    n = len(fake_chat.calls)
    second.policy(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    assert len(fake_chat.calls) == n


def test_publish_carries_the_new_columns_and_keeps_the_not_digital_out_of_the_commitments(
        pg, corpus, fake_chat, fake_model, band_on):
    c = first_pass(pg, corpus, 0.6, units=(0, 2))
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = 0.95 WHERE kind = 'measure' AND item_id = %s",
               (c.index[2]["unit_id"],))
    pg.commit()
    u0, u1 = c.index[0]["unit_id"], c.index[2]["unit_id"]
    funnel_assets = funnel.run(pg, corpus, "assets", corpus=c, api_key="k", **QUIET)
    assert funnel_assets is not None
    second.measure(pg, corpus, c, "cra", api_key="k", **QUIET)
    checks.types(pg, corpus, c, "cra", api_key="k", **QUIET)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    second.policy(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    logged = []
    decide.publish(pg, corpus, corpus=c, log=logged.append)
    rows = {r[0]: r for r in db.rows(
        pg, f"SELECT unit_id, is_committed, decided_by, score_first, score_context, second_grade, is_digital, "
            f"digital_p, instrument FROM {db.SCHEMA}.measure_findings")}
    r0, r1 = rows[u0], rows[u1]
    assert r0[1:3] == (True, "second_look") and float(r0[3]) == 0.6 and float(r0[4]) >= 0.7 and r0[5] == 3
    assert r0[6] is True and r0[8] == "investment"
    assert r1[1:3] == (True, "score") and r1[4] is None and r1[5] is None and r1[6] is False and float(r1[7]) < 0.5
    nd = db.rows(pg, f"SELECT unit_id, project_id, digital_p FROM {db.SCHEMA}.not_digital_findings")
    assert [(u, p) for u, p, _ in nd] == [(u1, "P1")]
    assert u1 not in {u for (u,) in db.rows(pg, f"SELECT unit_id FROM {db.SCHEMA}.commitment_units")}
    assert u0 in {u for (u,) in db.rows(pg, f"SELECT unit_id FROM {db.SCHEMA}.commitment_units")}
    assert db.one(pg, f"SELECT band_on, band_low, digital_on, digital_floor, second_model "
                      f"FROM {db.SCHEMA}.dashboard_settings") == (True, 0.5, True, 0.5, "anthropic/claude-haiku-5.5")


def test_publish_says_how_many_pairs_wait_for_a_second_look(pg, corpus, fake_model, band_on):
    c = first_pass(pg, corpus, 0.6)
    logged = []
    decide.publish(pg, corpus, corpus=c, log=logged.append, views=False)
    assert any("waiting for a second look" in line for line in logged)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.measure_findings WHERE is_committed")[0] == 0
