"""Unit tests for reading the STEP tables out of a procurement plan rendition.

Two defects are under test here and they are independent, which is why they get
one file between them rather than one each: the rendition can separate a
borrower reference from the description it belongs to, and the closed value sets
the parser matches against were written in English while the plans are not.

Every fixture below is invented. Real plan text is permitted in exactly one
committed place (inputs/labels/samples/) and tests/ is not it. What these
reproduce is the SHAPE of the rendition - where it breaks a line, what it puts
in the gap - not any borrower's words.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import fetch_procurement as F  # noqa: E402
import clean_procurement as C  # noqa: E402


class TestOrphanedReference:
    """A reference complete on its own line, with its '/ description' arriving
    one to four lines later and another column's text in the gap.

    Before this was handled the reference was invisible and, worse, its text was
    swallowed into the PREVIOUS package's description, so one row went missing
    and another was corrupted. Both halves are asserted.
    """

    def test_the_reference_and_its_description_are_rejoined(self):
        out = F._join_wrapped_refs([
            "ZZ-AAA-111-CS-QCBS",
            "        4. Some other column's text",
            "/ A description here",
            "trailing line",
        ])
        assert "ZZ-AAA-111-CS-QCBS / A description here" in out

    def test_no_line_is_ever_dropped(self):
        """The join consumes the slash line and nothing else. An early version
        of this loop fell through a `break` without appending and silently ate
        the lines it had scanned past."""
        for block in (
            ["ZZ-AAA-111-CS-QCBS", "   other column", "/ A description here", "tail"],
            ["ZZ-AAA-111-CS-QCBS", "a", "b", "c", "d", "e"],
            ["ZZ-AAA-111-CS-QCBS", "no slash anywhere"],
        ):
            out = F._join_wrapped_refs(list(block))
            joined = "".join(out).replace(" ", "")
            for line in block:
                assert line.replace(" ", "").lstrip("/") == "" or \
                    line.replace(" ", "").lstrip("/") in joined

    def test_the_scan_will_not_cross_into_the_next_record(self):
        """A slash line belonging to a LATER reference must not be stolen by an
        earlier one. Stopping at the next reference is the only thing between
        this and silently attaching one package's description to another."""
        out = F._join_wrapped_refs([
            "ZZ-AAA-111-CS-QCBS",
            "ZZ-BBB-222-GO-RFQ",
            "/ Belongs to the second one",
        ])
        assert "ZZ-AAA-111-CS-QCBS / Belongs to the second one" not in out
        assert "ZZ-AAA-111-CS-QCBS" in out

    def test_a_reference_with_no_digits_is_not_a_reference(self):
        out = F._join_wrapped_refs(["SOME-ALL-CAPS-HEADING", "/ not a description"])
        assert out == ["SOME-ALL-CAPS-HEADING", "/ not a description"]


class TestClosedValueSets:
    """The Method, Market Approach and Process Status cells.

    These were matched against English-only token lists, so a francophone
    borrower's packages came back with no status and no method at all - Niger
    read 3% of rows populated and it was mistaken for a layout failure.
    """

    def test_an_accented_status_is_recognised(self):
        assert F._first_token("... 50,000.00  Achevé  2019-11-20", F.STATUS_TOKENS)

    def test_a_status_split_across_a_column_wrap_is_recognised(self):
        """The cell is wider than its column, so it prints as two pieces with
        the rest of the row's gap between them. 9,036 rows of the corpus."""
        assert F._first_token("0.00   Under Implementati      on   2021-05-05",
                              F.STATUS_TOKENS) == "Under Implementation"

    def test_the_longer_exact_match_wins_over_a_shorter_despaced_one(self):
        """Both passes run over the whole token list before the next begins. Per
        token instead, a despaced hit on 'Signed' would beat an exact hit on
        'Contract Signed' further down."""
        assert F._first_token("   Contract Signed   ", F.STATUS_TOKENS) == "Contract Signed"

    def test_a_french_method_is_recognised(self):
        assert F._first_token("  Demande de prix   Limited  ", F.METHOD_TOKENS) \
            == "Demande de prix"

    def test_nothing_is_returned_for_a_cell_that_says_nothing(self):
        assert F._first_token("   2021-05-05   0.00   ", F.STATUS_TOKENS) == ""


class TestNormalisationOfForeignValues:

    @pytest.mark.parametrize("raw,expected", [
        ("Achevé", "signed"),
        ("Annulé", "cancelled"),
        ("Signé", "signed"),
        ("En cours d’exécution", "under_execution"),
        ("En attente d'exécution", "planned"),
        ("Concluído", "signed"),
        ("Cancelado", "cancelled"),
    ])
    def test_a_foreign_status_reaches_the_closed_set(self, raw, expected):
        assert C.norm_status(raw) == expected

    @pytest.mark.parametrize("raw", ["Terminated", "Résilié"])
    def test_termination_is_unknown_rather_than_guessed(self, raw):
        """A1.24 has no value for a contract that was signed and then ended
        early, and neither `signed` nor `cancelled` is true of one. An unmapped
        value goes to `unknown` and is counted; `status_raw` keeps the word."""
        assert C.norm_status(raw) == "unknown"

    def test_a_french_method_reaches_the_closed_set(self):
        assert C.norm_method("Demande de prix") == "request_for_quotations"
        assert C.norm_method(
            "Sélection fondée sur les qualifications des consultants"
        ) == "consultant_qualification"

    def test_enum_folding_does_not_touch_the_stored_description(self):
        """enum_key exists so that folding accents for a map lookup never
        reaches the text that gets stored and read by a person."""
        french = "Fourniture et installation d'équipements"
        assert "é" in C.collapse(french)
        assert "é" not in C.enum_key(french)
