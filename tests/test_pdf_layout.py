"""Unit tests for the PDF reader's rules and the sentence splitter.

No PDF is needed: each rule is a function of line text or of character
records, and the records below are invented in the shape pdfplumber returns.
No document text is ever committed.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pdf_layout as PL  # noqa: E402
import sentences as S  # noqa: E402


def _chars(spec, font="Calibri"):
    """[(text, size, bottom)] -> character records laid left to right."""
    out, x = [], 0.0
    for text, size, bottom in spec:
        out.append({"text": text, "size": size, "bottom": bottom, "x0": x,
                    "x1": x + size * 0.5, "fontname": font, "upright": True})
        x += size * 0.5
    return out


def _line(chars):
    return {"text": "".join(c["text"] for c in chars), "chars": chars}


def test_a_raised_small_digit_is_a_footnote_marker_and_goes():
    chars = _chars([(c, 11, 100) for c in "process"] + [("1", 7, 96), ("2", 7, 96)]
                   + [(" ", 11, 100)] + [(c, 11, 100) for c in "next"])
    assert PL._line_text(_line(chars), chars, 11) == "process next"


def test_a_lowered_small_digit_is_a_subscript_and_stays():
    chars = _chars([("C", 11, 100), ("O", 11, 100), ("2", 7, 102)])
    assert PL._line_text(_line(chars), chars, 11) == "CO2"


def test_a_footnote_keeps_its_whole_opening_number():
    chars = _chars([("4", 6, 96), ("3", 6, 96), (" ", 9, 100)] + [(c, 9, 100) for c in "ITU"])
    assert PL._line_text(_line(chars), chars, 9).startswith("43")


def test_a_symbol_font_bullet_becomes_a_bullet():
    chars = _chars([("y", 11, 100)], font="Wingdings") + \
        _chars([(" ", 11, 100)] + [(c, 11, 100) for c in "Item"])
    assert PL._line_text(_line(chars), chars, 11) == "• Item"


@pytest.mark.parametrize("line,expected", [
    ("V. KEY RISKS 25", True),
    ("ANNEX 3: Climate Risks and Proposed Actions 48", True),
    ("V. KEY RISKS", False),
    ("The project will connect 25", False),
])
def test_a_contents_entry_without_leaders_is_recognised(line, expected):
    assert PL._contents_entry(line) is expected


@pytest.mark.parametrize("line,before,expected", [
    ("• Inclusion of women", "the following:", True),
    ("(ii) above; and support for", "as described in sub-part", False),
    ("(ii) establishment of a centre", "support for (i) a study;", True),
    ("23. The project will", "as a result.", True),
    ("23. The project will", "reaching about", False),
])
def test_an_enumerator_only_opens_a_block_after_a_stopping_point(line, before, expected):
    assert PL._opens_block(line, before) is expected


def test_dehyphenation_follows_the_document():
    vocab = {"infrastructure"}
    assert PL._dehyphenate("national infra-", "structure plan", vocab) == \
        "national infrastructure plan"
    assert PL._dehyphenate("climate-", "resilient towers", vocab) == \
        "climate-resilient towers"
    assert PL._dehyphenate("national", "plan", vocab) == "national plan"


@pytest.mark.parametrize("text,expected", [
    ("38. Component 3 seeks to enhance, e.g. data centres. It will finance U.S. kit.",
     ["38. Component 3 seeks to enhance, e.g. data centres.", "It will finance U.S. kit."]),
    ("The project will finance: (a) towers; (b) fiber. No. 5 is excluded.",
     ["The project will finance: (a) towers; (b) fiber.", "No. 5 is excluded."]),
    ("It costs US$3.2 million (see Annex 2). Mr. J. Smith agreed.",
     ["It costs US$3.2 million (see Annex 2).", "Mr. J. Smith agreed."]),
    ("No full stop at the end", ["No full stop at the end"]),
])
def test_sentence_split(text, expected):
    assert [text[a:b] for a, b in S.split(text)] == expected


def test_sentence_offsets_index_the_paragraph():
    text = "This is the first one. This is the second one."
    spans = S.split(text)
    assert spans == [(0, 22), (23, 46)]


def test_a_sentence_of_fewer_than_three_words_is_not_cut_off():
    text = "See above. The project will finance towers."
    assert [text[a:b] for a, b in S.split(text)] == [text]
