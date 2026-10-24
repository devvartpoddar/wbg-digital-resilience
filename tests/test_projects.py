"""The projects table (src/projects.py). Invented records shaped like the
projects interface's."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import dates  # noqa: E402
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
    assert (rec["commitment_v2"], rec["commitment_v3"]) == ("200", "50")   # both kept
    row = projects.project_row("P1", rec, [])
    assert (row["commitment_v2_usd_m"], row["commitment_v3_usd_m"]) == ("200", "50")
    only3 = F.merge({}, {"id": "P5", "project_name": "X", "boardapprovaldate": "2026-07-01T00:00:00Z"})
    row = projects.project_row("P5", only3, [])
    assert row["project_name"] == "X" and row["approval_fy"] == "2027"
    assert row["in_projects_interface"] == "true" and row["record_source"] == "v3"
    assert F.merge({}, {}) == {}


class _Resp:
    def __init__(self, status, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}
        self.text = content.decode("latin-1")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _serve(monkeypatch, answers):
    import api
    seen = []

    def fake(method, url, **kw):
        seen.append(url)
        return answers.pop(0)
    monkeypatch.setattr(api.requests, "request", fake)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    return seen


def test_a_redirect_to_http_is_upgraded_and_throttling_is_asked_again(monkeypatch):
    import fetch
    seen = _serve(monkeypatch, [_Resp(302, headers={"location": "http://d.example/x.pdf"}),
                                _Resp(403), _Resp(200, b"%PDF-1.7 ... %%EOF\n")])
    assert fetch.get("https://a.example/x").status_code == 200
    assert seen == ["https://a.example/x", "https://d.example/x.pdf", "https://d.example/x.pdf"]


def test_a_missing_document_is_not_asked_again(monkeypatch):
    import api
    import fetch
    seen = _serve(monkeypatch, [_Resp(404), _Resp(200)])
    with pytest.raises(api.Refused):
        fetch.get("https://a.example/gone")
    assert len(seen) == 1


def test_only_a_whole_pdf_counts_as_fetched(tmp_path, monkeypatch):
    import fetch
    assert fetch.complete_pdf(b"%PDF-1.4\n" + b"x" * 5000 + b"\n%%EOF\n")
    assert not fetch.complete_pdf(b"%PDF-1.4\n" + b"x" * 5000)          # cut short
    assert not fetch.complete_pdf(b"<html>%%EOF</html>")
    monkeypatch.setattr(fetch, "get", lambda url, timeout: _Resp(200, b"%PDF-1.4 truncated"))
    counts, notes = {"pdf_missing": 0, "pdf_cached": 0, "pdf_fetched": 0}, []
    doc = {"doc_id": "D1", "pdfurl": "https://a.example/D1.pdf"}
    assert fetch.fetch_pdf(doc, str(tmp_path), counts, notes, "P1", 0) == (None, "")
    assert counts["pdf_missing"] == 1 and "FAILED" in notes[0]
    assert os.listdir(tmp_path) == []
    whole = b"%PDF-1.4\nbody\n%%EOF\n"
    monkeypatch.setattr(fetch, "get", lambda url, timeout: _Resp(200, whole))
    blob, stamp = fetch.fetch_pdf(doc, str(tmp_path), counts, notes, "P1", 0)
    assert blob == whole and stamp.endswith("Z")
    assert os.listdir(tmp_path) == ["D1.pdf"]
    assert fetch.fetch_pdf(doc, str(tmp_path), counts, notes, "P1", 0) == (whole, "")


# ------------------------------------------------------------- dates.iso_date

def test_every_interface_shape_reads_to_iso():
    assert dates.iso_date("2023-03-31T00:00:00Z") == "2023-03-31"
    assert dates.iso_date("2023-03-31") == "2023-03-31"
    assert dates.iso_date("14-Sep-2026") == "2026-09-14"
    assert dates.iso_date("14 Sep 2026") == "2026-09-14"
    assert dates.iso_date("10/31/2028 12:00:00 AM", "mdy") == "2028-10-31"
    assert dates.iso_date("31/10/2028") == "2028-10-31"
    assert dates.iso_date("12-03-24", "dmy") == "2024-03-12"


def test_nothing_unreadable_or_ambiguous_passes_through():
    assert dates.iso_date("") == ""
    assert dates.iso_date(None) == ""
    assert dates.iso_date("9/14/2026") == "2026-09-14"      # only one reading fits
    assert dates.iso_date("03/06/2024") == ""                # two readings fit
    assert dates.iso_date("03/06/2024", "mdy") == "2024-03-06"
    assert dates.iso_date("2024-13-01") == ""                # not a date
    assert dates.iso_date("soon") == ""
