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
from wbg_portfolio.paths import file_sha256  # noqa: E402


def test_a_checksum_is_remembered_and_forgotten_when_the_file_changes(tmp_path, monkeypatch):
    from wbg_portfolio import paths
    monkeypatch.setattr(paths, "REMEMBER_MIN_BYTES", 0)
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


def test_a_small_file_gets_no_sidecar(tmp_path):
    """Hand-kept inputs are small; hashing one must not write beside it."""
    f = tmp_path / "labels.csv"
    f.write_text("id,name\n1,a\n")
    assert file_sha256(str(f)) == file_sha256(str(f), remember=False)
    assert not (tmp_path / "labels.csv.sha256").exists()


def test_the_unit_index_is_computed_once_per_state_of_its_inputs(tmp_path, monkeypatch):
    from wbg_portfolio import units
    data = tmp_path / "data"
    (data / "appraisal" / "text").mkdir(parents=True)
    text = "The project will finance ten towers. A hazard assessment will be made."
    (data / "appraisal" / "text" / "D.txt").write_text(text)
    from wbg_portfolio.clean import PARA_COLS
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

