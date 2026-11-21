"""The chat model's asker, the context state, the band rule, the digital and
policy questions (src/wbg_portfolio/chat.py, second.py, decide.py), end to end on
the invented corpus with the fake decision and chat models. No model is called."""
import json

import pytest

from helpers import QUIET, first_pass
from wbg_portfolio import chat
from wbg_portfolio import checks
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import funnel
from wbg_portfolio import load_pg
from wbg_portfolio import panel
from wbg_portfolio import questions as Q
from wbg_portfolio import second


def look(pg, corpus, c, name, **kw):
    """The measure's second look, after the digital question (which decides which sentences are asked)."""
    if not kw.get("dry_run"):
        second.digital(pg, corpus, c, [name], api_key="k", **QUIET)
    return second.measure_second(pg, corpus, c, name, **{"api_key": "k", **QUIET, **kw})

GRADE = Q.chat_question("Grade it.", "grade", maximum=3)
CHOICE = Q.chat_question("Which?", "choice", ["investment", "policy"])


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
    ("Yes, the answer is yes.", {"form": "yesno"}),
    ('{"grade": 1} or maybe {"grade": 2}', {"form": "grade", "max": 3}),
    ('{"grade": 1}{"grade": 1}', {"form": "grade", "max": 3}),
    ('Sure: {"grade": 7} done', {"form": "grade", "max": 3}),
    ('Sure: {"answer": "yes" and more', {"form": "yesno"}),
    ('{"answer": "maybe"}', {"form": "yesno"}),
    ('{"answer": "other"}', {"form": "choice", "options": ["investment", "policy"]}),
    ('{"labels": ["c"]}', {"form": "labels", "options": ["a", "b"], "max": 2}),
    ('{"labels": ["a", "b", "a"]}', {"form": "labels", "options": ["a", "b"], "max": 2}),
    ("", {"form": "grade", "max": 3}),
])
def test_anything_else_is_not_read(text, reply):
    with pytest.raises(ValueError):
        chat.parse(text, reply)


@pytest.mark.parametrize("text,reply,expect", [
    ('Sure: {"grade": 2}', {"form": "grade", "max": 3}, {"grade": 2, "labels": None}),
    ('{"answer": "no"}\nI chose no because the text is silent.', {"form": "yesno"}, {"grade": 0, "labels": ["no"]}),
    ('Reasoning first.\n```json\n{"labels": []}\n```\nDone.', {"form": "labels", "options": ["a"], "max": 2},
     {"grade": None, "labels": []}),
])
def test_prose_before_or_after_one_json_object_is_read_from_the_object(text, reply, expect):
    assert chat.parse(text, reply) == expect


def test_reasoning_is_switched_off_in_the_request(pg, fake_chat):
    chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert fake_chat.calls[-1]["reasoning"] == {"enabled": False}
    assert panel.require("second.reasoning") is False or panel.require("second.reasoning") == False  # noqa: E712
    assert "reasoning" not in GRADE and "reasoning" not in GRADE["reply"]


def test_the_reasoning_setting_decides(pg, fake_chat, monkeypatch):
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "second.reasoning": True})
    chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert "reasoning" not in fake_chat.calls[-1]


def test_the_estimate_is_an_upper_bound_at_the_token_cap():
    q = Q.chat_question("Grade it.", "grade", maximum=3)
    assert chat.estimate("x" * 400, q) >= 1000 * 0.5 / 1e6


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


def test_an_unreadable_reply_is_a_failure_row_never_an_answer_and_a_later_reading_is_kept(pg, fake_chat):
    fake_chat.reply = lambda prompt: "Yes, I think so."
    got, st = chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert got == {} and st["unparsed"] == 1
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 0
    f = db.rows(pg, f"SELECT model, state_sha, question_sha, raw, at FROM {db.STORE}.chat_failures")
    assert len(f) == 1 and f[0][3] == "Yes, I think so." and f[0][4] is not None
    fake_chat.reply = lambda prompt: '{"grade": 1}'
    got, st = chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert got["a"]["grade"] == 1 and st["unparsed"] == 0 and len(fake_chat.calls) == 2
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 1
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.chat_failures")[0] == 1       # the failure row stays
    assert chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)[1]["to_ask"] == 0


def test_a_question_unreadable_max_attempts_times_is_given_up_and_a_third_run_asks_nothing(pg, fake_chat):
    fake_chat.reply = lambda prompt: "prose, not JSON"
    items = [("a", {"sentence": "one"}, GRADE), ("b", {"sentence": "two"}, GRADE)]
    logged = []
    for run_no, asked in ((1, 2), (2, 2), (3, 0), (4, 0)):
        n = len(fake_chat.calls)
        got, st = chat.ask(pg, items, stage="t", api_key="k", log=logged.append)
        assert got == {} and len(fake_chat.calls) - n == asked, run_no
    assert st["gave_up"] == 2 and "gave up on 2 question(s)" in chat.estimate_line("t", st)
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.chat_failures")[0] == 4
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 0           # never a row, never a yes
    # a reply that is JSON but not the answer asked for is a failure of the same kind
    # (the answers table is only ever added to: no UPDATE, no DELETE)


def test_the_chat_asker_never_updates_or_deletes_an_answer_row(pg, fake_chat):
    pg.execute(f"CREATE OR REPLACE FUNCTION {db.STORE}.no_change() RETURNS trigger AS $$ BEGIN "
               f"RAISE EXCEPTION 'answers is only added to'; END $$ LANGUAGE plpgsql")
    pg.execute(f"CREATE TRIGGER no_change BEFORE UPDATE OR DELETE ON {db.STORE}.answers "
               f"FOR EACH ROW EXECUTE FUNCTION {db.STORE}.no_change()")
    pg.commit()
    for reply in ("not json", "still not json", '{"grade": 2}'):
        fake_chat.reply = lambda prompt, r=reply: r
        chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
        chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)


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


def test_a_context_question_says_what_the_paragraph_is_for():
    q = Q.graded_question({"id": "x", "name": "X", "definition": "d"}, ["a", "b"])
    cq = Q.in_context(q)
    assert cq["instructions"].startswith("The 'context' field is the paragraph around the sentence. It is there "
                                         "only to show what the sentence refers to. ")
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

def committed(pg, c, name="cra"):
    return {u: (r["committed"], r["decided_by"], r["waiting"]) for u, r in decide.measure_units(pg, c, name).items()}


def test_a_pair_in_the_band_is_committed_only_when_both_looks_agree(pg, corpus, fake_chat):
    c = first_pass(pg, corpus, 0.6)
    u0 = c.index[0]["unit_id"]
    assert committed(pg, c)[u0] == (False, "", True)          # no second look yet: waiting, not committed
    assert look(pg, corpus, c, "cra") == 1
    assert committed(pg, c)[u0] == (True, "second_look", False)
    row = decide.measure_units(pg, c, "cra")[u0]
    assert row["score_first"] == pytest.approx(0.6) and row["score_context"] >= 0.7 and row["second_grade"] == 3
    ask = fake_chat.prompts()[0]
    assert "[sentence]" in ask and "[section]" in ask and "[context]" not in ask      # no paragraph for the chat grade


def test_the_chat_model_saying_no_keeps_the_pair_out(pg, corpus, fake_chat):
    c = first_pass(pg, corpus, 0.6)
    fake_chat.reply = lambda prompt: '{"grade": 1}'
    look(pg, corpus, c, "cra")
    assert committed(pg, c)[c.index[0]["unit_id"]] == (False, "", False)


def test_an_unreadable_second_look_is_never_a_yes(pg, corpus, fake_chat):
    c = first_pass(pg, corpus, 0.6)
    fake_chat.reply = lambda prompt: "grade 3"
    assert look(pg, corpus, c, "cra") is not None
    assert committed(pg, c)[c.index[0]["unit_id"]] == (False, "", True)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE kind = 'measure_second'")[0] == 0


def test_with_the_asked_band_narrowed_a_score_at_the_keep_level_needs_no_second_look(
        pg, corpus, fake_chat, fake_model, monkeypatch):
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "band.ask_low": live()["band.low"],
                                                    "band.ask_high": live()["band.keep"]})
    c = first_pass(pg, corpus, 0.95)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    n = len(fake_model)
    assert look(pg, corpus, c, "cra") == 0
    assert len(fake_model) == n and not fake_chat.calls
    assert committed(pg, c)[c.index[0]["unit_id"]] == (True, "score", False)


def test_a_rerun_with_everything_stored_asks_nothing(pg, corpus, fake_chat, fake_model):
    c = first_pass(pg, corpus, 0.6, units=(0, 1))
    look(pg, corpus, c, "cra")
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    assert n_chat == 2
    look(pg, corpus, c, "cra")
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE kind = 'measure_ctx'")[0] == 2


def test_a_dry_run_and_a_scope_with_no_such_project_ask_nothing(pg, corpus, fake_chat, fake_model):
    c = first_pass(pg, corpus, 0.6)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    n = len(fake_model)
    assert look(pg, corpus, c, "cra", dry_run=True) is None
    assert look(pg, corpus, c, "cra", projects=["P9"]) == 0
    assert len(fake_model) == n and not fake_chat.calls
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE kind = 'measure_ctx'")[0] == 0


def test_later_stages_ask_about_the_sentences_committed_under_the_band_rule(pg, corpus, fake_chat, fake_model):
    c = first_pass(pg, corpus, 0.6)
    assert checks.committed_at_ask(pg, c, "cra") == []                # in the band, no second look: not asked
    assert second.measure_second(pg, corpus, c, "cra", api_key="k", **QUIET) == 0   # no digital answer yet: waiting
    assert checks.committed_at_ask(pg, c, "cra") == []                # nothing asked, nothing committed
    look(pg, corpus, c, "cra")
    assert checks.committed_at_ask(pg, c, "cra") == [c.index[0]["unit_id"]]
    assert checks.types(pg, corpus, c, "cra", api_key="k", **QUIET) == 2
    # the same sentence below the band is not asked: floor.ask no longer decides
    c = first_pass(pg, corpus, 0.6, units=())
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = 0.8 WHERE kind = 'measure'")
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind IN ('measure_ctx', 'measure_second')")
    pg.commit()
    assert checks.committed_at_ask(pg, c, "cra") == []


def test_digital_is_asked_once_per_committed_sentence_and_a_rerun_asks_nothing(pg, corpus, fake_chat, fake_model):
    c = first_pass(pg, corpus, 0.95, units=(0, 2))
    u0, u1 = c.index[0]["unit_id"], c.index[2]["unit_id"]
    assert second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET) == 2
    asked = [b for b in fake_model if "digital" in json.dumps(b["questions"]).lower()]
    assert len(asked) == 2 and "context" in asked[0]["state"]
    q = list(asked[0]["questions"].values())[0]
    assert q["instructions"].startswith(Q.CONTEXT_NOTE + "How far is the sentence in the 'sentence' field about a "
                                        "digital technology? A digital technology is: ")
    assert len(q["criteria"]) == 4 and q["criteria"][0] == "Not at all."
    dig = decide.digital_scores(pg)
    assert dig[u0] >= 0.5 > dig[u1]
    assert (decide.is_digital(dig.get(u0)), decide.is_digital(dig.get(u1)), decide.is_digital(None)) == (True, False, False)
    assert decide.committed_digital_units(pg, c, ["cra"]) == [u0]
    n = len(fake_model)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    assert len(fake_model) == n


def test_loosening_band_context_counts_the_newly_committed_sentences_with_no_model_call(
        pg, corpus, fake_chat, fake_model, monkeypatch):
    c = first_pass(pg, corpus, 0.6, units=(0,))
    u0 = c.index[0]["unit_id"]
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "band.context": 1.01})   # nothing passes the look
    look(pg, corpus, c, "cra")
    assert second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET) == 1           # asked though not committed
    assert committed(pg, c)[u0] == (False, "", False)
    assert decide.committed_digital_units(pg, c, ["cra"]) == []
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "band.context": 0.6})    # loosened at publish
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    assert committed(pg, c)[u0] == (True, "second_look", False)
    assert decide.committed_digital_units(pg, c, ["cra"]) == [u0]
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.measure_findings WHERE is_committed AND is_digital")[0] == 1
    assert second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET) == 1                      # all from the store
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat)


def test_policy_is_asked_of_committed_digital_sentences_only_and_a_rerun_asks_nothing(pg, corpus, fake_chat):
    c = first_pass(pg, corpus, 0.95, units=(0, 2))
    u0, u1 = c.index[0]["unit_id"], c.index[2]["unit_id"]            # u1 (the road) is committed but not digital
    fake_chat.reply = lambda prompt: ('{"answer": "policy"}' if "road" in prompt else '{"answer": "investment"}')
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    n = len(fake_chat.calls)
    assert second.policy(pg, corpus, c, ["cra"], api_key="k", **QUIET) == 1
    assert len(fake_chat.calls) - n == 1 and decide.instruments(pg) == {u0: "investment"}
    assert "[context]" in fake_chat.prompts()[-1] and "technical assistance to draft a regulation" in fake_chat.prompts()[-1]
    second.policy(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    assert len(fake_chat.calls) - n == 1


def test_publish_carries_the_new_columns_and_keeps_the_not_digital_out_of_the_commitments(
        pg, corpus, fake_chat, fake_model):
    c = first_pass(pg, corpus, 0.6, units=(0, 2))
    pg.execute(f"UPDATE {db.SCHEMA}.scores SET score = 0.95 WHERE kind = 'measure' AND item_id = %s",
               (c.index[2]["unit_id"],))
    pg.commit()
    u0, u1 = c.index[0]["unit_id"], c.index[2]["unit_id"]
    funnel_assets = funnel.run(pg, corpus, "assets", corpus=c, api_key="k", **QUIET)
    assert funnel_assets is not None
    look(pg, corpus, c, "cra")
    checks.types(pg, corpus, c, "cra", api_key="k", **QUIET)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    second.policy(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    logged = []
    decide.publish(pg, corpus, corpus=c, log=logged.append)
    rows = {r[0]: r for r in db.rows(
        pg, f"SELECT unit_id, is_committed, decided_by, score_first, score_context, second_grade, is_digital, "
            f"digital_p, instrument, passes_measure_rule FROM {db.SCHEMA}.measure_findings")}
    r0, r1 = rows[u0], rows[u1]
    assert r0[1:3] == (True, "second_look") and float(r0[3]) == 0.6 and float(r0[4]) >= 0.7 and r0[5] == 3
    assert r0[6] is True and r0[8] == "investment" and r0[9] is True
    # the road passes the measure rule but is not digital: it is not kept
    assert r1[1:3] == (False, "score") and r1[9] is True
    assert r1[4] is None and r1[5] is None and r1[6] is False and float(r1[7]) < 0.5
    nd = db.rows(pg, f"SELECT unit_id, project_id, digital_p FROM {db.SCHEMA}.not_digital_findings")
    assert [(u, p) for u, p, _ in nd] == [(u1, "P1")]
    assert u1 not in {u for (u,) in db.rows(pg, f"SELECT unit_id FROM {db.SCHEMA}.commitment_units")}
    assert u0 in {u for (u,) in db.rows(pg, f"SELECT unit_id FROM {db.SCHEMA}.commitment_units")}
    assert db.one(pg, f"SELECT band_low, digital_floor, second_model "
                      f"FROM {db.SCHEMA}.dashboard_settings") == (0.5, 0.5, "anthropic/claude-haiku-5.5")


def test_publish_says_how_many_pairs_wait_for_a_second_look(pg, corpus, fake_model):
    c = first_pass(pg, corpus, 0.6)
    logged = []
    decide.publish(pg, corpus, corpus=c, log=logged.append, views=False)
    assert any("waiting for a second look" in line for line in logged)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.measure_findings WHERE is_committed")[0] == 0


@pytest.mark.parametrize("step,kind", [("digital", "digital"), ("policy", "policy")])
def test_a_run_of_some_measures_keeps_the_rows_of_the_other_measures_sentences(
        pg, corpus, fake_chat, fake_model, step, kind):
    c = first_pass(pg, corpus, 0.95, units=(0, 2))
    other = "X:other-measures-sentence"
    run = getattr(second, step)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)           # policy asks only digital sentences
    checks.write_scores(pg, kind, kind, [checks._row(kind, kind, kind, other, {"s": 1}, {"q": 1},
                                                      {"labels": ["policy"]}, score=1)])
    pg.commit()
    # a subset run (cra of cra and another measure): the other measure's row stays
    kept = run(pg, corpus, c, ["cra"], every=["cra", "another"], api_key="k", **QUIET)
    assert kept == (2 if step == "digital" else 1)
    items = {r[2] for r in decide.scores(pg, kind)}
    assert other in items and len(items) == kept + 1
    # a run over every measure replaces the whole kind, as before
    run(pg, corpus, c, ["cra"], every=["cra"], api_key="k", **QUIET)
    assert other not in {r[2] for r in decide.scores(pg, kind)}


def test_every_chat_request_carries_max_tokens(pg, fake_chat):
    chat.ask(pg, [("a", {"sentence": "one"}, GRADE)], stage="t", api_key="k", **QUIET)
    assert fake_chat.calls[-1]["max_tokens"] == panel.require("second.max_tokens") == 1000


def test_a_response_without_a_cost_is_priced_from_its_tokens_or_the_estimate(pg, fake_chat, monkeypatch):
    from wbg_portfolio import api
    inner = api.post
    seen = {}

    def post(path, b, key, **kw):
        r = inner(path, b, key, **kw)
        r["usage"] = seen["usage"]
        return r
    monkeypatch.setattr(api, "post", post)
    q, state = GRADE, {"sentence": "one"}
    text = chat.prompt(state, q)
    seen["usage"] = {"prompt_tokens": 1_000_000, "completion_tokens": 2_000_000}      # no cost field
    got, st = chat.ask(pg, [("a", state, q)], stage="t", api_key="k", workers=1, log=lambda *_: None)
    assert abs(st["cost_usd"] - (0.10 + 2 * 0.50)) < 1e-9
    seen["usage"] = {}                                                                   # no usage at all
    got, st = chat.ask(pg, [("b", {"sentence": "two"}, q)], stage="t", api_key="k", workers=1, log=lambda *_: None)
    assert st["cost_usd"] > 0 and abs(st["cost_usd"] - chat.estimate(chat.prompt({"sentence": "two"}, q), q)) < 1e-12
    seen["usage"] = {"cost": 0.25}                                                       # a reported cost wins
    got, st = chat.ask(pg, [("c", {"sentence": "three"}, q)], stage="t", api_key="k", workers=1, log=lambda *_: None)
    assert st["cost_usd"] == 0.25


def test_the_estimate_uses_the_prompt_length_and_the_panels_prices(pg, fake_chat):
    short = chat.ask(pg, [("a", {"sentence": "x"}, GRADE)], stage="t", dry_run=True)[1]["estimate_usd"]
    long = chat.ask(pg, [("a", {"sentence": "x" * 40_000}, GRADE)], stage="t", dry_run=True)[1]["estimate_usd"]
    assert abs((long - short) - 0.001) < 1e-6                    # 10,000 more prompt tokens at US$0.10 per million


# ------------------------------------------------ what is asked and what decides

def pin(monkeypatch, **kw):
    live = panel.settings
    monkeypatch.setattr(panel, "settings", lambda: {**live(), **kw})


def test_a_pair_above_the_keep_line_gets_a_second_look_and_stays_committed_whatever_it_says(
        pg, corpus, fake_chat):
    c = first_pass(pg, corpus, 0.95)
    u0 = c.index[0]["unit_id"]
    fake_chat.reply = lambda prompt: '{"grade": 0}'
    assert look(pg, corpus, c, "cra") == 1
    row = decide.measure_units(pg, c, "cra")[u0]
    assert (row["committed"], row["decided_by"], row["waiting"]) == (True, "score", False)
    assert row["second_grade"] == 0 and row["score_context"] is not None      # stored, and readable


def test_moving_the_keep_line_up_changes_the_decision_with_no_model_call(pg, corpus, fake_chat, fake_model,
                                                                          monkeypatch):
    c = first_pass(pg, corpus, 0.95)
    u0 = c.index[0]["unit_id"]
    fake_chat.reply = lambda prompt: '{"grade": 1}'
    look(pg, corpus, c, "cra")
    calls = (len(fake_model), len(fake_chat.calls))
    assert committed(pg, c)[u0] == (True, "score", False)
    pin(monkeypatch, **{"band.keep": 0.97})                        # the 0.95 pair is now in the band
    assert committed(pg, c)[u0] == (False, "", False)              # the stored chat grade (1) is under 2
    fake_chat.reply = lambda prompt: '{"grade": 3}'
    pin(monkeypatch, **{"band.keep": 0.97, "band.second_grade": 1})
    assert committed(pg, c)[u0] == (True, "second_look", False)
    assert look(pg, corpus, c, "cra") == 1       # nothing new to ask
    assert (len(fake_model), len(fake_chat.calls)) == calls


def test_the_asked_band_is_what_is_asked_and_never_below_it(pg, corpus, fake_chat, monkeypatch):
    c = first_pass(pg, corpus, 0.45, units=(0, 1))
    assert look(pg, corpus, c, "cra") == 0       # under band.ask_low
    pin(monkeypatch, **{"band.ask_low": 0.4, "band.low": 0.4})
    assert look(pg, corpus, c, "cra") == 2
    pin(monkeypatch, **{"band.ask_high": 0.95})
    c2 = first_pass(pg, corpus, 0.95, units=(0,))
    assert decide.measure_units(pg, c2, "cra")[c2.index[0]["unit_id"]]["asked"] is False


@pytest.mark.parametrize("settings,words", [
    ({"band.ask_low": 0.6}, "band.ask_low (0.6) is above"),
    ({"band.ask_high": 0.8}, "band.ask_high (0.8) is below"),
    ({"link.asset_ask_low": 0.4}, "link.asset_ask_low (0.4) is above"),
    ({"link.asset_ask_high": 0.2}, "link.asset_ask_high (0.2) is below"),
])
def test_a_decision_band_wider_than_the_asked_band_fails_loudly(monkeypatch, settings, words):
    pin(monkeypatch, **settings)
    with pytest.raises(SystemExit) as e:
        decide.band()
        decide.package_band()
    assert words in str(e.value)


def test_the_asset_ask_line_cannot_be_under_the_asset_floor(monkeypatch):
    pin(monkeypatch, **{"asset.ask_high": 0.1})
    with pytest.raises(SystemExit) as e:
        decide.asset_ask_high()
    assert "asset.ask_high (0.1) is below asset.floor" in str(e.value)


# ----------------------------------------------- the digital question comes first and gates the looks

def test_the_second_look_is_asked_only_for_sentences_the_digital_question_does_not_rule_out(
        pg, corpus, fake_chat, fake_model):
    c = first_pass(pg, corpus, 0.6, units=(0, 2))                  # a tower sentence and a road sentence
    u0, u1 = c.index[0]["unit_id"], c.index[2]["unit_id"]
    lines = []
    # no digital answer yet: both wait, nothing is asked, and a dry run says it cannot estimate
    assert second.measure_second(pg, corpus, c, "cra", api_key="k", log=lines.append) == 0
    assert "2 waiting for the digital question" in lines[0] and not fake_chat.calls
    assert second.measure_second(pg, corpus, c, "cra", dry_run=True, log=lines.append) is None
    assert any("cannot estimate until digital has run" in x for x in lines)
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)       # tower 0.9, road 0.1 (under 0.2)
    lines.clear()
    assert second.measure_second(pg, corpus, c, "cra", api_key="k", log=lines.append) == 1
    assert "1 left out as not digital" in lines[0] and "0 waiting" in lines[0]
    asked = {r[0] for r in db.rows(pg, f"SELECT item_id FROM {db.SCHEMA}.scores WHERE kind = 'measure_ctx'")}
    assert asked == {u0}
    # the left-out pair is decided (not committed) and is not reported as waiting
    assert committed(pg, c)[u1] == (False, "", False)


def test_lowering_ask_low_asks_the_newly_included_and_an_ask_low_above_the_floor_fails_loudly(pg, corpus, fake_chat, fake_model,
                                                                             monkeypatch):
    live = panel.settings
    c = first_pass(pg, corpus, 0.6, units=(0, 2))
    second.digital(pg, corpus, c, ["cra"], api_key="k", **QUIET)
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "digital.ask_low": 0.0})
    assert second.measure_second(pg, corpus, c, "cra", api_key="k", **QUIET) == 2     # the road is asked too
    monkeypatch.setattr(panel, "settings", lambda: {**live(), "digital.ask_low": 0.3, "digital.floor": 0.2})
    with pytest.raises(SystemExit, match="digital.ask_low"):
        decide.digital_floor()
    with pytest.raises(SystemExit, match="digital.ask_low"):
        second.measure_second(pg, corpus, c, "cra", api_key="k", **QUIET)
