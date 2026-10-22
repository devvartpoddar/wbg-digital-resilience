"""The types stage (src/types.py): proposals from a fake generative model,
assignment by a fake decision model, on an invented measure run."""
import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import classify  # noqa: E402
import measure  # noqa: E402
import kinds as types_stage  # noqa: E402

UNITS = [("u1", "A hazard map will be prepared for the sites.", "3"),
         ("u2", "A site-specific risk assessment will be done for each tower.", "3"),
         ("u3", "The country faces floods.", "0")]


def _csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    run = data / "measures" / "cra-abc"
    _csv(str(run / "units.csv"), measure.UNIT_COLS, [
        {"label_id": "cra", "unit_id": u, "paragraph_id": "D:p1", "doc_id": "D",
         "project_ids": "P1", "component_number": "1", "sent": "true", "grade": g,
         "p3": 0.9 if g == "3" else 0.0, "text": t}
        for u, t, g in UNITS])
    (run / "manifest.json").write_text(json.dumps({"scale": ["a", "b", "c", "d"]}))
    monkeypatch.setattr(measure, "current_run", lambda name, d, **kw: str(run))
    import prior
    monkeypatch.setattr(prior, "word_df", lambda d: (10, {"hazards": 4, "maps": 2}))
    lab = tmp_path / "labels"
    _csv(str(lab / "cra.csv"), ["measure_id", "measure_name", "definition", "active"],
         [{"measure_id": "cra", "measure_name": "Climate risk assessment",
           "definition": "an assessment of hazards", "active": "true"}])
    import labels
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(lab),))
    gen_calls, dec_calls = [], []

    def fake_generate(body, **kw):
        gen_calls.append(body)
        if "NEW kinds" in body["messages"][0]["content"]:
            return {"model": "anthropic/claude-haiku-5.5", "usage": {"cost": 0.001},
                    "choices": [{"message": {"content": '{"types": [{"type_id": "Site",'
                                                        ' "type_name": "Site checks", "definition":'
                                                        ' "checks of one site", "examples": [1]}]}'}}]}
        return {"model": "anthropic/claude-haiku-5.5", "usage": {"cost": 0.001},
                "choices": [{"message": {"content": 'Here: {"types": [{"type_id": "Mapping",'
                                                    ' "type_name": "Hazard mapping", "definition":'
                                                    ' "maps of hazards", "examples": [1]}]}'}}]}

    def fake_decide(body, **kw):
        dec_calls.append(body)
        s = body["state"]["sentence"]
        out = {}
        for qid, q in body["questions"].items():
            opts = list(q["criteria"])
            pick = next((o for o in opts if o.split(".")[-1] in ("mapping",) and "map" in s),
                        None) or next((o for o in opts if o.split(".")[-1] == "site"
                                       and "site-specific" in s), None) or "none"
            out[qid] = {"type": "choice", "choice": pick,
                        "probabilities": {o: (0.9 if o == pick else 0.1 / (len(opts) - 1))
                                          for o in opts}}
        return {"model": measure.MODEL, "usage": {"cost": 0.00001}, "answers": out}
    monkeypatch.setattr(types_stage, "generate", fake_generate)
    monkeypatch.setattr(classify, "decide", fake_decide)
    return str(data), str(lab), gen_calls, dec_calls


def test_proposals_are_asked_once_per_run_with_only_committed_units(store):
    data, lab, gen_calls, _ = store
    out = types_stage.propose("cra", data, api_key="k", runs=2, log=lambda *_: None)
    assert len(gen_calls) == 2
    prompt = gen_calls[0]["messages"][0]["content"]
    assert "hazard map" in prompt and "faces floods" not in prompt
    rows = list(csv.DictReader(open(os.path.join(out, "proposals.csv"))))
    assert [(r["run"], r["type_id"]) for r in rows] == [("0", "mapping"), ("1", "mapping")]
    assert rows[0]["example_units"] in ("u1", "u2")
    assert types_stage.propose("cra", data, api_key="k", runs=2, log=lambda *_: None) == out
    assert len(gen_calls) == 2


def test_assignment_needs_an_approved_list_and_asks_one_yes_no_per_type(store):
    data, lab, _, dec_calls = store
    monkeypatch_data = os.environ.get("WBG_DATA")
    os.environ["WBG_DATA"] = data           # no proposal written yet: nothing to assign
    try:
        with pytest.raises(SystemExit):
            types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    finally:
        if monkeypatch_data is None:
            os.environ.pop("WBG_DATA", None)
        else:
            os.environ["WBG_DATA"] = monkeypatch_data
    _csv(os.path.join(lab, "cra_types.csv"),
         ["measure_id", "type_id", "type_name", "definition", "active"],
         [{"measure_id": "cra", "type_id": "mapping", "type_name": "Hazard mapping",
           "definition": "maps of hazards", "active": "true"},
          {"measure_id": "cra", "type_id": "site", "type_name": "Site assessment",
           "definition": "assessment of one site", "active": "true"}])
    out = types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    assert len(dec_calls) == 2
    qs = dec_calls[0]["questions"]
    assert list(qs) == ["type"] and qs["type"]["type"] == "choice"
    assert list(qs["type"]["criteria"]) == ["mapping", "site", "none"]
    rows = list(csv.DictReader(open(os.path.join(out, "assignments.csv"))))
    assert [(r["unit_id"], r["types_yes"]) for r in rows] == [("u1", "mapping"), ("u2", "site")]
    # The prior inputs: each type's name and definition matched against the sentence.
    assert {"prior_phrase_match_mapping", "prior_phrase_match_site"} <= set(rows[0])
    assert float(rows[0]["prior_phrase_match_mapping"]) > float(rows[0]["prior_phrase_match_site"])


def test_a_completion_without_the_json_is_not_stored():
    with pytest.raises(classify.TransportError):
        types_stage.parse_types({"choices": [{"message": {"content": "Sorry, no."}}]})


def test_the_proposal_is_the_list_until_a_person_writes_one(store, monkeypatch):
    data, lab, gen_calls, dec_calls = store
    monkeypatch.setenv("WBG_DATA", data)
    types_stage.propose("cra", data, api_key="k", runs=1, log=lambda *_: None)
    assert os.path.exists(os.path.join(data, "types", "cra_types.csv"))
    out = types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    rows = list(csv.DictReader(open(os.path.join(out, "assignments.csv"))))
    assert {r["types_yes"] for r in rows} <= {"mapping", ""} and len(rows) == 2


def test_a_person_list_means_no_proposals(store):
    data, lab, gen_calls, _ = store
    _csv(os.path.join(lab, "cra_types.csv"),
         ["measure_id", "type_id", "type_name", "definition", "active"],
         [{"measure_id": "cra", "type_id": "mapping", "type_name": "Hazard mapping",
           "definition": "maps of hazards", "active": "true"}])
    assert types_stage.propose("cra", data, api_key="k", runs=2, log=lambda *_: None)
    assert gen_calls == []


def test_units_no_type_claims_get_an_oth_type_from_the_gap_step(store, monkeypatch):
    data, lab, gen_calls, dec_calls = store
    monkeypatch.setenv("WBG_DATA", data)
    monkeypatch.setattr(types_stage, "OTH_MIN", 1)
    _csv(os.path.join(lab, "cra_types.csv"),
         ["measure_id", "type_id", "type_name", "definition", "active"],
         [{"measure_id": "cra", "type_id": "mapping", "type_name": "Hazard mapping",
           "definition": "maps of hazards", "active": "true"}])
    out = types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    assert len(gen_calls) == 1 and "NEW kinds" in gen_calls[0]["messages"][0]["content"]
    assert "Hazard mapping" in gen_calls[0]["messages"][0]["content"]
    rows = {r["unit_id"]: r for r in csv.DictReader(open(os.path.join(out, "assignments.csv")))}
    assert rows["u1"]["types_yes"] == "mapping"
    assert rows["u2"]["types_yes"] == "OTH.site"
    oth = list(csv.DictReader(open(os.path.join(data, "types", "cra_oth_types.csv"))))
    assert [(t["type_id"], t["type_name"]) for t in oth] == [("OTH.site", "OTH. Site checks")]
    import load_pg
    import labels
    typ = load_pg.type_rows(data)
    assert ("cra", "OTH.site") in {(t["label_set"], t["type_id"]) for t in typ}
    # a re-run asks nothing again
    n = (len(gen_calls), len(dec_calls))
    import shutil
    shutil.rmtree(out)
    types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    assert (len(gen_calls), len(dec_calls)) == n


def test_every_type_at_the_floor_counts(store, monkeypatch):
    data, lab, _, _ = store
    _csv(os.path.join(lab, "cra_types.csv"),
         ["measure_id", "type_id", "type_name", "definition", "active"],
         [{"measure_id": "cra", "type_id": "mapping", "type_name": "Hazard mapping",
           "definition": "maps of hazards", "active": "true"},
          {"measure_id": "cra", "type_id": "site", "type_name": "Site assessment",
           "definition": "assessment of one site", "active": "true"}])
    monkeypatch.setattr(types_stage, "TYPE_FLOOR", 0.04)     # the others answer 0.05
    out = types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    rows = {r["unit_id"]: r for r in csv.DictReader(open(os.path.join(out, "assignments.csv")))}
    assert rows["u1"]["types_yes"] == "mapping|site" and rows["u1"]["best_type"] == "mapping"
    monkeypatch.setattr(types_stage, "TYPE_MULTIPLE", False)
    out = types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    rows = {r["unit_id"]: r for r in csv.DictReader(open(os.path.join(out, "assignments.csv")))}
    assert rows["u1"]["types_yes"] == "mapping"
