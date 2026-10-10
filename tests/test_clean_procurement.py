"""Unit tests for the procurement cleaning rules, then gates on the real tables.

The fixtures are invented. No document text is ever committed, so no
description below is taken from a World Bank plan. What they reproduce is the SHAPE of the real thing:
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
sys.path.insert(0, os.path.join(ROOT, "src"))
from paths import data_root  # noqa: E402
DATA = data_root()

import clean_procurement as P  # noqa: E402
import audit_procurement as AP  # noqa: E402
import fetch_procurement as FP  # noqa: E402


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
    rebid flag out - which linking a plan package to its award depends on."""
    marks = P.split_markers("FIBER OPTIC CABLE SUPPLY - LOT 2 (REBID)")
    assert marks["lot_or_phase"] == "Lot 2"
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
    assert marks["lot_or_phase"] == ""


def test_step4_reads_phase_and_the_french_lot_forms():
    assert P.split_markers("Fourniture de materiel - Lote 3")["lot_or_phase"] == "Lot 3"
    assert P.split_markers("Works - Phase II")["lot_or_phase"] == "Phase II"
    assert P.split_markers("Lot No. 4: Laptops")["lot_or_phase"] == "Lot 4"


def test_step4_takes_a_marker_out_only_at_the_edges():
    """Taken from the middle, the words left would not read."""
    marks = P.split_markers("Phase 1 and Phase 2 works")
    assert marks["description"] == "Phase 1 and Phase 2 works"
    assert marks["lot_or_phase"] == "Phase 1; Phase 2"
    assert P.split_markers("Supply of laptops (Lot 2)")["description"] == "Supply of laptops"


def test_step4_does_not_read_a_broken_word_as_a_phase():
    assert P.split_markers("Phase s of works")["lot_or_phase"] == ""
    assert P.split_markers("Travaux de rehabilitation (Re-bidding)")["is_rebid"] is True


def test_step3_removes_the_reference_that_is_embedded_in_the_description():
    marks = P.split_markers("AA-AGENCY-123456-CS-QCBS - Provision of consulting services")
    assert marks["ref_in_text"] == "AA-AGENCY-123456-CS-QCBS"
    assert marks["description"] == "Provision of consulting services"


def test_step3_leaves_a_leading_code_that_is_not_a_reference():
    marks = P.split_markers("LOT 3 Supply of cables")
    assert "Supply of cables" in marks["description"]


# ------------------------------------------------ step 5: borrower reference

@pytest.mark.parametrize("raw,want", [
    ("aa agency 555555 cw rfb", "AA-AGENCY-555555-CW-RFB"),
    ("AA/AGENCY/555555/CW/RFB", "AA-AGENCY-555555-CW-RFB"),
    ("AA_AGENCY_555555_CW_RFB", "AA-AGENCY-555555-CW-RFB"),
    ("AA-AGENCY-555555-CW-RFB", "AA-AGENCY-555555-CW-RFB"),
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

@pytest.mark.parametrize("text", ["TBD", "Goods", "N/A", "", "AA-AGENCY-123456-CS-QCBS",
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
    ("Signed", "Signed"), ("Canceled", "Canceled"), ("Cancelled", "Canceled"),
    ("Under Implementation", "Under Implementation"),
    ("Under Implement ation", "Under Implementation"),
    ("Pending Implementation", "Pending Implementation"), ("Pending", "Pending"),
    ("Pending Impleme", "Pending Implementation"), ("Completed", "Completed"),
    ("En attente d'", "Pending Implementation"), ("Planned", "Planned"),
    ("", "unknown"), ("Whatever", "unknown"),
])
def test_step9_normalises_status_and_counts_the_rest(raw, want):
    assert P.norm_status(raw) == want


@pytest.mark.parametrize("method,category,want", [
    ("Request for Bids", "goods", "RFB"),
    ("Request for Quotations", "", "RFQ"),
    ("Direct Selection", "goods", "DIR"),
    # STEP numbers direct selection of consultants differently.
    ("Direct Selection", "consultant_services", "CDS"),
    ("Quality And Cost-Based Selection", "", "QCBS"),
    ("Quality And Cost Based Selection", "", "QCBS"),
    ("Least Cost Selection", "", "LCS"),
    ("Consultant Qualification  Selection", "", "CQS"),
    # The Bank has no method called 'other': an unknown word stays unknown.
    ("Individual Consultant Selection", "", "INDV"),
    ("Something else", "", "unknown"),
    ("", "", "unknown"),
])
def test_step9_maps_the_method_to_the_bank_code(method, category, want):
    assert P.norm_method(method, category) == want


def test_step9_reads_the_method_out_of_a_step_reference_code():
    """STEP writes the method code into the reference, which covers rows whose
    method cell was not read."""
    assert P.norm_method("AA-AGENCY-555555-CW-RFB") == "RFB"
    assert P.norm_method("AA-AGENCY-555556-CS-INDV") == "INDV"
    assert P.norm_method("AA-AGENCY-123456-GO-RFQ2") == "RFQ"
    assert P.norm_method("AA-AGENCY-12345-CS-SFQC") == "QCBS"
    assert P.norm_method("XX-ABC-123-GO-RF") == "unknown"


def test_a_clipped_method_cell_is_read_from_the_other_cells():
    assert P.method_in_cells("Component 2: Rural Radio Request for Propo Open") == "RFP"
    assert P.method_in_cells("Post Individual Consult ant Selection Open") == "INDV"
    assert P.method_in_cells("Post Open - National") == "unknown"


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
    path = os.path.join(DATA, "procurement", f"{table}.csv")
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
# Measured on the 70-project corpus after the October 2026 consistency pass. Each gate
# sits just above the measured rate, so a regression fails and ordinary
# variation as plans are re-published does not.
#
# Checks marked "source" describe the published data, not the cleaning: a plan
# that prints a planned date after its revised date, an award whose supplier
# amount exceeds the contract, a notice published with no description. They are
# reported, and gated against getting worse, but cleaning cannot fix them.
MAX_RATE = {
    "private use area character": 0.0,           # 0.00%
    "unicode replacement character": 0.0,        # 0.00%
    "control or format character": 0.0,          # 0.00%
    "whitespace not collapsed": 0.0,             # 0.00%
    "description carries a second borrower reference": 0.15,  # 0.10% - one plan's slash-coded references
    "description ends mid-word": 0.1,            # 0.03%
    "word glued to the next inside the description": 0.1,    # 0.05% - names left whole
    "word broken by a space inside the description": 0.05,   # 0.00%
    "borrower reference absent": 0.0,            # 0.00%
    "borrower reference not normalised": 0.0,    # 0.00%
    "category unmapped": 0.0,                    # 0.00%
    "method unmapped": 1.2,                      # 0.81% (soft) - Bank codes, no 'other'
    "status unmapped": 9.0,                      # 8.09% (soft) - newest plan prints none, no contract; older plans' status not carried
    "status raw present but unmapped": 0.1,      # 0.02% (soft)
    "planned date after revised date": 0.5,      # 0.31% - source
    "lot or phase marker left at the edge of the description": 0.05,  # 0.02%
    "another cell of the plan row left in the description": 0.05,   # 0.01%
    "component not found": 25.0,                 # 22.87% (soft) - not every plan prints one
    "table header text leaked into a description": 0.0,       # 0.00%
    "supplier amount exceeds the contract amount": 1.0,       # 0.55% - source
    "notice with no description": 1.0,           # 0.64% - source
}


@pytest.mark.parametrize("name", sorted(MAX_RATE))
def test_audit_check_is_within_its_gate(name):
    tables = {t: _load(t) for t in ("packages", "notices", "awards")}
    if not AP.VOCAB:
        AP.load_vocab(DATA)
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
               if c[0] not in MAX_RATE and c[2] in ("defect", "source")]
    assert missing == [], f"defect checks without a gate: {missing}"


# ------------------------------------------------- the despaced matching copy

class TestMatchKey:
    """The defect these guard against is silent. A package whose entire purpose
    is data centre infrastructure does not match the term "data center", the
    package drops out of the asset class, and nothing anywhere reports an
    error. These are the tests that make that failure loud."""

    GLUED = ("Delivery and set-up of Storage Equipment,Servers and Network "
             "equipment for the DataCenter Infrastructure of the river districts "
             "- Phase 3")

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


# ------------------------------------------------- one reference, two projects

def _pkg_row(project, ref, version, date, description, status="planned"):
    return {"package_id": ref, "project_id": project, "borrower_ref": ref,
            "borrower_ref_norm": P.norm_ref(ref), "description": description,
            "description_clean": description, "description_match": P.match_key(description),
            "description_sha256": P.desc_sha256(description), "description_lang": "en",
            "lot": "", "phase": "", "is_rebid": "false", "is_placeholder": "false",
            "superseded_by": "", "clean_version": P.CLEAN_VERSION, "category": "goods",
            "method": "open_national", "status": status, "status_raw": status,
            "planned_date": "", "revised_date": "", "estimated_amount": "100.00",
            "currency": "USD", "actual_amount": "", "plan_version": version,
            "fetched_at": "t", "plan_disclosure_date": date,
            "_plan_disclosure_date": date}


def test_step8_keeps_two_projects_apart_when_they_share_a_reference():
    """The borrower reference is unique within a project, not across them.
    'CS-INDV' and 'GO-RFB' are printed by three of the 70 projects each; keyed on
    the reference alone the second project's package is folded into the first
    project's row and disappears, taking its plan history with it."""
    rows = [_pkg_row("P1", "CS-INDV", "d1", "2024-01-01", "Supply of cable"),
            _pkg_row("P2", "CS-INDV", "d2", "2024-01-01", "Fourniture de cable")]
    kept, superseded = P.dedupe_packages(rows, Counter())
    assert sorted(r["project_id"] for r in kept) == ["P1", "P2"]
    assert sorted(r["description"] for r in kept) == ["Fourniture de cable", "Supply of cable"]
    assert superseded == []


def test_step8_still_folds_two_versions_of_one_project_package():
    rows = [_pkg_row("P1", "CS-INDV", "d1", "2024-01-01", "Supply of cable",
                     status="planned"),
            _pkg_row("P1", "CS-INDV", "d2", "2024-06-01", "Supply of cable",
                     status="signed")]
    counters = Counter()
    kept, superseded = P.dedupe_packages(rows, counters)
    assert [r["plan_version"] for r in kept] == ["d2"]
    assert counters["package_versions_superseded"] == 1


# ------------------------------------------- the reference that wrapped in two

# A minimal but complete STEP table: the section heading, the column heading
# band that tells it from the same word in a sentence, and one row whose
# reference wrapped. Records are only read from inside such a table, so the
# heading band is not decoration here - without it there is no table and the
# rendition correctly parses to nothing.
WRAPPED = ("Project information\n"
           "WORKS\n"
           "Activity Reference No. /   Loan / Credit N   Market Approac   Estimated Am\n"
           "      Description               o.                  h            ount (US$)\n"
           "AA-AGENCY-123456-CW-\n"
           "RFB / Construction of the fibre duct\n"
           "   IBRD / 90000   Open - National   100,000.00\n")


def test_a_reference_wrapped_across_two_lines_becomes_one_record():
    merged = FP._join_wrapped_refs(WRAPPED.split("\n"))
    assert any(ln.startswith("AA-AGENCY-123456-CW-RFB / Construction") for ln in merged)
    assert "AA-AGENCY-123456-CW-" not in merged


def test_a_reference_clipped_mid_token_needs_no_separator():
    """The same clip, one iteration earlier: the column edge fell inside 'QCBS'.
    Merging must not invent a separator that the rendition did not print."""
    assert FP._join_wrapped_refs(["AA-AGENCY-220449-CS-QCB",
                                  "S / Upgrades to the establishment"]) == \
        ["AA-AGENCY-220449-CS-QCBS / Upgrades to the establishment"]


def test_a_line_with_the_shape_but_no_number_is_left_alone():
    """A section heading is the same shape as a clipped reference. Every borrower
    reference in this corpus carries a digit; headings do not."""
    lines = ["NON-CONSULTING-", "S / SERVICES"]
    assert FP._join_wrapped_refs(lines) == lines


def test_parse_plan_text_recovers_a_wrapped_reference_end_to_end():
    """Without the merge REF_RE matches nothing in this rendition, every line is
    preamble and the document parses to zero package rows - which no counter in
    the report used to distinguish from a plan with an empty package table."""
    meta = {"project_id": "P1", "doc_id": "d1", "disclosure_date": "2024-01-01",
            "content_sha256": "x", "fetched_at": "t"}
    lines = WRAPPED.split("\n")
    assert not any(FP.REF_RE.match(ln) for ln in lines), \
        "fixture is wrong - the unmerged text should have no record boundary"
    rows, _free = FP.parse_plan_text(WRAPPED, meta)
    assert len(rows) == 1
    assert rows[0]["borrower_ref"] == "AA-AGENCY-123456-CW-RFB"
    assert rows[0]["description"] == "Construction of the fibre duct"


# ------------------------------------------------- one row per package

def _version(pv, date, **fields):
    base = {"package_id": "PK-1", "project_id": "P1", "plan_version": pv,
            "_plan_disclosure_date": date, "plan_disclosure_date": date,
            "borrower_ref": "PK-1", "borrower_ref_norm": "pk1",
            "description": "Supply of fiber", "description_match": "supply of fiber",
            "status": "unknown", "status_raw": "", "estimated_amount": "",
            "currency": "", "amount_source": ""}
    base.update(fields)
    return base


class TestNewestVersion:
    """The newest plan version's row is kept whole: nothing is carried from an
    older one, neither status nor amount."""

    def _run(self, versions):
        kept, _sup = P.dedupe_packages(versions, Counter())
        return kept[0]

    def test_a_later_blank_amount_stays_blank(self):
        row = self._run([
            _version("v1", "2020-01-01", estimated_amount="2000000.00",
                     amount_source="plan", currency="USD"),
            _version("v2", "2021-01-01"),
        ])
        assert (row["estimated_amount"], row["amount_source"], row["currency"]) == \
            ("", "", "")

    def test_a_later_zero_stays_zero(self):
        row = self._run([
            _version("v1", "2020-01-01", estimated_amount="200000.00"),
            _version("v2", "2021-01-01", estimated_amount="0.00"),
        ])
        assert row["estimated_amount"] == "0.00"

    def test_status_is_never_carried_forward(self):
        row = self._run([
            _version("v1", "2020-01-01", status="Signed", status_raw="Signed"),
            _version("v2", "2021-01-01"),
        ])
        assert (row["status"], row["status_raw"]) == ("unknown", "")

    def test_identity_and_description_come_from_the_newest_version(self):
        row = self._run([
            _version("v1", "2020-01-01", description="Supply of fiber"),
            _version("v2", "2021-01-01", description="Supply of fiber, revised"),
        ])
        assert row["description"] == "Supply of fiber, revised"
        assert row["plan_version"] == "v2"

    def test_supersession_points_at_the_row_that_replaced_it(self):
        kept, sup = P.dedupe_packages(
            [_version("v1", "2020-01-01"), _version("v2", "2021-01-01")], Counter())
        assert len(sup) == 1
        assert sup[0]["superseded_by"] == "v2"

    def test_two_projects_sharing_a_reference_stay_separate(self):
        a = _version("v1", "2020-01-01"); a["project_id"] = "P1"
        b = _version("v1", "2020-01-01"); b["project_id"] = "P2"
        kept, _ = P.dedupe_packages([a, b], Counter())
        assert {r["project_id"] for r in kept} == {"P1", "P2"}


# --- glued words and status sources (invented descriptions) -------------------

import glue  # noqa: E402


def _vocab():
    words = ("supply of digital equipment for the centre design and build "
             "radio programs national data base database statistics auditeur "
             "parties drone de la mise en place une plateforme")
    return glue.build_vocab([words] * 5)


@pytest.mark.parametrize("glued,fixed", [
    ("Design and Build ofDigital Equipment", "Design and Build of Digital Equipment"),
    ("TV andRadio Programs", "TV and Radio Programs"),
    ("supply ofequipment", "supply of equipment"),
    ("misede place", "mise de place"),
    ("Equipment,Supply", "Equipment, Supply"),
    ("Centre(NDC) design", "Centre (NDC) design"),
])
def test_glued_words_are_split(glued, fixed):
    assert glue.repair(glued, _vocab())[0] == fixed


@pytest.mark.parametrize("word", ["database", "Auditeur", "parties", "drone",
                                  "UNOPS", "ZoneI", "Zanzibarr"])
def test_real_words_and_acronyms_are_left_whole(word):
    assert glue.repair(word, _vocab()) == (word, 0)


def _pkg(status, disclosed, ref="R-1"):
    return {"package_id": f"P1:{ref}", "project_id": "P1", "borrower_ref_norm": ref,
            "status": status, "_disclosed": disclosed}


def test_status_from_a_later_signed_award_beats_an_older_plan():
    rows = [_pkg("Pending Implementation", "2023-01-01")]
    P.resolve_status(rows, [{"package_id": "P1:R-1", "signed_date": "2024-05-01"}], Counter())
    assert (rows[0]["status"], rows[0]["status_source"], rows[0]["status_as_of"]) == \
        ("Signed", "award", "2024-05-01")


def test_an_award_does_not_turn_completed_back_into_signed():
    rows = [_pkg("Completed", "2023-01-01")]
    P.resolve_status(rows, [{"package_id": "P1:R-1", "signed_date": "2024-05-01"}], Counter())
    assert (rows[0]["status"], rows[0]["status_source"]) == ("Completed", "plan")


def test_an_older_plan_status_is_never_carried_to_the_newest_version():
    # v1 printed a status; v2, the newest, printed none.
    base = {"project_id": "P1", "borrower_ref_norm": "R-1", "borrower_ref": "R-1",
            "description": "x", "description_match": "x", "estimated_amount": "",
            "plan_version": ""}
    rows = [dict(base, plan_version="v1", status="Under Implementation",
                 _plan_disclosure_date="2022-03-01"),
            dict(base, plan_version="v2", status="unknown",
                 _plan_disclosure_date="2024-01-01")]
    kept, _ = P.dedupe_packages(rows, Counter())
    P.resolve_status(kept, [], Counter())
    assert (kept[0]["status"], kept[0]["status_source"]) == ("unknown", "none")


def test_no_evidence_stays_unknown_and_says_so():
    rows = [_pkg("unknown", "2024-01-01")]
    P.resolve_status(rows, [], Counter())
    assert (rows[0]["status"], rows[0]["status_source"]) == ("unknown", "none")


# ------------------------------------------- other cells, and the component

def test_a_loan_number_between_wrapped_lines_is_taken_out():
    desc, cells = P.split_cells("Study of solar mini-gr IDA / 12345 ids for clinics")
    assert desc == "Study of solar mini-gr ids for clinics"
    assert cells == ""


def test_the_cells_after_a_loan_number_are_cut_off():
    desc, cells = P.split_cells(
        "Revue annuelle IDA / X1234 1. Cadre favorable A posteriori "
        "Sélection fondée sur la qualité et le coût Open - National")
    assert desc == "Revue annuelle"
    assert cells.startswith("1. Cadre favorable")


def test_review_method_and_approach_are_cut_off():
    desc, cells = P.split_cells("Supply of laptops Post Request for Bids Open - National")
    assert desc == "Supply of laptops"
    desc, _ = P.split_cells("Post-disaster needs assessment")
    assert desc == "Post-disaster needs assessment"


COMPONENT_ROWS = [
    {"project_ids": "P1", "disclosure_date": "2020-01-01", "number": "1",
     "name": "Rules and Institutions for Rural Radio"},
    {"project_ids": "P1", "disclosure_date": "2020-01-01", "number": "2",
     "name": "Shared Weather Stations, Masts and Relays"},
    # A restructuring renumbered the second component: the latest number wins.
    {"project_ids": "P1", "doc_id": "D9", "disclosure_date": "2023-01-01", "number": "3",
     "name": "Shared Weather Stations, Masts and Relays"},
]


def test_a_component_is_found_by_the_stretch_of_its_name_the_cells_carry():
    c = P.Components(COMPONENT_ROWS)
    got = c.in_cells("P1", "Shared Weather Stati Quality And Cost- Open - "
                           "Internationa IDA / 12345 ons, Masts and Rela Post")
    # The number and the document it comes from, so the package joins
    # components.csv on (doc_id, number).
    assert got == ("3", "Shared Weather Stations, Masts and Relays", "name_match", "D9")


def test_a_numbered_cell_with_no_name_match_keeps_only_the_plans_number():
    c = P.Components(COMPONENT_ROWS)
    assert c.in_cells("P1", "Component 4: Something Else Entirely Post") == ("4", "", "number_only", "")
    assert c.in_cells("P1", "Post Request for Bids Open - National") is None


def test_a_component_name_at_the_end_of_a_description_is_cut_off():
    c = P.Components(COMPONENT_ROWS)
    desc, comp = c.strip_tail("P1", "Repair of rooftop gauges Shared Weather Stati")
    assert desc == "Repair of rooftop gauges"
    assert comp[0] == "3" and comp[2] == "name_match"


def test_a_package_printed_twice_in_one_plan_is_two_packages():
    rows = [_version("v1", "2020-01-01", estimated_amount="13.00", package_version_id="d:00001"),
            _version("v1", "2020-01-01", estimated_amount="13.06", package_version_id="d:00002"),
            _version("v2", "2021-01-01", estimated_amount="13.00", package_version_id="e:00001"),
            _version("v2", "2021-01-01", estimated_amount="13.06", package_version_id="e:00002")]
    kept, sup = P.dedupe_packages(rows, Counter())
    assert sorted(r["estimated_amount"] for r in kept) == ["13.00", "13.06"]
    first, second = sorted(r["package_id"] for r in kept)
    assert second == first + ":2"
    assert all(s["superseded_by"] == "v2" for s in sup)


def test_a_package_the_latest_plan_no_longer_lists_is_flagged():
    rows = [_version("v1", "2020-01-01", borrower_ref_norm="old"),
            _version("v2", "2021-01-01", borrower_ref_norm="kept")]
    kept, _ = P.dedupe_packages(rows, Counter())
    assert {r["borrower_ref_norm"]: r["in_latest_plan"] for r in kept} == \
        {"old": "false", "kept": "true"}


def test_a_row_printed_twice_identically_is_one_package():
    rows = [_version("v1", "2020-01-01", estimated_amount="25.80", package_version_id="d:00001"),
            _version("v1", "2020-01-01", estimated_amount="25.80", package_version_id="d:00002")]
    kept, sup = P.dedupe_packages(rows, Counter())
    assert len(kept) == 1 and not sup
