"""Which component a paragraph sits under, from its place in the document."""
import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wbg_portfolio import clean as C        # noqa: E402
from wbg_portfolio import components as K   # noqa: E402

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
    from wbg_portfolio import components
    t = components.Tagger()
    assert t.see("1.2 Sectoral and Institutional Context", "heading", "I") == ("", "")
    t.see("Component 1: Connectivity", "heading", "II.B")
    assert t.see("1.2 Last-mile access", "heading", "II.B") == ("1", "1.2")


def test_read_headings_takes_a_bare_decimal_heading_only_under_its_component():
    from wbg_portfolio import components
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
    from wbg_portfolio import components as C
    from wbg_portfolio.keys import name_key

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


# ---- lettered and roman components (invented text)

def _rows(text, block="heading"):
    rows, pos = [], 0
    for line in text.splitlines(keepends=True):
        rows.append({"char_start": pos, "char_end": pos + len(line.rstrip("\n")),
                     "block": block, "page_from": 1})
        pos += len(line)
    return rows


LETTERED = [
    ("Component A: Rules and Institutions (US$5 million)", "heading", "II.B"),
    ("This component will draft the spectrum rules.", "narrative", "II.B"),
    ("Component B: Shared Masts", "heading", "II.B"),
    ("This component will build masts in three regions.", "narrative", "II.B"),
    ("Subcomponent B.1: Tower Sites", "heading", "II.B"),
    ("Sites will be leased for twenty years.", "narrative", "II.B"),
    ("Component B.2: Backup Power", "heading", "II.B"),
    ("Each mast gets a battery.", "narrative", "II.B"),
    ("Component C Project Management", "heading", "II.B"),
    ("Staff will be hired.", "narrative", "II.B"),
    ("Project Beneficiaries", "heading", "II.C"),
    ("Households will benefit.", "narrative", "II.C"),
]


def test_lettered_components_and_subcomponents_tag_their_paragraphs():
    t = K.Tagger()
    got = [t.see(text, block, path) for text, block, path in LETTERED]
    assert got == [("A", ""), ("A", ""), ("B", ""), ("B", ""), ("B", "B.1"), ("B", "B.1"),
                   ("B", "B.2"), ("B", "B.2"), ("C", ""), ("C", ""), ("", ""), ("", "")]


def test_lettered_titles_are_kept_without_their_letter():
    t = K.Tagger()
    t.see("Component A: Rules and Institutions (US$5 million)", "heading", "II.B")
    assert t.comp_name == "Rules and Institutions"
    t.see("Component A1: Spectrum Rules", "heading", "II.B")
    assert (t.comp, t.sub, t.sub_name) == ("A", "A.1", "Spectrum Rules")


def test_a_bare_lettered_subheading_opens_only_under_its_component():
    t = K.Tagger()
    assert t.see("B.1 Tower Sites", "heading", "II.B") == ("", "")
    t.see("Component B: Shared Masts", "heading", "II.B")
    assert t.see("B.1 Tower Sites", "heading", "II.B") == ("B", "B.1")


def test_a_lettered_subcomponent_heading_opens_its_missed_component():
    t = K.Tagger()
    t.see("Component A: Rules", "heading", "II.B")
    assert t.see("Subcomponent C.1: Training", "heading", "II.B") == ("C", "C.1")


def test_a_long_run_in_lettered_title_opens_it():
    t = K.Tagger()
    body = ("Component D: Data Centre. " + "This component will finance the building and "
            "equipping of a data centre with backup power for the ministry. " * 2)
    assert t.see(body, "narrative", "II.B") == ("D", "")
    body = "12. Subcomponent D.2: Cooling. " + "It will fit cooling units to the hall. " * 6
    assert t.see(body, "narrative", "II.B") == ("D", "D.2")


def test_words_that_start_with_capitals_are_not_component_letters():
    t = K.Tagger()
    assert t.see("Component CERC Details", "heading", "II.B") == ("", "")
    assert t.see("Component Management", "heading", "II.B") == ("", "")
    assert t.see("Component A will help.", "narrative", "II.B") == ("", "")


def test_read_headings_finds_lettered_components_and_subcomponents():
    text = ("Component A: Rules and Institutions (US$5.5 million)\n"
            "Subcomponent A.1: Spectrum Rules\n"
            "Component B: Shared Masts\n"
            "B.1 Tower Sites\n"
            "Component B.2: Backup Power\n"
            "Component C1: Training\n")
    found = K.read_headings(_rows(text), text)
    assert [(r["level"], r["number"]) for r in found] == [
        ("component", "A"), ("subcomponent", "A.1"), ("component", "B"),
        ("subcomponent", "B.1"), ("subcomponent", "B.2"), ("subcomponent", "C.1")]
    assert found[0]["cost_usd_m"] == "5.50"
    assert found[1]["name"] == "Spectrum Rules"


def test_roman_components_are_numbers_and_sort_by_value():
    from wbg_portfolio.keys import number_key, uses_roman
    text = "Component I: Rules\nComponent II: Masts\nComponent III: Management\n"
    found = K.read_headings(_rows(text), text)
    assert [r["number"] for r in found] == ["I", "II", "III"]
    # A lone I is a letter in a lettered document and a numeral beside II.
    assert not uses_roman(["A", "B", "I"]) and uses_roman(["I", "II", "III"])
    assert sorted(["III", "II", "I"], key=lambda n: number_key(n, True)) == ["I", "II", "III"]
    assert sorted(["I", "H", "A"], key=lambda n: number_key(n, False)) == ["A", "H", "I"]
    assert sorted(["B.2", "B.10", "A", "2", "10"], key=number_key) == \
        ["2", "10", "A", "B.2", "B.10"]


def test_numbered_components_still_read_as_before():
    t = K.Tagger()
    assert t.see("Component 1: Rules", "heading", "II.B") == ("1", "")
    assert t.see("Component 1 Extending connectivity", "heading", "II.B") == ("1", "")
    assert t.see("Sub-component 1.2: Masts", "heading", "II.B") == ("1", "1.2")
    assert K.mentions("Component 2 and Sub-component 10.1") == ["2", "10.1"]


@pytest.mark.parametrize("heading", [
    "Component 5: Contingent Emergency Response Component",
    "Component 5: Contingent Emergency Response Component (CERC)",
    "Component 5: Contingent Emergency Response Component (CERC) (US$0 million)",
    "Component 5: Contingent Emergency Response Component (Zero cost)",
    "Component E: Contingent Emergency Response Component (CERC)",
    "Component 5 Contingent Emergency Response Component (CERC) - US$0.00"])
def test_a_contingent_emergency_response_component_heading_opens_its_component(heading):
    t = K.Tagger()
    t.see("Component 4: Project Management", "heading", "II.B")
    comp, _ = t.see(heading, "heading", "II.B")
    assert comp == heading.split()[1].rstrip(":")
    assert t.see("This component supports an emergency response if needed.",
                 "narrative", "II.B")[0] == comp
    assert t.comp_name.startswith("Contingent Emergency Response Component")
    # a lettered component counts in a document whose lettered headings run A, B ... (rule: no lone letter)
    text = ("Component A: First\nComponent B: Second\nComponent C: Third\nComponent D: Fourth\n" if comp.isalpha() else "") \
        + heading + "\n"
    assert [r["number"] for r in K.read_headings(_rows(text), text)][-1] == comp


def test_mentions_read_lettered_components():
    assert K.mentions("Components A and B, and Sub-component C.2") == ["A", "B", "C.2"]
    assert K.mentions("Component A1 will") == ["A.1"]
    assert K.mentions("Component 3, A Study of masts") == ["3"]
    assert K.mentions("Component Bank financing") == []


def test_a_datasheet_table_with_lettered_components_is_read():
    grid = [["Component Name", "Cost (US$, millions)"],
            ["Component A: Rules and Institutions", "5.0"],
            ["Component B: Shared Masts", "12.5"],
            ["Component C: Project Management", "2.0"],
            ["Total", "19.5"]]
    got = K.read_datasheet(grid)
    assert [(c["number"], c["name"], c["number_printed"]) for c in got] == [
        ("A", "Rules and Institutions", True), ("B", "Shared Masts", True),
        ("C", "Project Management", True)]


def test_name_keys_and_names_drop_a_lettered_prefix():
    from wbg_portfolio.keys import name_key
    assert name_key("Component A: Data Centre") == name_key("Data centre") == "datacentre"
    assert name_key("Subcomponent B.1 Data Centre") == "datacentre"
    assert K.clean_name("Component A: Data Centre") == "Data Centre"
    assert K.clean_name("Component ICT Services") == "Component ICT Services"


def test_a_plan_matches_a_lettered_component_by_name_or_letter():
    from wbg_portfolio import clean_procurement as P
    rows = [{"project_ids": "P1", "doc_id": "D1", "disclosure_date": "2020-01-01",
             "number": "A", "name": "Rules and Institutions for Rural Radio"},
            {"project_ids": "P1", "doc_id": "D1", "disclosure_date": "2020-01-01",
             "number": "B.1", "name": "Shared Weather Stations, Masts and Relays"}]
    c = P.Components(rows)
    assert c.in_cells("P1", "Component A: Something Else Entirely Post") == \
        ("A", "", "number_only", "")
    assert c.in_cells("P1", "Component B1 Something Else Entirely Post") == \
        ("B.1", "", "number_only", "")
    got = c.in_cells("P1", "A.1 Shared Weather Stations, Masts and Relays")
    assert got[:3] == ("B.1", "Shared Weather Stations, Masts and Relays", "name_match")
    desc, comp = c.strip_tail("P1", "Repair of rooftop gauges Component B.1 Shared Weather "
                                    "Stations, Masts and Relays")
    assert desc == "Repair of rooftop gauges" and comp[0] == "B.1"
    assert P.comp_number("A. Rules and Institutions").group(1) == "A"
    assert P.comp_number("A plan for masts") is None


# ---- lettered false positives (invented headings)

@pytest.mark.parametrize("heading", [
    "Component E-Government Services",
    "Component X Activities of the project",
    "Component V Strengthening",
    "Component S Support",
    "Component A Plan",
    "Component E: E-Government Services",
])
def test_a_lone_lettered_heading_in_a_numbered_document_is_no_component(heading):
    letters = K.document_letters([("Component 1: Rules", "heading"), (heading, "heading"),
                                  ("Component 2: Masts", "heading")])
    t = K.Tagger(letters=letters)
    assert t.see("Component 1: Rules", "heading", "II.B") == ("1", "")
    assert t.see(heading, "heading", "II.B") == ("1", "")            # still under component 1
    text = "Component 1: Rules\n" + heading + "\nComponent 2: Masts\n"
    assert [r["number"] for r in K.read_headings(_rows(text), text)] == ["1", "2"]


def test_a_letter_that_runs_into_a_hyphenated_word_is_no_component_even_in_a_lettered_document():
    units = [("Component A: Rules", "heading"), ("Component B: Masts", "heading"),
             ("Component E-Government Services", "heading")]
    t = K.Tagger(letters=K.document_letters(units))
    t.see(*units[0][:1], "heading", "II.B")
    assert t.see("Component E-Government Services", "heading", "II.B")[0] == "A"
    assert K.comp_match("Component E - Government Services").group(1) == "E"      # a spaced dash is a separator


def test_a_lettered_document_needs_a_and_b_and_an_unbroken_run():
    docs = {"A: One|B: Two|C: Three": {"A", "B", "C"}, "A: One": set(), "B: Two|C: Three": set(),
            "A: One|B: Two|D: Four": {"A", "B"}}
    for heads, expect in docs.items():
        units = [(f"Component {h}", "heading") for h in heads.split("|")]
        assert K.document_letters(units) == expect, heads
    assert K.document_letters([("Component I: One", "heading"), ("Component II: Two", "heading")]) >= {"I", "II"}


def test_a_lettered_document_still_tags_its_components_and_a_stray_letter_is_ignored():
    units = [("Component A: Rules", "heading"), ("Component B: Masts", "heading"),
             ("Component X Activities of the project", "heading")]
    t = K.Tagger(letters=K.document_letters(units))
    assert t.see(units[0][0], "heading", "II.B") == ("A", "")
    assert t.see(units[2][0], "heading", "II.B") == ("A", "")
    assert t.see(units[1][0], "heading", "II.B") == ("B", "")


def _plan_components(*numbers):
    from wbg_portfolio import clean_procurement as P
    return P.Components([{"project_ids": "P1", "doc_id": "D1", "disclosure_date": "2020-01-01",
                          "number": n, "name": f"Invented component number {n} name"} for n in numbers])


@pytest.mark.parametrize("cell", ["J. Smith Consulting Ltd", "A. Supply of laptops", "B) Works",
                                  "I: Strengthening", "V. Something", "X) something", "J. Smith", "A. Smith"])
def test_a_leading_initial_in_a_plan_cell_is_no_component_in_a_numbered_project(cell):
    c = _plan_components("1", "2")
    assert c.in_cells("P1", cell) is None
    assert c.strip_tail("P1", "Supply of laptops for the ministry " + cell) == \
        ("Supply of laptops for the ministry " + cell, None)


def test_a_leading_letter_is_a_component_when_the_project_has_that_lettered_component():
    c = _plan_components("A", "B")
    assert c.in_cells("P1", "A. Supply of laptops")[0] == "A"
    assert c.in_cells("P1", "B) Works")[0] == "B"
    assert c.in_cells("P1", "J. Smith Consulting Ltd") is None             # the project has no J
    assert c.in_cells("P1", "A. Smith") is None                              # initial and surname, nothing else
    assert c.in_cells("P1", "Component A: Supply") == ("A", "", "number_only", "")


def test_numbered_plan_cells_read_as_before():
    c = _plan_components("1", "2")
    assert c.in_cells("P1", "2. Supply of laptops")[0] == "2"
    assert c.in_cells("P1", "Component 1 Supply")[0] == "1"
    assert c.in_cells("P1", "2.1 Supply of laptops")[0] == "2.1"
