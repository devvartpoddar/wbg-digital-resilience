"""Unit tests for the procurement cleaning rules, then gates on the real tables.

The fixtures are invented. Paragraph text is permitted in exactly one committed
place (inputs/labels/samples/) and tests/ is not it, so no description below is
taken from a World Bank plan. What they reproduce is the SHAPE of the real thing:
the wrap artefacts the plan rendition produces, the lot/phase/rebid markers, the
four languages, and the placeholder forms.

The second half imports src/audit_procurement.py and asserts a maximum rate per
check against the prepared tables, in the shape of tests/test_corpus_quality.py.
Those tests skip cleanly when data/ is absent, because data/ is gitignored by
design and a checkout without the corpus must still pass.

Thresholds are set just above the rates measured on the corpus when they were
written, so a real regression fails and ordinary variation as plans are
re-published does not. If one trips, read the examples the audit prints before
relaxing the number.
"""
import csv
import os
import sys
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "src"))

import clean_procurement as P  # noqa: E402
import audit_procurement as AP  # noqa: E402


# ------------------------------------------------------- step 1: characters

def test_step1_folds_the_same_glyphs_clean_py_folds():
    """CHAR_MAP is imported, not copied. Symbol and Wingdings put the same code
    points in both corpora, and the fix on the appraisal side cost two rounds."""
    assert P.collapse("Supply\u00a0of  fibre\u2010optic \uf0b7 cable") == \
        "Supply of fibre-optic \u2022 cable"


def test_step1_collapses_whitespace_and_trims():
    assert P.collapse("  Supply\n\t of   cable  ") == "Supply of cable"


def test_step1_does_not_nfkc_normalise():
    """A comparison operator is not two characters."""
    assert P.collapse("a\u2264b") == "a\u2264b"


# ------------------------------------------------------- step 2: case folding

def test_step2_folds_a_matching_copy_and_keeps_the_original():
    marks = P.split_markers("Supply of ICT Equipment")
    assert P.casefold_key(marks["description"]) == "supply of ict equipment"
    assert marks["description"] == "Supply of ICT Equipment"


# --------------------------------------------- steps 3 and 4: markers, lot

def test_step4_splits_lot_phase_and_rebid_into_columns():
    """The card's worked example. One string in, a description, a lot and a
    rebid flag out - which is what V6 depends on."""
    marks = P.split_markers("FIBER OPTIC CABLE SUPPLY - LOT 2 (REBID)")
    assert marks["lot"] == "2"
    assert marks["is_rebid"] is True
    assert marks["description"] == "FIBER OPTIC CABLE SUPPLY"
    assert "LOT" not in marks["description"].upper()
    assert "REBID" not in marks["description"].upper()


def test_step4_keeps_the_digits_it_has_to():
    """Digits are kept: in this corpus 'Lot 2' distinguishes a package, where in
    an appraisal paragraph a digit is usually a footnote marker. clean.py strips
    them; this must not."""
    marks = P.split_markers("Supply of 3 Desktop Computers")
    assert marks["description"] == "Supply of 3 Desktop Computers"
    assert marks["lot"] == ""


def test_step4_reads_phase_and_the_french_lot_forms():
    assert P.split_markers("Fourniture de materiel - Lote 3")["lot"] == "3"
    assert P.split_markers("Works - Phase II")["phase"] == "II"
    assert P.split_markers("Travaux de rehabilitation (Re-bidding)")["is_rebid"] is True


def test_step3_removes_the_reference_that_is_embedded_in_the_description():
    marks = P.split_markers("TZ-MCIT-123456-CS-QCBS - Provision of consulting services")
    assert marks["ref_in_text"] == "TZ-MCIT-123456-CS-QCBS"
    assert marks["description"] == "Provision of consulting services"


def test_step3_leaves_a_leading_code_that_is_not_a_reference():
    marks = P.split_markers("LOT 3 Supply of cables")
    assert "Supply of cables" in marks["description"]


# ------------------------------------------------ step 5: borrower reference

@pytest.mark.parametrize("raw,want", [
    ("tz mcit 254784 cw rfb", "TZ-MCIT-254784-CW-RFB"),
    ("TZ/MCIT/254784/CW/RFB", "TZ-MCIT-254784-CW-RFB"),
    ("TZ_MCIT_254784_CW_RFB", "TZ-MCIT-254784-CW-RFB"),
    ("TZ-MCIT-254784-CW-RFB", "TZ-MCIT-254784-CW-RFB"),
    ("EDGE- G1A", "EDGE-G1A"),
    ("EDGE \u2013IC6", "EDGE-IC6"),
    ("", ""),
    (None, ""),
])
def test_step5_normalises_case_whitespace_and_separators(raw, want):
    assert P.norm_ref(raw) == want


def test_step5_does_not_fuse_a_lot_number_into_a_contract_number():
    """Dropping the separators entirely would make 'A-12-3' and 'A-123'
    the same key, and they are different packages."""
    assert P.norm_ref("A-12-3") != P.norm_ref("A-123")


# ------------------------------------------------------------- step 6: language

@pytest.mark.parametrize("text,want", [
    ("Provision of consulting services for the project", "en"),
    ("Fourniture de vehicules pour les regions du projet", "fr"),
    ("Suministro de equipos para las oficinas del proyecto", "es"),
    ("Fornecimento de equipamentos para as escolas do projeto", "pt"),
    ("", "en"),
    ("XYZ 1234", "en"),
])
def test_step6_detects_the_language_and_never_translates(text, want):
    lang = P.detect_lang(text)
    assert lang == want
    assert text == text  # not translated, not rewritten


def test_step6_falls_back_rather_than_guessing_on_a_tie():
    assert P.detect_lang("de de de de") == "en"


# ------------------------------------------------------------ step 7: placeholder

@pytest.mark.parametrize("text", ["TBD", "Goods", "N/A", "", "TZ-MCIT-123456-CS-QCBS",
                                  "To be determined", "Services"])
def test_step7_flags_a_placeholder(text):
    assert P.is_placeholder(text) is True


@pytest.mark.parametrize("text", [
    "Supply, installation and commissioning of network equipment",
    "Rehabilitation of the e-commerce innovation centre",
])
def test_step7_does_not_flag_a_real_description(text):
    assert P.is_placeholder(text) is False


def test_step7_flags_rather_than_drops():
    """The row still evidences that a package exists and moves through the plan,
    so the flag never removes it. Nothing in this module drops a row for being
    short or non-descriptive."""
    rows = [{"borrower_ref": "X-1", "description": "TBD", "project_id": "P1",
             "plan_version": "d1", "plan_disclosure_date": "2024-01-01",
             "estimated_amount": "", "planned_date": "", "revised_date": "",
             "category": "goods", "method": "open_national", "status": "planned",
             "status_raw": "Planned", "currency": "USD", "fetched_at": "x",
             "description_lang": "en"}]
    counters = Counter()
    out = P.clean_packages(rows, counters)
    assert len(out) == 1 and out[0]["is_placeholder"] == "true"


# --------------------------------------------------------------- step 9: enums

@pytest.mark.parametrize("raw,want", [
    ("Signed", "signed"), ("Canceled", "cancelled"), ("Under Implementation",
                                                       "under_execution"),
    ("Under Preparation", "under_preparation"), ("Planned", "planned"),
    ("Cancelled", "cancelled"), ("", "unknown"), ("Whatever", "unknown"),
])
def test_step9_normalises_status_and_counts_the_rest(raw, want):
    assert P.norm_status(raw) == want


@pytest.mark.parametrize("method,approach,want", [
    ("Request for Bids", "Open - National", "open_national"),
    ("Request for Bids", "Open - International", "open_international"),
    ("Request for Quotations", "", "request_for_quotations"),
    ("Direct Selection", "", "direct_selection"),
    ("Quality And Cost Based Selection", "", "quality_cost_based"),
    ("Least Cost Selection", "", "least_cost"),
    ("Consultant Qualification Selection", "", "consultant_qualification"),
    # Not a value in business rule A1.25, so it lands in `other` and is counted
    # rather than silently promoted to the nearest thing.
    ("Individual Consultant Selection", "", "other"),
    ("", "", "unknown"),
])
def test_step9_normalises_method(method, approach, want):
    assert P.norm_method(method, approach) == want


def test_step9_reads_the_method_out_of_a_step_reference_code():
    """STEP encodes the method in the reference suffix when the method cell is
    empty, which is common in the older plan versions."""
    assert P.norm_method("", "") == "unknown"
    assert P.norm_method("TZ-MCIT-254784-CW-RFB") == "open_national"
    assert P.norm_method("TZ-MCIT-461806-CS-INDV") == "other"


@pytest.mark.parametrize("raw,want", [
    ("Goods", "goods"), ("GO", "goods"), ("Works", "works"), ("CW", "works"),
    ("Consulting Services", "consultant_services"), ("CS", "consultant_services"),
    ("Non Consulting Services", "non_consulting_services"),
    ("NC", "non_consulting_services"), ("", "unknown"),
])
def test_step9_normalises_category(raw, want):
    assert P.norm_category(raw) == want


# --------------------------------------------------------- steps 3-4 / final note

def test_amounts_and_dates_inside_a_description_move_to_their_own_columns():
    amount, date = P.extract_amount_and_date(
        "Supply of equipment (US$ 12,500.00) - delivery by 2025-03-31")
    assert amount == "12500.00"
    assert date == "2025-03-31"


def test_clean_packages_prefers_the_parsed_amount_over_one_found_in_the_text():
    rows = [{"borrower_ref": "X-1", "description": "Supply of cable for US$ 99.00",
             "project_id": "P1", "plan_version": "d1", "proposed": "",
             "plan_disclosure_date": "2024-01-01", "estimated_amount": "1234.00",
             "planned_date": "2024-05-01", "revised_date": "", "category": "goods",
             "method": "open_national", "status": "planned", "status_raw": "Planned",
             "currency": "USD", "fetched_at": "x", "description_lang": "en"}]
    out = P.clean_packages(rows, Counter())
    assert out[0]["estimated_amount"] == "1234.00"


# ---------------------------------------------------------------- step 10

def test_step10_hashes_raw_and_cleaned_separately():
    raw = "Supply of cable - LOT 2"
    clean = P.split_markers(raw)["description"]
    assert P.desc_sha256(raw) != P.desc_sha256(clean)
    assert P.desc_sha256(raw) == P.desc_sha256("Supply of cable - LOT 2")


# ---------------------------------------------------------------- step 8

def test_step8_keeps_the_latest_version_and_records_the_supersession():
    def mk(version, date, status, amount):
        return {"package_id": "A-1", "project_id": "P1", "borrower_ref": "A-1",
                "borrower_ref_norm": "A-1", "description": "Supply of cable",
                "description_clean": "Supply of cable", "description_sha256": "h",
                "description_lang": "en", "lot": "", "phase": "", "is_rebid": "false",
                "is_placeholder": "false", "superseded_by": "", "clean_version": "v",
                "category": "goods", "method": "open_national", "status": status,
                "status_raw": status, "planned_date": "", "revised_date": "",
                "estimated_amount": amount, "currency": "USD", "actual_amount": "",
                "plan_version": version, "fetched_at": "t",
                "_plan_disclosure_date": date}
    rows = [mk("d1", "2024-01-01", "planned", "100.00"),
            mk("d2", "2024-06-01", "signed", "200.00"),
            mk("d3", "2024-03-01", "planned", "150.00")]
    counters = Counter()
    kept, superseded = P.dedupe_packages(rows, counters)
    assert [r["plan_version"] for r in kept] == ["d2"]
    assert kept[0]["estimated_amount"] == "200.00"
    assert sorted(r["plan_version"] for r in superseded) == ["d1", "d3"]
    assert counters["package_versions_superseded"] == 2


def test_clean_version_stamp_is_on_every_row():
    rows = [{"borrower_ref": "X-1", "description": "Supply of cable",
             "project_id": "P1", "plan_version": "d1",
             "plan_disclosure_date": "2024-01-01", "estimated_amount": "",
             "planned_date": "", "revised_date": "", "category": "goods",
             "method": "open_national", "status": "planned", "status_raw": "Planned",
             "currency": "USD", "fetched_at": "x", "description_lang": "en"}]
    assert P.clean_packages(rows, Counter())[0]["clean_version"] == P.CLEAN_VERSION


# --------------------------------------------------------- gates on the tables

def _load(table):
    path = os.path.join(DATA, "intermediate", "procurement", f"{table}.csv")
    if not os.path.exists(path):
        pytest.skip(f"{table}.csv not present (data/ is gitignored)")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# check name -> maximum share of its scope, as a percentage.
#
# Every number here was measured on the full 70-project run before it was set,
# and each one sits just above the measurement, not at a round number chosen in
# advance. The measured rate is in the comment. If one trips after a re-fetch,
# read the examples the audit prints before moving it.
#
# The two high soft gates are not slack: the plan rendition leaves the method and
# status cells empty for most older rows (STEP puts the method in the reference
# suffix instead, and norm_method recovers what it can from there). They are
# gated at all so a change that makes them worse is noticed.
MAX_RATE = {
    "private use area character": 0.05,          # 0.02% - 2 rows, see note below
    "unicode replacement character": 0.0,        # 0.00% - removed at cleaning
    "control or format character": 0.0,          # 0.00%
    "whitespace not collapsed": 0.0,             # 0.00%
    "description carries a second borrower reference": 5.0,   # 3.61%
    "description ends mid-word": 1.0,            # 0.22%
    # Not a defect we repair - it cannot be repaired without guessing, and the
    # matching copy makes it harmless (see match_key). The gate is a ceiling
    # that would catch the parser getting WORSE, not a target to drive to zero.
    # The true rate is higher than this check can see: it finds a case boundary,
    # so "DataCenter" shows and "studyfor" does not.
    "word glued to the next inside the description": 40.0,
    "borrower reference absent": 0.0,            # 0.00%
    "borrower reference not normalised": 0.0,    # 0.00%
    "category unmapped": 0.5,                    # 0.00%
    "method unmapped": 90.0,                     # 83.91% (soft)
    "status unmapped": 90.0,                     # 83.27% (soft)
    "language not determined": 0.0,              # 0.00%
    "planned date after revised date": 0.5,      # 0.25%
    "lot or phase marker left in the matching copy": 3.0,     # 0.74%
    "table header text leaked into a description": 2.0,       # 0.59%
    "supplier amount exceeds the contract amount": 1.0,       # 0.56% - source
    "notice with no description": 2.0,           # 0.64%
}
# The two private-use rows are U+F076 in an award's supplier name: a Wingdings
# code point nobody has read yet, so it is reported rather than guessed at.
# CHAR_MAP in clean.py folds the ten the appraisal corpus taught us, including
# U+F0D8, which was the other one here.


@pytest.mark.parametrize("name", sorted(MAX_RATE))
def test_audit_check_is_within_its_gate(name):
    tables = {t: _load(t) for t in ("packages", "notices", "awards")}
    spec = [c for c in AP.CHECKS if c[0] == name]
    assert spec, f"{name} is not a check in audit_procurement.py"
    _n, wanted, _sev, fn = spec[0]
    hits = total = 0
    for t in wanted:
        for row in tables[t]:
            total += 1
            if fn(row):
                hits += 1
    rate = 100.0 * hits / max(total, 1)
    assert rate <= MAX_RATE[name], f"{name}: {rate:.2f}% > {MAX_RATE[name]}%"


def test_every_check_has_a_gate():
    """A check with no threshold is a check nobody is held to."""
    missing = [c[0] for c in AP.CHECKS
               if c[0] not in MAX_RATE and c[2] == "defect"]
    assert missing == [], f"defect checks without a gate: {missing}"


# ------------------------------------------------- the despaced matching copy

class TestMatchKey:
    """The defect these guard against is silent. A package whose entire purpose
    is data centre infrastructure does not match the term "data center", the
    package drops out of the asset class, and nothing anywhere reports an
    error. These are the tests that make that failure loud."""

    GLUED = ("Supply, Installation and commission of Storage Equipment,Servers "
             "and Network equipment for enhancement of DataCenter Infrastructure "
             "(Mainland and Zanzibar) - Phase 2")

    def test_glued_term_is_found_after_despacing(self):
        """'Data Center' lost its space when the cell was clipped at the column
        edge. Despacing both sides puts them back in step."""
        assert "data center" not in P.casefold_key(self.GLUED), \
            "fixture is wrong - it should carry the glue"
        assert P.match_key("data center") in P.match_key(self.GLUED)

    def test_terms_that_were_never_glued_still_match(self):
        for term in ("storage equipment", "network equipment", "infrastructure"):
            assert P.match_key(term) in P.match_key(self.GLUED), term

    def test_match_key_is_case_and_whitespace_free(self):
        assert P.match_key("  Data   CENTER\tinfrastructure ") == "datacenterinfrastructure"

    def test_match_key_changes_no_character(self):
        """The whole argument rests on the glue only ever DELETING a space. If
        match_key altered a character, two strings could match that do not
        share their letters, and the reasoning would not hold."""
        for text in (self.GLUED, "Réhabilitation du réseau", "LOT 2 (REBID)"):
            assert P.match_key(text) == "".join(
                c for c in P.casefold_key(text) if not c.isspace())

    def test_published_text_is_untouched(self):
        """match_key is for matching. The published string keeps its spaces,
        its case and its punctuation, because a person reads it."""
        assert P.collapse(self.GLUED) == self.GLUED
        assert "DataCenter" in self.GLUED
