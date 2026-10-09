"""The text rules shared by the PDF reader and the cleaning stages
(src/text_rules.py). Invented text throughout."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import text_rules as T  # noqa: E402


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


def test_tabular_detection_reads_gutters_before_whitespace_collapses():
    laid_out = "Households reached       Number      0.00        1,200       2,400"
    assert T.looks_tabular(laid_out)
    assert not T.looks_tabular("The project will build masts in three districts.")


def test_annex_heading_is_told_from_a_wrapped_cross_reference():
    assert T.annex_match("ANNEX 2: Detailed Project Description")
    assert not T.annex_match("Annex 1). The masts will be raised above the flood line.")
