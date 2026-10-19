"""The measure stage (src/measure.py): candidates graded by the decision
model with the asset map as columns, on an invented store and a fake
endpoint. No document text is committed."""
import csv
import hashlib
import json
import os
import sys

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import classify  # noqa: E402
import measure  # noqa: E402
from clean import PARA_COLS  # noqa: E402

PARAS = ["A site-specific hazard assessment will be conducted before the towers are built. "
         "The project will finance ten towers.",
         "The country faces floods and storms every year.",
         "Training will be provided to the ministry staff."]
SENTS = [("D:p00001", "A site-specific hazard assessment will be conducted before the towers are built."),
         ("D:p00001", "The project will finance ten towers."),
         ("D:p00002", "The country faces floods and storms every year."),
         ("D:p00003", "Training will be provided to the ministry staff.")]


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
    text = "\n\n".join(PARAS)
    (data / "appraisal" / "text").mkdir(parents=True)
    (data / "appraisal" / "text" / "D.txt").write_text(text, encoding="utf-8")
    paras, at = [], 0
    for i, t in enumerate(PARAS, 1):
        paras.append({"paragraph_id": f"D:p{i:05d}", "doc_id": "D", "project_ids": "P1",
                      "block": "narrative", "for_model": "true", "char_start": at,
                      "char_end": at + len(t), "section_title": "Project Components",
                      "component_number": "1", "n_tokens": len(t.split()),
                      "text_sha256": hashlib.sha256(t.encode()).hexdigest()})
        at += len(t) + 2
    _csv(str(data / "appraisal" / "paragraphs.csv"), PARA_COLS, paras)
    sents = []
    for n, (pid, s) in enumerate(SENTS, 1):
        start = text.index(s)
        sents.append({"sentence_id": f"{pid}:s{n:03d}", "paragraph_id": pid, "doc_id": "D",
                      "char_start": start, "char_end": start + len(s),
                      "n_tokens": len(s.split()),
                      "text_sha256": hashlib.sha256(s.encode()).hexdigest()})
    _csv(str(data / "appraisal" / "sentences.csv"),
         ["sentence_id", "paragraph_id", "doc_id", "char_start", "char_end", "n_tokens",
          "text_sha256"], sents)
    _csv(str(data / "appraisal" / "components.csv"), ["doc_id", "source", "number", "name"],
         [{"doc_id": "D", "source": "datasheet", "number": "1", "name": "Connectivity"}])
    # A search run and an index: unit vectors so cosines are well defined.
    import units
    us = [u for u in units.search_units(data) if not u["left_out"] and not u["copy_of"]]
    index_cols = ["row"] + [c for c in units.UNIT_COLS
                            if c not in ("char_start", "char_end", "n_tokens", "left_out",
                                         "copy_of")] + ["copies"]
    emb = data / "embeddings"
    emb.mkdir()
    rows = [dict(u, row=i, copies="") for i, u in enumerate(us)]
    _csv(str(emb / "sentence_index.csv"), index_cols, rows)
    m = numpy.zeros((len(us), 4), dtype="float32")
    for i in range(len(us)):
        m[i, i % 4] = 1.0
    numpy.save(str(emb / "sentence_emb.npy"), m)
    # Labels: one measure, the first sentence matches its pattern.
    lab = tmp_path / "labels"
    _csv(str(lab / "cra.csv"), ["measure_id", "measure_name", "definition", "active"],
         [{"measure_id": "cra", "measure_name": "Hazard assessment",
           "definition": "an assessment of hazards", "active": "true"}])
    _csv(str(lab / "cra_scale.csv"), ["grade", "description"],
         [{"grade": g, "description": d} for g, d in enumerate(
             ["Not {name}.", "{name} as background.", "{name} planned.", "{name} committed."])])
    _csv(str(lab / "cra_patterns.csv"), ["measure_id", "pattern_id", "pattern", "active"],
         [{"measure_id": "cra", "pattern_id": "hz", "pattern": r"\bhazard assessment\b",
           "active": "true"}])
    _csv(str(lab / "assets.csv"), ["asset_id", "asset_name", "definition", "active"],
         [{"asset_id": "towers", "asset_name": "Mobile networks", "definition": "masts",
           "active": "true"}])
    import labels
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(lab),))
    # A scored search run for the set: the pattern route is what matters here.
    import search
    queries = search.load_queries("cra")
    folder, _ = search.run_folder("cra", str(data))
    os.makedirs(folder)
    search._write(os.path.join(folder, "queries.csv"), search.QUERY_COLS, queries)
    scores = numpy.zeros((len(us), len(queries)), dtype="float32")
    scores[0, 0] = 0.9
    numpy.save(os.path.join(folder, "scores.npy"), scores)
    with open(os.path.join(folder, "manifest.json"), "w") as fh:
        json.dump({"index_sha256": search._sha_file(str(emb / "sentence_index.csv"))}, fh)
    # Commitment needs training groups; give it a fixed score instead.
    import commitment
    monkeypatch.setattr(commitment, "score", lambda d, log=print: str(data / "commitment" / "v"))
    monkeypatch.setattr(commitment, "current_scores",
                        lambda d: {u["unit_id"]: (0.5, "activity") for u in us})
    monkeypatch.setattr(commitment, "version", lambda d: "v")
    # The asset map: paragraph 1 is about an asset, the others are not.
    amap = data / "classify" / "assets-abc"
    amap.mkdir(parents=True)
    _csv(str(amap / "scores.csv"), classify.SCORE_COLS, [
        {"paragraph_id": "D:p00001", "doc_id": "D", "project_ids": "P1", "component_number": "1",
         "label_id": "towers", "grade": 3, "p0": 0.0, "p1": 0.1, "p2": 0.2, "p3": 0.7},
        {"paragraph_id": "D:p00002", "doc_id": "D", "project_ids": "P1", "component_number": "1",
         "label_id": "towers", "grade": 0, "p0": 0.9, "p1": 0.1, "p2": 0.0, "p3": 0.0}])
    (amap / "manifest.json").write_text("{}")
    monkeypatch.setattr(classify, "current_run", lambda name, d, taxonomy=None, projects=None: str(amap))
    calls = []

    def fake(body, **kw):
        calls.append(body)
        hit = "hazard assessment" in body["state"]["sentence"]
        probs = {"0": 0.05, "1": 0.05, "2": 0.1, "3": 0.8} if hit else \
            {"0": 0.9, "1": 0.1, "2": 0.0, "3": 0.0}
        answers = {"cra": {"type": "score", "confidence": 0.7, "probabilities": probs}}
        for qid in body["questions"]:                     # every yes/no asked: assets, kinds
            if qid != "cra":
                yes = (qid == "asset:towers" and "tower" in body["state"]["sentence"]) or \
                    (qid in ("kind:build", "facet:borrower") and hit)
                answers[qid] = {"type": "noul", "noul": 0.9 if yes else 0.1}
        return {"model": measure.MODEL, "usage": {"cost": 0.00001}, "answers": answers}
    monkeypatch.setattr(classify, "decide", fake)
    return str(data), calls


def _rows(out):
    with open(os.path.join(out, "units.csv"), newline="") as fh:
        return list(csv.DictReader(fh))


RUN = dict(api_key="k", log=lambda *_: None, workers=1, top=1, max_rounds=0)


def test_round_zero_grades_every_candidate_with_the_asset_answers(store):
    data, calls = store
    out = measure.measure("cra", data, **RUN)
    rows = _rows(out)
    assert [r["route"] for r in rows] == ["both"]           # pattern hit, embedding top 1
    r = rows[0]
    assert r["grade"] == "3" and r["asset_para"] == "towers" and r["asset_p_para"] == "0.9000"
    assert r["asset_comp"] == "towers" and r["sent"] == "true" and r["round"] == "0"
    assert r["asset_unit"] == "towers" and r["asset_p_unit"] == "0.900" and r["assets_yes"] == "towers"
    assert r["kinds_yes"] == "build" and r["kind_best"] == "build"
    assert r["actors_yes"] == "borrower" and r["actor_best"] == "borrower"
    assert float(r["conditional_p"]) < 0.5
    assert "paragraph" not in calls[0]["state"]              # sentence design by default
    assert calls[0]["state"]["component"] == "1 Connectivity"
    assert "towers" not in calls[0]["questions"]["cra"]["instructions"]
    # Two passes: the measure alone on the sentence, then asset, kind and
    # facet questions for the units graded 2 or more (here the one unit),
    # with the paragraph added to the state.
    assert list(calls[0]["questions"]) == ["cra"]
    assert calls[1]["state"]["sentence"] == calls[0]["state"]["sentence"]
    assert "paragraph" in calls[1]["state"] and "ten towers" in calls[1]["state"]["paragraph"]
    assert calls[1]["questions"]["asset:towers"]["type"] == "noul"
    assert "cra" not in calls[1]["questions"]
    assert calls[0]["model"] == measure.MODEL


def test_feedback_rounds_read_the_nearest_unseen_units_until_the_yield_drops(store):
    data, calls = store
    out = measure.measure("cra", data, **dict(RUN, max_rounds=3, batch=2, min_yield=1))
    rows = _rows(out)
    # Round 0: the one candidate. Round 1: the two units nearest its vector,
    # neither committed, so the loop stops there.
    assert [(r["round"], r["route"]) for r in rows] == [("0", "both"), ("1", "feedback"),
                                                        ("1", "feedback")]
    assert [r["grade"] for r in rows] == ["3", "0", "0"]
    assert len(calls) == 4            # round 0: measure + extras; round 1: two units, measure only


def test_a_rerun_reads_the_store_and_asks_nothing(store):
    data, calls = store
    first = measure.measure("cra", data, **RUN)
    n = len(calls)
    assert measure.measure("cra", data, **RUN) == first
    assert len(calls) == n


def test_the_paragraph_design_is_another_version_with_the_paragraph(store):
    data, calls = store
    a = measure.measure("cra", data, **RUN)
    b = measure.measure("cra", data, **dict(RUN, context="paragraph"))
    assert a != b and calls[-1]["state"]["paragraph"].startswith("A site-specific")


def test_a_gate_leaves_the_unit_in_the_table_but_unsent(store):
    data, calls = store
    out = measure.measure("cra", data, **dict(RUN, gate="paragraph", asset_min=0.95))
    rows = _rows(out)
    assert rows[0]["sent"] == "false" and rows[0]["grade"] == "" and calls == []


def test_a_dry_run_asks_nothing(store):
    data, calls = store
    assert measure.measure("cra", data, dry_run=True, log=lambda *_: None, top=1) is None
    assert calls == []
