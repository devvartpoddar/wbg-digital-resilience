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
import argparse, csv, hashlib, json, os, re, sys, threading, time, unicodedata, urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan_table                                     # noqa: E402
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
# Does a rendition's text look like it carries borrower references at all? Used
# only to tell an empty package table apart from a parse failure.
PLAN_REF_SHAPE = re.compile(r"\b[A-Z]{2,}[-\u2013][A-Z0-9]{1,12}[-\u2013][0-9]{3,}")
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
# The closed value sets STEP prints in the Method, Market Approach and Process
# Status cells. Longest first, because _first_token returns the first that
# matches and 'Signed' is a prefix of nothing but 'Under' is a prefix of three.
#
# These lists were English-only until they were checked against the corpus, and
# that was a silent, large defect: a francophone borrower prints 'Achevé', not
# 'Completed', so every one of its packages came back with no status and no
# method. Niger read 5% populated and Mozambique 3%, and both were read as
# layout failures when the layout was fine and the vocabulary was not.
#
# The values below are not translations guessed at a desk. They were mined from
# the 496 renditions without assuming any vocabulary at all - the Process Status
# cell is whatever sits between the Actual Amount and the first milestone date,
# so a regex over that gap enumerates the set - and the whole corpus yields 13
# distinct status strings. Adding a borrower in a new language means re-running
# that mining step, not inventing words.
METHOD_TOKENS = ("Request for Bids", "Request for Quotations", "Direct Selection",
                 "Direct Contracting", "Consultant Qualification Selection",
                 "Consultant Qualification  Selection", "Quality And Cost Based Selection",
                 "Quality and Cost Based Selection", "Quality And Cost-Based Selection",
                 "Least Cost Selection", "Individual Consultant Selection",
                 "Single Source Selection", "Framework Agreement", "Competitive Dialogue",
                 # French
                 "Selection fondee sur les qualifications des consultants",
                 "Selection fondee sur la qualite et le cout",
                 "Selection au moindre cout", "Passation de marche de gre a gre",
                 "Entente directe", "Demande de prix", "Appel d'offres",
                 "Consultant individuel", "Individuel")
APPROACH_TOKENS = ("Open - International", "Open - National", "Limited - International",
                   "Limited - National", "Direct - International", "Direct - National",
                   "Open - international", "Open - national", "Open / National",
                   "Limited", "Direct", "Open")
STATUS_TOKENS = ("Pending Implementation", "Under Implementation", "Under Preparation",
                 "Under Evaluation", "Under Review", "Not Started", "In Progress",
                 "Contract Completed", "Contract Signed", "Terminated", "Completed",
                 "Cancelled", "Canceled", "Advertised", "Signed", "Planned", "Pending",
                 # French
                 "En attente d'execution", "En cours d'execution", "En cours d'examen",
                 "En cours de preparation", "En cours d'evaluation",
                 "Acheve", "Annule", "Resilie", "Signe", "Planifie",
                 # Portuguese
                 "Em execucao", "Em preparacao", "Concluido", "Cancelado", "Assinado")

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
        if out and out[-1].isalnum() and p[0].isalnum():
            # Two alphanumerics run together with nothing between them, which is
            # the only seam where a space could have been lost. Counted, not
            # repaired: see the docstring.
            seams += 1
        out += p
    return out, seams


# Some renditions clip the column edge INSIDE the reference itself, so the last
# segment of the reference sits at the start of the next line and NO line carries
# 'reference /'. REF_RE then matches nothing in the whole document, every line is
# treated as preamble, and the document parses to zero package rows while looking
# perfectly healthy in the report - 218 of the 2,463 cached renditions did that.
# Both halves are reference-shaped, so this is a shape test, not a guess at
# wording. Three shapes are in the corpus:
#
#   ' PE-PRONATEL-196394-CW-'          the reference alone on its line
#   'RFB / Elaboración del'
#
#   'SO-MOCT-FGS-358521-CS-IN       Component 4. Project Manag  ...'
#   'DV / PIU Project Coordinator    Component 4. Project Manag  ...'
#   the reference line also carries that row's other columns
#
#   'DM-MPWDE-220449-CS-QCB'           the clip fell inside the token, so the
#   'S / Upgrades to the establish'    join needs no separator at all
#
# The digit requirement keeps this from firing on a heading: an all-caps section
# line like 'NON-CONSULTING SERVICES' is the same shape, and every borrower
# reference in this corpus carries a number. The second guard is stronger: the
# merged line has to be a record boundary REF_RE accepts, so a merge that would
# produce something that is not a reference at all is not made.
REF_WRAP_HEAD = re.compile(
    r"^\s*([A-Z][A-Z0-9]{1,}(?:[-\u2013\u2014]\s?[A-Z0-9]+){1,5}[-\u2013\u2014]?)"
    r"(\s{2,}.*)?$")
REF_WRAP_TAIL = re.compile(r"^\s*([A-Z0-9][A-Z0-9\u2013\u2014-]{0,20})\s*/\s*(.*)$")

# A second, different separation, and the one that costs the most. Here the
# reference is COMPLETE on its own line and the '/ description' that belongs
# with it arrives one to four lines later, with other columns' text in between:
#
#   ' NE-PCU-SV-229733-GO-RFQ'
#   '                    4. Strengthening project ma'      <- a different column
#   "/ Fourniture et installation d'        Single Stage - One E"
#
# In a wide-layout rendition the columns wrap independently and the extractor
# emits them interleaved by vertical position, so the reference's own line ends
# before the slash is reached. REF_RE wants both on one line and matches
# neither, and the record is lost.
#
# Measured on the biggest plan of each of eight countries: Mozambique parses 3
# references normally against 47 orphaned this way, Niger 128 against 61,
# Uganda 153 against 21. Tanzania, Nigeria and Kenya have none - their
# renditions keep the slash on the reference's line.
# Alone in its COLUMN, not necessarily alone on its line: a fixed-width
# rendition prints the row's other columns beside it, and requiring the whole
# line to hold nothing else misses the shape wherever the layout kept its
# columns. Those renditions are 474 of 496.
REF_ALONE = re.compile(
    r"^\s*([A-Z][A-Z0-9]{1,}(?:[-\u2013\u2014]\s?[A-Z0-9]+){2,5})(\s{2,}\S.*)?\s*$")
SLASH_TAIL = re.compile(r"^\s*(/\s*\S.*)$")
# The observed gap is one or two lines. Four allows some slack without letting
# the scan wander into the next record.
MAX_ORPHAN_GAP = 4


def _join_wrapped_refs(lines):
    """Put a reference that wrapped across two physical lines back on one.

    Returns a new line list; nothing else about the rendition is touched. The
    second half of the reference is glued to the first, the description that
    followed it stays where it was, and the rest of the line - the row's other
    columns, which carry the method, the status and the dates - is kept.

        [' PE-PRONATEL-196394-CW-',      ->  ['PE-PRONATEL-196394-CW-RFB / Elaboración del']
         'RFB / Elaboración del']

        ['SO-MOCT-FGS-358521-CS-IN   Component 4. ...',  ->  ['...CS-INDV / PIU Project
         'DV / PIU Project Coordinator   Component 4. ...']       Coordinator   Component 4. ...']
    """
    out, i = [], 0
    while i < len(lines):
        head = REF_WRAP_HEAD.match(lines[i])
        if head and re.search(r"\d", head.group(1)) and i + 1 < len(lines):
            tail = REF_WRAP_TAIL.match(lines[i + 1])
            # A Loan / Credit cell has the same shape as the tail of a wrapped
            # reference - 'IDA / D9060' reads as the token 'IDA' followed by a
            # slash and a description - and gluing one on destroys the package:
            # 'MZ-MJACER-423921-GO-RFQ' plus 'IDA / D9060' becomes
            # 'MZ-MJACER-423921-GO-RFQIDA / D9060', which REF_RE happily accepts
            # and which matches no package that exists. It cost 17 of them.
            #
            # This lay dormant while the join only ever ran over fixed-width
            # renditions, where the loan cell shares a line with the columns
            # either side of it and never stands alone. It fires the moment the
            # join is applied to a rendition that prints one cell per line.
            if tail and not plan_table.LOAN_RE.match(lines[i + 1]):
                # The head is re-glued with the tail's token, and everything
                # else - the description the tail carried and the columns the
                # head carried - is appended after it.
                candidate = f"{head.group(1)}{tail.group(1)} / {tail.group(2)}"
                candidate += head.group(2) or ""
                if REF_RE.match(candidate):
                    out.append(candidate)
                    i += 2
                    continue

        # The orphaned-reference shape: a complete reference alone on its line,
        # with its slash arriving a line or two later. The scan stops at the
        # next reference so it cannot reach across a record boundary, and the
        # joined line still has to be something REF_RE accepts.
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
                    # Whatever else printed on the reference's own line belongs
                    # to this row's other columns - the component, the review
                    # type - and is carried onto the joined line rather than
                    # dropped with it.
                    out.append(candidate + (alone.group(2) or ""))
                    # The lines between the two belong to other columns of this
                    # same record and are kept where they were; only the slash
                    # line is consumed, because it is now part of the join.
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


def _parse_collapsed(tables, doc_meta):
    """Rows out of a rendition that prints the table with no column positions.

    These are 22 of the corpus's 496 renditions and they used to yield nothing
    at all - the parser for the fixed-width layout splits a line at the first
    run of two spaces, and in a collapsed rendition there are none, so every
    line became the description and no metadata column was ever found. They
    matter far out of proportion to their number: the newest rendition of six
    of the eight projects is one, and the newest rendition is what the
    current-state table reads.

    A document whose columns came apart gives up its figures wholesale rather
    than risk attaching one package's amount to another's reference. It keeps
    its references and descriptions, which are still the borrower's and still
    correct, and the figures come from its earlier renditions instead.
    """
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
            ref, desc = split_ref_chain(collapse_ws(rec["description"]), "")
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
                "planned_date": dates[0] if dates else "",
                "revised_date": dates[-1] if len(dates) > 1 else "",
                "estimated_amount": ""
                    if refused else _as_amount(rec["estimated_amount"]),
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
    """Pull the STEP plan tables out of a plan's text rendition.

    Only the tables. A plan document is two things bolted together and one of
    them is not data: the front is narrative the borrower writes, sometimes with
    annex tables of its own, and the back is generated by STEP. plan_table finds
    the five STEP sections exactly - all 496 renditions of the eight-country
    corpus carry all five, with the "Activity Reference No." heading as the test
    that tells a heading from the same word in a sentence - and records are
    built from inside those bounds and nowhere else.

    This used to scan the whole document, and got away with it: no record has
    ever been built from outside a table, because REF_RE needs a borrower
    reference at the start of a line and the narrative has none. That is a
    property of eight countries' prose, not a property the code enforced, and
    70 projects is a larger sample of borrower annexes. Now it is enforced, and
    what falls outside is counted as free text rather than silently skipped.

    Each of the five tables is read with its own heading band, because they do
    not carry the same columns. The section comes from the table rather than
    from a heading matched anywhere in the document, which alone corrected the
    category of 4,003 records in 34 renditions where a bare "CONSULTING FIRMS"
    in a preamble was re-labelling every record after it.

    Within a table, the metadata columns are still located by matching their
    closed value sets across the record block rather than by character
    position - see the note in plan_table on why slicing was not adopted.
    """
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
        # Asked of THIS table's heading band. 19% of renditions print no
        # Estimated Amount column at all, and on those the figure on the row is
        # the actual - filing it as an estimate would invent a number the plan
        # never stated, on the one field that is carried forward.
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
    """Split one table's rows into records.

    A record opens on the line whose first column holds a borrower reference and
    runs to the next one, because that reference is the first thing STEP prints
    in the first column of every row of every section. Column zero - the
    reference and the description - is taken exactly; everything to the right of
    the first gap is the record's body, where the metadata cells are matched.
    """
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
    ref, desc = split_ref_chain(desc, rec["ref"])
    body = " ".join(rec["body"])
    numbers = DATE_RE.findall(body)
    amount = None
    if has_estimated:
        for tok in re.split(r" {2,}|\s{3,}", body):
            if AMOUNT_RE.match(tok.strip()):
                try:
                    amount = float(tok.replace(",", ""))
                except ValueError:
                    continue
                break
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
        "status_raw": _first_token(body, STATUS_TOKENS),
        "planned_date": numbers[0] if numbers else "",
        "revised_date": numbers[-1] if len(numbers) > 1 else "",
        "estimated_amount": "" if amount is None else f"{amount:.2f}",
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


def _clip_tail(cell):
    """A fragment's tail may be the next column: 'Supply of four f' + loan id."""
    return LENDER_RE.sub("", cell)


def _match_form(value):
    """Fold a cell to the form the closed value sets are written in.

    Three foldings, each answering a way the rendition disguises a known value:
    accents, because the token lists are ASCII and the plans are not; the
    curly apostrophe, because STEP prints both; and case.

    Returns (folded, despaced). The despaced form exists because a cell that is
    too wide for its column wraps mid-word, and the two halves reach this
    function with a gap between them - 'Under Implementati' and 'on' is 9,036
    rows of the corpus. Removing every space from both the text and the token
    turns that back into a match, and it is safe for the same reason the
    procurement match_key is safe: wrapping only ever inserts a gap, never a
    character, so despacing both sides cannot make two different values equal
    unless they differed by spacing alone.
    """
    folded = unicodedata.normalize("NFKD", value or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.replace("\u2019", "'").replace("\u02bc", "'").casefold()
    return folded, re.sub(r"\s+", "", folded)


def _first_token(text, tokens):
    """The first of `tokens` that appears in `text`, or ''.

    Two passes, and never one: an exact match on the folded text is tried for
    every token before any despaced match is considered. Doing it token by token
    instead would let a despaced match on a short token beat an exact match on a
    longer one further down the list, which is how 'Signed' wins over 'Contract
    Signed'.
    """
    ftext, dtext = _match_form(text)
    for t in tokens:
        ft, _ = _match_form(t)
        if re.search(r"(?<!\w)" + re.escape(ft) + r"(?!\w)", ftext):
            return t
    for t in tokens:
        _, dt = _match_form(t)
        if len(dt) >= 6 and dt in dtext:
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
    empty_docs = []
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
        if not parsed:
            # A rendition that parsed to nothing is either a plan whose package
            # table is genuinely empty (common: 648 of them carry no
            # reference-shaped token anywhere) or a parse failure. The two must
            # not be the same number in the report, or a silent partial reads as
            # an empty plan.
            empty_docs.append((d["doc_id"], pid,
                               bool(PLAN_REF_SHAPE.search(text))))
        for r in parsed:
            r["package_id"] = r["borrower_ref"]
            r["package_version_id"] = f"{d['doc_id']}:{r['borrower_ref']}"
            rows.append(r)

    print(f"  parsed {len(rows):,} package rows from {len(blobs):,} renditions",
          flush=True)
    return (rows, listing_failures, no_plan, fetch_failures, free_text_chars,
            bundle_docs, empty_docs)


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
    # Keyed by (project, key), not by key alone. Generic references recur across
    # projects - 'CS-INDV', 'GO-RFB', 'CS-QCBS' each appear under three - so a
    # key-only dict compares one project's package against another's and reports
    # changes that never happened.
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
            prev = seen.get((project, key))
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
        for (sp, key), (pr, basis) in list(seen.items()):
            if sp == project and key not in current:
                changes.append(_change(project, key, basis, pr["borrower_ref"],
                                       "disappeared", "", "", "", pr["plan_version"],
                                       version, stamp))
        seen.update({(project, k): v for k, v in current.items()})
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
    bundle_docs, empty_docs = [], []
    if not args.skip_plans:
        (plan_rows, listing_fail, no_plan, plan_fail, free_chars, bundle_docs,
         empty_docs) = collect_plans(projects, args.data, log, args.refresh,
                                     args.delay, workers=args.workers)

    # Prepare at output, not at input: nothing is dropped for looking irrelevant.
    seen_hash = {}
    prep, wrap_seams_total, planned_only = [], 0, 0
    section_columns = Counter()
    no_estimated_column = 0
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
        row.pop("_layout", None)
        section_columns[(r["section"], row.pop("_columns", ""))] += 1
        no_estimated_column += 1 if row.pop("_no_est", False) else 0
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

        # Which columns each of the five STEP sections actually printed, and in
        # how many rows. They do not carry the same set - the consulting
        # sections have a Contract Type where goods and works have a
        # Prequalification - and one difference changes a value rather than the
        # layout: a table with no Estimated Amount column prints the ACTUAL as
        # its only figure. This table is how that is checked from a run's
        # output rather than by reading the parser.
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
        # Counted from the parser's own decision, not inferred from the column
        # list above: that list is what could be ORDERED out of the heading
        # band, and a band truncated by an immediate first data row yields less
        # than the band contains. The Estimated Amount heading has no
        # confusable sibling, so its presence is asked directly.
        fh.write(f"  rows from a table printing NO Estimated Amount column: "
                 f"{no_estimated_column:,} "
                 f"({100 * no_estimated_column / max(1, len(prep)):.1f}%) - "
                 f"their estimated_amount is left blank, never filled from the "
                 f"actual\n")
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
        # The per-DOCUMENT view, which the project-level line above hides: a
        # project with one readable plan and four unreadable ones looks fine.
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
