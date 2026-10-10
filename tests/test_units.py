"""Search units (src/units.py) on an invented store."""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import units  # noqa: E402
from clean import PARA_COLS, SENT_COLS  # noqa: E402


def _write(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def test_units_are_model_sentences_and_whole_footnotes_with_their_context(tmp_path):
    data = str(tmp_path)
    ap = os.path.join(data, "appraisal")
    _write(os.path.join(ap, "paragraphs.csv"), PARA_COLS, [
        {"paragraph_id": "D:p00001", "doc_id": "D", "project_ids": "P1", "block": "narrative",
         "for_model": "true", "component_number": "2", "char_start": 0, "char_end": 40},
        {"paragraph_id": "D:p00002", "doc_id": "D", "project_ids": "P1", "block": "footnote",
         "for_model": "true", "char_start": 41, "char_end": 60, "n_tokens": 3,
         "text_sha256": "f"},
        {"paragraph_id": "D:p00003", "doc_id": "D", "project_ids": "P1", "block": "table",
         "for_model": "false", "char_start": 61, "char_end": 70}])
    _write(os.path.join(ap, "sentences.csv"), SENT_COLS, [
        {"sentence_id": "D:p00001:s001", "paragraph_id": "D:p00001", "doc_id": "D",
         "char_start": 0, "char_end": 20, "n_tokens": 4, "text_sha256": "a"},
        {"sentence_id": "D:p00003:s001", "paragraph_id": "D:p00003", "doc_id": "D",
         "char_start": 61, "char_end": 70, "n_tokens": 2, "text_sha256": "b"}])
    _write(os.path.join(ap, "components.csv"), ["doc_id", "source", "level", "number",
                                                "name_key"], [
        {"doc_id": "D", "source": "heading", "level": "component", "number": "2",
         "name_key": "masts"},
        {"doc_id": "D", "source": "datasheet", "level": "component", "number": "",
         "name_key": "unallocated"}])
    got = units.search_units(data)
    assert [(u["unit_id"], u["kind"], u["component_key"]) for u in got] == [
        ("D:p00001:s001", "sentence", "masts"),
        # A footnote is one unit; untagged, it takes no unnumbered component's key.
        ("D:p00002", "footnote", "")]
