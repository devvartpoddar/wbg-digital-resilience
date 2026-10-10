"""Searching sentences for a label set (src/search.py), on invented vectors."""
import csv
import json
import os
import sys

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import embed  # noqa: E402
import search  # noqa: E402

DIM, MODEL = 4, "test/model"
UNITS = [  # unit, paragraph, vector
    ("D:p00001:s001", "D:p00001", [1, 0, 0, 0]),
    ("D:p00001:s002", "D:p00001", [0.9, 0.1, 0, 0]),
    ("D:p00002:s001", "D:p00002", [0, 1, 0, 0]),
    ("D:p00003", "D:p00003", [0, 0, 1, 0]),
]


def _unit(v):
    a = numpy.asarray(v, dtype="float32")
    return a / numpy.linalg.norm(a)


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "embeddings").mkdir(parents=True)
    numpy.save(data / "embeddings" / "sentence_emb.npy",
               numpy.stack([_unit(v) for _, _, v in UNITS]))
    with open(data / "embeddings" / "sentence_index.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(embed.SENTENCE_INDEX_COLS)
        for i, (u, p, _v) in enumerate(UNITS):
            row = {"row": i, "unit_id": u, "kind": "sentence", "paragraph_id": p,
                   "doc_id": "D", "project_ids": "P1", "block": "narrative",
                   "section_path": "II", "component_number": "1",
                   "subcomponent_number": "", "component_key": "masts",
                   "text_sha256": f"{i:064d}"}
            w.writerow([row[c] for c in embed.SENTENCE_INDEX_COLS])
    tax = tmp_path / "taxonomy"
    tax.mkdir()
    with open(tax / "assets.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["asset_id", "asset_name", "definition", "active", "version"])
        w.writerow(["fiber", "Fibre", "cable  in   the ground", "true", "v1"])
        w.writerow(["towers", "Towers", "masts", "true", "v1"])
        w.writerow(["old", "Old", "retired", "false", "v1"])
    calls = []

    def fake(texts, **kw):
        calls.append(list(texts))
        vecs = [list(_unit([1, 0, 0, 0] if t.startswith("Fibre") else [0, 1, 0, 0]))
                for t in texts]
        return vecs, {"tokens": 1, "model": MODEL, "rate_limit": {}}
    monkeypatch.setattr(embed, "embed_texts", fake)
    return str(data), str(tax), calls


def _run(store, **kw):
    data, tax, _ = store
    return search.search("assets", data, top=2, model=MODEL, dim=DIM, api_key="k",
                         taxonomy=tax, log=lambda *_: None, **kw)


def _read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_only_active_labels_are_searched_with_name_and_definition(store):
    _, tax, _ = store
    assert search.load_labels("assets", tax) == [
        ("fiber", "Fibre: cable in the ground"), ("towers", "Towers: masts")]


def test_each_label_keeps_its_best_units_with_their_context(store):
    out = _run(store)
    hits = _read(os.path.join(out, "sentence_hits.csv"))
    fiber = [h for h in hits if h["label_id"] == "fiber"]
    assert [h["unit_id"] for h in fiber] == ["D:p00001:s001", "D:p00001:s002"]
    assert fiber[0]["rank"] == "1" and float(fiber[0]["score"]) == pytest.approx(1.0)
    assert fiber[0]["paragraph_id"] == "D:p00001" and fiber[0]["component_key"] == "masts"


def test_paragraphs_are_rolled_up_from_their_units(store):
    out = _run(store)
    paras = [p for p in _read(os.path.join(out, "paragraph_hits.csv"))
             if p["label_id"] == "fiber"]
    assert [(p["paragraph_id"], p["best_unit_id"], p["units_in_top"]) for p in paras] == \
        [("D:p00001", "D:p00001:s001", "2")]


def test_an_unchanged_run_is_skipped_and_costs_nothing(store):
    _, _, calls = store
    first = _run(store)
    n = len(calls)
    assert _run(store) == first and len(calls) == n


def test_a_changed_definition_is_a_new_run(store):
    data, tax, calls = store
    first = _run(store)
    rows = list(csv.reader(open(os.path.join(tax, "assets.csv"))))
    rows[2][2] = "masts and towers"
    with open(os.path.join(tax, "assets.csv"), "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    second = _run(store)
    assert second != first
    # Only the changed definition was embedded again.
    assert calls[-1] == ["Towers: masts and towers"]


def test_the_manifest_says_what_was_run(store):
    out = _run(store)
    m = json.load(open(os.path.join(out, "manifest.json")))
    assert (m["label_set"], m["labels"], m["top"], m["model"]) == ("assets", 2, 2, MODEL)
