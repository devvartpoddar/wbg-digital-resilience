"""Classification with a decision model (src/classify.py), on an invented store
and a fake endpoint. No document text is committed."""
import csv
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import classify  # noqa: E402

TEXTS = ["The project will finance towers on raised plinths.",
         "Background on the economy.",
         "The project will finance towers on raised plinths."]   # a repeat


def _csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


@pytest.fixture
def store(tmp_path, monkeypatch):
    # The sentence map's items come from the embedded search units; here an
    # invented list of sentences, the component's name read from
    # components.csv as sentences() does.
    monkeypatch.setattr(classify, "MIN_COSINE", 0.0)
    data = tmp_path / "data"
    _csv(str(data / "appraisal" / "components.csv"), ["doc_id", "source", "number", "name"],
         [{"doc_id": "D", "source": "datasheet", "number": "1", "name": "Connectivity"}])

    def sentences(data_):
        names = {(r["doc_id"], r["number"]): r["name"] for r in csv.DictReader(
            open(os.path.join(data_, "appraisal", "components.csv")))}
        out = []
        for i, t in enumerate(TEXTS, 1):
            comp = "1" if i != 2 else ""
            state = {"section": "Components", "sentence": t}
            if comp:
                state["component"] = f"{comp} {names[('D', comp)]}"
            out.append({"row": {"unit_id": f"D:p{i:05d}:s001", "paragraph_id": f"D:p{i:05d}",
                                "doc_id": "D", "project_ids": "P1", "component_number": comp},
                        "state": state})
        return out
    monkeypatch.setattr(classify, "sentences", sentences)
    import prior
    import search
    monkeypatch.setattr(prior, "word_df", lambda data_: (10, {}))
    monkeypatch.setattr(search, "load_queries", lambda name, taxonomy=None: [])
    tax = tmp_path / "taxonomy"
    _csv(str(tax / "assets.csv"), ["asset_id", "asset_name", "definition", "active"], [
        {"asset_id": "towers", "asset_name": "Mobile networks", "definition": "masts",
         "active": "true"},
        {"asset_id": "old", "asset_name": "Old", "definition": "x", "active": "false"}])
    _csv(str(tax / "assets_scale.csv"), ["grade", "description"], [
        {"grade": 0, "description": "Not about {name}."},
        {"grade": 1, "description": "{name} mentioned."},
        {"grade": 2, "description": "{name} financed."}])
    calls = []

    def fake(body, **kw):
        calls.append(body)
        hit = "towers" in body["state"]["sentence"]
        probs = {"0": 0.1, "1": 0.1, "2": 0.8} if hit else {"0": 0.9, "1": 0.1, "2": 0.0}
        return {"model": "typesafe/jev-1.13-20260917", "usage": {"cost": 0.00001},
                "answers": {"towers": {"type": "score", "score": 1.7, "confidence": 0.8,
                                       "probabilities": probs}}}
    monkeypatch.setattr(classify, "decide", fake)
    return str(data), str(tax), calls


def _run(store, **kw):
    data, tax, _ = store
    kw.setdefault("min_cosine", 0.0)      # the full map: these stores have no search run
    return classify.classify("assets", data, api_key="k", taxonomy=tax, log=lambda *_: None,
                             workers=1, **kw)


def test_each_label_is_one_graded_question_naming_it():
    labels = [{"id": "towers", "name": "Mobile networks", "definition": "masts"}]
    q = classify.questions(labels, ["Not about {name}.", "{name} financed."])
    assert q["towers"]["type"] == "score"
    assert "Mobile networks means: masts" in q["towers"]["instructions"]
    assert q["towers"]["criteria"] == ["Not about Mobile networks.", "Mobile networks financed."]


def test_every_sentence_is_graded_and_a_repeat_asked_once(store):
    out = _run(store)
    _, _, calls = store
    assert len(calls) == 2                     # three sentences, one a repeat
    assert calls[0]["state"]["component"] == "1 Connectivity"
    rows = list(csv.DictReader(open(os.path.join(out, "scores.csv"))))
    assert [(r["unit_id"], r["grade"]) for r in rows] == [
        ("D:p00001:s001", "2"), ("D:p00002:s001", "0"), ("D:p00003:s001", "2")]
    assert set(classify.SCORE_COLS) <= set(rows[0])


def test_a_rerun_reads_the_store_and_asks_nothing(store):
    first = _run(store)
    _, _, calls = store
    n = len(calls)
    assert _run(store) == first and len(calls) == n


def test_an_incomplete_answer_is_not_stored(store, monkeypatch):
    monkeypatch.setattr(classify, "decide", lambda body, **kw: {"answers": {}})
    assert _run(store) is None
    data, _, _ = store
    import answers
    assert not os.path.exists(answers.root(data, classify.MODEL))


def test_the_grades_must_be_given(store):
    _, tax, _ = store
    os.remove(os.path.join(tax, "assets_scale.csv"))
    with pytest.raises(SystemExit):
        _run(store)


def test_a_dry_run_asks_nothing(store):
    _, _, calls = store
    assert _run(store, dry_run=True) is None and calls == []


def test_a_refusal_stops_the_run_and_stores_nothing(store, monkeypatch):
    calls = []

    def refuse(body, **kw):
        calls.append(1)
        raise classify.Refused("HTTP 402: no credit")
    monkeypatch.setattr(classify, "decide", refuse)
    with pytest.raises(SystemExit):
        _run(store)
    data, _, _ = store
    import answers
    assert not os.path.exists(answers.root(data, classify.MODEL))


def test_a_change_in_what_is_sent_is_a_new_version(store):
    data, tax, _ = store
    first = _run(store)
    _csv(os.path.join(data, "appraisal", "components.csv"), ["doc_id", "source", "number",
                                                             "name"],
         [{"doc_id": "D", "source": "datasheet", "number": "1", "name": "Renamed"}])
    assert _run(store) != first


def test_expected_grade_is_computed_from_the_probabilities(store):
    out = _run(store)
    rows = list(csv.DictReader(open(os.path.join(out, "scores.csv"))))
    assert rows[0]["expected"] == "1.700"          # 0*0.1 + 1*0.1 + 2*0.8


def test_spend_is_recorded_per_run_and_summed_for_today(store):
    data, _, _ = store
    _run(store)
    calls, cost = classify.spend_today(data)
    assert calls == 2 and cost == 0.0        # the ledger keeps four decimals; US$0.00003 rounds away


def test_a_ledger_row_holding_a_float_count_still_reads(store):
    """A link run before its count was an integer wrote "33.0" into the
    ledger, and the ledger is never rewritten: the day's total must still
    parse, or the next stage's log line stops the run."""
    data, _, _ = store
    _run(store)
    calls, cost = classify.spend_today(data)
    path = os.path.join(data, "reports", "spend.csv")
    with open(path, newline="") as fh:
        cols = csv.DictReader(fh).fieldnames
    assert cols
    with open(path, "a", newline="") as fh:
        csv.DictWriter(fh, fieldnames=cols, lineterminator="\n").writerow(
            {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stage": "link",
             "label_set": "packages", "model": classify.MODEL, "calls": "33.0",
             "cost_usd": "0.0007", "seconds": "2", "run": "packages-bc0a3596c38d"})
    again = classify.spend_today(data)
    assert again[0] == calls + 33
    assert again[1] == pytest.approx(cost + 0.0007)


def test_readers_find_the_run_whatever_gate_it_was_made_with(store):
    """The measure and the load ask for the current map without naming a
    gate: the marker the run wrote says which one to look under."""
    data, tax, _ = store
    first = _run(store)
    assert classify.current_run("assets", data, taxonomy=tax) == first           # gate None
    assert classify.current_run("assets", data, taxonomy=tax, min_cosine=0.0) == first
    marker = classify._marker(data, "assets", None)
    assert os.path.exists(marker)


def test_a_run_made_before_markers_gets_one_when_found_again(store):
    data, tax, _ = store
    first = _run(store)
    os.remove(classify._marker(data, "assets", None))
    assert classify.current_run("assets", data, taxonomy=tax) == first   # falls back to the default gate
    assert _run(store) == first                                           # "already written" path
    assert os.path.exists(classify._marker(data, "assets", None))


def test_a_reader_finds_the_run_without_planning_again(store, monkeypatch):
    """current_run remembers which folder its inputs plan to: the second
    lookup reads that, and a changed input plans again."""
    data, tax, _ = store
    first = _run(store)
    assert classify.current_run("assets", data, taxonomy=tax, min_cosine=0.0) == first
    calls = []
    real = classify.plan
    monkeypatch.setattr(classify, "plan", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert classify.current_run("assets", data, taxonomy=tax, min_cosine=0.0) == first
    assert calls == []
    _csv(os.path.join(data, "appraisal", "components.csv"), ["doc_id", "source", "number",
                                                             "name"],
         [{"doc_id": "D", "source": "datasheet", "number": "1", "name": "Renamed"}])
    assert classify.current_run("assets", data, taxonomy=tax, min_cosine=0.0) is None
    assert calls == [1]


def test_a_set_never_classified_is_not_planned(store, monkeypatch):
    data, tax, _ = store
    monkeypatch.setattr(classify, "plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert classify.current_run("cra", data, taxonomy=tax) is None


def test_answers_under_the_alias_are_reused_for_the_snapshot(tmp_path, monkeypatch):
    """An answer asked under the moving alias counts for the dated snapshot
    when that snapshot served it; one served by another snapshot does not."""
    import answers
    data = str(tmp_path)
    q = {"type": "noul", "instructions": "towers?", "criteria": {"true": "y", "false": "n"}}
    for state, served in (({"p": "a"}, "snap"), ({"p": "b"}, "other")):
        answers.write(answers.root(data, "alias"), answers.key("alias", state, q),
                      {"answer": {"type": "noul", "noul": 0.9}, "model": served})
    asked = []
    monkeypatch.setattr(classify, "decide", lambda body, **k: asked.append(body) or
                        {"model": "snap", "answers": {"q": {"type": "noul", "noul": 0.1}}})
    got, st = answers.ask("snap", [({"p": "a"}, {"q": q}), ({"p": "b"}, {"q": q})], data,
                          api_key="k", workers=1, aliases=("alias",))
    assert len(asked) == 1 and asked[0]["state"] == {"p": "b"}
    assert got[0]["q"]["answer"]["noul"] == 0.9 and got[0]["q"]["migrated_from"] == "alias"


def test_assets_get_the_measures_routes_patterns_and_feedback_rounds(store, monkeypatch, tmp_path):
    """A gated map asks the gate's sentences and the pattern's, then looks
    again below the gate: each round asks the sentences nearest those that
    count, and an asset stops when a round finds fewer than min_yield."""
    import numpy
    data, tax, calls = store
    texts = ["The project will finance towers.",          # s1 above the gate
             "Raised towers on plinths.",                  # s2 below it, near s1
             "Background on the economy.",                 # s3 below, far
             "Other towers elsewhere.",                    # s4 below, farthest
             "New masts in the north."]                    # s5 below; the pattern matches
    vecs = numpy.array([[1, 0, 0], [0.95, 0.05, 0], [0.2, 1, 0], [0, 0, 1], [0, 0.3, 1]],
                       dtype="float32")
    vecs /= numpy.linalg.norm(vecs, axis=1, keepdims=True)
    os.makedirs(os.path.join(data, "embeddings"), exist_ok=True)
    numpy.save(os.path.join(data, "embeddings", "sentence_emb.npy"), vecs)
    ids = [f"D:p{i:05d}:s001" for i in range(1, 6)]
    _csv(os.path.join(data, "embeddings", "sentence_index.csv"), ["unit_id", "project_ids"],
         [{"unit_id": u, "project_ids": "P1"} for u in ids])
    monkeypatch.setattr(classify, "sentences", lambda d: [
        {"row": {"unit_id": u, "paragraph_id": u[:8], "doc_id": "D", "project_ids": "P1",
                 "component_number": ""}, "state": {"section": "S", "sentence": t}, "i": i}
        for i, (u, t) in enumerate(zip(ids, texts))])
    monkeypatch.setattr(classify, "best_cosines", lambda name, d, taxonomy=None: {
        ("towers", u): c for u, c in zip(ids, [0.5, 0.1, 0.1, 0.05, 0.05])})
    _csv(os.path.join(tax, "assets_patterns.csv"), ["asset_id", "pattern_id", "pattern", "active"],
         [{"asset_id": "towers", "pattern_id": "masts", "pattern": r"\bmasts?\b",
           "active": "true"}])
    p = tmp_path / "panel.csv"
    p.write_text("setting,value\nread.grades,2|3\nasset.floor,0.5\nfeedback.batch,1\n"
                 "feedback.min_yield,1\nfeedback.max_rounds,3\nfeedback.negative_weight,0.5\n")
    monkeypatch.setenv("WBG_PANEL", str(p))
    out = _run(store, min_cosine=0.3)
    rows = {r["unit_id"]: r for r in csv.DictReader(open(os.path.join(out, "scores.csv")))}
    got = {u: (rows[u]["asked"], rows[u]["route"], rows[u]["round"]) for u in ids}
    assert got[ids[0]] == ("true", "embedding", "0")
    assert got[ids[4]] == ("true", "pattern", "0")
    assert got[ids[1]] == ("true", "feedback", "1")      # nearest the yes: asked, and yes
    assert got[ids[2]][:2] == ("true", "feedback")        # the next round: no yes, so it stops
    assert got[ids[3]][0] == "false"
    man = json.load(open(os.path.join(out, "manifest.json")))
    assert [h["yes"] for h in man["feedback_rounds"]["towers"]] == [1, 0]
    # A rerun reads everything from the store.
    n = len(calls)
    import shutil
    shutil.rmtree(out)
    assert _run(store, min_cosine=0.3) == out and len(calls) == n
