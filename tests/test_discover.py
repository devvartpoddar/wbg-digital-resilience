#!/usr/bin/env python3
"""The parts of the discovery stage that decide something.

The clustering itself is not tested here - UMAP and HDBSCAN are other people's
code and a test that re-asserts their behaviour tests nothing. What is tested is
the logic this repository owns: which section counts as junk, what counts as a
commitment rather than a description, and the sieve that turns those into a
verdict. Those are the calls that decide whether a community reaches a person,
so they are the ones worth pinning.

No document text appears here. The strings below are written for the test.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "analysis", "discovery"))

discover = pytest.importorskip("discover")


# --------------------------------------------------------- section provenance

@pytest.mark.parametrize("section", [
    "Fiduciary",
    "Implementation Arrangements and Support Plan",
    "KEY RISKS",
    "Institutional and Implementation Arrangements",
    "Financial Management",
    "Environmental and Social",
    "Lessons Learned and Reflected in the Project Design",
    "PROJECT STATUS AND RATIONALE FOR RESTRUCTURING",
    "DESCRIPTION OF PROPOSED CHANGES",
])
def test_junk_sections_are_caught(section):
    assert discover.is_junk_section(section)


@pytest.mark.parametrize("section", [
    "Project Components",
    "Detailed Project Description",
    "Sectoral and Institutional Context",
    "Country Context",
    "Technical, Economic and Financial Analysis",
    "ANNEX 6",
])
def test_substantive_sections_survive(section):
    assert not discover.is_junk_section(section)


def test_section_match_is_case_insensitive_and_partial():
    # The inventory carries the same section under several spellings, so the
    # test is on containment, not equality.
    assert discover.is_junk_section("fiduciary")
    assert discover.is_junk_section("B. Fiduciary Arrangements")
    assert not discover.is_junk_section("")
    assert not discover.is_junk_section(None)


# ------------------------------------------------------------ commitment shape

def test_commitment_needs_both_an_agent_and_a_modal():
    # Agent plus modal: a commitment.
    assert discover.commitment_shaped(
        "the project will deploy underground cable along the corridor")
    assert discover.commitment_shaped(
        "operators will be required to reinforce existing masts")
    # Modal with no agent cue: not attributable to anyone.
    assert not discover.commitment_shaped(
        "cables will degrade faster in saline conditions")
    # Agent with no modal: a description of the world, not a commitment.
    assert not discover.commitment_shaped(
        "the project covers four provinces and two islands")
    # Neither.
    assert not discover.commitment_shaped("rainfall intensity has risen")
    assert not discover.commitment_shaped("")


def test_commitment_shape_ignores_whitespace_and_case():
    assert discover.commitment_shaped(
        "  THE   BORROWER  SHALL   elevate  the  equipment  ")


# ------------------------------------------------------------------- the sieve

def _row(**kw):
    base = {"junk_section_share": 0.0, "n_projects": 10}
    base.update(kw)
    return base


def test_sieve_flags_junk_sections_and_thin_projects():
    rows = discover.apply_sieve([
        _row(),                                   # survives
        _row(junk_section_share=0.9),             # junk by location
        _row(n_projects=1),                       # one project: boilerplate
        _row(junk_section_share=0.9, n_projects=1),  # both reasons
    ], junk_share=0.6, min_projects=3)

    assert [r["verdict"] for r in rows] == ["shortlist", "junk", "junk", "junk"]
    assert rows[0]["verdict_reason"] == ""
    assert "junk_section_share" in rows[1]["verdict_reason"]
    assert "n_projects" in rows[2]["verdict_reason"]
    # Both reasons are recorded, not just the first one to fire: a community
    # rejected twice over should not look marginal in the stoplist.
    assert rows[3]["verdict_reason"].count(";") == 1


def test_sieve_boundary_is_inclusive_on_junk_share():
    rows = discover.apply_sieve(
        [_row(junk_section_share=0.6), _row(junk_section_share=0.59)],
        junk_share=0.6, min_projects=3)
    assert rows[0]["verdict"] == "junk"
    assert rows[1]["verdict"] == "shortlist"


def test_sieve_thresholds_are_arguments_not_constants():
    # The same row goes either way depending on the threshold, so a future
    # change of policy is a flag and not an edit.
    row = _row(n_projects=4)
    assert discover.apply_sieve([dict(row)], 0.6, 3)[0]["verdict"] == "shortlist"
    assert discover.apply_sieve([dict(row)], 0.6, 5)[0]["verdict"] == "junk"


# ----------------------------------------------------------------- l2 and norm

def test_l2_leaves_a_zero_row_alone_rather_than_dividing_by_zero():
    numpy = pytest.importorskip("numpy")
    mat = numpy.array([[3.0, 4.0], [0.0, 0.0]], dtype=numpy.float32)
    out = discover.l2(mat)
    assert out[0].tolist() == pytest.approx([0.6, 0.8])
    assert out[1].tolist() == [0.0, 0.0]


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
