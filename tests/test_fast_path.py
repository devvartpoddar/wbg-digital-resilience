"""What keeps a measure's path short: checksums remembered beside the file,
the unit index cached on disk, a search run's rollup kept in its folder,
and a reader Run that loads no corpus. Invented data only."""
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
from paths import file_sha256  # noqa: E402


def test_a_checksum_is_remembered_and_forgotten_when_the_file_changes(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"a" * 1000)
    first = file_sha256(str(f))
    side = tmp_path / "big.bin.sha256"
    assert side.exists() and first in side.read_text()
    # A stale sidecar that lies is ignored once the bytes change.
    time.sleep(0.01)
    f.write_bytes(b"b" * 1001)
    assert file_sha256(str(f)) != first
    # The same bytes written again with a new mtime hash the same, and the
    # sidecar is trusted on the next call.
    f.write_bytes(b"a" * 1000)
    assert file_sha256(str(f)) == first
    side.write_text(side.read_text().replace(first, "0" * 64))
    assert file_sha256(str(f)) == "0" * 64   # trusted: size and mtime match (by design)
    f.write_bytes(b"a" * 999)
    assert file_sha256(str(f)) != "0" * 64


def test_the_unit_index_is_computed_once_per_state_of_its_inputs(tmp_path, monkeypatch):
    import units
    data = tmp_path / "data"
    (data / "appraisal" / "text").mkdir(parents=True)
    text = "The project will finance ten towers. A hazard assessment will be made."
    (data / "appraisal" / "text" / "D.txt").write_text(text)
    from clean import PARA_COLS
    with open(data / "appraisal" / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PARA_COLS)
        w.writeheader()
        w.writerow({c: "" for c in PARA_COLS} | {
            "paragraph_id": "D:p00001", "doc_id": "D", "project_ids": "P1", "block": "narrative",
            "for_model": "true", "char_start": 0, "char_end": len(text),
            "section_title": "Components", "n_tokens": 12, "text_sha256": "x"})
    with open(data / "appraisal" / "sentences.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["sentence_id", "paragraph_id", "doc_id", "char_start",
                                           "char_end", "n_tokens", "text_sha256"])
        w.writeheader()
        w.writerow({"sentence_id": "D:p00001:s001", "paragraph_id": "D:p00001", "doc_id": "D",
                    "char_start": 0, "char_end": 36, "n_tokens": 6, "text_sha256": "a"})
    calls = []
    real = units._search_units
    monkeypatch.setattr(units, "_search_units", lambda d: calls.append(d) or real(d))
    a = units.search_units(str(data))
    b = units.search_units(str(data))
    assert a == b and len(calls) == 1
    cached = list((data / "appraisal" / "cache").glob("search_units-*.json"))
    assert len(cached) == 1
    # A changed sentences table is a new key, computed again.
    time.sleep(0.01)
    with open(data / "appraisal" / "sentences.csv", "a", newline="") as fh:
        fh.write("D:p00001:s002,D:p00001,D,37,70,6,b\n")
    c = units.search_units(str(data))
    assert len(calls) == 2 and len(c) == len(a) + 1


def test_a_search_runs_rollup_is_kept_in_its_folder(tmp_path, monkeypatch):
    import load_pg
    import search
    folder = tmp_path / "search" / "cra-abc"
    folder.mkdir(parents=True)
    hits = [{"label_id": "cra", "paragraph_id": "D:p1", "doc_id": "D", "project_ids": "P1",
             "component_number": "1", "component_key": "k", "score": "0.9", "percentile": "99",
             "unit_id": "D:p1:s1", "best_query_id": "cra:definition", "queries_agreeing": 1,
             "queries": 1},
            {"label_id": "cra", "paragraph_id": "D:p1", "doc_id": "D", "project_ids": "P1",
             "component_number": "1", "component_key": "k", "score": "0.5", "percentile": "80",
             "unit_id": "D:p1:s2", "best_query_id": "cra:definition", "queries_agreeing": 1,
             "queries": 1}]
    calls = []
    monkeypatch.setattr(search, "run_labels", lambda f: ["cra"])
    monkeypatch.setattr(search, "hits", lambda f, d, labels=None: calls.append(1) or hits)
    first = load_pg.run_rollup(str(folder), str(tmp_path))
    assert len(first) == 1 and first[0]["best_unit_id"] == "D:p1:s1" and first[0]["units_hit"] == 2
    assert (folder / "paragraph_hits.csv").exists()
    again = load_pg.run_rollup(str(folder), str(tmp_path))
    assert len(calls) == 1
    assert [r["best_unit_id"] for r in again] == ["D:p1:s1"]
    assert str(again[0]["units_hit"]) == "2"


def test_a_reader_run_reads_no_corpus(tmp_path, monkeypatch):
    """measure.current_run (the load, the types stage) must not open the
    matrix or the texts: it names the folder from the inputs alone."""
    import measure
    import search
    import classify
    import labels
    lab = tmp_path / "labels"
    lab.mkdir()
    for name, cols, rows in (
            ("cra.csv", ["measure_id", "measure_name", "definition", "active"],
             [{"measure_id": "cra", "measure_name": "Hazard assessment",
               "definition": "an assessment", "active": "true"}]),
            ("cra_scale.csv", ["grade", "description"],
             [{"grade": g, "description": f"{g} {{name}}"} for g in range(4)]),
            ("assets.csv", ["asset_id", "asset_name", "definition", "active"],
             [{"asset_id": "fiber", "asset_name": "Fiber", "definition": "f", "active": "true"}])):
        with open(lab / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(lab),))
    monkeypatch.setattr(classify, "TAXONOMY", str(lab))
    folder = tmp_path / "search" / "cra-1"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text("{}")
    monkeypatch.setattr(search, "run_folder", lambda *a, **k: (str(folder), []))
    monkeypatch.setattr(classify, "current_run", lambda *a, **k: None)
    monkeypatch.setattr(measure, "paragraph_rows", lambda d: (_ for _ in ()).throw(AssertionError("corpus read")))
    run = measure.Run("cra", str(tmp_path), build=False, log=lambda *_: None)
    assert run.out.startswith(str(tmp_path)) and not run._loaded
    assert measure.current_run("cra", str(tmp_path)) is None
