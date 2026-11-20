"""The plan PDF enrichment on invented tables: header variants, reference reading, the guard,
and that a value the text reading produced is never overwritten. The tables are what
pdfplumber's extract_tables returns (lists of cell strings); one test draws a small invented
PDF with reportlab when it is installed (a test-only dependency)."""
import csv
import os
import sys
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wbg_portfolio import clean_procurement as P  # noqa: E402
from wbg_portfolio import cli  # noqa: E402
from wbg_portfolio import fetch_procurement as FP  # noqa: E402
from wbg_portfolio import panel, plan_pdf  # noqa: E402
from wbg_portfolio.paths import LAYOUT  # noqa: E402

HEADER = ["Activity Reference No. /\nDescription", "Loan / Credit\nNo.", "Component", "Review Type",
          "Method", "Estimated Am\nount (US$)", "Actual Amount\n(US$)"]


def row(ref, comp="", amount="", desc="Supply of laptops"):
    return [f"{ref} / {desc}", "IDA / 1", comp, "Post", "RFB", amount, "0.00"]


def table(*rows):
    return [["GOODS"] + [""] * 6, HEADER, [""] * 7, *rows]


COMPONENTS = [
    {"project_ids": "P1", "disclosure_date": "2020-01-01", "number": "1",
     "name": "Rules and Institutions for Rural Radio"},
    {"project_ids": "P1", "disclosure_date": "2020-01-01", "number": "2",
     "name": "Shared Weather Stations, Masts and Relays"},
]


def package(ref, doc="D1", component_number="", component="", amount="", amount_source="", project="P1"):
    return {"project_id": project, "plan_version": doc, "borrower_ref": ref,
            "component_number": component_number, "component": component,
            "component_source": "name_match" if component else "", "component_doc_id": "",
            "estimated_amount": amount, "amount_source": amount_source}


# ------------------------------------------------------------------ reading

def test_the_header_is_found_by_its_words_whatever_the_column_edge_did_to_them():
    m = plan_pdf.header_map(HEADER)
    assert m == {"ref": 0, "component": 2, "amount": 5}
    assert plan_pdf.header_map(["Loan / Credit No.", "Component"]) is None


def test_header_variants_come_from_the_vocabulary_file_not_the_code():
    fr = ["Référence", "Composante", "Montant estimé (US$)"]
    assert plan_pdf.header_map(["Référence de l'activité", "Composante",
                                "Montant estimé (US$)"]) == {"ref": 0, "component": 1, "amount": 2}
    assert set(plan_pdf.HEADERS) == {"reference", "component", "amount"}


def test_a_reference_is_read_through_a_clipped_cell_and_bled_text():
    assert plan_pdf.ref_in_cell("MD-STATEUNI-410921-GO-R\nFQ / IT equipment and periph\nerals") == \
        "MD-STATEUNI-410921-GO-RFQ"
    # the previous row's last line arrives at the top of the cell
    assert plan_pdf.ref_in_cell("(NRIS). MW-PPPC-454297-NC-RFB / Supply, installation") == \
        "MW-PPPC-454297-NC-RFB"
    assert plan_pdf.ref_in_cell("-mp CR loe IF- inQB t C /d P Te -rE saM lvo") == ""


def test_the_reference_key_ignores_where_a_clipped_cell_put_its_hyphens():
    assert plan_pdf.ref_key("MD-STATEUNI-410921-GO-RFQ") == plan_pdf.ref_key(P.norm_ref("MD-STATEUNI-410921-GO-R FQ"))


def test_amounts_are_plain_numbers_in_the_form_the_text_reading_stores():
    assert plan_pdf.parse_amount("21,828.00") == "21828.00"
    assert plan_pdf.parse_amount(" 1,5\n00.50 ") == "1500.50"
    assert plan_pdf.parse_amount("TBD") == "" and plan_pdf.parse_amount("") == ""


def test_rows_of_a_table_that_continues_without_a_header_use_the_last_header_of_that_width():
    first = table(row("AA-X-100001-GO-RFB", "2 - Weather", "5,000.00"))
    cont = [row("AA-X-100002-GO-RFB", "Component 1", "7.50")]
    rows, unread = plan_pdf.rows_from_tables([(1, first), (2, cont)])
    assert [r["ref_key"] for r in rows] == ["AAX100001GORFB", "AAX100002GORFB"]
    assert rows[1]["amount"] == "7.50" and rows[1]["component_cell"] == "Component 1"
    assert unread == 0


def test_section_names_subheadings_and_unreadable_rows_are_told_apart():
    scrambled = ["-mp CR loe IF- inQB t C /d P Te", "IDA / 1", "Pornoitjeocritn", "Post", "RFB", "", ""]
    rows, unread = plan_pdf.rows_from_tables([(1, table(row("AA-X-100001-GO-RFB"), scrambled))])
    assert len(rows) == 1 and unread == 1


def test_a_table_before_any_header_is_not_read():
    rows, unread = plan_pdf.rows_from_tables([(1, [row("AA-X-100001-GO-RFB", "1", "5.00")])])
    assert rows == [] and unread == 0


# ------------------------------------------------------------------ merging

def pdf_of(*cells, unread=0):
    """{doc: {...}} from (ref, component cell, amount) triples, as plan_pdf.read_all returns."""
    rows = [{"ref_key": plan_pdf.ref_key(ref), "component_cell": comp, "amount": amount}
            for ref, comp, amount in cells]
    return {"D1": {"rows": rows, "unread": unread}}


def merge(packages, pdf, share=0.5):
    counters = Counter()
    P.merge_plan_pdf(packages, pdf, P.Components(COMPONENTS), share, counters)
    return counters


def test_a_missing_component_and_amount_are_filled_and_the_source_is_recorded():
    pk = [package("AA-X-100001-GO-RFB")]
    c = merge(pk, pdf_of(("AA-X-100001-GO-RFB", "2 – Shared Weather Stations, Masts and Relays", "5000.00")))
    r = pk[0]
    assert (r["component_number"], r["component"], r["component_source"]) == \
        ("2", "Shared Weather Stations, Masts and Relays", "plan_pdf")
    assert (r["estimated_amount"], r["amount_source"]) == ("5000.00", "plan_pdf")
    assert c["plan_pdf_plans_accepted"] == 1


def test_a_component_given_as_a_number_alone_is_kept_as_the_plans_number():
    pk = [package("AA-X-100001-GO-RFB")]
    merge(pk, pdf_of(("AA-X-100001-GO-RFB", "Component A: Something else", "")))
    assert (pk[0]["component_number"], pk[0]["component_source"]) == ("A", "plan_pdf")


def test_nothing_the_text_reading_produced_is_overwritten_and_a_disagreement_is_counted():
    pk = [package("AA-X-100001-GO-RFB", component_number="1", component="Rules and Institutions for Rural Radio",
                  amount="10.00", amount_source="plan"),
          package("AA-X-100002-GO-RFB", amount="3.00", amount_source="description")]
    c = merge(pk, pdf_of(("AA-X-100001-GO-RFB", "Component 2: Shared Weather Stations", "99.00"),
                         ("AA-X-100002-GO-RFB", "", "4.00")))
    a, b = pk
    assert (a["component_number"], a["component"], a["estimated_amount"], a["amount_source"]) == \
        ("1", "Rules and Institutions for Rural Radio", "10.00", "plan")
    assert (b["estimated_amount"], b["amount_source"]) == ("3.00", "description")
    assert a["_plan_pdf_disagrees"] == 1 and a["_plan_pdf_amount_differs"] == 1
    assert c["plan_pdf_plans_accepted"] == 1


def test_a_plan_whose_rows_do_not_match_its_packages_is_rejected():
    pk = [package(f"AA-X-10000{i}-GO-RFB") for i in range(1, 5)]
    # four packages; the PDF read one matching row and five other references or none
    pdf = pdf_of(("AA-X-100001-GO-RFB", "Component 1", "1.00"), ("ZZ-Q-500001-GO-RFB", "Component 1", "1.00"),
                 ("ZZ-Q-500002-GO-RFB", "Component 1", "1.00"), unread=3)
    c = merge(pk, pdf)
    assert c["plan_pdf_plans_rejected_by_guard"] == 1 and c["plan_pdf_plans_accepted"] == 0
    assert all(not r["component_number"] and not r["estimated_amount"] for r in pk)


def test_a_scrambled_plan_reads_no_reference_and_is_rejected():
    pk = [package("AA-X-100001-GO-RFB")]
    c = merge(pk, pdf_of(unread=30))
    assert c["plan_pdf_plans_rejected_by_guard"] == 1 and pk[0]["component_number"] == ""


def test_the_share_is_the_control_panels_and_a_lower_one_accepts_the_same_table():
    pk = [package("AA-X-100001-GO-RFB")]
    pdf = pdf_of(("AA-X-100001-GO-RFB", "Component 1", "1.00"), unread=3)      # 1 of 4
    assert merge([dict(p) for p in pk], pdf, 0.5)["plan_pdf_plans_accepted"] == 0
    assert merge([dict(p) for p in pk], pdf, 0.25)["plan_pdf_plans_accepted"] == 1


def test_a_reference_printed_twice_is_not_matched_on_a_guess():
    pk = [package("AA-X-100001-GO-RFB")]
    c = merge(pk, pdf_of(("AA-X-100001-GO-RFB", "Component 1", "1.00"), ("AA-X-100001-GO-RFB", "Component 2", "2.00")))
    assert pk[0]["component_number"] == "" and pk[0]["estimated_amount"] == ""
    assert c["plan_pdf_rows_ambiguous_reference"] == 1


def test_a_pdf_of_another_plan_document_does_not_touch_this_plans_packages():
    pk = [package("AA-X-100001-GO-RFB", doc="D2")]
    merge(pk, pdf_of(("AA-X-100001-GO-RFB", "Component 1", "1.00")))
    assert pk[0]["component_number"] == ""


# ------------------------------------------------------------------ reading a real (invented) PDF

def test_a_pdf_drawn_with_reportlab_is_read_end_to_end(tmp_path):
    rl = pytest.importorskip("reportlab")
    assert rl
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    data = [["Activity Reference No. / Description", "Component", "Estimated Amount (US$)"],
            ["AA-X-100001-GO-RFB / Supply of laptops", "2 - Weather stations", "5,000.00"],
            ["AA-X-100002-GO-RFB / Supply of chairs", "1 - Radio", "7.25"]]
    t = Table(data)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    path = str(tmp_path / "D1.pdf")
    SimpleDocTemplate(path, pagesize=landscape(A4)).build([t])
    rows, unread = plan_pdf.read_pdf(path)
    assert [(r["ref_key"], r["component_cell"], r["amount"]) for r in rows] == \
        [("AAX100001GORFB", "2 - Weather stations", "5000.00"), ("AAX100002GORFB", "1 - Radio", "7.25")]
    assert unread == 0
    # read_all keeps the rows beside the data and reads a PDF once
    pdf_dir = tmp_path / "raw" / "plan_pdf"
    pdf_dir.mkdir(parents=True)
    os.replace(path, pdf_dir / "D1.pdf")
    got = plan_pdf.read_all(str(tmp_path))
    assert len(got["D1"]["rows"]) == 2
    cache = tmp_path / "procurement" / "plan_pdf_rows.csv"
    before = cache.read_bytes()
    again = plan_pdf.read_all(str(tmp_path), log=lambda *_: None)
    assert cache.read_bytes() == before and len(again["D1"]["rows"]) == 2


def test_with_no_pdf_the_cache_file_is_still_written_so_the_stage_is_not_always_stale(tmp_path):
    assert plan_pdf.read_all(str(tmp_path)) == {}
    assert (tmp_path / "procurement" / "plan_pdf_rows.csv").exists()


# ------------------------------------------------------------------ wiring

def test_the_panel_line_is_written():
    with open(os.path.join(ROOT, "inputs", "config", "control_panel.csv"), newline="", encoding="utf-8") as fh:
        assert {r["setting"]: r["value"] for r in csv.DictReader(fh)}["plan_pdf.min_match_share"] == "0.5"
    assert panel.require("plan_pdf.min_match_share") == 0.5


def test_the_stage_reruns_when_a_pdf_arrives_and_its_outputs_include_the_cache():
    stage = next(s for s in cli.STAGES if s[0] == "clean_procurement")
    assert ("plan_pdf", "*.pdf") in stage[3] and "plan_pdf.py" in stage[2]
    assert ("procurement", "plan_pdf_rows.csv") in stage[4]
    assert LAYOUT["plan_pdf"] == "raw/plan_pdf"


class _Resp:
    def __init__(self, content):
        self.content = content


def test_a_plan_pdf_is_fetched_once_kept_and_a_bad_body_is_refused(tmp_path, monkeypatch):
    good = b"%PDF-1.4\n...\n%%EOF\n"
    calls = []
    monkeypatch.setattr(FP, "get", lambda url, timeout=0: calls.append(url) or _Resp(good))
    path = str(tmp_path / "raw" / "plan_pdf" / "D1.pdf")
    sha, new = FP._fetch_plan_pdf(path, "https://a.example/1.pdf", False, 0)
    assert new and sha and open(path, "rb").read() == good
    assert FP._fetch_plan_pdf(path, "https://a.example/1.pdf", False, 0) == ("", False)
    assert len(calls) == 1
    monkeypatch.setattr(FP, "get", lambda url, timeout=0: _Resp(b"<html>throttled</html>"))
    with pytest.raises(RuntimeError):
        FP._fetch_plan_pdf(str(tmp_path / "D2.pdf"), "https://a.example/2.pdf", False, 0)
    assert not (tmp_path / "D2.pdf").exists()


def test_a_plan_whose_pdf_fails_is_listed_and_skipped_not_fatal(tmp_path, monkeypatch):
    def fake(path, url, refresh, delay):
        if url.endswith("bad.pdf"):
            raise RuntimeError("HTTP 403")
        return "abc", True
    monkeypatch.setattr(FP, "_fetch_plan_pdf", fake)

    class Log:
        def __init__(self):
            self.rows = []

        def record(self, *a):
            self.rows.append(a)
    log = Log()
    tasks = [("P1", {"doc_id": "1", "pdfurl": "https://a/ok.pdf"}),
             ("P1", {"doc_id": "2", "pdfurl": "https://a/bad.pdf"}),
             ("P2", {"doc_id": "3", "pdfurl": ""})]
    out = FP.fetch_plan_pdfs(tasks, str(tmp_path), log, False, 0, 2)
    assert out["fetched"] == 1 and out["no_pdf"] == ["3"]
    assert [(p, d) for p, d, _ in out["failed"]] == [("P1", "2")]
    assert len(log.rows) == 1 and log.rows[0][0] == "plan_pdf"


# ---------------------------------------------------- only the plans packages reference

def test_only_the_plan_documents_packages_reference_are_downloaded(tmp_path, monkeypatch):
    """A listed plan rendition no package draws on is not downloaded: the corpus lists about
    thirteen thousand renditions against a few hundred plan documents the published table
    keeps, and only the latter can fill a component or an amount."""
    seen = []
    monkeypatch.setattr(FP, "_fetch_plan_pdf",
                        lambda path, url, refresh, delay: seen.append(url) or ("abc", True))

    class Log:
        def record(self, *a):
            pass
    tasks = [("P1", {"doc_id": "1", "pdfurl": "https://a/1.pdf"}),
             ("P1", {"doc_id": "2", "pdfurl": "https://a/2.pdf"}),
             ("P2", {"doc_id": "3", "pdfurl": "https://a/3.pdf"})]
    out = FP.fetch_plan_pdfs(tasks, str(tmp_path), Log(), False, 0, 2, referenced={"1", "3"})
    assert out["fetched"] == 2 and out["not_referenced"] == ["2"]
    assert sorted(seen) == ["https://a/1.pdf", "https://a/3.pdf"]
    # No referenced set: every plan with a PDF address, as before.
    out = FP.fetch_plan_pdfs(tasks, str(tmp_path), Log(), False, 0, 2)
    assert out["fetched"] == 3 and out["not_referenced"] == []


def test_the_referenced_plan_documents_are_the_published_tables_plan_versions(tmp_path):
    """referenced_plan_docs reads the cleaned packages table; the plan documents a package
    was not kept under are not in it. No table yet: nothing is referenced."""
    assert FP.referenced_plan_docs(str(tmp_path)) == set()
    proc = tmp_path / "procurement"
    proc.mkdir()
    (proc / "packages.csv").write_text(
        "package_id,plan_version\nP1:a,100\nP1:b,100\nP1:c,200\n",
        encoding="utf-8")
    assert FP.referenced_plan_docs(str(tmp_path)) == {"100", "200"}


def test_reading_is_restricted_to_the_referenced_plan_documents(tmp_path, monkeypatch):
    """read_all(only=...) reads the documents named and leaves the others to the cache: a PDF
    on disk that no package draws on is not read, and its rows are not dropped from the
    cache."""
    pdf_dir = tmp_path / "raw" / "plan_pdf"
    pdf_dir.mkdir(parents=True)
    for doc in ("D1", "D2"):
        (pdf_dir / f"{doc}.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    read = []

    def fake_read_pdf(p):
        read.append(os.path.basename(p))
        return ([{"ref_key": "AAX1", "component_cell": "", "amount": "",
                  "ref_printed": "AA-X-1", "page": 1, "has_amount_column": ""}], 0)

    monkeypatch.setattr(plan_pdf, "read_pdf", fake_read_pdf)
    got = plan_pdf.read_all(str(tmp_path), log=lambda *_: None, only={"D1"})
    assert read == ["D1.pdf"] and set(got) == {"D1"}
    # D2 is read when a later run asks for it; D1 comes from the cache, not the PDF again.
    got = plan_pdf.read_all(str(tmp_path), log=lambda *_: None, only={"D2"})
    assert read == ["D1.pdf", "D2.pdf"] and set(got) == {"D2"}


def test_the_cache_holds_each_plan_before_the_next_one_is_read(tmp_path, monkeypatch):
    """Reading a few hundred plans takes about an hour, so the cache is written as each plan is
    read, not once at the end: a run stopped part way through keeps the plans already read and
    the next run reads only the rest."""
    pdf_dir = tmp_path / "raw" / "plan_pdf"
    pdf_dir.mkdir(parents=True)
    for doc in ("D1", "D2"):
        (pdf_dir / f"{doc}.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    def fake_read_pdf(p):
        return ([{"ref_key": "AAX1", "component_cell": "Comp 1", "amount": "1.00",
                  "ref_printed": "AA-X-1", "page": 1, "has_amount_column": "true"}], 0)

    def sha_or_stop(path, remember=False):
        if path.endswith("D2.pdf"):
            raise KeyboardInterrupt("the run was stopped here")
        return "sha-of-" + os.path.basename(path)

    monkeypatch.setattr(plan_pdf, "read_pdf", fake_read_pdf)
    monkeypatch.setattr(plan_pdf, "file_sha256", sha_or_stop)
    with pytest.raises(KeyboardInterrupt):
        plan_pdf.read_all(str(tmp_path), log=lambda *_: None)

    with open(tmp_path / "procurement" / "plan_pdf_rows.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["doc_id"] for r in rows] == ["D1", "D1"]
    assert [r["row_index"] for r in rows] == ["-1", "0"]
