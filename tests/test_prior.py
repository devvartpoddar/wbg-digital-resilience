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


def test_structure_reads_the_unit_id_and_component():
    s = prior.structure("D:p00012:s003", "One two three four.", "2")
    assert s == {"prior_table_row": "false", "prior_position": 3, "prior_words": 4,
                 "prior_has_component": "true"}
    t = prior.structure("D:p00012:t002", "A row", "")
    assert t["prior_table_row"] == "true" and t["prior_position"] == 0
    assert t["prior_has_component"] == "false"


def test_paragraph_context_is_the_other_sentences_similarity_to_any_asset_or_measure(
        tmp_path, monkeypatch):
    """Two measure sets and the assets, each a search run: for each sentence,
    the average and the highest best cosine of the OTHER sentences of its
    paragraph, over every asset and over every measure. No cut-off."""
    import numpy
    import labels
    import search
    emb = tmp_path / "embeddings"
    emb.mkdir()
    (emb / "sentence_index.csv").write_text(
        "unit_id,paragraph_id\na1,pA\na2,pA\na3,pA\nb1,pB\n")
    scores = {"assets": [[0.30, 0.1], [0.10, 0.2], [0.26, 0.0], [0.9, 0.9]],   # a1, a3 assets
              "cra": [[0.50], [0.10], [0.10], [0.1]],                             # a1 a measure
              "ews": [[0.10], [0.46], [0.10], [0.1]]}                             # a2 a measure
    for name in scores:
        d = tmp_path / "search" / f"{name}-x"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}")
    monkeypatch.setattr(labels, "label_sets", lambda dirs=None: ["assets", "cra", "ews"])
    monkeypatch.setattr(search, "run_folder",
                        lambda name, data: (str(tmp_path / "search" / f"{name}-x"), []))
    calls = []

    def load_run(folder, data):
        calls.append(folder)
        return [], numpy.array(scores[os.path.basename(folder)[:-2]], dtype="float32"), []
    monkeypatch.setattr(search, "load_run", load_run)
    key, ctx = prior.paragraph_context(str(tmp_path))
    # best asset cosine: a1 0.30, a2 0.20, a3 0.26; best measure cosine: a1 0.50, a2 0.46, a3 0.10
    assert ctx["a1"] == (0.23, 0.26, 0.28, 0.46)
    assert ctx["a2"] == (0.28, 0.3, 0.3, 0.5)
    assert ctx["a3"] == (0.25, 0.3, 0.48, 0.5)
    assert ctx["b1"] == (None, None, None, None)              # alone in its paragraph
    assert prior.paragraph_inputs(ctx, "a2") == {
        "prior_para_assets_mean": "0.2800", "prior_para_assets_max": "0.3000",
        "prior_para_measures_mean": "0.3000", "prior_para_measures_max": "0.5000"}
    assert set(prior.paragraph_inputs(ctx, "unknown").values()) == {""}
    n = len(calls)
    assert prior.paragraph_context(str(tmp_path)) == (key, ctx) and len(calls) == n  # kept
    assert prior.paragraph_key(str(tmp_path))[0] == key


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
    monkeypatch.setattr(prior, "paragraph_context", lambda data, sets=None: (
        "k", {"D:p1:s001": (0.2, 0.2, 0.5, 0.5), "D:p1:s002": (0.6, 0.6, 0.1, 0.1)}))
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
    assert f1["prior_para_assets_max"] == "0.2000" and f1["prior_para_measures_mean"] == "0.5000"
    assert got[("towers", "D:p1:s001")]["prior_phrase_match"] == "0.0000"
    assert got[("towers", "D:p1:s002")]["prior_para_assets_mean"] == "0.6000"
    assert got[("fiber", "D:p1:s002")]["prior_has_component"] == "false"
