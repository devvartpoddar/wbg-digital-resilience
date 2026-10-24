"""One asker for every question (src/ask.py): what is stored is read back,
what is not is asked in batches and stored under the exact text and
question sent."""
import json
import os

import pytest

import ask
import db
import questions as Q

LAB = {"id": "cra", "name": "Hazard assessment", "definition": "an assessment of hazards"}
SCALE = ["Not {name}.", "{name} as background.", "{name} planned.", "{name} committed."]
GQ = Q.graded_question(LAB, SCALE)
FQ = Q.fit_question({"id": "site", "name": "Site assessment", "definition": "at sites"},
                    given="Already committed. ")


def items(n, word="hazard assessment"):
    return [(f"u{i}", {"sentence": f"{word if i % 2 == 0 else 'road'} {i}"}, {"cra": GQ})
            for i in range(n)]


def test_the_score_reads_the_raw_probabilities_out_of_their_total():
    a = {"probabilities": {"0": 0.2, "1": 0.2, "2": 0.2, "3": 0.6}}   # sums to 1.2
    assert abs(Q.score(a) - 0.8 / 1.2) < 1e-9
    f = {"probabilities": {"0": 0.1, "1": 0.1, "2": 0.8}}
    assert abs(Q.score(f, fit=True) - 0.8) < 1e-9


def test_what_is_asked_is_stored_and_a_rerun_asks_nothing(pg, fake_model):
    got, st = ask.ask(pg, "m", items(5), stage="t", api_key="k", batch=10, log=lambda *_: None)
    assert st["calls"] == 1 and len(got) == 5
    assert Q.score(got["u0"]["cra"]) > 0.85 > 0.1 > Q.score(got["u1"]["cra"])
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 5
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.states")[0] == 5
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.questions")[0] == 1
    assert db.one(pg, f"SELECT calls, cost_usd FROM {db.STORE}.spend")[0] == 1
    got2, st2 = ask.ask(pg, "m", items(6), stage="t", api_key="k", batch=10, log=lambda *_: None)
    assert st2["calls"] == 1 and len(fake_model[-1]["questions"]) == 1    # the new item alone
    assert got2["u0"]["cra"]["probabilities"] == got["u0"]["cra"]["probabilities"]


def test_a_dry_run_asks_nothing_and_estimates(pg, fake_model):
    got, st = ask.ask(pg, "m", items(4), stage="t", dry_run=True, batch=10)
    assert not fake_model and got == {} and st["to_ask"] == 1 and st["estimate_usd"] > 0


def test_batches_are_split_by_question_set_and_ordered_by_state_hash(pg, fake_model):
    mixed = items(4) + [("p0", {"sentence": "site x"}, {"site": FQ})]
    ask.ask(pg, "m", mixed, stage="t", api_key="k", batch=10, log=lambda *_: None)
    assert len(fake_model) == 2
    sizes = sorted(len(b["questions"]) for b in fake_model)
    assert sizes == [1, 4]
    big = next(b for b in fake_model if len(b["questions"]) == 4)
    shown = [big["state"][f"i{i:02d}"] for i in range(1, 5)]
    expect = sorted(shown, key=lambda s: Q.sha({"sentence": s.split(": ", 1)[1]}))
    assert shown == expect


def test_one_item_a_request_when_the_batch_is_off(pg, fake_model):
    ask.ask(pg, "m", items(3), stage="t", api_key="k", batch=0, log=lambda *_: None)
    assert len(fake_model) == 3
    assert {b["state"]["sentence"] for b in fake_model} == {"hazard assessment 0", "road 1",
                                                            "hazard assessment 2"}


def test_an_answer_in_the_old_file_store_is_read_once_and_copied(pg, fake_model, tmp_path):
    state, q = {"sentence": "hazard assessment 0"}, GQ
    k = Q.sha({"model": "m", "state": state, "question": q})
    path = tmp_path / "classify" / "answers" / "m" / k[:2] / f"{k}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"answer": {"type": "score", "probabilities":
                                           {"0": 0, "1": 0, "2": 0.5, "3": 0.5}},
                                "model": "m", "cost_usd": 0.001}))
    got, st = ask.ask(pg, "m", items(1), stage="t", api_key="k", data=str(tmp_path),
                      log=lambda *_: None)
    assert not fake_model and st["imported"] == 1
    assert got["u0"]["cra"]["probabilities"]["2"] == 0.5
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 1


def test_a_refusal_stops_and_records_the_spend_so_far(pg, monkeypatch):
    import api
    n = [0]

    def post(path, b, key, **kw):
        n[0] += 1
        if n[0] == 1:
            return {"model": "m", "usage": {"cost": 0.01}, "answers": {
                bq: {"type": "score", "probabilities": {"0": 1, "1": 0, "2": 0, "3": 0}}
                for bq in b["questions"]}}
        raise api.Refused("HTTP 401")
    monkeypatch.setattr(api, "post", post)
    with pytest.raises(api.Refused):
        ask.ask(pg, "m", items(2), stage="t", api_key="k", batch=0, workers=1, log=lambda *_: None)
    assert db.one(pg, f"SELECT calls FROM {db.STORE}.spend")[0] == 1


def test_the_spend_cap_stops_before_a_step_that_would_pass_it(pg, fake_model, monkeypatch):
    import api
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "0.0000001")
    monkeypatch.setenv("WBG_SPEND_SINCE", "1970-01-01")
    with pytest.raises(api.CapReached):
        ask.ask(pg, "m", items(2), stage="t", api_key="k", batch=10, log=lambda *_: None)
    assert not fake_model


def test_an_incomplete_answer_is_a_failure_and_is_not_stored(pg, monkeypatch):
    import api
    monkeypatch.setattr(api, "post", lambda *a, **k: {"model": "m", "answers": {}})
    got, st = ask.ask(pg, "m", items(1), stage="t", api_key="k", batch=0, log=lambda *_: None)
    assert got == {} and len(st["failures"]) == 1
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 0


def test_a_sentence_state_carries_section_component_and_optionally_the_paragraph(corpus,
                                                                                   monkeypatch):
    c = Q.Corpus(corpus)
    uid = c.index[0]["unit_id"]
    s = c.state(uid)
    assert s["sentence"].startswith("The project will finance a hazard assessment")
    assert s["section"] == "Project Components" and s["component"] == "1 Connectivity"
    assert "paragraph" not in s
    import panel
    monkeypatch.setenv("WBG_PANEL", os.environ["WBG_PANEL"])
    monkeypatch.setattr(panel, "get", lambda k, d=None: "paragraph" if k == "ask.context" else d)
    c2 = Q.Corpus(corpus)
    assert "Floods are frequent." in c2.state(uid)["paragraph"]
    assert c.vector(uid).shape == (4,) and c.projects(uid) == ["P1"]
