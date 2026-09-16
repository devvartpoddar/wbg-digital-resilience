"""Unit tests for corpus-wide asset scoring.

Builds a synthetic corpus, trains on it, scores it, and checks the scorer agrees
with the model it loaded. The failure this is really guarding against is silent:
a scorer that applies the weights slightly differently from the trainer produces
plausible probabilities for the wrong reasons, and nothing downstream notices.
"""
import csv
import json
import os
import sys

import numpy
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import detect_assets as D  # noqa: E402
import train_assets as T  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_train_assets import build_corpus  # noqa: E402


@pytest.fixture
def scored(tmp_path, monkeypatch):
    data, inputs = build_corpus(str(tmp_path), n_docs=12, per_doc=12)
    # clean/ files so the report can resolve text
    os.makedirs(os.path.join(data, "clean"), exist_ok=True)
    with open(os.path.join(data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    bodies = {}
    for r in rows:
        bodies.setdefault(r["doc_id"], []).append(r["paragraph_id"])
    with open(os.path.join(data, "paragraphs.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["paragraph_id", "doc_id", "block", "char_start", "char_end"])
        for did, pids in bodies.items():
            text, pos = "", 0
            for p in pids:
                body = f"paragraph {p} about fiber optic cable"
                w.writerow([p, did, "narrative", pos, pos + len(body)])
                text += body + "\n\n"
                pos += len(body) + 2
            with open(os.path.join(data, "clean", f"{did}.txt"), "w", encoding="utf-8") as cf:
                cf.write(text)
    monkeypatch.setattr(sys, "argv", ["train_assets.py", "--data", data,
                                      "--inputs", inputs, "--folds", "4",
                                      "--meta", os.path.join(str(tmp_path), "meta")])
    T.main()
    monkeypatch.setattr(sys, "argv", ["detect_assets.py", "--data", data,
                                      "--inputs", inputs, "--top", "3"])
    assert D.main() == 0
    with open(os.path.join(data, "intermediate", "detect", "paragraph_asset.csv"),
              newline="", encoding="utf-8") as fh:
        return data, inputs, list(csv.DictReader(fh))


def test_every_paragraph_gets_a_row_for_every_trained_class(scored):
    data, _inputs, rows = scored
    manifest = json.load(open(os.path.join(data, "intermediate", "models",
                                           "asset_models.json"), encoding="utf-8"))
    n_classes = len(manifest["classes"])
    with open(os.path.join(data, "emb_index.csv"), newline="", encoding="utf-8") as fh:
        n_paras = len(list(csv.DictReader(fh)))
    assert len(rows) == n_paras * n_classes
    per = {}
    for r in rows:
        per[r["paragraph_id"]] = per.get(r["paragraph_id"], 0) + 1
    assert set(per.values()) == {n_classes}


def test_sub_threshold_rows_are_kept(scored):
    """Methodology Stage 3 step 2. Keeping only fired rows would mean re-running
    the model every time the threshold moved."""
    _d, _i, rows = scored
    assert any(r["fired"] == "false" for r in rows), "nothing below threshold was written"
    assert any(r["fired"] == "true" for r in rows), "nothing fired at all"


def test_probabilities_are_in_range(scored):
    _d, _i, rows = scored
    for r in rows:
        assert 0.0 <= float(r["probability"]) <= 1.0


def test_scorer_reproduces_the_saved_model(scored):
    """The scorer must apply the same weights the trainer measured. If these
    diverge, every probability is confidently wrong."""
    data, _i, rows = scored
    z = numpy.load(os.path.join(data, "intermediate", "models", "asset_fiber.npz"))
    X = numpy.load(os.path.join(data, "paragraph_emb.npy"))
    with open(os.path.join(data, "emb_index.csv"), newline="", encoding="utf-8") as fh:
        order = [r["paragraph_id"] for r in csv.DictReader(fh)]
    expect = 1.0 / (1.0 + numpy.exp(-(z["platt_a"] * (X @ z["w"]) + z["platt_b"])))
    got = {r["paragraph_id"]: float(r["probability"]) for r in rows if r["asset_id"] == "fiber"}
    for i, pid in enumerate(order):
        assert abs(got[pid] - float(expect[i])) < 1e-4, pid


def test_fired_follows_the_threshold_not_the_probability(scored, monkeypatch):
    """fired must be decided on the raw score against the stored threshold, not
    on probability > 0.5.

    Comparing the two rules on the natural fixture proves nothing: Platt scaling
    is monotone, so prob >= 0.5 is exactly score >= -b/a, and when the F1-chosen
    threshold happens to land near that midpoint the two rules agree everywhere.
    A first version of this test did that and a deliberate mutation to
    `probs[aid] >= 0.5` passed it.

    So the threshold is moved somewhere the two rules must disagree, and the
    disagreement is asserted to exist before the rule is checked.
    """
    data, inputs, _rows = scored
    npz_path = os.path.join(data, "intermediate", "models", "asset_fiber.npz")
    z = dict(numpy.load(npz_path))
    X = numpy.load(os.path.join(data, "paragraph_emb.npy"))
    scores = X @ z["w"]

    # A threshold at the 80th percentile of the scores, which is nowhere near
    # the Platt midpoint, so the two rules cannot agree by accident.
    moved = float(numpy.quantile(scores, 0.8))
    z["threshold"] = numpy.float32(moved)
    numpy.savez(npz_path, **z)

    by_threshold = scores >= moved
    by_probability = (1.0 / (1.0 + numpy.exp(-(z["platt_a"] * scores + z["platt_b"])))) >= 0.5
    assert (by_threshold != by_probability).any(), \
        "fixture cannot distinguish the two rules; the test would be vacuous"

    monkeypatch.setattr(sys, "argv", ["detect_assets.py", "--data", data,
                                      "--inputs", inputs, "--top", "1"])
    D.main()
    with open(os.path.join(data, "intermediate", "detect", "paragraph_asset.csv"),
              newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["asset_id"] == "fiber"]
    with open(os.path.join(data, "emb_index.csv"), newline="", encoding="utf-8") as fh:
        order = [r["paragraph_id"] for r in csv.DictReader(fh)]
    got = {r["paragraph_id"]: r["fired"] for r in rows}
    for i, pid in enumerate(order):
        assert got[pid] == ("true" if by_threshold[i] else "false"), pid


def test_an_untrained_class_is_absent_rather_than_guessed(scored):
    """'rare' has 8 labels in the fixture, too few to train. It must not appear
    with a made-up probability."""
    _d, _i, rows = scored
    assert not any(r["asset_id"] == "rare" for r in rows)


def test_report_leads_with_fire_rate_and_the_baseline(scored):
    data, _i, _rows = scored
    body = open(os.path.join(data, "detect_assets_report.txt"), encoding="utf-8").read()
    assert "fired" in body and "base" in body
    assert "NOT a corpus base rate" in body, \
        "the report must warn that the enriched draw cannot validate a fire rate"


def test_missing_models_abort_rather_than_produce_an_empty_table(tmp_path, monkeypatch):
    data, inputs = build_corpus(str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["detect_assets.py", "--data", data, "--inputs", inputs])
    with pytest.raises(SystemExit) as exc:
        D.main()
    assert "train_assets" in str(exc.value)
