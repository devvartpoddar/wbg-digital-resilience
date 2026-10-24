#!/usr/bin/env python3
"""Fetch the cohort's procurement plans, notices and awards.

Reads  inputs/config/cohort.csv
Writes data/raw/{plans,notices,awards}/ (stored responses), data/raw/fetch_log.csv,
       data/procurement/{packages,notices,awards}_raw.csv,
       data/reports/fetch_procurement.txt

Every plan version is kept and content-hashed; a stored file is not fetched again
and --refresh keeps an earlier, different copy beside the new one. Plan renditions
are fixed-width text whose cells clip at the column edge; parse_plan_text reads
the STEP tables in them. Everything derived (codes, labels, language) is computed
in clean_procurement.
"""
import argparse, json, os, re, sys, time, unicodedata, urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where, write_fetch_status  # noqa: E402

import plan_table                                     # noqa: E402
from fetch import get, now_stamp, read_cohort, write_atomic  # noqa: E402
from common import read_csv, sha_text, write_csv  # noqa: E402
from dates import iso_date                    # noqa: E402
from clean_procurement import (                        # noqa: E402
    STATUS_MAP, METHOD_TEXT, fold_chars,
)

WDS = "https://search.worldbank.org/api/v3/wds"
NOTICES = "https://search.worldbank.org/api/v2/procnotices"
AWARDS = "https://search.worldbank.org/api/contractdata"
PLAN_DOCTY = "Procurement Plan"
PAGE = 500          # records per page from the notices and awards interfaces
PLAN_PAGE = 200     # documents per page from the documents interface


def last_page(batch, records, response, offset, cap=20000, page=PAGE):
    """Whether a paged listing is done: its own total is reached when it gives
    one, else a page came back empty or short."""
    total = _total(response)
    if total is not None:
        return len(records) >= total or not batch or offset > cap
    return len(batch) < page or offset > cap


def _total(response):
    try:
        return int(response.get("total"))
    except (TypeError, ValueError):
        return None


def fetch_pages(url, params, items, *, what, expected=(), page=PAGE, delay=0.3, timeout=90,
                log=None, source=""):
    """(records, total) of a paged listing. `params` is the query without
    paging, `items(json)` the page's records; every page is checked for HTML,
    for JSON and for the `expected` keys, and recorded in `log`. Raises
    RuntimeError when fewer records came back than the interface's own total,
    so a truncated list is never written."""
    records, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"format": "json", "rows": str(page), "os": str(offset),
                                    **params})
        r = get(f"{url}?{q}", timeout=timeout)
        j = json_body(r, what)
        batch = items(j)
        check_keys(batch, expected, what)
        if log is not None:
            log.record(source, url, q, len(batch), sha_text(r.content))
        records.extend(batch)
        offset += page
        if last_page(batch, records, j, offset, page=page):
            break
        time.sleep(delay)
    total = _total(j)
    if total is not None and len(records) < total:
        raise RuntimeError(f"{what}: {len(records)} of {total} records returned; "
                           f"a truncated list is not written")
    return records, total


def _documents(j):
    """The records of a documents-interface page (its 'facets' entry is not one)."""
    return [v for k, v in (j.get("documents") or {}).items() if k != "facets"]


# A date and a figure as a plan prints them in its milestone and amount cells.
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")
# The reference grammar is plan_table's (REF_TOKEN), so where a table's rows begin and where
# a record begins are one rule.
_SEP, _FIELD = plan_table.REF_SEP, plan_table.REF_FIELD
REF_TOKEN = plan_table.REF_TOKEN
REF_RE = re.compile(rf"^\s*({REF_TOKEN})\s*/\s*(.*)$")
# Does a rendition's text look like it carries borrower references at all?
PLAN_REF_SHAPE = re.compile(r"\b[A-Z]{2,}[-\u2013][A-Z0-9]{1,12}[-\u2013][0-9]{3,}")
LENDER_RE = re.compile(r"\s*" + plan_table.LOAN_INLINE.pattern + r"\s*$")
# Column headings the rendition repeats down every page.
HEADER_LINE_RE = re.compile(
    r"^\s*(Activity Reference No\.|Project information|"
    r"Project Implementation agency|Loan / Credit No|Description\b.*Component|"
    r"General Information|Country:|Project ID|Project Name|Executing Agency|"
    r"Date of the Procurement Plan|Period covered|Revised Plan Date|"
    r"PROCUREMENT|PLAN\s*$|"
    # The 'Planned  Actual' band under the milestone headings, reprinted on a page turn:
    # read as a cell it was taken for the status 'Planned'.
    r"(?:Planned|Actual)(?:\s+(?:Planned|Actual))*\s*$)")
# The closed value sets STEP prints in the Method, Market Approach and Process Status cells.
METHOD_TOKENS = tuple(sorted(METHOD_TEXT, key=lambda k: (-len(k), k)))
STATUS_TOKENS = tuple(sorted(STATUS_MAP, key=lambda k: (-len(k), k)))
APPROACH_TOKENS = ("Open - International", "Open - National", "Limited - International",
                   "Limited - National", "Direct - International", "Direct - National",
                   "Open - international", "Open - national", "Open / National",
                   "Limited", "Direct", "Open")
RAW_PACKAGE_COLS = [
    "package_version_id", "project_id", "plan_version", "plan_doc_id",
    "plan_disclosure_date", "borrower_ref", "description", "category_raw",
    "method_raw", "market_approach", "status_raw", "planned_date", "revised_date",
    "estimated_amount", "actual_amount", "currency", "content_sha256", "fetched_at",
    "section", "record_index", "cells_raw",
]
RAW_NOTICE_COLS = [
    "notice_id", "project_id", "notice_type", "notice_status", "publication_date",
    "deadline_date", "borrower_ref", "description", "category_raw", "method_code",
    "method_raw", "country_name", "notice_lang",
]
RAW_AWARD_COLS = [
    "contract_id", "project_id", "borrower_ref", "description", "signed_date",
    "no_objection_date", "total_amount", "currency", "category_raw", "method_raw",
    "review_type", "supplier_name", "supplier_country", "supplier_amount", "region",
    "sector",
]
FETCH_LOG_COLS = ["fetch_id", "source", "endpoint", "query_params", "fetched_at",
                  "record_count", "response_sha256"]

# ------------------------------------------------------------------ utilities

class FetchLog:
    """Append every interface call, keyed so a re-run updates rather than repeats."""

    def __init__(self, path):
        self.path = path
        self.rows = {r["fetch_id"]: r for r in read_csv(path)}

    def record(self, source, endpoint, params, record_count, response_sha256):
        fid = sha_text("|".join([source, endpoint, params]).encode())[:16]
        self.rows[fid] = {
            "fetch_id": fid, "source": source, "endpoint": endpoint,
            "query_params": params, "fetched_at": now_stamp(),
            "record_count": record_count, "response_sha256": response_sha256,
        }

    def write(self):
        write_csv(self.path, FETCH_LOG_COLS, [self.rows[k] for k in sorted(self.rows)])


# --------------------------------------------------------------- plan listing

def list_plans(project_id, log, delay):
    recs, _ = fetch_pages(WDS, {"projectid": project_id, "docty": PLAN_DOCTY,
                                "fl": "id,docty,display_title,disclosure_date,txturl,pdfurl,lang,projectid"},
                          _documents, what=f"plan listing {project_id}", expected=PLAN_LIST_KEYS,
                          page=PLAN_PAGE, delay=delay, timeout=60, log=log, source="documents")
    docs = []
    for val in recs:
        did = str(val.get("id") or "").strip()
        if not did:
            continue
        # The documents interface returns the identifier with a D prefix on some routes and
        # without it on others.
        did = did[1:] if did.startswith("D") and did[1:].isdigit() else did
        docs.append({
            "doc_id": did,
            "title": (val.get("display_title") or "").replace("\xa0", " ").strip(),
            "disclosure_date": (val.get("disclosure_date") or "")[:10],
            "lang": val.get("lang") or "",
            "txturl": val.get("txturl") or "",
        })
    time.sleep(delay)
    return docs


# ---------------------------------------------------------------- plan parsing

def _join_wrapped(parts):
    """Stitch a column's line fragments back into one string."""
    out, seams = "", 0
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and out[-1].isalnum() and p[0].isalnum():
            # Two alphanumerics run together with nothing between them, which is the only
            # seam where a space could have been lost.
            seams += 1
        out += p
    return out, seams


# Some renditions clip the column edge INSIDE the reference itself, so the last segment of
# the reference sits at the start of the next line and NO line carries 'reference /'.
REF_WRAP_HEAD = re.compile(
    rf"^\s*({REF_TOKEN}(?:[-\u2013\u2014]\(?[0-9.]*)?)(\s{{2,}}.*)?$")
REF_WRAP_TAIL = re.compile(r"^\s*([A-Z0-9(][A-Z0-9().\u2013\u2014-]{0,20})\s*/\s*(.*)$")

# A reference complete on its own line whose '/ description' arrives one to four lines
# later, other columns' text in between (wide layouts interleave columns).
REF_ALONE = re.compile(
    rf"^\s*([A-Z][A-Z0-9]{{1,}}(?:{_SEP}{_FIELD}){{2,7}})(\s{{2,}}\S.*)?\s*$")
SLASH_TAIL = re.compile(r"^\s*(/\s*\S.*)$")
MAX_ORPHAN_GAP = 4      # lines a wrapped reference's tail may sit below its head
MAX_GAPPED_TAIL = 6     # characters of a tail not on the adjacent line ('V', 'DV', 'RFB')


def _join_wrapped_refs(lines):
    """Put a reference that wrapped across two physical lines back on one."""
    out, i = [], 0
    while i < len(lines):
        head = REF_WRAP_HEAD.match(lines[i])
        if head and re.search(r"\d", head.group(1)):
            joined = False
            for d in range(1, MAX_ORPHAN_GAP + 1):
                j = i + d
                if j >= len(lines):
                    break
                # A head ending on a dash or bracket was clipped: a record-shaped next line
                # ('GO-RFB / Procurement of') is its tail.
                clipped = re.search(r"[-\u2013\u2014(.]$", head.group(1))
                early = REF_WRAP_TAIL.match(lines[j]) if clipped and d == 1 else None
                if early and not re.search(r"\d{3,}", early.group(1)):
                    candidate = f"{head.group(1)}{early.group(1)} / {early.group(2)}"
                    candidate += head.group(2) or ""
                    if REF_RE.match(candidate):
                        out.append(candidate)
                        i = j + 1
                        joined = True
                        break
                if REF_RE.match(lines[j]) or REF_ALONE.match(lines[j]):
                    break               # the next record started; do not cross it
                if SLASH_TAIL.match(lines[j]):
                    # A line opening with the slash is the orphan shape below: the head
                    # was complete, not clipped.
                    break
                # A Loan / Credit cell ('IDA / X1111') has a tail's shape; gluing it on
                # would fabricate a reference. It sits between head and tail, so skip it.
                if plan_table.LOAN_RE.match(lines[j]):
                    continue
                tail = REF_WRAP_TAIL.match(lines[j])
                if not tail:
                    continue
                # Beyond the adjacent line a tail is a clipped fragment, never a phrase.
                if d > 1 and len(tail.group(1)) > MAX_GAPPED_TAIL:
                    continue
                # Head + tail token, then the tail's description and the head's columns.
                candidate = f"{head.group(1)}{tail.group(1)} / {tail.group(2)}"
                candidate += head.group(2) or ""
                if REF_RE.match(candidate):
                    out.append(candidate)
                    # The lines in between are this record's other cells; kept in place.
                    out.extend(lines[i + 1:j])
                    i = j + 1
                    joined = True
                    break
            if joined:
                continue

        # The orphan shape: a complete reference alone, its slash a line or two later.
        alone = REF_ALONE.match(lines[i])
        if alone and re.search(r"\d", alone.group(1)):
            joined = False
            for d in range(1, MAX_ORPHAN_GAP + 1):
                j = i + d
                if j >= len(lines):
                    break
                if REF_ALONE.match(lines[j]) or REF_RE.match(lines[j]):
                    break               # the next record started; do not cross it
                tail = SLASH_TAIL.match(lines[j])
                if not tail:
                    continue
                candidate = f"{alone.group(1)} {tail.group(1)}"
                if REF_RE.match(candidate):
                    # The reference line's other columns are carried onto the joined line,
                    # the lines between kept in place; only the slash line is consumed.
                    out.append(candidate + (alone.group(2) or ""))
                    out.extend(lines[i + 1:j])
                    i = j + 1
                    joined = True
                    break
            if joined:
                continue

        out.append(lines[i])
        i += 1
    return out


def segment_for_project(text, project_id):
    """Cut a bundled rendition down to this project's own plan."""
    lines = text.split("\n")
    preambles = [i for i, ln in enumerate(lines) if ln.startswith("Project information")]
    if len(preambles) < 2:
        return text, "single"
    bounds = preambles + [len(lines)]
    for a, b in zip(bounds, bounds[1:]):
        head = "\n".join(lines[a:min(a + 6, b)])
        if project_id in head:
            return "\n".join(lines[a:b]), "cut"
    return text, "not_found"


def _parse_collapsed(tables, doc_meta):
    """Rows out of a rendition that prints the table with no column positions."""
    rows, all_rows, refused = [], [], False
    per_table = []
    for table in tables:
        read = plan_table.read_collapsed_table(table)
        per_table.append((table, read))
        all_rows.extend(read)
    if not plan_table.collapsed_is_aligned(all_rows):
        refused = True

    n = 0
    for table, read in per_table:
        columns = "|".join(plan_table.present_columns(table))
        for rec in read:
            ref, desc = plan_table.split_ref_chain(collapse_ws(rec["description"]), "")
            if not ref:
                continue
            block = rec["block"]
            dates = [] if refused else rec["dates"]
            rows.append({
                "project_id": doc_meta["project_id"],
                "plan_version": doc_meta["doc_id"],
                "plan_doc_id": doc_meta["doc_id"],
                "plan_disclosure_date": doc_meta["disclosure_date"],
                "borrower_ref": ref,
                "description": desc,
                "category_raw": rec["section"],
                "method_raw": "" if refused else _first_token(block, METHOD_TOKENS),
                "market_approach": "" if refused else _first_token(block, APPROACH_TOKENS),
                "status_raw": "" if refused else rec["status_raw"],
                "cells_raw": rec["cells"],
                "planned_date": dates[0] if dates else "",
                "revised_date": dates[-1] if len(dates) > 1 else "",
                "estimated_amount": ""
                    if refused else _as_amount(rec["estimated_amount"]),
                "actual_amount": ""
                    if refused else _as_amount(rec["actual_amount"]),
                "currency": "USD",
                "content_sha256": doc_meta["content_sha256"],
                "fetched_at": doc_meta["fetched_at"],
                "section": rec["section"],
                "record_index": n,
                "_wrap_seams": 0,
                "_dates": len(dates),
                "_columns": columns,
                "_no_est": not plan_table.has_column(table, plan_table.ESTIMATED_LABEL),
                "_layout": "collapsed_refused" if refused else "collapsed",
            })
            n += 1
    return rows, []


def _as_amount(value):
    try:
        return f"{float((value or '0').replace(',', '')):.2f}" if value else ""
    except ValueError:
        return ""


def collapse_ws(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_plan_text(text, doc_meta):
    """Pull the STEP plan tables out of a plan's text rendition."""
    lines = _join_wrapped_refs(text.replace("\r\n", "\n").split("\n"))
    tables = plan_table.find_tables(lines)
    if not tables:
        return [], lines
    if sum(plan_table.is_collapsed(t) for t in tables) > len(tables) / 2:
        return _parse_collapsed(tables, doc_meta)

    out, n = [], 0
    covered = set()
    for table in tables:
        start, end = plan_table.table_span(table)
        covered.update(range(start, end))
        # Asked of THIS table's heading band.
        has_estimated = plan_table.has_column(table, plan_table.ESTIMATED_LABEL)
        columns = "|".join(plan_table.present_columns(table))
        for rec in _records_in_table(table):
            row = _plan_row(rec, table.section, has_estimated, doc_meta, n)
            row["_columns"] = columns
            row["_no_est"] = not has_estimated
            out.append(row)
            n += 1
    free_text = [l for i, l in enumerate(lines) if i not in covered]
    return out, free_text


def _records_in_table(table):
    """Split one table's rows into records."""
    records, cur = [], None
    for ln in table.data:
        m = REF_RE.match(ln)
        if m:
            if cur:
                records.append(cur)
            rest = re.split(r" {2,}", m.group(2), maxsplit=1)
            cur = {"ref": m.group(1), "col0": [_clip_tail(rest[0])], "body": []}
            if len(rest) > 1:
                cur["body"].append(rest[1])
            continue
        if cur is None:
            continue
        if HEADER_LINE_RE.match(ln):
            continue
        cell = re.split(r" {2,}", ln, maxsplit=1)
        head = _clip_tail(cell[0])
        if head.strip():
            cur["col0"].append(head)
        if len(cell) > 1:
            cur["body"].append(cell[1])
    if cur:
        records.append(cur)
    return records


def _plan_row(rec, section, has_estimated, doc_meta, index):
    """One package row from one record of a fixed-width table."""
    desc, seams = _join_wrapped(rec["col0"])
    ref, desc = plan_table.split_ref_chain(desc, rec["ref"])
    body = " ".join(rec["body"])
    numbers = DATE_RE.findall(body)
    # The figures in print order: Estimated then Actual where the table has an Estimated
    # column; only the Actual where it does not (older plans).
    figures = []
    for tok in re.split(r"\s+", body):
        if AMOUNT_RE.match(tok.strip()):
            try:
                figures.append(float(tok.replace(",", "")))
            except ValueError:
                continue
    estimated = figures[0] if has_estimated and figures else None
    actual = (figures[1] if len(figures) > 1 else None) if has_estimated else \
        (figures[0] if figures else None)
    return {
        "project_id": doc_meta["project_id"],
        "plan_version": doc_meta["doc_id"],
        "plan_doc_id": doc_meta["doc_id"],
        "plan_disclosure_date": doc_meta["disclosure_date"],
        "borrower_ref": ref,
        "description": desc,
        "category_raw": section,
        "method_raw": _first_token(body, METHOD_TOKENS),
        "market_approach": _first_token(body, APPROACH_TOKENS),
        "status_raw": _status_token(body),
        "cells_raw": _cells_text(body),
        "planned_date": numbers[0] if numbers else "",
        "revised_date": numbers[-1] if len(numbers) > 1 else "",
        "estimated_amount": "" if estimated is None else f"{estimated:.2f}",
        "actual_amount": "" if actual is None else f"{actual:.2f}",
        "currency": "USD",
        "content_sha256": doc_meta["content_sha256"],
        "fetched_at": doc_meta["fetched_at"],
        "section": section,
        "record_index": index,
        "_wrap_seams": seams,
        "_dates": len(numbers),
        "_columns": "",
        "_no_est": False,
    }


def _cells_text(body):
    """The record's other cells as one string, figures and dates taken out:
    the text the component is read from at clean time (clean_procurement)."""
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b[\d,]+\.\d{2}\b", " ", body)
    return collapse_ws(text)


def _clip_tail(cell):
    """A fragment's tail may be the next column: 'Supply of four f' + loan id."""
    return LENDER_RE.sub("", cell)


def _match_form(value):
    """Fold a cell to the form the closed value sets are written in."""
    folded = unicodedata.normalize("NFKD", value or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.replace("\u2019", "'").replace("\u02bc", "'").casefold()
    return folded, re.sub(r"\s+", "", folded)


def _status_token(body):
    """The Process Status cell's word. A status too wide for its column wraps
    and is interleaved with the next columns' text: 'Pending Impl' ... 'ementat'
    ... 'ion', or 'Under Imple' ... 'mentation'. An exact match would take the
    first piece for 'Pending' (a different STEP value) or find nothing, so a
    clipped head is matched too - only when it opens exactly one status."""
    found = _first_token(body, STATUS_TOKENS)
    folded, flat = _match_form(body)
    if found.lower() == "pending" and re.search(r"pending\b.{0,80}?\bimpl", folded):
        return "Pending Implementation"
    if found:
        return found
    # Each status by the longest head of it the row prints (ten characters at least).
    best, length = set(), 0
    for tok in STATUS_TOKENS:
        _, dt = _match_form(tok)
        n = next((k for k in range(len(dt), 9, -1) if dt[:k] in flat), 0)
        if n > length:
            best, length = {tok}, n
        elif n and n == length:
            best.add(tok)
    if len(best) != 1:
        return ""
    t = best.pop()
    return t[:1].upper() + t[1:]


def _fold_same_length(text):
    """_match_form's folding, one character for one, so a match position in the
    folded text is the same position in the printed text."""
    out = []
    for ch in text or "":
        base = unicodedata.normalize("NFKD", ch)[:1] or ch
        base = "'" if base in "\u2019\u02bc" else base
        low = base.lower()
        out.append(low if len(low) == 1 else base)
    return "".join(out)


def _first_token(text, tokens):
    """The words of the first of `tokens` found in `text`, as printed, or ''."""
    ftext = _fold_same_length(text)
    _, dtext = _match_form(text)
    for t in tokens:
        ft, _ = _match_form(t)
        m = re.search(r"(?<!\w)" + re.escape(ft) + r"(?!\w)", ftext)
        if m:
            return re.sub(r"\s+", " ", text[m.start():m.end()])
    for t in tokens:
        _, dt = _match_form(t)
        if len(dt) >= 6 and dt in dtext:
            return t[:1].upper() + t[1:]
    return ""


# ---------------------------------------------------------------- main stages

def collect_plans(projects, data, log, refresh, delay, workers=6, previous=None):
    """List every project's plans, download the renditions, then parse them."""
    previous = previous or {}
    raw_dir = where(data, "plans")
    os.makedirs(raw_dir, exist_ok=True)
    listing_failures, no_plan, tasks = [], [], []
    total = len(projects)
    for n, proj in enumerate(projects, 1):
        pid = proj["project_id"]
        try:
            docs = list_plans(pid, log, delay)
        except Exception as exc:                       # noqa: BLE001
            listing_failures.append((pid, str(exc)))
            print(f"[{n}/{total}] {pid} plan listing failed: {exc}", flush=True)
            continue
        docs = [d for d in docs if d["txturl"]]
        if not docs:
            no_plan.append(pid)
            continue
        for d in docs:
            tasks.append((pid, d))
        print(f"[{n}/{total}] {pid}: {len(docs)} plans listed, "
              f"{len(tasks)} renditions to have", flush=True)

    rows, fetch_failures, free_text_chars, bundle_docs = [], [], [], []
    empty_docs = []
    blobs, stamps = {}, {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_fetch_plan_file, os.path.join(raw_dir, f"{d['doc_id']}.txt"),
                        d["txturl"], refresh, delay): (pid, d)
            for pid, d in tasks
        }
        done = 0
        for fut in as_completed(futures):
            pid, d = futures[fut]
            done += 1
            try:
                blob, stamp = fut.result()
                blobs[(pid, d["doc_id"])] = blob
                stamps[d["doc_id"]] = stamp
            except Exception as exc:                   # noqa: BLE001
                fetch_failures.append((pid, d["doc_id"], str(exc)))
            if done % 200 == 0:
                print(f"  downloaded {done}/{len(tasks)} renditions", flush=True)

    for pid, d in sorted(tasks, key=lambda t: (t[0], t[1]["doc_id"])):
        blob = blobs.get((pid, d["doc_id"]))
        if blob is None:
            continue
        text = blob.decode("utf-8", errors="replace")
        text, kind = segment_for_project(text, pid)
        if kind != "single":
            bundle_docs.append((d["doc_id"], kind))
        if kind == "not_found":
            # A bundle that does not name this project: kept on disk, counted, and not
            # parsed, so no other operation's packages land here.
            continue
        sha = sha_text(blob)
        old_sha, old_at = previous.get(d["doc_id"], ("", ""))
        meta = {
            "project_id": pid, "doc_id": d["doc_id"],
            "disclosure_date": d["disclosure_date"],
            "content_sha256": sha,
            "fetched_at": (old_at if old_sha == sha and old_at else
                           stamps.get(d["doc_id"]) or now_stamp()),
        }
        parsed, free = parse_plan_text(text, meta)
        free_text_chars.append(sum(len(x) for x in free))
        if not parsed:
            # Parsed to nothing: an empty package table (no reference-shaped token
            # anywhere) or a parse failure; the report counts them apart.
            empty_docs.append((d["doc_id"], pid,
                               bool(PLAN_REF_SHAPE.search(text))))
        for r in parsed:
            # Position in the plan, not the reference: a plan can list two packages under
            # one generic reference ('CS-INDV').
            r["package_version_id"] = f"{d['doc_id']}:{r['record_index']:05d}"
            rows.append(r)

    print(f"  parsed {len(rows):,} package rows from {len(blobs):,} renditions",
          flush=True)
    return (rows, listing_failures, no_plan, fetch_failures, free_text_chars,
            bundle_docs, empty_docs)


def store_keeping_old(path, blob):
    """Write a fetched file. When one is already there with different bytes -
    a re-published plan, a changed interface response - the old one is kept
    beside it, named by its own checksum, never overwritten."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            old = fh.read()
        if old == blob:
            return
        stem, ext = os.path.splitext(path)
        keep = f"{stem}.{sha_text(old)[:12]}{ext}.old"
        if not os.path.exists(keep):
            os.replace(path, keep)
    write_atomic(path, blob)


def refuse_page(blob, what):
    """Raise when a body is empty or an HTML page: a throttled or failing
    interface answers 200 with one, and it must not be stored as data."""
    head = blob[:512].lstrip().lower()
    if not head:
        raise RuntimeError(f"{what}: empty body, not stored")
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head:
        raise RuntimeError(f"{what}: an HTML page, not stored")


def json_body(r, what):
    """The parsed JSON of a response, refusing an empty body or HTML page."""
    refuse_page(r.content, what)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{what}: response is not JSON") from None


def check_keys(records, expected, what):
    """Stop the run when the interface renamed a field: an expected key absent
    from every record of a non-empty page would otherwise be read as blank."""
    if not records:
        return
    for k in expected:
        if not any(isinstance(r, dict) and k in r for r in records):
            raise SystemExit(f"{what}: no record carries '{k}'; the interface's "
                             f"response changed, update the reader")


def _fetch_plan_file(path, url, refresh, delay):
    """(bytes, fetched_at) of a plan rendition; fetched_at is blank when the
    file was already on disk."""
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as fh:
            return fh.read(), ""
    stamp = now_stamp()
    blob = get(url, timeout=120).content
    refuse_page(blob, f"plan rendition {os.path.basename(path)}")
    store_keeping_old(path, blob)
    time.sleep(delay)
    return blob, stamp


# Fields naming or reaching a person: the procurement contact on a notice and the Bank staff
# member on an award.
PERSONAL = {"contact_name", "contact_email", "contact_phone_no", "contact_address",
            "teammemfullname"}


# Keys every page of the interface's records carries; absent from a whole page, the
# interface changed (check_keys).
NOTICE_KEYS = ("id", "notice_type", "noticedate")
AWARD_KEYS = ("contr_id", "contr_desc", "contr_sgn_date")
PLAN_LIST_KEYS = ("id", "display_title")


def _without_personal(record):
    return {k: v for k, v in record.items() if k not in PERSONAL}


def _stored_records(path, refresh, delay, fetch):
    """One project's records: the stored response, or `fetch()`ed, stripped of
    personal fields and stored (an earlier, different copy is kept)."""
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    records = [_without_personal(r) for r in fetch()[0]]
    store_keeping_old(path, json.dumps(records, ensure_ascii=False, sort_keys=True,
                                       indent=0).encode())
    time.sleep(delay)
    return records


def _collect(projects, data, folder, refresh, delay, fetch, to_row):
    """(rows, failures) over projects: each project's records, stored under
    data/<folder>/<project_id>.json, made rows by to_row(record, pid)."""
    raw_dir = where(data, folder)
    os.makedirs(raw_dir, exist_ok=True)
    rows, failures = [], []
    for proj in projects:
        pid = proj["project_id"]
        try:
            records = _stored_records(os.path.join(raw_dir, f"{pid}.json"), refresh, delay,
                                      lambda: fetch(pid))
        except Exception as exc:                       # noqa: BLE001
            failures.append((pid, str(exc)))
            continue
        rows.extend(to_row(rec, pid) for rec in records)
    return rows, failures


def collect_notices(projects, data, log, refresh, delay):
    def fetch(pid):
        return fetch_pages(NOTICES, {"project_id": pid}, lambda j: j.get("procnotices") or [],
                           what=f"notices {pid}", expected=NOTICE_KEYS, delay=delay, log=log,
                           source="notices")
    return _collect(projects, data, "notices_json", refresh, delay, fetch, _notice_row)


def _notice_row(rec, pid):
    return {
        "notice_id": str(rec.get("id") or ""),
        "project_id": rec.get("project_id") or pid,
        "notice_type": rec.get("notice_type") or "",
        "notice_status": rec.get("notice_status") or "",
        "publication_date": iso_date(rec.get("noticedate")),
        "deadline_date": iso_date(rec.get("submission_deadline_date")),
        # The borrower reference the notice was issued under: the key that ties a notice to
        # its plan package and its award.
        "borrower_ref": (rec.get("bid_reference_no") or "").strip(),
        "description": _clean_space(rec.get("bid_description") or ""),
        "category_raw": rec.get("procurement_group") or "",
        "method_code": rec.get("procurement_method_code") or "",
        "method_raw": rec.get("procurement_method_name") or "",
        "country_name": rec.get("project_ctry_name") or "",
        "notice_lang": rec.get("notice_lang_name") or "",
    }


def collect_awards(projects, data, log, refresh, delay):
    def fetch(pid):
        return fetch_pages(AWARDS, {"projectid": pid}, lambda j: j.get("contract") or [],
                           what=f"awards {pid}", expected=AWARD_KEYS, delay=delay, log=log,
                           source="awards")
    return _collect(projects, data, "awards_json", refresh, delay, fetch, _award_row)


def _award_row(rec, pid):
    supp = rec.get("suppinfo") or []
    if isinstance(supp, dict):
        supp = [supp]
    supp = [s for s in supp if isinstance(s, dict)]
    names = [s.get("name") for s in supp if s.get("name")]
    countries = [c for c in (s.get("countryname") or s.get("countryshortname") for s in supp) if c]
    amounts = []
    for s in supp:
        try:
            amounts.append(float(s.get("supplier_contr_amount") or 0))
        except (TypeError, ValueError):
            pass
    return {
        "contract_id": str(rec.get("contr_id") or ""),
        "project_id": rec.get("projectid") or pid,
        "borrower_ref": (rec.get("contr_refnum") or "").strip(),
        "description": _clean_space(rec.get("contr_desc") or ""),
        "signed_date": iso_date(rec.get("contr_sgn_date")),
        "no_objection_date": iso_date(rec.get("contr_no_obj_dat")),
        "total_amount": _num(rec.get("total_contr_amnt")),
        "currency": "USD",
        "category_raw": rec.get("procurement_group") or "",
        "method_raw": (rec.get("procu_meth_text") or "").strip(),
        "review_type": (rec.get("rvw_type") or "").strip().lower(),
        "supplier_name": "|".join(dict.fromkeys(n for n in names if n)),
        "supplier_country": "|".join(dict.fromkeys(countries)),
        "supplier_amount": "" if not amounts else f"{sum(amounts):.2f}",
        "region": rec.get("regionname") or "",
        "sector": _join_sector(rec.get("sector")),
    }


def _clean_space(s):
    return re.sub(r"\s+", " ", fold_chars(s)).strip()


def _num(v):
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return ""


def _join_sector(v):
    if isinstance(v, list):
        return "|".join(str(x) for x in v)
    return str(v or "")


# -------------------------------------------------------------------- the diff

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=os.path.join(ROOT, "inputs/config/cohort.csv"))
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--limit", type=int, default=0, help="first N projects (smoke run)")
    ap.add_argument("--projects", default="",
                    help="comma-separated project ids: fetch only these; the other "
                         "projects' rows are kept from the earlier run")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--workers", type=int, default=6,
                    help="concurrent plan rendition downloads")
    ap.add_argument("--refresh", action="store_true",
                    help="re-download even where a raw file exists")
    ap.add_argument("--refresh-records", action="store_true",
                    help="request notices and awards again (they change); plan "
                         "renditions already on disk are kept")
    ap.add_argument("--skip-plans", action="store_true")
    args = ap.parse_args()

    projects = read_cohort(args.cohort)
    if args.projects:
        wanted = {p.strip() for p in args.projects.split(",") if p.strip()}
        projects = [p for p in projects if p["project_id"] in wanted]
    if args.limit:
        projects = projects[:args.limit]
    proc_dir = where(args.data, "procurement")
    os.makedirs(proc_dir, exist_ok=True)

    log = FetchLog(where(args.data, "fetch_log"))
    t0 = time.time()

    plan_rows, listing_fail, no_plan, plan_fail, free_chars = [], [], [], [], []
    bundle_docs, empty_docs = [], []
    if not args.skip_plans:
        previous = {}
        for r in read_csv(os.path.join(proc_dir, "packages_raw.csv")):
            previous.setdefault(r["plan_doc_id"], (r["content_sha256"], r["fetched_at"]))
        (plan_rows, listing_fail, no_plan, plan_fail, free_chars, bundle_docs,
         empty_docs) = collect_plans(projects, args.data, log, args.refresh,
                                     args.delay, workers=args.workers, previous=previous)

    # Prepare at output, not at input: nothing is dropped for looking irrelevant.
    seen_hash = {}
    prep, wrap_seams_total = [], 0
    section_columns = Counter()
    no_estimated_column = 0
    for r in plan_rows:
        row = dict(r)
        row.pop("_wrap_seams", None)
        row.pop("_dates", None)
        row.pop("_layout", None)
        section_columns[(r["section"], row.pop("_columns", ""))] += 1
        no_estimated_column += 1 if row.pop("_no_est", False) else 0
        wrap_seams_total += r.get("_wrap_seams", 0)
        if not seen_hash.get(r["plan_doc_id"]):
            seen_hash[r["plan_doc_id"]] = r["content_sha256"]
        prep.append(row)

    refresh_records = args.refresh or args.refresh_records
    notices, notice_fail = collect_notices(projects, args.data, log, refresh_records,
                                           args.delay)
    awards, award_fail = collect_awards(projects, args.data, log, refresh_records,
                                        args.delay)

    # Only the rows of projects fully re-read are replaced: a partial or failed run never
    # shrinks a table.
    asked = {p["project_id"] for p in projects}
    plans_ok = set() if args.skip_plans else asked - (
        {p for p, _ in listing_fail} | {f[0] for f in plan_fail})
    notices_ok = asked - {p for p, _ in notice_fail}
    awards_ok = asked - {p for p, _ in award_fail}
    for name, cols, rows, order, ok in (
            ("packages_raw.csv", RAW_PACKAGE_COLS, prep,
             ("project_id", "plan_version", "record_index"), plans_ok),
            ("notices_raw.csv", RAW_NOTICE_COLS, notices, ("project_id", "notice_id"),
             notices_ok),
            ("awards_raw.csv", RAW_AWARD_COLS, awards, ("project_id", "contract_id"), awards_ok)):
        path = os.path.join(proc_dir, name)
        rows = [r for r in rows if r["project_id"] in ok] + \
            [r for r in read_csv(path) if r["project_id"] not in ok]
        rows.sort(key=lambda r: tuple(str(r.get(k, "")).zfill(8) if k == "record_index"
                                      else str(r.get(k, "")) for k in order))
        write_csv(path, cols, rows)
    log.write()
    # With --skip-plans the plan rows are kept as they were, which is not a failed fetch:
    # the status covers only what this run asked for.
    done = (asked if args.skip_plans else plans_ok) & notices_ok & awards_ok
    write_fetch_status(args.data, "procurement", sorted(done), sorted(asked - done))

    distinct_versions = len({(r["project_id"], r["plan_version"]) for r in prep})
    distinct_hashes = len({r["content_sha256"] for r in prep})
    by_project = {}
    for r in prep:
        by_project.setdefault(r["project_id"], set()).add(r["plan_version"])

    report = where(args.data, "reports", "fetch_procurement.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"run at            {now_stamp()}   ({time.time() - t0:.0f}s)\n")
        fh.write(f"projects included {len(projects)}\n")
        fh.write(f"projects with plans {len(by_project)}\n")
        fh.write(f"plan documents fetched/cached: {len(seen_hash)} "
                 f"(distinct content hashes {distinct_hashes})\n")
        fh.write(f"package rows (one per plan version x package): {len(prep):,}\n")
        fh.write(f"distinct (project, plan document) pairs: {distinct_versions:,}\n")
        fh.write(f"notices rows: {len(notices):,}\n")
        fh.write(f"awards rows: {len(awards):,}\n")

        # Which columns each STEP section printed, and in how many rows.
        fh.write("\ncolumns found per STEP section (rows read with that set)\n")
        for section in ("WORKS", "GOODS", "NON CONSULTING SERVICES",
                        "CONSULTING FIRMS", "INDIVIDUAL CONSULTANTS"):
            variants = [(cols, n) for (sec, cols), n in section_columns.items()
                        if sec == section]
            total = sum(n for _, n in variants)
            fh.write(f"  {section}  ({total:,} rows)\n")
            for cols, n in sorted(variants, key=lambda v: -v[1])[:4]:
                shown = cols.replace("|", ", ") or "(none read)"
                fh.write(f"      {n:>7,}  {shown}\n")
        # Counted from the parser's own decision, not the column list above.
        fh.write(f"  rows from a table printing NO Estimated Amount column: "
                 f"{no_estimated_column:,} "
                 f"({100 * no_estimated_column / max(1, len(prep)):.1f}%) - "
                 f"their estimated_amount is left blank, never filled from the "
                 f"actual\n")
        fh.write(f"cell fragments glued (no space inserted, never guessed): "
         f"{wrap_seams_total:,}\n")
        fh.write(f"preamble characters outside the plan tables: {sum(free_chars):,}\n")
        cut = [d for d, k in bundle_docs if k == "cut"]
        lost = [d for d, k in bundle_docs if k == "not_found"]
        fh.write(f"bundled renditions cut to this project's plan ({len(cut)}): "
                 f"{', '.join(cut)}\n")
        fh.write(f"bundled renditions not naming this project, not parsed "
                 f"({len(lost)}): {', '.join(lost)}\n")
        fh.write(f"projects returning no procurement plan ({len(no_plan)}): "
                 f"{', '.join(no_plan)}\n")
        parsed_pids = {r["project_id"] for r in plan_rows}
        zero_rows = sorted({p["project_id"] for p in projects}
                           - parsed_pids - set(no_plan))
        fh.write(f"projects whose parse yielded no package row ({len(zero_rows)}): "
                 f"{', '.join(zero_rows)}\n")
        # The per-DOCUMENT view, which the project-level line above hides: a project with
        # one readable plan and four unreadable ones looks fine.
        failed = [(d, p) for d, p, looks in empty_docs if looks]
        fh.write(f"renditions that parsed to zero package rows ({len(empty_docs)}): "
                 f"{len(empty_docs) - len(failed)} carry no reference-shaped token "
                 f"anywhere (an empty package table - not a failure), "
                 f"{len(failed)} carry one (a real parse failure)\n")
        for n, (did, pid) in enumerate(failed):
            fh.write(f"{did}/{pid}{chr(10) if n % 4 == 3 else '  '}")
        if failed:
            fh.write("\n")
        fh.write(f"plan listing failures ({len(listing_fail)}):\n")
        for pid, exc in listing_fail:
            fh.write(f"  {pid}\t{exc}\n")
        fh.write(f"plan fetch failures ({len(plan_fail)}):\n")
        for pid, did, exc in plan_fail:
            fh.write(f"  {pid}\t{did}\t{exc}\n")
        fh.write(f"notice failures ({len(notice_fail)}):\n")
        for pid, exc in notice_fail:
            fh.write(f"  {pid}\t{exc}\n")
        fh.write(f"award failures ({len(award_fail)}):\n")
        for pid, exc in award_fail:
            fh.write(f"  {pid}\t{exc}\n")

    print(f"\nplan documents: {len(seen_hash)}  packages: {len(prep):,}  "
          f"notices: {len(notices):,}  awards: {len(awards):,}")
    print(f"wrote {proc_dir}/{{packages_raw,notices_raw,awards_raw}}.csv")
    print(f"wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
