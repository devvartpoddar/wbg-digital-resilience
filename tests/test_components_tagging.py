"""Which component a paragraph sits under, from its place in the document."""
import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import clean as C        # noqa: E402
import components as K   # noqa: E402

DOC = [  # (text, block, section_path) - invented
    ("Project Development Objective", "heading", "II.A"),
    ("The objective is to widen rural radio coverage.", "narrative", "II.A"),
    ("Component 1: Rules and Institutions (US$5 million)", "heading", "II.B"),
    ("This component will draft the spectrum rules.", "narrative", "II.B"),
    ("Component 2: Shared Masts", "heading", "II.B"),
    ("This component will build masts in three regions.", "narrative", "II.B"),
    ("Project Beneficiaries", "heading", "II.C"),
    ("Households in the three regions will benefit.", "narrative", "II.C"),
]


def test_paragraphs_take_the_component_above_them_until_the_section_changes():
    t = K.Tagger()
    got = [t.see(text, block, path)[0] for text, block, path in DOC]
    assert got == ["", "", "1", "1", "2", "2", "", ""]


def test_a_footnote_that_is_only_a_reference_and_a_link_is_left_out_of_the_model():
    assert C.citation_only("World Bank. 2021. Radio Access Review. [link]", "footnote")
    assert not C.citation_only("World Bank. 2021. Radio Access Review. [link]", "narrative")
    long = ("The towers will be raised above the recorded flood level and fitted "
            "with backup power for at least seventy two hours, see [link]")
    assert not C.citation_only(long, "footnote")


def test_a_run_in_subcomponent_title_opens_it():
    t = K.Tagger()
    t.see("Component 2: Shared Masts", "heading", "II.B")
    long = ("31. Subcomponent 2.2: Weather-proof cabinets. This subcomponent will finance "
            "cabinets for the shared masts with backup power and cooling, sized for the "
            "hottest month on record and raised above the recorded flood level at each site.")
    assert t.see(long, "narrative", "II.B") == ("2", "2.2")


@pytest.mark.parametrize("raw,clean", [
    ("Project Management Management", "Project Management"),
    ("Rural Radio and Masts The World Bank", "Rural Radio and Masts"),
    ("Shared Weather Stations Weather Stations", "Shared Weather Stations"),
])
def test_names_lose_a_doubled_tail_and_the_page_footer(raw, clean):
    assert K.clean_name(raw) == clean


@pytest.mark.parametrize("cell,ok", [("+2.5 +3.3", False), ("0.1", False),
                                     ("Rural radio", True), ("Données", True)])
def test_a_cell_of_figures_is_not_a_name(cell, ok):
    assert K._has_name(cell) is ok


@pytest.mark.parametrize("cell", ["IDA", "Front-end Fee", "Total"])
def test_financing_lines_end_the_component_list(cell):
    assert K.STOP_ROW.match(cell)


def test_an_exchange_rate_line_is_not_a_subcomponent():
    text = "5.72 BRL = US$1\nComponent 2: Rural Radio Masts"
    rows = [{"block": "heading", "char_start": "0", "char_end": "15", "page_from": "2"},
            {"block": "heading", "char_start": "16", "char_end": str(len(text)), "page_from": "9"}]
    got = K.read_headings(rows, text)
    assert [(c["level"], c["number"]) for c in got] == [("component", "2")]


def test_a_numbered_row_with_no_cost_is_the_next_component():
    grid = [["Component Name", "Cost (US$)"],
            ["1. Rural radio masts", "10,000,000.00"],
            ["2. Weather stations", "5,000,000.00"],
            ["3. Shared data hosting", None],
            ["and backup", None]]
    got = K.read_datasheet(grid)
    assert [(c["number"], c["name"]) for c in got] == [
        ("1", "Rural radio masts"), ("2", "Weather stations"),
        ("3", "Shared data hosting and backup")]


def test_a_restructuring_keeps_the_cost_before_and_after():
    grid = [["Current Component Name", "Current Cost (USD)", "Action",
             "Proposed Component Name", "Proposed Cost (USD)"],
            ["Component 1: Rural radio masts", "40,000,000.00", "Revised",
             "Component 1: Rural radio masts", "2,000,000.00"],
            ["Component 2: Project management", "4,000,000.00", "No Change",
             "Component 2: Project management", "4,000,000.00"]]
    got = K.read_restructuring(grid)
    assert [(c["cost_usd_m"], c["cost_before_usd_m"], c["action"]) for c in got] == \
        [("2.00", "40.00", "Revised"), ("4.00", "4.00", "No Change")]


def test_a_cost_wrapped_inside_its_cell_is_still_a_cost():
    grid = [["Component Name", "Cost (USD)"],
            ["Digital access", "53,400,000.00"],
            ["Digital public services", "125,000,000.0\n0"]]
    assert [(c["name"], c["cost_usd_m"]) for c in K.read_datasheet(grid)] == \
        [("Digital access", "53.40"), ("Digital public services", "125.00")]


def test_a_title_leading_in_a_list_is_read_as_a_heading():
    text = ("Component 3: Shared Data Hosting. The proposed changes include:\n(a) a new "
            "site;\n(b) backup power.\n\nComponent 4 will finance the remaining works.\n"
            "(a) roads.")
    cut = text.index("\n\n")
    rows = [{"block": "narrative", "char_start": "0", "char_end": str(cut), "page_from": "5"},
            {"block": "narrative", "char_start": str(cut + 2), "char_end": str(len(text)),
             "page_from": "5"}]
    got = K.read_headings(rows, text)
    assert [(c["level"], c["number"]) for c in got] == [("component", "3")]
    assert got[0]["name"].startswith("Shared Data Hosting")


def test_a_heading_with_no_colon_opens_its_component_and_names_it():
    t = K.Tagger()
    t.see("Component 5: Project implementation", "heading", "II.B")
    assert t.see("Component 1 Extending rural connectivity (US$45 million equivalent)",
                 "heading", "II.B") == ("1", "")
    assert t.comp_name == "Extending rural connectivity"
    # 'Component 1 will support...' is a sentence, not a heading.
    assert not K.COMP_HEAD.match("Component 1 will support the extension")


def test_a_subcomponent_heading_opens_its_own_component_when_another_is_open():
    t = K.Tagger()
    t.see("Component 2: Digital finance", "heading", "II.B")
    long = "51. Sub-component 3.1 - Strengthening the data ecosystem. " + "word " * 40
    assert t.see(long, "narrative", "II.B") == ("3", "3.1")
    assert t.sub_name == "Strengthening the data ecosystem"


@pytest.mark.parametrize("text,found", [
    ("Components 1 and 3 will finance", ["1", "3"]),
    ("Component 3, 911 Central dispatch", ["3"]),
    ("Component 324 refers", []),
    ("Sub-component 2.1 and 2.2", ["2.1", "2.2"])])
def test_mentions_read_whole_numbers_only(text, found):
    assert K.mentions(text) == found


def test_a_bare_decimal_heading_opens_nothing_without_its_component():
    import components
    t = components.Tagger()
    assert t.see("1.2 Sectoral and Institutional Context", "heading", "I") == ("", "")
    t.see("Component 1: Connectivity", "heading", "II.B")
    assert t.see("1.2 Last-mile access", "heading", "II.B") == ("1", "1.2")


def test_read_headings_takes_a_bare_decimal_heading_only_under_its_component():
    import components
    text = "1.2 Sectoral Context\nComponent 1: Connectivity\n1.2 Last-mile access\n"
    rows, pos = [], 0
    for line in text.splitlines(keepends=True):
        rows.append({"char_start": pos, "char_end": pos + len(line.rstrip("\n")),
                     "block": "heading", "page_from": 1})
        pos += len(line)
    found = components.read_headings(rows, text)
    assert [(r["level"], r["number"]) for r in found] == [("component", "1"),
                                                          ("subcomponent", "1.2")]
    assert found[1]["name"] == "Last-mile access"


def test_a_table_without_numbers_takes_them_from_the_names():
    """A restructuring table that prints no numbers and lists component 2
    first is numbered by name from the headings, not by row order (invented
    names)."""
    import components as C
    from keys import name_key

    def row(source, number, name, printed=None, doc="D2"):
        r = {"doc_id": doc, "project_ids": "P1", "source": source, "level": "component",
             "number": number, "name": name, "name_key": name_key(name)}
        if printed is not None:
            r["number_printed"] = printed
        return r
    rows = [row("heading", "1", "Building the Backbone Network", doc="D1"),
            row("heading", "2", "Digital Services for Citizens", doc="D1"),
            row("heading", "3", "Project Management and Coordination", doc="D1"),
            row("restructuring", "1", "Digital Services for Citizens", False),
            row("restructuring", "2", "Building the Backbone Network", False),
            row("restructuring", "3", "Project Management", False),
            row("restructuring", "4", "Contingent Emergency Response", False),
            row("datasheet", "7", "Printed Seven", True)]
    C.number_by_name(rows)
    got = {r["name"]: r["number"] for r in rows if r["source"] != "heading"}
    assert got == {"Digital Services for Citizens": "2", "Building the Backbone Network": "1",
                   "Project Management": "3", "Contingent Emergency Response": "4",
                   "Printed Seven": "7"}
