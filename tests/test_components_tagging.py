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
