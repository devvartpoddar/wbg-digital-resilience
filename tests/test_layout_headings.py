"""Layout features that hid a heading in real documents, reproduced with invented text:
a bold component title ending in a full stop, section titles set in the body font, and a
run-in title whose first full stop sits behind 'Component 1'.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wbg_portfolio import pdf_layout as PL  # noqa: E402
from wbg_portfolio import components as K  # noqa: E402


def _ln(text, bold=False, x0=36.0, x1=300.0):
    return {"text": text, "bold": bold, "x0": x0, "x1": x1}


def test_a_bold_component_title_ending_in_a_full_stop_is_a_heading():
    assert PL._stopped_title(_ln("Component 5: Emergency Response Component, ERC (US$0).", True))
    # Not bold, no colon after the number, or a long sentence: body.
    assert not PL._stopped_title(_ln("Component 5: Emergency Response Component (US$0).", False))
    assert not PL._stopped_title(_ln("Component 1 will finance the roads.", True))
    assert not PL._stopped_title(_ln("Component 2: " + "word " * 40 + "end.", True))


def test_lettered_titles_follow_a_numeral_title_in_order_without_a_bold_font():
    state, width = {"letter": None}, 540
    # Before any numeral title, a lettered line is nothing.
    assert not PL._section_title(_ln("A. Project Objective"), state, width)
    assert PL._section_title(_ln("II. PROJECT DESCRIPTION"), state, width)
    assert PL._section_title(_ln("A. Project Objective"), state, width)
    assert not PL._section_title(_ln("C. Skipped Letter"), state, width)       # out of order
    assert not PL._section_title(_ln("B. Ends like a sentence."), state, width)
    assert PL._section_title(_ln("B. Theory of Change"), state, width)
    assert not PL._section_title(_ln("B. Theory of Change"), state, width)     # B is used
    # A numeral title resets the run.
    assert PL._section_title(_ln("III. PROJECT IMPLEMENTATION"), state, width)
    assert PL._section_title(_ln("A. Institutional Arrangements"), state, width)


BODY = "Helvetica"
FILLER = ("The project will support the reform of public agencies and the people who work in "
          "them over the coming years, and the financing is described in the table below.")


def _invented_pdf(path, rows):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    y = 740
    for text, font, gap in rows:
        y -= gap
        c.setFont(font, 11)
        c.drawString(36, y, text)
    c.save()


def _read(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return PL.extract(pdf, [])


def test_titles_set_in_the_body_font_still_open_sections(tmp_path):
    pytest.importorskip("reportlab")
    rows = [("I. STRATEGIC CONTEXT", BODY, 20), ("A. Country Context", BODY, 20),
            ("1. " + FILLER, BODY, 20), ("II. PROJECT DESCRIPTION", BODY, 40),
            ("A. Project Objective", BODY, 20), ("2. " + FILLER, BODY, 20),
            ("B. Components", BODY, 40), ("3. " + FILLER, BODY, 20)]
    _invented_pdf(tmp_path / "a.pdf", rows)
    text, spans, _ = _read(tmp_path / "a.pdf")
    units = [(text[a:b][:25], path, block) for a, b, path, _t, block, *_ in spans]
    assert ("II. PROJECT DESCRIPTION", "II", "heading") in units
    assert ("A. Project Objective", "II.A", "heading") in units
    assert ("B. Components", "II.B", "heading") in units
    para = [u for u in units if u[0].startswith("2. ")][0]
    assert para[1] == "II.A" and para[2] == "narrative"


def test_a_bold_component_title_with_a_full_stop_is_not_run_into_the_paragraph_above(tmp_path):
    pytest.importorskip("reportlab")
    rows = [("I. PROJECT DESCRIPTION", "Helvetica-Bold", 20),
            ("21. " + FILLER, BODY, 30),
            ("Component 5: Contingent Emergency Response Component, CERC (US$0).",
             "Helvetica-Bold", 16),
            ("22. Following an eligible crisis, the government may ask for funds to be moved.",
             BODY, 16)]
    _invented_pdf(tmp_path / "b.pdf", rows)
    text, spans, _ = _read(tmp_path / "b.pdf")
    units = [(text[a:b], block) for a, b, _p, _t, block, *_ in spans]
    assert ("Component 5: Contingent Emergency Response Component, CERC (US$0).",
            "heading") in units
    assert not any("agencies" in t and "Component 5" in t for t, _ in units)


def test_a_run_in_title_numbered_with_a_full_stop_keeps_its_name():
    """'21. Component 1. Public finance (US$3 million). ...': the paragraph's first full stop
    sits behind 'Component 1', so cutting there left an empty name."""
    t = K.Tagger()
    tail = (" The component will finance work on budgets, audits and registries across ministries,"
            " including training for staff, new reports and a review of the rules in each district.")
    assert t.see("21. Component 1. Expenditure management strengthening (US$3.0 million)." + tail,
                 "narrative", "II.B") == ("1", "")
    assert t.comp_name == "Expenditure management strengthening"
    assert t.see("22. Subcomponent 1.1. Budget system enhancement. This subcomponent will finance "
                 "the budget system, its training and its reports for ministries and districts, together "
                "with a review of the rules that each ministry follows when it plans its spending.",
                 "narrative", "II.B") == ("1", "1.1")
    assert t.sub_name == "Budget system enhancement"
