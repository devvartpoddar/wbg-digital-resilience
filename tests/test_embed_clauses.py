#!/usr/bin/env python3
"""The clause text resolver, which is the part that can silently be wrong.

clauses.csv stores offsets, not text. If a clean file or the splitter changed,
the offsets still parse and still yield SOMETHING - just not what was measured.
The recorded text_sha256 is what turns that into an abort, so these tests pin
the abort rather than the happy path.

The UMAP/HDBSCAN discovery stage that used to be tested alongside this is gone:
its scores selected sentence stems ("This subcomponent will finance activities")
as high-commitment communities, so the numbers it produced were withdrawn and
the code with them.

No document text appears here. The strings below are written for the test.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "analysis", "discovery"))

# -------------------------------------------------- the clause text resolver

embed_clauses = pytest.importorskip("embed_clauses")


def _corpus(tmp_path, clause_text, recorded_sha=None):
    """A one-document, one-paragraph, one-clause corpus on disk."""
    import csv as _csv
    data = tmp_path / "data"
    (data / "clean").mkdir(parents=True)
    para = "PREFIX. " + clause_text + " TAIL."
    (data / "clean" / "900.txt").write_text(para, encoding="utf-8")
    base = 0
    cs, ce = para.index(clause_text), para.index(clause_text) + len(clause_text)
    sha = recorded_sha or __import__("hashlib").sha256(
        clause_text.encode("utf-8")).hexdigest()

    with open(data / "paragraphs.csv", "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=["paragraph_id", "doc_id", "block",
                                            "char_start", "char_end"])
        w.writeheader()
        w.writerow({"paragraph_id": "900:p00000", "doc_id": "900",
                    "block": "narrative", "char_start": base,
                    "char_end": len(para)})
    cl = data / "clauses.csv"
    with open(cl, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=["clause_id", "paragraph_id",
                                            "char_start", "char_end",
                                            "text_sha256"])
        w.writeheader()
        w.writerow({"clause_id": "900:p00000:c00:split-1",
                    "paragraph_id": "900:p00000", "char_start": cs,
                    "char_end": ce, "text_sha256": sha})
    return data, cl


def test_clause_text_is_located_by_paragraph_relative_offsets(tmp_path):
    data, cl = _corpus(tmp_path, "the project will elevate the equipment")
    paras = embed_clauses.read_paragraphs(str(data / "paragraphs.csv"))
    order, unique, empty = embed_clauses.resolve_clauses(
        str(data), str(cl), paras, lambda _m: None)
    assert len(order) == 1 and not empty
    assert list(unique.values()) == ["the project will elevate the equipment"]


def test_a_hash_mismatch_aborts_rather_than_embedding_the_wrong_text(tmp_path):
    # The whole reason the hash is stored: offsets still parse against a
    # regenerated clean file and still yield SOMETHING. A matrix of
    # confidently wrong vectors is worse than a failed run.
    data, cl = _corpus(tmp_path, "the project will elevate the equipment",
                       recorded_sha="0" * 64)
    paras = embed_clauses.read_paragraphs(str(data / "paragraphs.csv"))
    with pytest.raises(SystemExit) as exc:
        embed_clauses.resolve_clauses(str(data), str(cl), paras, lambda _m: None)
    assert "does not match its recorded hash" in str(exc.value)


def test_non_prose_blocks_are_skipped(tmp_path):
    import csv as _csv
    data, cl = _corpus(tmp_path, "a table cell fragment")
    rows = list(_csv.DictReader(open(data / "paragraphs.csv", newline="")))
    rows[0]["block"] = "table"
    with open(data / "paragraphs.csv", "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    paras = embed_clauses.read_paragraphs(str(data / "paragraphs.csv"))
    order, unique, _ = embed_clauses.resolve_clauses(
        str(data), str(cl), paras, lambda _m: None)
    assert order == [] and unique == {}
