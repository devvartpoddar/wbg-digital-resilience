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
UNITS = [  # unit, paragraph, project, vector
    ("D:p00001:s001", "D:p00001", "P1", [1, 0, 0, 0]),
    ("D:p00001:s002", "D:p00001", "P1", [0.9, 0.1, 0, 0]),
    ("D:p00002:s001", "D:p00002", "P2", [0, 1, 0, 0]),
    ("D:p00003", "D:p00003", "P2", [0, 0, 1, 0]),
]
# What the fake model returns for each query text: definitions point one way,
# phrases another, so which query matched is visible.
QUERY_VECTORS = {
    "Fibre: cable in the ground": [1, 0, 0, 0],
    "backbone": [0.9, 0.1, 0, 0],
    "Towers: masts": [0, 1, 0, 0],
    "cell site": [0, 0, 1, 0],
}


def _unit(v):
    a = numpy.asarray(v, dtype="float32")
    return a / numpy.linalg.norm(a)


def _csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "embeddings").mkdir(parents=True)
    numpy.save(data / "embeddings" / "sentence_emb.npy",
               numpy.stack([_unit(v) for *_, v in UNITS]))
    rows = []
    for i, (u, p, proj, _v) in enumerate(UNITS):
        row = {"row": i, "unit_id": u, "kind": "footnote" if u == "D:p00003" else "sentence",
               "paragraph_id": p, "doc_id": "D", "project_ids": proj, "block": "narrative",
               "section_path": "II", "component_number": "1", "subcomponent_number": "",
               "component_key": "masts", "text_sha256": f"{i:064d}"}
        rows.append([row[c] for c in embed.SENTENCE_INDEX_COLS])
    _csv(data / "embeddings" / "sentence_index.csv", embed.SENTENCE_INDEX_COLS, rows)
    tax = tmp_path / "taxonomy"
    tax.mkdir()
    _csv(tax / "assets.csv", ["asset_id", "asset_name", "definition", "active", "version"], [
        ["fiber", "Fibre", "cable  in   the ground", "true", "v1"],
        ["towers", "Towers", "masts", "true", "v1"],
        ["old", "Old", "retired", "false", "v1"]])
    _csv(tax / "assets_queries.csv", ["asset_id", "query_id", "query", "active"], [
        ["fiber", "backbone", "backbone", "true"],
        ["towers", "cell_site", "cell site", "true"],
        ["towers", "unused", "radio", "false"]])
    calls = []

    def fake(texts, **kw):
        calls.append(list(texts))
        return ([list(_unit(QUERY_VECTORS[t])) for t in texts],
                {"tokens": 1, "model": MODEL, "rate_limit": {}})
    monkeypatch.setattr(embed, "embed_texts", fake)
    return str(data), str(tax), calls


def _score(store):
    data, tax, _ = store
    return search.search("assets", data, model=MODEL, dim=DIM, api_key="k", taxonomy=tax,
                         log=lambda *_: None)


def test_each_label_is_searched_by_its_definition_and_its_phrases(store):
    _, tax, _ = store
    got = [(q["query_id"], q["kind"], q["text"]) for q in search.load_queries("assets", tax)]
    assert got == [("fiber:definition", "definition", "Fibre: cable in the ground"),
                   ("towers:definition", "definition", "Towers: masts"),
                   ("fiber:backbone", "phrase", "backbone"),
                   ("towers:cell_site", "phrase", "cell site")]


def test_every_unit_is_scored_against_every_query(store):
    out = _score(store)
    scores = numpy.load(os.path.join(out, "scores.npy"))
    assert scores.shape == (len(UNITS), 4)


def test_hits_name_the_query_that_matched(store):
    data, _, _ = store
    rows = search.hits(_score(store), data, labels=["towers"], top=2)
    assert [(r["unit_id"], r["best_query_id"]) for r in rows] == [
        ("D:p00002:s001", "towers:definition"), ("D:p00003", "towers:cell_site")]


def test_filters_combine(store):
    data, _, _ = store
    out = _score(store)
    assert {r["unit_id"] for r in search.hits(out, data, min_score=0.99)} == \
        {"D:p00001:s001", "D:p00001:s002", "D:p00002:s001", "D:p00003"}
    assert [r["unit_id"] for r in search.hits(out, data, labels=["fiber"], projects=["P2"],
                                               min_score=0.5)] == []
    assert [r["unit_id"] for r in search.hits(out, data, labels=["towers"],
                                               kinds=["footnote"])] == ["D:p00003"]


def test_percentile_is_within_each_query(store):
    data, _, _ = store
    rows = search.hits(_score(store), data, labels=["fiber"], min_percentile=100.0)
    # Each query's top unit is at its 100th percentile: s001 for the
    # definition, s002 for the 'backbone' phrase.
    assert [(r["unit_id"], r["best_query_id"], r["percentile"]) for r in rows] == [
        ("D:p00001:s001", "fiber:definition", "100.000"),
        ("D:p00001:s002", "fiber:backbone", "100.000")]


def test_paragraphs_are_rolled_up_from_their_hits(store):
    data, _, _ = store
    paras = search.paragraph_rollup(search.hits(_score(store), data, labels=["fiber"],
                                                min_score=0.9))
    assert [(p["paragraph_id"], p["best_unit_id"], p["units_hit"]) for p in paras] == \
        [("D:p00001", "D:p00001:s001", 2)]


def test_an_unchanged_set_is_not_scored_again(store):
    _, _, calls = store
    first = _score(store)
    n = len(calls)
    assert _score(store) == first and len(calls) == n


def test_a_new_phrase_is_a_new_version_embedding_only_that_phrase(store):
    data, tax, calls = store
    first = _score(store)
    QUERY_VECTORS["mast"] = [0, 1, 0, 0]
    with open(os.path.join(tax, "assets_queries.csv"), "a", newline="") as fh:
        csv.writer(fh).writerow(["towers", "mast", "mast", "true"])
    assert _score(store) != first
    assert calls[-1] == ["mast"]


def test_the_manifest_says_what_was_run(store):
    out = _score(store)
    m = json.load(open(os.path.join(out, "manifest.json")))
    assert (m["labels"], m["queries"], m["units"]) == (2, 4, len(UNITS))
