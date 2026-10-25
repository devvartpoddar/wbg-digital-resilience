"""The PDF reader's rules and the sentence splitter, on invented line text and
character records shaped like pdfplumber's; no PDF needed.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wbg_portfolio import pdf_layout as PL  # noqa: E402
from wbg_portfolio import text_rules as T  # noqa: E402
from wbg_portfolio import sentences as S  # noqa: E402


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
    # The next item of a list the paragraph began inline continues it.
    ("(ii) establishment of a centre", "support for (i) a study;", False),
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
     # Each item of an inline list is its own sentence, however short.
     ["The project will finance:", "(a) towers;", "(b) fiber.", "No. 5 is excluded."]),
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


@pytest.mark.parametrize("text,expected", [
    ("See https://www.example.org/report.pdf for details.", "See [link] for details."),
    ("Source: www.example.org/en/ country/overview. More.", "Source: [link] More."),
    ("Data from the agency (2021).", "Data from the agency (2021)."),
])
def test_a_web_address_becomes_a_link_marker(text, expected):
    assert PL.URL.sub("[link]", text) == expected


def test_a_rotated_character_is_dropped_and_a_level_one_kept():
    assert PL._not_rotated({"object_type": "char", "matrix": (1, 0, 0, 1, 5, 5)})
    assert not PL._not_rotated({"object_type": "char", "matrix": (0, 1, -1, 0, 5, 5)})
    assert PL._not_rotated({"object_type": "rect"})


def test_an_inline_list_is_split_one_item_per_sentence():
    t = ("The Project draws on: (a) civil registration systems; (b) national "
         "identification systems; and (c) legal frameworks for privacy. It also draws "
         "on other projects.")
    assert [t[a:b] for a, b in S.split(t)] == [
        "The Project draws on:", "(a) civil registration systems;",
        "(b) national identification systems; and",
        "(c) legal frameworks for privacy.", "It also draws on other projects."]


def test_a_list_begun_inline_carries_on_across_a_line_break():
    before = "57. The Project draws on best practices on: (a) civil registration systems;"
    assert PL._opens_block("(b) national identification systems; (c) legal", before) is False
    # A paragraph that is itself a list item: the next item is its own.
    assert PL._opens_block("(b) next item", "(a) first item of a bulleted list;") is True
    # Only the next marker in sequence continues the list.
    assert PL._opens_block("(c) a later item", "on: (a) civil registration;") is True


def test_a_character_drawn_off_the_page_is_dropped():
    keep = PL._on_page((0, 0, 600, 800))
    char = {"object_type": "char", "x0": 10, "x1": 15, "top": 100, "bottom": 110}
    assert keep(char)
    assert not keep(dict(char, x0=-110, x1=-105))     # the stamp left of the page
    assert keep(dict(char, x0=-2, x1=3))              # half on the page stays
    assert keep({"object_type": "rect", "x0": -50, "x1": -40, "top": 0, "bottom": 5})


def test_a_list_set_out_line_by_line_splits_one_item_per_sentence():
    t = ("36. Sub-component 1.2 will finance consultancies for:\n(a) technical "
         "analysis;\n(b) towers; and\n(c) procurement support.")
    assert [t[a:b] for a, b in S.split(t)] == [
        "36. Sub-component 1.2 will finance consultancies for:", "(a) technical analysis;",
        "(b) towers; and", "(c) procurement support."]
    # A one-word item stands alone on its own line.
    t = "The project will finance:\nFiber\nTowers"
    assert [t[a:b] for a, b in S.split(t)] == ["The project will finance:", "Fiber", "Towers"]


@pytest.mark.parametrize("answer", ["N/A", "N/A.", "None", "Not applicable."])
def test_a_form_answer_alone_is_not_a_sentence(answer):
    t = f"Describe any project alternatives considered to avoid impacts. {answer}"
    assert [t[a:b] for a, b in S.split(t)] == [
        "Describe any project alternatives considered to avoid impacts."]


def test_a_component_title_is_neither_a_lead_in_nor_a_list_item():
    assert PL._title_line("Component 2: Public Sector Support for Digital Economy")
    assert PL._title_line("Sub-component 2.3: Building a National Data Center (US$5.5 million)")
    assert not PL._title_line("Component 2 will finance the following activities.")
    assert not PL._title_line("Component 2: Public Sector Support\nIntegrated platforms")


def test_a_title_set_as_a_bullet_is_still_a_title():
    assert PL._title_line("• Sub-component 2.3: Building a National Data Center")


def test_a_glued_footnote_number_goes_but_a_version_number_stays():
    from collections import Counter
    from wbg_portfolio import pdf_layout as P
    stats = Counter()
    defined = {6, 44}
    assert P.strip_inline_marks("as hoped44 for", defined, stats) == "as hoped for"
    assert P.strip_inline_marks("routed over IPv6 and Wi-Fi6 links", defined, stats) == \
        "routed over IPv6 and Wi-Fi6 links"
    assert P.strip_inline_marks("under IDA19 terms", defined, stats) == "under IDA19 terms"
    assert P.strip_inline_marks("in phase6 of", {3}, stats) == "in phase6 of"
    assert stats["inline_marks_removed"] == 1


def _typed(size, text, top=0):
    return {"size": size, "text": text, "top": top}


def test_footnotes_are_the_small_numbered_type_at_the_foot_of_a_page():
    page = [_typed(11, "Body text runs here."), _typed(11, "More body text."),
            _typed(9, "12 World Bank. 2022. A report."), _typed(9, "a second line of it")]
    assert PL._footnote_start(page, 11) == 2


def test_an_annex_set_a_point_smaller_keeps_its_text_above_its_footnotes():
    page = [_typed(10, "Annex text in smaller type.")] * 30 + \
        [_typed(9, "72 Areas to be covered."), _typed(9, "the footnote goes on")]
    assert PL._footnote_start(page, 11) == 30


def test_a_page_set_small_with_no_footnote_is_not_a_footnote():
    page = [_typed(10, "Annex text in smaller type.")] * 30
    assert PL._footnote_start(page, 11) == 30


def test_a_short_carried_footnote_below_body_text_is_a_footnote():
    page = [_typed(11, "Body text.")] * 20 + [_typed(9, "continued from the page before.")]
    assert PL._footnote_start(page, 11) == 20


def test_footnotes_in_the_texts_own_small_type_start_after_the_gap():
    page = [_typed(11, "ANNEX 2: GENDER ANALYSIS", 100)] + \
        [_typed(9, "Benin: women used the internet less,63 such as", 150 + 11 * i) for i in range(30)] + \
        [_typed(9, "63 World Bank. 2025. Benin gender data.", 500), _typed(9, "64 Afrobarometer. 2020.", 511)]
    assert PL._footnote_start(page, 11) == 31


def test_a_bulleted_line_hung_left_of_a_table_is_in_the_table():
    box = (36, 90, 576, 709)
    bullet = {"x0": 23, "x1": 560, "top": 179, "bottom": 189}
    assert PL._in_box(bullet, box)
    # A line beside the table, mostly outside it, is not.
    assert not PL._in_box({"x0": 0, "x1": 60, "top": 179, "bottom": 189}, box)
    # Nor one above it.
    assert not PL._in_box({"x0": 40, "x1": 500, "top": 50, "bottom": 60}, box)


@pytest.mark.parametrize("text,expected", [
    ("Training was given on: a) civil registration; b) national ID; and c) privacy law.",
     ["Training was given on:", "a) civil registration;", "b) national ID; and",
      "c) privacy law."]),
    ("Training was given on: a. civil registration; b. national ID; and c. privacy law.",
     ["Training was given on:", "a. civil registration;", "b. national ID; and",
      "c. privacy law."]),
    ("The unit will work on: i. data centres; ii. fibre routes; iv) towers.",
     ["The unit will work on:", "i. data centres;", "ii. fibre routes;", "iv) towers."]),
    ("The unit will work on: 1) data centres; 2) fibre routes.",
     ["The unit will work on:", "1) data centres;", "2) fibre routes."]),
])
def test_every_enumerator_form_opens_an_inline_list_item(text, expected):
    assert [text[a:b] for a, b in S.split(text)] == expected


@pytest.mark.parametrize("text", [
    "Mr. J. Smith of the U.S. office agreed on a budget of US$3.2 million.",
    "The case Smith v. Jones was cited, and the ratio was 4.5 to 1 overall.",
    "38. Component 3 seeks to enhance the data centres of the ministry.",
])
def test_initials_decimals_and_paragraph_numbers_are_not_list_markers(text):
    assert [text[a:b] for a, b in S.split(text)] == [text]


def test_every_enumerator_form_marks_a_list_item():
    for item in ("(a) towers;", "a) towers;", "a. towers;", "ii. towers;", "iv) towers;",
                 "1) towers;", "(xii) towers;"):
        assert S.ITEM_MARKER.match(item), item
    for not_item in ("J. Smith agreed.", "U.S. agencies", "3.2 million", "38. Component 3",
                     "e.g. towers", "A. Background"):
        assert not S.ITEM_MARKER.match(not_item), not_item


def test_a_list_separated_by_commas_splits_when_its_markers_run_in_sequence():
    t = ("The agency is responsible for (a) setting the toll tariff, (b) running the toll "
         "stations and collecting tolls, and (c) raising revenue for maintenance.")
    assert [t[a:b] for a, b in S.split(t)] == [
        "The agency is responsible for (a) setting the toll tariff,",
        "(b) running the toll stations and collecting tolls, and",
        "(c) raising revenue for maintenance."]
    t = "The project will (i) build towers, (ii) lay fibre, and (iii) train staff in the regions."
    assert len(S.split(t)) == 3


@pytest.mark.parametrize("text", [
    # A cross-reference in brackets begins no list.
    "This is covered elsewhere (see (a) above), (b) is not relevant to the towers.",
    # Markers out of sequence are not a list.
    "The unit will do (a) planning, (c) monitoring of works across the regions.",
    # A single marker after a comma, with no list begun.
    "The towers are listed in the annex, (b) refers to the fibre routes there.",
])
def test_a_comma_before_a_marker_out_of_a_list_is_not_a_boundary(text):
    assert [text[a:b] for a, b in S.split(text)] == [text]


def test_two_footnotes_printed_on_one_line_are_two_footnotes():
    line = ("Data from the monthly report of the agency, February 2023 14 A joint study "
            "conducted in the region has shown the effect.")
    assert PL._footnote_pieces(line, 13) == [
        ("Data from the monthly report of the agency, February 2023", 13),
        ("14 A joint study conducted in the region has shown the effect.", 14)]
    # A number that is not the next footnote, or not at a footnote's end, stays.
    assert len(PL._footnote_pieces("There were 14 Member States in 2020.", 13)) == 1
    assert len(PL._footnote_pieces("Report of 15 Agencies in 2020.", 13)) == 1
    assert PL._footnote_pieces("See https://example.org/x 8 The survey covered it.", 7)[1] == \
        ("8 The survey covered it.", 8)


def test_footnotes_read_as_body_text_are_one_unit_each():
    from collections import Counter
    # The next footnote on a line of its own opens a new unit.
    assert PL._starts_next_note("10 Average price data from 2021.",
                                "9 Ministry of Transport Statistical Annual Report")
    assert PL._starts_next_note("15 The third note.", "13 Data from 2023 14 A joint study")
    assert not PL._starts_next_note("10 Average price.", "38. Component 3 will finance")
    assert not PL._starts_next_note("11 Average price.", "9 Ministry of Transport")
    # Footnotes joined inline into one body unit are cut apart.
    units = [["body", "13 Data from the agency, February 2023 14 A joint study was done. "
              "15 The third note is here.", 3, 3, {"src": [10, 90]}],
             ["body", "The project will finance 14 Towers in 2023 15 Districts.", 3, 3, {}]]
    got = PL._split_glued_notes(units, Counter())
    assert [u[1] for u in got] == [
        "13 Data from the agency, February 2023", "14 A joint study was done.",
        "15 The third note is here.", "The project will finance 14 Towers in 2023 15 Districts."]
    assert got[1][4]["src"] == [10, 90] and got[1][4]["src"] is not got[0][4]["src"]


def test_the_parser_version_follows_the_reading_constants(monkeypatch):
    from wbg_portfolio import clean
    v = clean.parser_version()
    assert v == clean.parser_version() and v.startswith(clean.PARSER + "-")
    assert "/pdfplumber-" in v and "/pdfminer-" in v
    monkeypatch.setattr(PL, "LINE_GAP", PL.LINE_GAP + 0.01)
    assert clean.parser_version() != v


# ------------------------------------------------- text rules (src/wbg_portfolio/text_rules.py)

def test_unicode_hyphens_fold_to_ascii():
    """U+2010 looks like a hyphen but tokenises as something else."""
    assert T.fold_chars("Sub‐component and Non‑Consulting") == \
        "Sub-component and Non-Consulting"


def test_private_use_glyphs_fold_to_what_they_were_drawn_as():
    """Symbol and Wingdings code points mean nothing once the font is gone."""
    folded = T.fold_chars(" towers  masts NCI")
    assert not any(0xE000 <= ord(c) <= 0xF8FF for c in folded)
    assert folded == "• towers • masts [NCI] "


def test_check_marks_are_left_alone():
    """A tick in a safeguards table is data: it says which policy applies."""
    assert "✔" in T.fold_chars("Natural habitats ✔")


def test_zero_width_and_directional_marks_go():
    assert T.fold_chars("radio​mast‎") == "radiomast"


def test_a_running_line_fingerprint_ignores_spacing_and_page_numbers():
    assert T.fingerprint("Rural Radio Project (P999001)  12") == \
        T.fingerprint("Rural Radio Project(P999001) 13")


def test_a_row_of_figures_reads_as_tabular():
    laid_out = "Households reached       Number      0.00        1,200       2,400"
    assert T.looks_tabular(laid_out)
    assert not T.looks_tabular("The project will build masts in three districts.")


def test_annex_heading_is_told_from_a_wrapped_cross_reference():
    assert T.annex_match("ANNEX 2: Detailed Project Description")
    assert not T.annex_match("Annex 1). The masts will be raised above the flood line.")
