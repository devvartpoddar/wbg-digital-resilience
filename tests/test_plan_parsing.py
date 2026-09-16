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


import plan_table as PT  # noqa: E402


def _doc(*blocks):
    return "\n".join(blocks).split("\n")


HEADER = """GOODS
Activity Reference No. /
Description
Loan / Credit N o.
Component Review Type Method
Market Approac h
Estimated Am ount (US$)
Actual Amount
(US$)
Process St atus
Planned Actual Planned Actual"""


class TestFindingTheTable:
    """Everything outside the STEP tables is the borrower's own narrative and
    is not data. Locating the table exactly is what lets the rest be ignored."""

    def test_a_section_name_in_the_narrative_is_not_a_section(self):
        """'GOODS' and 'WORKS' occur alone on a line in preamble prose. The
        column heading on the next line is what tells a heading from a word;
        without that test one corpus document reports 3,822 sections."""
        lines = _doc("Some preamble about", "WORKS", "and their financing,",
                     "which continues for", "several more lines of prose.",
                     HEADER, "ZZ-AAA-1 / A thing", "IDA / 1234")
        found = PT.find_tables(lines)
        assert [t.section for t in found] == ["GOODS"]

    def test_the_heading_band_is_not_read_as_a_row(self):
        lines = _doc(HEADER, "ZZ-AAA-1 / A thing", "IDA / 1234",
                     "100.00 0.00 Signed 2021-01-01")
        table = PT.find_tables(lines)[0]
        assert not any(PT.ANCHOR_RE.search(l) for l in table.data)


class TestCollapsedRendition:
    """A rendition with no column positions at all. These were read as nothing
    whatever - the fixed-width parser splits a line at its first run of two
    spaces and a collapsed rendition has none - and they are the newest
    rendition of six of the eight projects, so they decide the current state."""

    def test_cells_packed_onto_one_line_are_read(self):
        lines = _doc(HEADER, "ZZ-AAA-1 / A thing", "IDA / 1234",
                     "Post Direct Selection Direct 10,000.00 0.00 Canceled 2019-08-04")
        row = PT.read_collapsed_table(PT.find_tables(lines)[0])[0]
        assert row["estimated_amount"] == "10,000.00"
        assert row["actual_amount"] == "0.00"
        assert row["status_raw"] == "Canceled"

    def test_cells_on_their_own_lines_are_read_the_same_way(self):
        """The same table in a different rendition. Read as lines these need
        two readers; read as a token stream they are one shape."""
        lines = _doc(HEADER, "ZZ-AAA-1 / A thing", "IDA / 1234", "", "Post", "",
                     "10,000.00", "", "0.00", "", "Signed", "", "2024-05-19")
        row = PT.read_collapsed_table(PT.find_tables(lines)[0])[0]
        assert row["estimated_amount"] == "10,000.00"
        assert row["status_raw"] == "Signed"

    def test_the_status_is_found_without_knowing_the_word(self):
        """Between the last amount and the first milestone date, in whatever
        language the plan is written in. No vocabulary is consulted here."""
        lines = _doc(HEADER, "ZZ-AAA-1 / Une chose", "IDA / 1234",
                     "50,000.00 45,492.48 Achevé 2019-11-20")
        row = PT.read_collapsed_table(PT.find_tables(lines)[0])[0]
        assert row["status_raw"] == "Achevé"

    def test_a_run_of_dates_is_never_read_as_a_status(self):
        lines = _doc(HEADER, "ZZ-AAA-1 / A thing", "IDA / 1234",
                     "50,000.00 0.00 2019-11-20 2020-01-15 2020-03-04")
        row = PT.read_collapsed_table(PT.find_tables(lines)[0])[0]
        assert row["status_raw"] == ""

    def test_a_single_amount_is_the_actual_when_no_estimated_column_is_printed(self):
        """19% of renditions are old enough that STEP printed only the actual.
        Read positionally without checking the heading, an actual of 0.00 is
        filed as an estimate of zero - which is worse than reading nothing."""
        lines = _doc(HEADER, "ZZ-AAA-1 / A thing", "IDA / 1234", "0.00 Signed")
        table = PT.find_tables(lines)[0]
        row = PT.read_collapsed_table(table, has_estimated=False)[0]
        assert row["actual_amount"] == "0.00" and row["estimated_amount"] == ""
        row = PT.read_collapsed_table(table, has_estimated=True)[0]
        assert row["estimated_amount"] == "0.00"


class TestRefusingScrambledRenditions:
    """Some renditions emit each column of a page as its own run, so the
    reference column and the money column are in different orders. Attaching
    one package's amount to another's reference is worse than reading neither,
    because the result looks like data."""

    def test_a_record_split_by_a_reprinted_heading_gives_up_its_figures(self):
        lines = _doc(HEADER, "ZZ-AAA-1 / A thing", "IDA / 1234",
                     "Process St atus", "Documents",
                     "50,000.00 0.00 Signed 2019-11-20")
        row = PT.read_collapsed_table(PT.find_tables(lines)[0])[0]
        assert row["interleaved"]
        assert row["estimated_amount"] == "" and row["status_raw"] == ""
        assert row["description"].startswith("ZZ-AAA-1")

    def test_a_table_where_most_records_lost_their_money_is_refused(self):
        rows = [{"estimated_amount": "1.00", "actual_amount": ""}] + \
               [{"estimated_amount": "", "actual_amount": ""} for _ in range(4)]
        assert not PT.collapsed_is_aligned(rows)

    def test_an_intact_table_is_not_refused(self):
        rows = [{"estimated_amount": "1.00", "actual_amount": ""} for _ in range(19)] + \
               [{"estimated_amount": "", "actual_amount": ""}]
        assert PT.collapsed_is_aligned(rows)
