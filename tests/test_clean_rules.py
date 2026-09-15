"""Unit tests for the cleaning rules, on a synthetic document.

The fixture below is written for this test. It is NOT taken from a World Bank
document: paragraph text is permitted in exactly one committed place
(inputs/labels/samples/), and tests/ is not it. Every hazard the cleaner has hit
in the real corpus is reproduced here in invented prose, so a regression fails
here rather than being found by reading output months later.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import clean as C  # noqa: E402

FF = "\f"

# Page 1: cover and contents. Note the contents entry for ANNEX 2 ends with a
# SINGLE space before its page number, which is what defeated an earlier
# per-line rule and is why contents detection works from a span.
PAGE_COVER = """\
     The World Bank
     Riverine Connectivity Project (P999001)

     CONTENTS

I.    FIRST SECTION ...................................................... 1
II.   SECOND SECTION .................................................... 4
III.  THIRD SECTION ..................................................... 7
ANNEX 1: Arrangements ................................................... 9
ANNEX 2: Detailed Description 11
"""

# Page 2: body opens. A paragraph wraps across the page break, an inline
# footnote marker is glued to a word, and several abbreviation+number tokens
# must survive untouched.
PAGE_BODY_A = """\
     The World Bank
     Riverine Connectivity Project (P999001)

I. FIRST SECTION
A. Background

1.   The programme was appraised in FY01 and revised under IDA19 terms. Costs
were estimated at EUR25.8 million, with a floor of US$1 million and a ceiling
of XOF603.59 million under SOP1. Works began in the delta and the towers were
sited to avoid flood-prone ground, which the design calls climate-resilient
throughout.

2.   Uptake rose from 35 percent to 40.5 percent16 over the period, and the
operators reported that SDGs 9 and 11 were served. Component 1 will build the
regulatory base, and Component 2 will extend it. See annex 2 for detail. Lines
100 and 911 will coexist. Coverage reached
Page 2
"""

# Page 3: the paragraph continues from page 2, then a footnote region with both
# layouts - number joined to its text, and number alone above its text - whose
# body wraps onto continuation lines.
PAGE_BODY_B = """\
     The World Bank
     Riverine Connectivity Project (P999001)

nine of the twelve districts by the close of the period, against a target of
eight.

B. Approach

3.   The approach is set out below and is expected to hold for the duration.

16Uptake figures are drawn from the operator returns of that year and are
rounded to one decimal place.
17
  The district boundaries were redrawn during the period, which limits
comparability across the series.
Page 3 of 9
"""

# Page 4: a flattened table, with column gutters, and wordy row labels that a
# numeric-density test alone would not catch.
PAGE_TABLE = """\
     The World Bank
     Riverine Connectivity Project (P999001)

II. SECOND SECTION

Indicator                              Baseline        Target        Actual
Households connected to the network      0.00      45,000.00     31,200.00
Share of connections in flood zones      0.00         30.00         18.40
"""

# Page 5: an annex heading appears for real, after the body sections.
PAGE_ANNEX = """\
     The World Bank
     Riverine Connectivity Project (P999001)

ANNEX 2: Detailed Description

4.   The detailed description restates the component design and adds the
siting criteria applied to each district.

5.   Sub‐component 3.1 covers Non‐Consulting services, and the review
found:  a baseline study,  a costing note.

Safeguard triggered
Environmental Assessment OP/BP 4.01 ✔
Natural Habitats OP/BP 4.04 ✔
"""

DOC = FF.join([PAGE_COVER, PAGE_BODY_A, PAGE_BODY_B, PAGE_TABLE, PAGE_ANNEX])


@pytest.fixture(scope="module")
def cleaned():
    text, spans, state = C.clean_document(DOC)
    return text, spans, state


def bodies(cleaned, block=None):
    text, spans, _ = cleaned
    return [text[a:b] for a, b, _p, _t, blk in spans if block is None or blk == block]


def joined(cleaned):
    return " ".join(bodies(cleaned))


# --------------------------------------------------------------- destruction

@pytest.mark.parametrize("token", [
    "FY01", "IDA19", "EUR25.8", "US$1", "XOF603.59", "SOP1",
    "SDGs 9 and 11", "Component 1 will build", "Component 2 will extend",
    "annex 2 for detail", "Lines 100 and 911", "climate-resilient",
])
def test_survives_cleaning(cleaned, token):
    """Abbreviations, currency figures and quantities must reach the output
    unchanged. An earlier rule deleted the digits in five of these."""
    assert token in joined(cleaned), f"{token!r} was destroyed by cleaning"


# ---------------------------------------------------------------- characters

def test_unicode_hyphens_are_folded_to_ascii(cleaned):
    """U+2010 renders identically to a plain hyphen and reads identically to a
    person, but tokenises as something else. 665 of them survived the first
    full corpus, in "Non-Consulting" and "Sub-component"."""
    t = joined(cleaned)
    assert "\u2010" not in t and "\u2011" not in t
    assert "Sub-component 3.1" in t
    assert "Non-Consulting services" in t


def test_private_use_characters_are_folded(cleaned):
    """A Private Use Area code point means whatever font emitted it and nothing
    once the font is gone, so it is noise rather than a character. These two are
    Symbol's and Wingdings' bullets."""
    t = joined(cleaned)
    assert not any(0xE000 <= ord(c) <= 0xF8FF for c in t), \
        "a private-use character reached the stored text"
    assert "\u2022 a baseline study" in t, "the bullet it was drawn as should remain"


# The four glyphs the first full corpus turned up that the fixture above does
# not carry: two more Wingdings bullets, and Symbol's brackets and space. A
# second one-page document keeps them out of the fixture, so adding them cannot
# move a span any other test indexes.
PUA_PAGE = """\
     The World Bank
     Riverine Connectivity Project (P999001)

I. FOURTH SECTION

6.   Support was agreed for the districts as follows:
\uf0a7 households connected to the network
\uf0a7 clinics served by the backbone
\uf0d8 district offices on the link

7.   The institution \uf05bNCI\uf05d leads delivery, with support from its
partners. \uf020
"""


def test_later_private_use_glyphs_are_folded():
    """U+F0A7 and U+F0D8 are Wingdings' black small square and arrowhead, the
    same glyphs as U+25AA and U+27A2 which already fold to a bullet. U+F05B and
    U+F05D are Symbol's brackets and U+F020 its space. Found by the audit's
    private-use check on the full corpus, not by inspection."""
    text, spans, _state = C.clean_document(PUA_PAGE)
    assert not any(0xE000 <= ord(c) <= 0xF8FF for c in text), \
        "a private-use character reached the stored text"
    narrative = [text[a:b] for a, b, _p, _t, blk in spans if blk == "narrative"]
    bullets = [b for b in narrative if b.startswith("\u2022")]
    assert len(bullets) == 3, (
        "the two Wingdings bullets must each start their own paragraph, not "
        f"leave one glued run: got {narrative!r}")
    assert any("households connected to the network" in b for b in bullets)
    assert any("offices on the link" in b for b in bullets)
    assert "[NCI]" in text, "Symbol's brackets should fold to ASCII brackets"


def test_check_marks_are_left_alone(cleaned):
    """A tick in the safeguards table is DATA - it says which policy is
    triggered. Folding it to a bullet, as the other markers are folded, would
    destroy the distinction between checked and unchecked."""
    assert "\u2714" in joined(cleaned), "a check mark was folded away"


# ----------------------------------------------------------------- footnotes

def test_glued_footnote_marker_is_stripped(cleaned):
    """40.5 percent16 -> 40.5 percent, because footnote 16 is declared on the
    following page. The figure itself must be intact."""
    t = joined(cleaned)
    assert "percent16" not in t
    assert "40.5 percent" in t


def test_footnote_bodies_are_their_own_block(cleaned):
    fn = " ".join(bodies(cleaned, "footnote"))
    assert "operator returns" in fn
    assert "district boundaries were redrawn" in fn


def test_footnote_continuation_does_not_orphan_into_narrative(cleaned):
    """A footnote's wrapped text used to be emitted as a narrative paragraph
    starting mid-sentence."""
    narrative = bodies(cleaned, "narrative")
    assert not any(b.startswith("rounded to one decimal") for b in narrative)
    assert not any(b.startswith("comparability across") for b in narrative)


# -------------------------------------------------------------------- layout

def test_running_header_and_page_numbers_removed(cleaned):
    t = joined(cleaned)
    assert "The World Bank" not in t
    assert "(P999001)" not in t
    assert not re.search(r"\bPage \d", t)


def test_paragraph_rejoined_across_page_break(cleaned):
    """Paragraph 2 runs off page 2 and resumes on page 3."""
    assert any("Coverage reached nine of the twelve districts" in b
               for b in bodies(cleaned)), "paragraph was not rejoined across the page break"


def test_no_whitespace_runs_in_stored_text(cleaned):
    for body in bodies(cleaned):
        assert not re.search(r"\S {3,}\S", body), f"whitespace run left in {body[:60]!r}"


def test_no_sentinel_leaks_into_stored_text(cleaned):
    assert C.FOOTNOTE_SENTINEL not in joined(cleaned)


# --------------------------------------------------------------------- blocks

def test_table_rows_are_labelled_table(cleaned):
    tables = " ".join(bodies(cleaned, "table"))
    assert "Households connected to the network" in tables, \
        "a wordy table row was not recognised as tabular"


def test_headings_are_their_own_block(cleaned):
    heads = bodies(cleaned, "heading")
    assert any(h.startswith("I. FIRST SECTION") for h in heads)
    assert not any(h.startswith("I. FIRST SECTION") for h in bodies(cleaned, "narrative"))


def test_contents_entries_are_not_treated_as_body_headings(cleaned):
    """Every contents line must be suppressed, including the ANNEX 2 entry whose
    page number is preceded by a single space."""
    for _a, _b, path, title, block in cleaned[1]:
        assert not (block == "heading" and "..." in title)
    assert "CONTENTS" not in " ".join(bodies(cleaned, "narrative"))


def test_real_annex_heading_is_found_after_the_body(cleaned):
    paths = {p for _a, _b, p, _t, _blk in cleaned[1]}
    assert "ANNEX 2" in paths
    annex = " ".join(bodies(cleaned, "annex"))
    assert "siting criteria applied" in annex


def test_section_path_is_recorded_for_body_paragraphs(cleaned):
    paths = {p for _a, _b, p, _t, blk in cleaned[1] if blk == "narrative"}
    assert "I.A" in paths or "I.B" in paths


# --------------------------------------------------------------- helper units

def test_norm_of_tabular_detection_runs_before_collapse():
    """Gutters are the signal, so the test must see the laid-out line."""
    laid_out = "Households in flood zones served      none      forty      twenty"
    assert C.looks_tabular(laid_out), "gutters should mark a wordy row as tabular"
    assert not C.looks_tabular(re.sub(r"\s+", " ", laid_out)), \
        "with gutters collapsed there is nothing left to detect"


def test_annex_match_rejects_a_wrapped_cross_reference():
    assert C.annex_match("ANNEX 2: Detailed Description")
    assert not C.annex_match(
        "Annex 1). The one-time subsidies will support broadband infrastructure "
        "in targeted villages where the market has not reached.")


def test_contents_span_covers_unevenly_spaced_entries():
    lines = ["I.   A ............ 1", "", "", "", "", "",
             "II.  B ............ 4", "", "", "", "", "",
             "III. C ............ 7", "ANNEX 1: D 9", "", "I. A", "body text"]
    toc = C.find_contents_lines(lines)
    # Entries 0, 6 and 12 carry page numbers; 13 lost its leader so it is only
    # caught because it sits immediately after one that did.
    assert {0, 6, 12, 13} <= toc, "gaps between contents entries broke detection"
    # The first body heading must NOT be absorbed into the contents span.
    assert 15 not in toc, "contents detection swallowed a real body heading"


def test_undecodable_bytes_are_dropped_not_replaced():
    """The Bank's own converter truncates a sequence, so a closing quote reaches
    us as b'\\xe2\\x80?' instead of b'\\xe2\\x80\\x9d', and one real document
    carries CESU-8 surrogate pairs. Decoding must not turn any of that into
    U+FFFD, which would reach the embeddings as noise; those bytes form no
    character, so the fragment is dropped and the count is kept."""
    text, dropped = C.decode_raw(b'services ("eFaas\xe2\x80? and more')
    assert "\ufffd" not in text
    assert text == 'services ("eFaas? and more'
    assert dropped == 2
    text, dropped = C.decode_raw(b"value \xed\xa0\xb5\xed\xb1\x87 end")
    assert text == "value  end"
    assert dropped == 6
