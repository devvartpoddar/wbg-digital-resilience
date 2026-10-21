"""Ten items a request (src/answers.py BATCH): every question set out once,
each item in its own field, every answer stored under its own item and
question, so a re-run asks nothing whatever the neighbours were."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import answers  # noqa: E402
import classify  # noqa: E402

Q = {"m": {"type": "score", "instructions": "Grade the sentence in the 'sentence' field.",
           "criteria": ["not about it", "background", "planned", "committed"]},
     "k": {"type": "noul", "instructions": "Is it built?",
           "criteria": {"true": "Yes: built.", "false": "No."}},
     "c": {"type": "choice", "instructions": "Which asset?",
           "criteria": {"fiber": "Fiber networks: cables.", "none": "None of these."}}}


def fake_factory(calls):
    def fake(body, **kw):
        calls.append(body)
        out = {}
        for bq, q in body["questions"].items():
            item = bq.split(" | ")[0]
            hit = "tower" in body["state"][item]
            if q["type"] == "score":
                out[bq] = {"type": "score", "probabilities":
                           {"0": 0.1, "1": 0.1, "2": 0.1, "3": 0.7} if hit else
                           {"0": 0.7, "1": 0.1, "2": 0.1, "3": 0.1}}
            elif q["type"] == "noul":
                out[bq] = {"type": "noul", "noul": 0.9 if hit else 0.1}
            else:
                out[bq] = {"type": "choice", "probabilities":
                           {"fiber": 0.8 if hit else 0.2, "none": 0.2 if hit else 0.8}}
        return {"model": "m", "usage": {"cost": 0.0001}, "answers": out}
    return fake


def test_items_go_ten_a_request_and_come_back_under_their_own_keys(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(classify, "decide", fake_factory(calls))
    reqs = [({"sentence": f"{'tower' if i % 3 == 0 else 'road'} {i}"}, Q) for i in range(23)]
    got, st = answers.ask("m", reqs, str(tmp_path), api_key="k", log=lambda *_: None,
                          workers=1, batch=10)
    assert st["calls"] == 3 and len(calls) == 3
    assert [len(c["questions"]) for c in calls] == [30, 30, 9]
    assert "question m" in calls[0]["state"] and "item 01" in calls[0]["state"]
    assert all(g is not None for g in got)
    for (state, _), g in zip(reqs, got):
        hit = "tower" in state["sentence"]
        assert (g["m"]["answer"]["probabilities"]["3"] == 0.7) == hit
        assert (g["k"]["answer"]["noul"] == 0.9) == hit
        assert set(g["c"]["answer"]["probabilities"]) == {"fiber", "none"}
    # a re-run asks nothing; a new neighbour asks only itself
    got2, st2 = answers.ask("m", reqs + [({"sentence": "tower new"}, Q)], str(tmp_path),
                            api_key="k", log=lambda *_: None, workers=1, batch=10)
    assert st2["calls"] == 1 and len(calls[-1]["questions"]) == 3
    assert [g["m"]["answer"] for g in got2[:23]] == [g["m"]["answer"] for g in got]


def test_one_a_request_is_the_old_form(tmp_path, monkeypatch):
    calls = []

    def fake(body, **kw):
        calls.append(body)
        return {"model": "m", "usage": {"cost": 0}, "answers": {
            "m": {"type": "score", "probabilities": {"0": 1, "1": 0, "2": 0, "3": 0}}}}
    monkeypatch.setattr(classify, "decide", fake)
    reqs = [({"sentence": f"s {i}"}, {"m": Q["m"]}) for i in range(3)]
    answers.ask("m", reqs, str(tmp_path), api_key="k", log=lambda *_: None, workers=1, batch=0)
    assert len(calls) == 3 and calls[0]["state"] == {"sentence": "s 0"}


def test_one_sentence_asked_two_questions_with_one_id_keeps_both(tmp_path, monkeypatch):
    """The cross-measure pair question has the id "pair" for every pair of
    measures: one sentence, several questions, each answer kept apart."""
    calls = []

    def fake(body, **kw):
        calls.append(body)
        out = {}
        for bq in body["questions"]:
            out[bq] = {"type": "noul", "noul": 0.9 if "A or B" in body["state"]["question pair"] else 0.2}
        return {"model": "m", "usage": {"cost": 0}, "answers": out}
    monkeypatch.setattr(classify, "decide", fake)
    s = {"sentence": "solar with batteries"}
    qa = {"pair": {"type": "noul", "instructions": "A or B?", "criteria": {"true": "y", "false": "n"}}}
    qb = {"pair": {"type": "noul", "instructions": "C or D?", "criteria": {"true": "y", "false": "n"}}}
    got, _ = answers.ask("m", [(s, qa), (s, qb)], str(tmp_path), api_key="k",
                         log=lambda *_: None, workers=1, batch=10)
    assert got[0] and got[1]
    assert got[0]["pair"]["answer"]["noul"] == 0.9 and got[1]["pair"]["answer"]["noul"] == 0.2
