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
         "project_ids": "P1", "component_number": "1", "sent": "true", "grade": g, "text": t}
        for u, t, g in UNITS])
    (run / "manifest.json").write_text(json.dumps({"scale": ["a", "b", "c", "d"]}))
    monkeypatch.setattr(measure, "current_run", lambda name, d, **kw: str(run))
    lab = tmp_path / "labels"
    _csv(str(lab / "cra.csv"), ["measure_id", "measure_name", "definition", "active"],
         [{"measure_id": "cra", "measure_name": "Climate risk assessment",
           "definition": "an assessment of hazards", "active": "true"}])
    import labels
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(lab),))
    gen_calls, dec_calls = [], []

    def fake_generate(body, **kw):
        gen_calls.append(body)
        return {"model": "anthropic/claude-haiku-5.5", "usage": {"cost": 0.001},
                "choices": [{"message": {"content": 'Here: {"types": [{"type_id": "Mapping",'
                                                    ' "type_name": "Hazard mapping", "definition":'
                                                    ' "maps of hazards", "examples": [1]}]}'}}]}

    def fake_decide(body, **kw):
        dec_calls.append(body)
        s = body["state"]["sentence"]
        return {"model": measure.MODEL, "usage": {"cost": 0.00001},
                "answers": {"mapping": {"type": "noul", "noul": 0.9 if "map" in s else 0.1},
                            "site": {"type": "noul", "noul": 0.9 if "site-specific" in s else 0.2}}}
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
    with pytest.raises(SystemExit):
        types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    _csv(os.path.join(lab, "cra_types.csv"),
         ["measure_id", "type_id", "type_name", "definition", "active"],
         [{"measure_id": "cra", "type_id": "mapping", "type_name": "Hazard mapping",
           "definition": "maps of hazards", "active": "true"},
          {"measure_id": "cra", "type_id": "site", "type_name": "Site assessment",
           "definition": "assessment of one site", "active": "true"}])
    out = types_stage.assign("cra", data, api_key="k", log=lambda *_: None, workers=1)
    assert len(dec_calls) == 2
    assert {q["type"] for q in dec_calls[0]["questions"].values()} == {"noul"}
    rows = list(csv.DictReader(open(os.path.join(out, "assignments.csv"))))
    assert [(r["unit_id"], r["types_yes"]) for r in rows] == [("u1", "mapping"), ("u2", "site")]


def test_a_completion_without_the_json_is_not_stored():
    with pytest.raises(classify.TransportError):
        types_stage.parse_types({"choices": [{"message": {"content": "Sorry, no."}}]})
