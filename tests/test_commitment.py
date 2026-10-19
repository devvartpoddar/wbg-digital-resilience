"""The commitment scorer (src/commitment.py) and the candidates stage
(src/candidates.py), on invented data."""
import csv
import os
import re
import sys

import numpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import candidates  # noqa: E402
import commitment  # noqa: E402
import labels  # noqa: E402
import search  # noqa: E402


def test_the_logistic_fit_separates_two_groups_and_is_repeatable():
    rng = numpy.random.default_rng(0)
    X = numpy.vstack([rng.normal(1, 1, (50, 3)), rng.normal(-1, 1, (150, 3))])
    y = numpy.r_[numpy.ones(50), numpy.zeros(150)]
    w, b = commitment.fit_logistic(X, y)
    p = commitment.predict(X, w, b)
    assert ((p >= 0.5) == (y == 1)).mean() > 0.9
    w2, b2 = commitment.fit_logistic(X, y)
    assert numpy.array_equal(w, w2) and b == b2


def test_firmness_reads_the_wording():
    assert commitment.firmness("The project will finance underground ducts.") == "activity"
    assert commitment.firmness("Sub-component 1.1 will finance:\n(b) towers;") == "activity"
    assert commitment.firmness("The digital ID will enable faster disaster relief.") == "outcome"
    # 'would' is how appraisal documents describe planned works: not an outcome.
    assert commitment.firmness("New cell sites would be fully solar-powered.") == "none"


def test_training_units_come_from_section_titles_alone():
    index = [{"unit_id": f"u{i}", "paragraph_id": f"p{i}"} for i in range(4)]
    sections = {"p0": "Project Components", "p1": "Project Components",
                "p2": "Country Context", "p3": "Key Risks"}
    texts = {"u0": "Component 1 will finance towers.", "u1": "Towers matter.",
             "u2": "The country is exposed to floods.", "u3": "Risks will be managed."}
    pos, neg = commitment.training_sets(index, sections, texts)
    # A component sentence without will/shall, and other sections, are left out.
    assert (pos, neg) == (["u0"], ["u2"])


def test_candidates_are_pattern_hits_or_the_embedding_top(tmp_path, monkeypatch):
    data = str(tmp_path / "data")
    tax = tmp_path / "labels"
    tax.mkdir()
    for name, header, rows in [
            ("m.csv", ["measure_id", "measure_name", "definition", "active"],
             [["fibre", "Fibre", "cable laid in the ground", "true"]]),
            ("m_patterns.csv", ["measure_id", "pattern_id", "pattern", "active"],
             [["fibre", "buried", r"\bburied\b", "true"]]),
            ("m_types.csv", ["measure_id", "type_id", "type_name", "pattern", "active"],
             [["fibre", "underground", "Underground", r"\bburied\b|\bunderground\b", "true"]])]:
        with open(tax / name, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
    texts = {"a": "Cables will be buried along the coast.", "b": "Fibre backbone.",
             "c": "Fibre is often buried elsewhere.", "d": "Schools will be connected."}
    index = [dict(unit_id=u, kind="sentence", paragraph_id=f"P:{u}", doc_id="D",
                  project_ids="P1", component_number="", subcomponent_number="",
                  component_key="", copies="") for u in texts]
    scores = numpy.array([[0.2], [0.9], [0.1], [0.5]], dtype="float32")
    run = tmp_path / "data" / "search" / "m-run"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text("{}")
    monkeypatch.setattr(labels, "folder", lambda name, dirs=None: str(tax))
    monkeypatch.setattr(search, "run_folder", lambda name, d: (str(run), []))
    monkeypatch.setattr(search, "load_run", lambda folder, d: (
        [{"query_id": "fibre:definition", "label_id": "fibre"}], scores, index))
    commit_dir = tmp_path / "data" / "commitment" / "v1"
    monkeypatch.setattr(commitment, "score", lambda d, log=None: str(commit_dir))
    monkeypatch.setattr(commitment, "unit_texts", lambda d, idx: texts)
    monkeypatch.setattr(commitment, "current_scores", lambda d: {
        u: (0.9 if "will" in t else 0.1, commitment.firmness(t)) for u, t in texts.items()})
    out = candidates.build("m", data, top=1, log=lambda *_: None)
    with open(os.path.join(out, "candidates.csv"), newline="") as fh:
        got = [(r["unit_id"], r["route"], r["rank"], r["types"], r["p_commitment"])
               for r in csv.DictReader(fh)]
    # b is the embedding's top 1; a and c only the pattern finds; d neither.
    # Reading order: pattern hits first, the likelier commitment first.
    assert got == [("a", "pattern", "3", "underground", "0.9000"),
                   ("c", "pattern", "4", "underground", "0.1000"),
                   ("b", "embedding", "1", "", "0.1000")]
    # The same inputs are the same version: not built again.
    assert candidates.build("m", data, top=1, log=lambda *_: None) == out


def test_a_pattern_that_does_not_compile_is_an_error(tmp_path):
    with open(tmp_path / "m.csv", "w") as fh:
        fh.write("measure_id,measure_name,definition,active\nx,X,d,true\n")
    with open(tmp_path / "m_patterns.csv", "w") as fh:
        fh.write("measure_id,pattern_id,pattern,active\nx,bad,(unclosed,true\n")
    try:
        labels.load_patterns("m", taxonomy=str(tmp_path))
    except SystemExit as e:
        assert "does not compile" in str(e)
    else:
        raise AssertionError("expected an error")
