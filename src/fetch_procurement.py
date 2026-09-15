#!/usr/bin/env python3
"""Fetch procurement data for the cohort: plans, solicitation notices, awards.

Reads  inputs/config/cohort.csv
       data/raw/plans/{doc_id}.txt              cached plan text renditions
       data/raw/notices/{project_id}.json       cached notice responses
       data/raw/awards/{project_id}.json        cached award responses
Writes data/raw/...                              (gitignored raw artefacts)
       meta/api_fetch_log.csv                    every interface call, per data model s5
       data/intermediate/procurement/packages_raw.csv
       data/intermediate/procurement/notices_raw.csv
       data/intermediate/procurement/awards_raw.csv
       data/intermediate/procurement/package_changes.csv
       data/intermediate/procurement/fetch_procurement_report.txt

Built for re-running, because the procurement plan is the document that recurs.
Three disciplines, all from the card and all load-bearing:

  * Content-hash every artefact. A re-fetch that returns identical bytes is not a
    new version and writes no new row. The hash is the identity of a plan
    rendition, the same way the SHA-256 of a text is the identity of an embedding
    cache entry - which is why re-running the embedder is free.
  * Never overwrite a plan version. Every distinct (doc_id, content_sha256) is a
    row in packages_raw with its own fetched_at. History is the product.
  * Write the diff. package_changes.csv says, per package, what moved between
    consecutive plan versions: appeared, status, amount, disappeared.

Re-run cost: a plan whose raw file is already on disk is not re-downloaded, which
mirrors src/fetch.py. --refresh forces the download and re-hash, which is how a
re-published rendition under the same doc_id is noticed.

Two of the three sources are structured JSON. The third is not: procurement plans
are published only as a PDF and its text rendition, and the text rendition is a
fixed-width print of a STEP table whose cells clip mid-word at the column edge.
The parser for it is `parse_plan_text` and it is deliberately conservative - it
recovers what it can and reports what it could not key.
"""
import argparse, csv, hashlib, json, os, re, sys, threading, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch import get, HEADERS, read_cohort            # noqa: E402
from clean_procurement import (                        # noqa: E402
    CLEAN_VERSION, norm_ref, split_markers, is_placeholder, detect_lang,
    norm_status, norm_method, norm_category, desc_sha256, fold_chars,
)

WDS = "https://search.worldbank.org/api/v3/wds"
NOTICES = "https://search.worldbank.org/api/v2/procnotices"
AWARDS = "https://search.worldbank.org/api/contractdata"

PLAN_DOCTY = "Procurement Plan"
PAGE = 500

PROC_DIR = "intermediate/procurement"

# The metadata columns of the STEP plan table, in the order the header prints
# them. Only the pairs we can locate reliably are used; see parse_plan_text.
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")
REF_RE = re.compile(r"^\s*([A-Z][A-Z0-9]{1,}(?:[-\u2013\u2014]\s?[A-Z0-9]+){1,5})\s*/\s*(.*)$")
LENDER_RE = re.compile(r"\s*IDA\s*/\s*[\dA-Za-z]+\s*$")
# Column headings the rendition repeats down every page. A heading taken as a
# continuation line is how "Activity Reference No." ends up inside a package
# description - found by reading the first full run, where 213 of 4,077 rows
# (5.22%) carried one.
HEADER_LINE_RE = re.compile(
    r"^\s*(Activity Reference No\.|Project information|"
    r"Project Implementation agency|Loan / Credit No|Description\b.*Component|"
    r"General Information|Country:|Project ID|Project Name|Executing Agency|"
    r"Date of the Procurement Plan|Period covered|Revised Plan Date|"
    r"PROCUREMENT|PLAN\s*$)")
SECTION_RE = re.compile(r"^\s*(WORKS|GOODS|CONSULTING SERVICES|CONSULTING FIRMS|"
                        r"INDIVIDUAL CONSULTANTS|NON[ -]CONSULTING SERVICES|"
                        r"NON CONSULTING SERVICES)\s*$")
METHOD_TOKENS = ("Request for Bids", "Request for Quotations", "Direct Selection",
                 "Direct Contracting", "Consultant Qualification Selection",
                 "Consultant Qualification  Selection", "Quality And Cost Based Selection",
                 "Quality and Cost Based Selection", "Least Cost Selection",
                 "Individual Consultant Selection", "Single Source Selection",
                 "Framework Agreement", "Competitive Dialogue")
APPROACH_TOKENS = ("Open - International", "Open - National", "Limited - International",
                   "Limited - National", "Open - international", "Open - national")

PACKAGE_COLS = [
    "package_version_id", "package_id", "project_id", "plan_version", "plan_doc_id",
    "plan_disclosure_date", "borrower_ref", "description", "description_sha256",
    "description_lang", "is_placeholder", "category", "category_raw", "method",
    "method_raw", "market_approach", "status", "status_raw", "planned_date",
    "revised_date", "estimated_amount", "currency", "content_sha256", "fetched_at",
    "section", "record_index",
]
NOTICE_COLS = [
    "notice_id", "project_id", "notice_type", "publication_date", "deadline_date",
    "bid_description", "bid_description_sha256", "description_lang", "is_placeholder",
    "procurement_category", "country_code", "country_name", "sector", "url",
]
AWARD_COLS = [
    "contract_id", "project_id", "borrower_ref", "description", "description_sha256",
    "description_lang", "is_placeholder", "signed_date", "no_objection_date",
    "total_amount", "currency", "procurement_group", "method", "review_type",
    "supplier_name", "supplier_country", "supplier_amount", "region", "sector",
]
FETCH_LOG_COLS = ["fetch_id", "source", "endpoint", "query_params", "fetched_at",
                  "record_count", "response_sha256"]
CHANGE_COLS = ["project_id", "key", "key_basis", "borrower_ref", "change", "field",
               "from_value", "to_value", "from_plan_version", "to_plan_version",
               "detected_at"]


# ------------------------------------------------------------------ utilities

def now_stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(blob):
    return hashlib.sha256(blob).hexdigest()


def write_csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


class FetchLog:
    """Append every interface call, keyed so a re-run updates rather than repeats."""

    def __init__(self, path):
        self.path = path
        self.rows = {}
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    self.rows[r["fetch_id"]] = r

    def record(self, source, endpoint, params, record_count, response_sha256):
        fid = sha256_bytes("|".join([source, endpoint, params]).encode())[:16]
        self.rows[fid] = {
            "fetch_id": fid, "source": source, "endpoint": endpoint,
            "query_params": params, "fetched_at": now_stamp(),
            "record_count": record_count, "response_sha256": response_sha256,
        }

    def write(self):
        write_csv(self.path, FETCH_LOG_COLS, [self.rows[k] for k in sorted(self.rows)])


# --------------------------------------------------------------- plan listing

def list_plans(project_id, log, delay):
    q = urllib.parse.urlencode({
        "format": "json", "rows": "200", "projectid": project_id, "docty": PLAN_DOCTY,
        "fl": "id,docty,display_title,disclosure_date,txturl,pdfurl,lang,projectid"})
    url = f"{WDS}?{q}"
    r = get(url, timeout=60)
    data = r.json()
    docs = []
    for key, val in (data.get("documents") or {}).items():
        if key == "facets":
            continue
        did = str(val.get("id") or "").strip()
        if not did:
            continue
        # The documents interface returns the identifier with a D prefix on some
        # routes and without it on others. One form, so a re-run matches.
        did = did[1:] if did.startswith("D") and did[1:].isdigit() else did
        docs.append({
            "doc_id": did,
            "title": (val.get("display_title") or "").replace("\xa0", " ").strip(),
            "disclosure_date": (val.get("disclosure_date") or "")[:10],
            "lang": val.get("lang") or "",
            "txturl": val.get("txturl") or "",
        })
    log.record("documents", WDS, q, len(docs), sha256_bytes(r.content))
    time.sleep(delay)
    return docs


# ---------------------------------------------------------------- plan parsing

def _join_wrapped(parts):
    """Stitch a column's line fragments back into one string.

    The rendition clips each cell at the column edge, so a fragment usually ends
    mid-word - 'S' + 'upply', 'commi' + 'ssion', 'equipm' + 'ent' - and the two
    halves must be glued with nothing between them. Occasionally the clip lands
    exactly ON a space instead, which is then stripped as trailing whitespace,
    and gluing runs two words together: 'Data' + 'Center' becomes 'DataCenter'.

    From the text alone the two cases are indistinguishable. The fragments in
    one real cell end at 26, 29, 27, 26, 27 and 29 characters, so "did this line
    fill the column?" does not separate them either, and neither does anything
    else in the rendition. So this does NOT guess: it always glues, and returns
    the number of seams so the loss has a number attached.

    An earlier version consulted /usr/share/dict/words to decide. It was removed
    for two reasons. Its space-inserting branch was unreachable - the elif above
    it fired whenever the first condition was false - so it always glued anyway
    and the list only inflated a counter. And a dictionary cannot settle this in
    principle: 'Equipment,Servers' in the corpus was typed without a space by
    the borrower, so there is no correct answer to look up.

    The loss is handled where it belongs, in the matching copy. Gluing only ever
    DELETES a space, never alters a character, so comparing on a
    whitespace-stripped copy makes it invisible: 'DataCenter' and 'data center'
    both reduce to 'datacenter'. See description_match in clean_procurement.py.
    """
    out, seams = "", 0
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not out:
            out = p
            continue
        if out[-1].isalnum() and p[0].isalnum():
            out += p
            seams += 1
        else:
            out += p
    return out, seams


def segment_for_project(text, project_id):
    """Cut a bundled rendition down to this project's own plan.

    Some plan documents are published with a text rendition that concatenates
    the plans of many operations - one 8.9 MB rendition returned for Tanzania's
    P160766 carries preambles for 59 projects. Parsing it whole would attribute
    other countries' packages to this project, which is worse than not parsing
    it, because the row looks valid. A rendition with more than one preamble is
    therefore split at the preambles and only the segment naming this project is
    used; a rendition with one preamble is returned untouched.
    """
    preambles = [i for i, ln in enumerate(text.split("\n"))
                 if ln.startswith("Project information")]
    if len(preambles) < 2:
        return text, False
    lines = text.split("\n")
    bounds = preambles + [len(lines)]
    for a, b in zip(bounds, bounds[1:]):
        head = "\n".join(lines[a:min(a + 6, b)])
        if project_id in head:
            return "\n".join(lines[a:b]), True
    return text, True


def split_ref_chain(desc, fallback):
    """Recover the package reference from the joined first column.

    The reference cell is a chain of one or two references followed by the
    description: 'TZ-MCIT-254784-CW-RFB / Rehabilitation of ...', or
    'BJ-UCP / PADA-43641-GO-RFQ / Fourniture de ...' where the PIU prints its own
    name first. Either can be clipped mid-token by the column edge - 'PADA-43641-
    GO-' plus 'RFQ' on the next line - so this reads the JOINED string rather
    than the first line, takes the last reference-shaped segment that carries a
    number, and returns what is left as the description.
    """
    parts = desc.split(" / ")
    ref, i = fallback, 0
    while i < len(parts) and i < 3:
        tok = parts[i].strip()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9\u2013\u2014-]{1,40}", tok):
            break
        if re.search(r"\d", tok):
            ref = tok
        elif i > 0:
            break
        i += 1
    rest = " / ".join(parts[i:]).strip()
    return ref, (rest or desc)


def parse_plan_text(text, doc_meta):
    """Pull the STEP plan table out of a plan's text rendition.

    Records are delimited by the borrower reference, which is the first thing in
    the first column of every row of every section. Everything between two
    references belongs to the first. Column zero (reference and description) is
    recovered exactly; the metadata columns are located by matching their closed
    value sets anywhere in the record block, because the rendition wraps each
    cell over an unknown number of physical lines and no column offset is stable
    across records.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    records, cur, section = [], None, ""
    free_text = []

    for i, ln in enumerate(lines):
        sec = SECTION_RE.match(ln)
        if sec:
            section = sec.group(1)
            continue
        m = REF_RE.match(ln)
        if m:
            if cur:
                records.append(cur)
            rest = re.split(r" {2,}", m.group(2), maxsplit=1)
            cur = {"section": section, "line": i, "ref": m.group(1),
                   "col0": [_clip_tail(rest[0])], "body": []}
            if len(rest) > 1:
                cur["body"].append(rest[1])
            continue
        if cur is None:
            free_text.append(ln)
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

    out = []
    for n, rec in enumerate(records):
        desc, seams = _join_wrapped(rec["col0"])
        ref, desc = split_ref_chain(desc, rec["ref"])
        body = " ".join(rec["body"])
        numbers = DATE_RE.findall(body)
        amounts = [c for c in re.split(r" {2,}|\s{3,}", body) if AMOUNT_RE.match(c.strip())]
        amount = None
        for tok in amounts:
            try:
                amount = float(tok.replace(",", ""))
                break
            except ValueError:
                continue
        status_raw = _first_token(body, ("Signed", "Canceled", "Cancelled",
                                         "Under Implementation", "Under Preparation",
                                         "Not Started", "Planned", "Completed",
                                         "Under implementation", "Under preparation",
                                         "Pending", "In Progress"))
        method_raw = _first_token(body, METHOD_TOKENS)
        approach = _first_token(body, APPROACH_TOKENS)
        row = {
            "project_id": doc_meta["project_id"],
            "plan_version": doc_meta["doc_id"],
            "plan_doc_id": doc_meta["doc_id"],
            "plan_disclosure_date": doc_meta["disclosure_date"],
            "borrower_ref": ref,
            "description": desc,
            "category_raw": rec["section"],
            "method_raw": method_raw,
            "market_approach": approach,
            "status_raw": status_raw,
            "planned_date": numbers[0] if numbers else "",
            "revised_date": numbers[-1] if len(numbers) > 1 else "",
            "estimated_amount": "" if amount is None else f"{amount:.2f}",
            "currency": "USD",
            "content_sha256": doc_meta["content_sha256"],
            "fetched_at": doc_meta["fetched_at"],
            "section": rec["section"],
            "record_index": n,
            "_wrap_seams": seams,
            "_dates": len(numbers),
        }
        out.append(row)
    return out, free_text


def _clip_tail(cell):
    """A fragment's tail may be the next column: 'Supply of four f' + loan id."""
    return LENDER_RE.sub("", cell)


def _first_token(text, tokens):
    for t in tokens:
        if re.search(r"(?<![A-Za-z])" + re.escape(t) + r"(?![A-Za-z])", text):
            return t
    return ""


# ---------------------------------------------------------------- main stages

def collect_plans(projects, data, log, refresh, delay, workers=6):
    """List every project's plans, download the renditions, then parse them.

    Listing is serial because it is one request per project and the interface is
    shared; downloads run in a thread pool because they are the expensive part -
    a 150 KB rendition from documents.worldbank.org takes several seconds, and
    this cohort publishes about thirty plan revisions per project, which is over
    two thousand files. The pool is modest on purpose: the box has four cores and
    the constraint here is the far side's latency, not ours.

    Parsing stays serial and in a deterministic order, so a re-run produces the
    same rows whatever order the downloads completed in.
    """
    raw_dir = os.path.join(data, "raw", "plans")
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
    blobs = {}
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
                blob, _fetched = fut.result()
                blobs[(pid, d["doc_id"])] = blob
            except Exception as exc:                   # noqa: BLE001
                fetch_failures.append((pid, d["doc_id"], str(exc)))
            if done % 200 == 0:
                print(f"  downloaded {done}/{len(tasks)} renditions", flush=True)

    for pid, d in sorted(tasks, key=lambda t: (t[0], t[1]["doc_id"])):
        blob = blobs.get((pid, d["doc_id"]))
        if blob is None:
            continue
        path = os.path.join(raw_dir, f"{d['doc_id']}.txt")
        text = blob.decode("utf-8", errors="replace")
        text, bundled = segment_for_project(text, pid)
        if bundled:
            # A multi-operation bundle. Kept on disk, counted, and NOT parsed:
            # attributing another operation's packages to this project would put
            # rows in the table that look exactly like real ones.
            bundle_docs.append(d["doc_id"])
            continue
        meta = {
            "project_id": pid, "doc_id": d["doc_id"],
            "disclosure_date": d["disclosure_date"],
            "content_sha256": sha256_bytes(blob),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(os.path.getmtime(path))),
        }
        parsed, free = parse_plan_text(text, meta)
        free_text_chars.append(sum(len(x) for x in free))
        for r in parsed:
            r["package_id"] = r["borrower_ref"]
            r["package_version_id"] = f"{d['doc_id']}:{r['borrower_ref']}"
            rows.append(r)

    print(f"  parsed {len(rows):,} package rows from {len(blobs):,} renditions",
          flush=True)
    return rows, listing_failures, no_plan, fetch_failures, free_text_chars, bundle_docs


def _fetch_plan_file(path, url, refresh, delay):
    if os.path.exists(path) and not refresh:
        return open(path, "rb").read(), False
    r = get(url, timeout=120)
    blob = r.content
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, path)
    time.sleep(delay)
    return blob, True


def collect_notices(projects, data, log, refresh, delay):
    raw_dir = os.path.join(data, "raw", "notices")
    os.makedirs(raw_dir, exist_ok=True)
    rows, failures = [], []
    for proj in projects:
        pid = proj["project_id"]
        path = os.path.join(raw_dir, f"{pid}.json")
        records = None
        try:
            if os.path.exists(path) and not refresh:
                records = json.load(open(path, encoding="utf-8"))
            else:
                records, pages = [], []
                offset = 0
                while True:
                    q = urllib.parse.urlencode({"format": "json", "rows": str(PAGE),
                                                "os": str(offset), "project_id": pid})
                    r = get(f"{NOTICES}?{q}", timeout=90)
                    j = r.json()
                    batch = j.get("procnotices") or []
                    log.record("notices", NOTICES, q, len(batch), sha256_bytes(r.content))
                    records.extend(batch)
                    pages.append(len(batch))
                    offset += PAGE
                    if len(batch) < PAGE or offset > 20000:
                        break
                    time.sleep(delay)
                blob = json.dumps(records, ensure_ascii=False, sort_keys=True,
                                  indent=0).encode()
                tmp = path + ".part"
                open(tmp, "wb").write(blob)
                os.replace(tmp, path)
                time.sleep(delay)
        except Exception as exc:                       # noqa: BLE001
            failures.append((pid, str(exc)))
            continue
        for rec in records:
            desc = _clean_space(rec.get("bid_description") or "")
            rows.append({
                "notice_id": str(rec.get("id") or ""),
                "project_id": rec.get("project_id") or pid,
                "notice_type": rec.get("notice_type") or "",
                "publication_date": _iso_date(rec.get("noticedate")),
                "deadline_date": _iso_date(rec.get("submission_deadline_date")),
                "bid_description": desc,
                "bid_description_sha256": desc_sha256(desc),
                "description_lang": detect_lang(desc),
                "is_placeholder": str(is_placeholder(desc)).lower(),
                "procurement_category": norm_category(rec.get("procurement_group")),
                "country_code": rec.get("country_code") or rec.get("contact_ctry_code") or "",
                "country_name": rec.get("project_ctry_name") or "",
                "sector": _join_sector(rec.get("sector")),
                "url": rec.get("url") or "",
            })
    return rows, failures


def collect_awards(projects, data, log, refresh, delay):
    raw_dir = os.path.join(data, "raw", "awards")
    os.makedirs(raw_dir, exist_ok=True)
    rows, failures = [], []
    for proj in projects:
        pid = proj["project_id"]
        path = os.path.join(raw_dir, f"{pid}.json")
        records = None
        try:
            if os.path.exists(path) and not refresh:
                records = json.load(open(path, encoding="utf-8"))
            else:
                records = []
                offset = 0
                while True:
                    q = urllib.parse.urlencode({"format": "json", "rows": str(PAGE),
                                                "os": str(offset), "projectid": pid})
                    r = get(f"{AWARDS}?{q}", timeout=90)
                    j = r.json()
                    batch = j.get("contract") or []
                    log.record("awards", AWARDS, q, len(batch), sha256_bytes(r.content))
                    records.extend(batch)
                    offset += PAGE
                    if len(batch) < PAGE or offset > 20000:
                        break
                    time.sleep(delay)
                blob = json.dumps(records, ensure_ascii=False, sort_keys=True,
                                  indent=0).encode()
                tmp = path + ".part"
                open(tmp, "wb").write(blob)
                os.replace(tmp, path)
                time.sleep(delay)
        except Exception as exc:                       # noqa: BLE001
            failures.append((pid, str(exc)))
            continue
        for rec in records:
            desc = _clean_space(rec.get("contr_desc") or "")
            supp = rec.get("suppinfo") or []
            if isinstance(supp, dict):
                supp = [supp]
            names = [s.get("name") for s in supp if isinstance(s, dict) and s.get("name")]
            countries = [s.get("countryname") or s.get("countryshortname")
                         for s in supp if isinstance(s, dict)]
            countries = [c for c in countries if c]
            amounts = []
            for s in supp:
                if not isinstance(s, dict):
                    continue
                try:
                    amounts.append(float(s.get("supplier_contr_amount") or 0))
                except (TypeError, ValueError):
                    pass
            rows.append({
                "contract_id": str(rec.get("contr_id") or ""),
                "project_id": rec.get("projectid") or pid,
                "borrower_ref": (rec.get("contr_refnum") or "").strip(),
                "description": desc,
                "description_sha256": desc_sha256(desc),
                "description_lang": detect_lang(desc),
                "is_placeholder": str(is_placeholder(desc)).lower(),
                "signed_date": _iso_date(rec.get("contr_sgn_date")),
                "no_objection_date": _iso_date(rec.get("contr_no_obj_dat")),
                "total_amount": _num(rec.get("total_contr_amnt")),
                "currency": "USD",
                "procurement_group": norm_category(rec.get("procurement_group")),
                "method": norm_method(rec.get("procu_meth_text") or ""),
                "review_type": (rec.get("rvw_type") or "").strip().lower(),
                "supplier_name": "|".join(dict.fromkeys(n for n in names if n)),
                "supplier_country": "|".join(dict.fromkeys(countries)),
                "supplier_amount": "" if not amounts else f"{sum(amounts):.2f}",
                "region": rec.get("regionname") or "",
                "sector": _join_sector(rec.get("sector")),
            })
    return rows, failures


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


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def _iso_date(v):
    """The interfaces mix ISO timestamps and '14-Sep-2026'."""
    s = str(v or "").strip()
    if not s:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s)
    if m and m.group(2).title() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(2).title()]:02d}-{int(m.group(1)):02d}"
    return s


# -------------------------------------------------------------------- the diff

def package_changes(rows):
    """What moved between consecutive plan versions, per package.

    Keyed on the normalised borrower reference where the reference is present,
    and on a hash of the normalised description where it is not. A package with
    neither cannot be followed across versions; those are counted in the report
    rather than guessed at, because a wrong key silently invents a change.
    """
    by_plan = {}
    for r in rows:
        by_plan.setdefault((r["project_id"], r["plan_version"]), []).append(r)
    # plan order: oldest disclosure date first, tie-broken by doc id
    order = sorted(by_plan, key=lambda k: (by_plan[k][0]["plan_disclosure_date"], k[1]))
    changes, unkeyable = [], 0
    seen = {}
    for project, version in order:
        packages = by_plan[(project, version)]
        stamp = packages[0]["fetched_at"]
        current = {}
        for r in packages:
            ref_norm = norm_ref(r["borrower_ref"])
            if ref_norm:
                key, basis = ref_norm, "borrower_ref_norm"
            else:
                key, basis = "d:" + desc_sha256(split_markers(r["description"])["description"]), \
                    "description_sha256"
                unkeyable += 1
            current[key] = (r, basis)
        for key, (r, basis) in current.items():
            prev = seen.get(key)
            if prev is None:
                changes.append(_change(project, key, basis, r["borrower_ref"], "appeared",
                                       "", "", "", "", version, stamp))
            else:
                pr, pv = prev
                for field in ("status", "estimated_amount", "description", "planned_date",
                              "revised_date", "method", "category"):
                    if str(pr.get(field, "")) != str(r.get(field, "")):
                        changes.append(_change(project, key, basis, r["borrower_ref"],
                                               "changed", field, pr.get(field, ""),
                                               r.get(field, ""), pv, version, stamp))
        for key, (pr, basis) in seen.items():
            if key not in current and pr["project_id"] == project:
                changes.append(_change(project, key, basis, pr["borrower_ref"],
                                       "disappeared", "", "", "", pr["plan_version"],
                                       version, stamp))
        seen.update(current)
    # packages never seen twice contribute no change rows
    return changes, unkeyable


def _change(project, key, basis, ref, change, field, frm, to, from_v, to_v, stamp):
    return {"project_id": project, "key": key, "key_basis": basis, "borrower_ref": ref,
            "change": change, "field": field, "from_value": frm, "to_value": to,
            "from_plan_version": from_v, "to_plan_version": to_v, "detected_at": stamp}


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=os.path.join(ROOT, "inputs/config/cohort.csv"))
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "meta"))
    ap.add_argument("--limit", type=int, default=0, help="first N projects (smoke run)")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--workers", type=int, default=6,
                    help="concurrent plan rendition downloads")
    ap.add_argument("--refresh", action="store_true",
                    help="re-download even where a raw file exists")
    ap.add_argument("--skip-plans", action="store_true")
    args = ap.parse_args()

    projects = read_cohort(args.cohort)
    if args.limit:
        projects = projects[:args.limit]
    proc_dir = os.path.join(args.data, PROC_DIR)
    os.makedirs(proc_dir, exist_ok=True)

    log = FetchLog(os.path.join(args.meta, "api_fetch_log.csv"))
    t0 = time.time()

    plan_rows, listing_fail, no_plan, plan_fail, free_chars = [], [], [], [], []
    bundle_docs = []
    if not args.skip_plans:
        plan_rows, listing_fail, no_plan, plan_fail, free_chars, bundle_docs = (
            collect_plans(projects, args.data, log, args.refresh, args.delay,
                          workers=args.workers))

    # Prepare at output, not at input: nothing is dropped for looking irrelevant.
    seen_hash = {}
    prep, wrap_seams_total, planned_only = [], 0, 0
    for r in plan_rows:
        desc = r["description"]
        lang = detect_lang(desc)
        row = dict(r)
        row["description_sha256"] = desc_sha256(desc)
        row["description_lang"] = lang
        row["is_placeholder"] = str(is_placeholder(desc)).lower()
        row["category"] = norm_category(r["category_raw"])
        row["method"] = norm_method(r["method_raw"], r["market_approach"])
        row["status"] = norm_status(r["status_raw"])
        row["planned_date"] = r["planned_date"]
        row["revised_date"] = r["revised_date"]
        row.pop("_wrap_seams", None)
        row.pop("_dates", None)
        wrap_seams_total += r.get("_wrap_seams", 0)
        if not seen_hash.get(r["plan_doc_id"]):
            seen_hash[r["plan_doc_id"]] = r["content_sha256"]
        prep.append(row)

    notices, notice_fail = collect_notices(projects, args.data, log, args.refresh, args.delay)
    awards, award_fail = collect_awards(projects, args.data, log, args.refresh, args.delay)

    changes, unkeyable = package_changes(prep)

    write_csv(os.path.join(proc_dir, "packages_raw.csv"), PACKAGE_COLS, prep)
    write_csv(os.path.join(proc_dir, "notices_raw.csv"), NOTICE_COLS, notices)
    write_csv(os.path.join(proc_dir, "awards_raw.csv"), AWARD_COLS, awards)
    write_csv(os.path.join(proc_dir, "package_changes.csv"), CHANGE_COLS, changes)
    log.write()

    distinct_versions = len({(r["project_id"], r["plan_version"]) for r in prep})
    distinct_hashes = len({r["content_sha256"] for r in prep})
    by_project = {}
    for r in prep:
        by_project.setdefault(r["project_id"], set()).add(r["plan_version"])

    report = os.path.join(proc_dir, "fetch_procurement_report.txt")
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
        fh.write(f"package_changes rows: {len(changes):,}\n")
        fh.write(f"packages with no usable key: {unkeyable:,}\n")
        fh.write(f"cell fragments glued (no space inserted, never guessed): "
         f"{wrap_seams_total:,}\n")
        fh.write(f"preamble characters outside the plan tables: {sum(free_chars):,}\n")
        fh.write(f"bundled renditions cut to this project ({len(bundle_docs)}): "
                 f"{', '.join(bundle_docs)}\n")
        fh.write(f"projects returning no procurement plan ({len(no_plan)}): "
                 f"{', '.join(no_plan)}\n")
        parsed_pids = {r["project_id"] for r in plan_rows}
        zero_rows = sorted({p["project_id"] for p in projects}
                           - parsed_pids - set(no_plan))
        fh.write(f"projects whose parse yielded no package row ({len(zero_rows)}): "
                 f"{', '.join(zero_rows)}\n")
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
        ch = {}
        for c in changes:
            ch[c["change"] + "/" + (c["field"] or "-")] = \
                ch.get(c["change"] + "/" + (c["field"] or "-"), 0) + 1
        fh.write("\nchanges by kind:\n")
        for k in sorted(ch, key=lambda x: -ch[x]):
            fh.write(f"  {k:<28}{ch[k]:>8}\n")

    print(f"\nplan documents: {len(seen_hash)}  packages: {len(prep):,}  "
          f"notices: {len(notices):,}  awards: {len(awards):,}  "
          f"changes: {len(changes):,}")
    print(f"wrote {proc_dir}/{{packages_raw,notices_raw,awards_raw,package_changes}}.csv")
    print(f"wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
