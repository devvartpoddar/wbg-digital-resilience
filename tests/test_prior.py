"""The prior inputs that need no grading (src/prior.py). Invented text."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import prior  # noqa: E402


def _weight():
    # 100 units: "the" and "project" in every one, "fibre" in two, "backbone" in five
    return prior.Weights(100, {"the": 100, "project": 100, "fibre": 2, "backbone": 5,
                               "national": 40})


def test_a_rare_word_counts_for_more_than_a_common_one():
    w = _weight()
    assert w("fibre") > w("backbone") > w("national") > w("project") == 0.0
    assert w("unseen") == max(w(x) for x in ("fibre", "backbone", "unseen"))


def test_the_phrase_match_is_the_best_phrases_share_of_its_weight():
    w = _weight()
    ph = prior.phrases(["the national fibre backbone", "the project"], w)
    assert len(ph) == 1                                   # "the project" has no weight
    full = prior.phrase_match(set(prior.words("A national fibre backbone will be built.")), ph)
    rare = prior.phrase_match(set(prior.words("The fibre will reach every district.")), ph)
    common = prior.phrase_match(set(prior.words("A national programme.")), ph)
    assert full == 1.0 and 0 < common < rare < 1.0
    assert prior.phrase_match({"fibre"}, []) == 0.0


def test_plurals_meet_their_singular():
    assert prior.words("Towers, maps and gas processes.") == ["tower", "map", "and", "gas", "processe"]


def test_paragraph_counts_are_other_sentences_classified_with_an_asset_or_a_measure(
        tmp_path, monkeypatch):
    """After every stage: an asset where the asset map's best asset reaches
    asset.floor (0.3), a measure where any measure run committed it; the
    sentence itself never counts."""
    import labels
    import measure
    emb = tmp_path / "embeddings"
    emb.mkdir()
    (emb / "sentence_index.csv").write_text(
        "unit_id,paragraph_id,project_ids\na1,pA,P1\na2,pA,P1\na3,pA,P1\nb1,pB,P2\n")
    monkeypatch.setattr(measure, "asset_map", lambda data, projects=None, vector_of=None: (
        {"a1": {"fiber": 0.9}, "a2": {"fiber": 0.2, "towers": 0.31}, "a3": {"fiber": 0.1}},
        "assets-x"))
    runs = {}
    for name, committed in (("cra", {"a1"}), ("ews", {"a1", "a3"})):
        d = tmp_path / "measures" / f"{name}-x"
        d.mkdir(parents=True)
        (d / "units.csv").write_text("unit_id,committed\n" + "".join(
            f"{u},{'true' if u in committed else 'false'}\n" for u in ("a1", "a2", "a3", "b1")))
        runs[name] = str(d)
    monkeypatch.setattr(labels, "label_sets", lambda dirs=None: ["assets", "cra", "ews"])
    monkeypatch.setattr(measure, "current_run", lambda name, data, projects=None: runs[name])
    key, rows = prior.paragraph_classified(str(tmp_path))
    got = {r["unit_id"]: (r["prior_para_assets"], r["prior_para_measures"]) for r in rows}
    # assets: a1 (0.9) and a2 (0.31); measures: a1 and a3
    assert got == {"a1": (1, 1), "a2": (1, 2), "a3": (2, 1), "b1": (0, 0)}
    assert "assets-x" in key and "cra-x" in key and "ews-x" in key
    assert [r["unit_id"] for r in prior.paragraph_classified(str(tmp_path), ["P2"])[1]] == ["b1"]


def test_document_frequency_is_computed_once_and_kept(tmp_path, monkeypatch):
    import measure
    emb = tmp_path / "embeddings"
    emb.mkdir()
    (emb / "sentence_index.csv").write_text("unit_id,paragraph_id\nu1,p1\nu2,p1\n")
    calls = []

    def texts(data, index):
        calls.append(1)
        return {"u1": "The fibre backbone.", "u2": "The towers and the fibre."}
    monkeypatch.setattr(measure, "unit_texts", texts)
    n, df = prior.word_df(str(tmp_path))
    assert n == 2 and df["fibre"] == 2 and df["the"] == 2 and df["tower"] == 1
    assert prior.word_df(str(tmp_path)) == (n, df) and len(calls) == 1
    cache = [f for f in os.listdir(tmp_path / "prior") if f.startswith("word_df-")]
    assert len(cache) == 1 and json.load(open(tmp_path / "prior" / cache[0]))["units"] == 2


def test_the_asset_map_stores_every_input_for_every_sentence_and_asset(monkeypatch):
    import classify
    import search
    monkeypatch.setattr(prior, "word_df", lambda data: (100, {"fibre": 2, "tower": 3}))
    monkeypatch.setattr(search, "load_queries", lambda name, taxonomy=None: [
        {"label_id": "fiber", "kind": "definition", "text": "fibre optic cables"},
        {"label_id": "fiber", "kind": "phrase", "text": "national fibre backbone"},
        {"label_id": "towers", "kind": "phrase", "text": "mobile towers"}])
    monkeypatch.setattr(classify, "load_labels", lambda name, taxonomy=None: (
        [{"id": "fiber"}, {"id": "towers"}], ["0", "1", "2", "3"]))
    items = [{"row": {"unit_id": "D:p1:s001", "paragraph_id": "D:p1", "component_number": "1"},
              "state": {"sentence": "The project will lay fibre."}},
             {"row": {"unit_id": "D:p1:s002", "paragraph_id": "D:p1", "component_number": ""},
              "state": {"sentence": "Towers will be raised."}}]
    cos = {("fiber", "D:p1:s001"): 0.6, ("towers", "D:p1:s001"): 0.1,
           ("fiber", "D:p1:s002"): 0.2, ("towers", "D:p1:s002"): 0.5}
    of = classify.prior_inputs("assets", "unused", None, items, cos, 0.25)
    got = {(a, u): of(a, u) for a in ("fiber", "towers") for u in ("D:p1:s001", "D:p1:s002")}
    assert of("fiber", "not a sentence of the map") == {}
    assert set(got) == {(a, u) for a in ("fiber", "towers") for u in ("D:p1:s001", "D:p1:s002")}
    f1 = got[("fiber", "D:p1:s001")]
    assert set(f1) == set(prior.COLS)
    assert f1["prior_phrase_cosine"] == "0.6000" and 0.2 < float(f1["prior_phrase_match"]) < 0.4   # "fibre" of three rare words
    assert got[("towers", "D:p1:s001")]["prior_phrase_match"] == "0.0000"
