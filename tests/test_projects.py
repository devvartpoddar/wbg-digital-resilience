"""The projects table (src/projects.py). Invented records shaped like the
projects interface's."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import projects  # noqa: E402

RECORD = {
    "id": "P000001", "project_name": "Rural  Radio Project", "status": "Active",
    "boardapprovaldate": "2023-03-31T00:00:00Z", "approvalfy": "2023",
    "closingdate": "10/31/2028 12:00:00 AM", "regionname": "Eastern and Southern Africa",
    "countrycode": ["AA"], "countryshortname": "Aland", "lendinginstr": "Investment Project Financing",
    "curr_total_commitment": "150",
    "project_gp_info": ('<PROJECT_GP_MAPPING PROJECTID="P000001"><GP_PRACTICE_DETAILS>'
                        '<GP_PRACTICE_CODE>DDT</GP_PRACTICE_CODE><GP_PRACTICE_NAME>'
                        '<![CDATA[Digital Development]]'),
    "sector1": {"Name": "ICT Services", "Percent": 64},
    "sector2": {"Name": "ICT Infrastructure", "Percent": 36},
}
DOCS = [
    {"doc_id": "D1", "disclosure_date": "2023-03-01", "owner_unit": "Digital Dev - East (IDD04)"},
    {"doc_id": "D2", "disclosure_date": "2025-06-01", "owner_unit": ""},
    {"doc_id": "D3", "disclosure_date": "2024-01-01", "owner_unit": "N/A"},
]


def test_a_record_becomes_one_row():
    row = projects.project_row("P000001", RECORD, DOCS)
    assert row["project_name"] == "Rural Radio Project"
    assert (row["approval_date"], row["closing_date"]) == ("2023-03-31", "2028-10-31")
    assert (row["practice"], row["practice_code"]) == ("Digital Development", "DDT")
    assert row["sectors"] == "ICT Services (64%); ICT Infrastructure (36%)"
    assert row["in_projects_interface"] == "true"


def test_the_unit_is_the_latest_document_that_records_one():
    row = projects.project_row("P000001", RECORD, DOCS)
    # D2 is newer but records no owner; D3's "N/A" is not a unit.
    assert (row["managing_unit"], row["unit_code"], row["unit_doc_id"], row["unit_as_of"]) == \
        ("Digital Dev - East (IDD04)", "IDD04", "D1", "2023-03-01")


@pytest.mark.parametrize("owner,codes", [
    ("Digital Dev - East (IDD04)", "IDD04"),
    ("IDD02 - Digital Dev - West", "IDD02"),
    ("IDD02", "IDD02"),
    ("Digital Dev - South (IDD06); Infrastructure VPU (GGIVP)", "IDD06|GGIVP"),
    ("EAP", ""),
    ("Office of the Regional Vice President", ""),
])
def test_unit_codes_are_read_as_printed(owner, codes):
    assert projects.unit_codes(owner) == codes


def test_a_project_the_interface_does_not_know_keeps_a_row():
    row = projects.project_row("P000009", {}, [])
    assert row["in_projects_interface"] == "false"
    assert row["project_name"] == "" and row["managing_unit"] == ""


def test_a_record_holding_only_its_id_is_no_record():
    row = projects.project_row("P000009", {"id": "P000009"}, [])
    assert row["in_projects_interface"] == "false"


def test_the_v3_record_is_preferred_and_fills_what_v2_lacks():
    import fetch_projects as F
    v2 = {"id": "P1", "closingdate": "5/30/2026 12:00:00 AM", "curr_total_commitment": "200",
          "approvalfy": "2021", "lendinginstr": "Investment Project Financing"}
    v3 = {"id": "P1", "closingdate": "2027-08-31", "curr_total_commitment": "50000000",
          "status": "Active"}
    rec = F.merge(v2, v3)
    assert rec["closingdate"] == "2027-08-31" and rec["curr_total_commitment"] == "50"
    assert rec["lendinginstr"] == "Investment Project Financing" and rec["record_source"] == "v2+v3"
    only3 = F.merge({}, {"id": "P5", "project_name": "X", "boardapprovaldate": "2026-07-01T00:00:00Z"})
    row = projects.project_row("P5", only3, [])
    assert row["project_name"] == "X" and row["approval_fy"] == "2027"
    assert row["in_projects_interface"] == "true" and row["record_source"] == "v3"
    assert F.merge({}, {}) == {}
