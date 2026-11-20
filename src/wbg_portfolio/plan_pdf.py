#!/usr/bin/env python3
"""Read the tables of a procurement plan PDF: reference, component and estimated amount.

The text rendition the Bank publishes (plan_table.py, fetch_procurement.py) loses the
table's column structure; the PDF keeps it. pdfplumber's table extraction reads the plan's
own Component and Estimated Amount columns on most plans. This module only reads; what to do
with the cells (fill what the text reading left blank, never overwrite) is
clean_procurement.merge_plan_pdf. No model is called.

A table's header row is found by the text of its cells (variants in procurement_vocab.json,
"plan_pdf_headers"), whitespace and case ignored: the PDF breaks a heading mid-word at the
column edge ("Estimated Am / ount (US$)"). A table that continues on the next page repeats
no header, so the last header with the same number of columns is used.

Rows are cached in data/procurement/plan_pdf_rows.csv, keyed on each PDF's checksum, so a
PDF is read once however often the stage runs.
"""
import argparse
import glob
import os
import re
import sys
import unicodedata

from wbg_portfolio import common
from wbg_portfolio.paths import data_root, file_sha256, where
from wbg_portfolio.plan_table import VOCAB

HEADERS = VOCAB["plan_pdf_headers"]
SECTIONS = {re.sub(r"\W", "", s.lower()) for s in VOCAB["plan_sections"]}

ROW_COLS = ["doc_id", "pdf_sha256", "row_index", "page", "ref_printed", "ref_key",
            "component_cell", "amount", "has_amount_column"]

# A borrower reference followed by the slash that opens the description; read with every
# space removed, since a clipped cell breaks the reference anywhere ("GO-R / FQ"). Text the
# previous row's description left at the top of the cell sits before it; the first field is
# upper case, so the match starts at the reference's own first capital.
REF_IN_CELL = re.compile(r"([A-Z][A-Z0-9]*(?:[-–—]{1,2}[A-Za-z0-9.()]+){2,7})(?:/|$)")
AMOUNT = re.compile(r"^\d[\d,]*(?:\.\d+)?$")


def _flat(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text or "")).lower()


def collapse(text):
    return " ".join((text or "").split())


def ref_key(text):
    """The comparison key of a borrower reference: upper case, letters and digits only. The
    plan reader's normaliser (clean_procurement.norm_ref) differs from it only in where a
    hyphen falls, which a clipped PDF cell moves."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def ref_in_cell(cell):
    """The borrower reference a first-column cell opens with (whitespace removed), or ''."""
    m = REF_IN_CELL.search(re.sub(r"\s+", "", cell or ""))
    return m.group(1) if m else ""


def parse_amount(cell):
    """'21,828.00' as '21828.00' (the form the text reading stores), or '' when the cell is
    not a plain number."""
    s = re.sub(r"\s+", "", cell or "")
    if not AMOUNT.match(s):
        return ""
    try:
        return f"{float(s.replace(',', '')):.2f}"
    except ValueError:
        return ""


def header_map(row):
    """{'ref': i, 'component': j, 'amount': k} when the row is a table's heading row (its
    reference column is named), else None. A column the table lacks is not in the map."""
    cells = [_flat(c) for c in row]
    ref = next((i for i, c in enumerate(cells) if c and any(c.startswith(v) for v in HEADERS["reference"])), None)
    if ref is None:
        return None
    m = {"ref": ref}
    for key, field in (("component", "component"), ("amount", "amount")):
        i = next((i for i, c in enumerate(cells) if c and any(c.startswith(v) for v in HEADERS[field])), None)
        if i is not None:
            m[key] = i
    return m


def rows_from_tables(tables):
    """Rows of every table of a plan, in page order.

    tables: a list of (page, table), a table being a list of rows of cell strings (what
    pdfplumber's extract_tables returns). Returns (rows, unread): rows are dicts with
    ref_printed, ref_key, component_cell, amount, has_amount_column and page; unread counts
    the data rows whose reference could not be read (they count against the plan in the
    merge's guard)."""
    maps, rows, unread = {}, [], 0
    for page, table in tables:
        for row in table or ():
            row = list(row or ())
            if not row:
                continue
            hm = header_map(row)
            if hm:
                maps[len(row)] = hm
                continue
            hm = maps.get(len(row))
            first = row[hm["ref"]] if hm and hm["ref"] < len(row) else None
            if not hm or not (first or "").strip():
                continue                          # no heading seen yet, or a sub-heading line
            if _flat(first) in SECTIONS:
                continue                          # the section's name row
            ref = ref_in_cell(first)
            if not ref:
                unread += 1
                continue

            def cell(k):
                return collapse(row[hm[k]]) if k in hm and hm[k] < len(row) else ""
            rows.append({"ref_printed": ref, "ref_key": ref_key(ref), "page": page,
                         "component_cell": cell("component"),
                         "amount": parse_amount(cell("amount")),
                         "has_amount_column": "true" if "amount" in hm else "false"})
    return rows, unread


def read_pdf(path):
    """(rows, unread) of one plan PDF, with pdfplumber's default line detection."""
    import pdfplumber
    tables = []
    with pdfplumber.open(path) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            for t in page.extract_tables():
                tables.append((n, t))
    return rows_from_tables(tables)


def _cache_path(data):
    return where(data, "procurement", "plan_pdf_rows.csv")


def read_all(data, log=print):
    """Rows of every plan PDF in the data folder, read once per PDF checksum.

    Returns {doc_id: {"rows": [...], "unread": n, "sha": ...}} for every PDF on disk; the
    cache file holds them (one summary row per PDF, row_index -1, carries the unread count)
    and is rewritten only when a PDF is new or changed. A PDF that cannot be read is logged
    and left out."""
    cache = {}
    path = _cache_path(data)
    if os.path.exists(path):
        for r in common.read_csv(path):
            d = cache.setdefault(r["doc_id"], {"sha": r["pdf_sha256"], "rows": [], "unread": 0})
            if r["row_index"] == "-1":
                d["unread"] = int(r["ref_printed"] or 0)
            else:
                d["rows"].append(r)
    out, changed, failed = {}, False, []
    pdfs = sorted(glob.glob(where(data, "plan_pdf", "*.pdf")))
    for p in pdfs:
        doc = os.path.splitext(os.path.basename(p))[0]
        sha = file_sha256(p, remember=False)
        old = cache.get(doc)
        if old and old["sha"] == sha:
            out[doc] = old
            continue
        try:
            rows, unread = read_pdf(p)
        except Exception as exc:                  # noqa: BLE001 - a damaged PDF must not stop a run
            failed.append((doc, str(exc)[:100]))
            log(f"plan_pdf: {doc} could not be read: {str(exc)[:100]}")
            continue
        for i, r in enumerate(rows):
            r.update(doc_id=doc, pdf_sha256=sha, row_index=i)
        out[doc] = {"sha": sha, "rows": rows, "unread": unread}
        changed = True
    if changed or set(cache) - set(out) or not os.path.exists(path):
        flat = []
        for doc in sorted(out):
            d = out[doc]
            flat.append({"doc_id": doc, "pdf_sha256": d["sha"], "row_index": -1,
                         "ref_printed": d["unread"]})
            flat.extend(dict(r, doc_id=doc, pdf_sha256=d["sha"]) for r in d["rows"])
        common.write_csv(path, ROW_COLS, flat)
    return out


def main():
    ap = argparse.ArgumentParser(description="read every plan PDF's tables into the cache")
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    got = read_all(args.data)
    print(f"plan_pdf: {len(got)} PDFs, {sum(len(d['rows']) for d in got.values()):,} rows "
          f"(cache {_cache_path(args.data)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
