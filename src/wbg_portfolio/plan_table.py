#!/usr/bin/env python3
"""Find the STEP tables in a procurement plan rendition and read their columns.

The generated part of a plan is always the same five sections (WORKS, GOODS, NON
CONSULTING SERVICES, CONSULTING FIRMS, INDIVIDUAL CONSULTANTS) with fixed English
column headings, so the tables are located exactly and the borrower's narrative
around them is ignored. A rendition either keeps column gutters (fetch_procurement
reads those rows by their closed value sets) or is collapsed, one cell per line
(read here, as a token stream). The vocabularies are in procurement_vocab.json.
"""
import json
import os
import re

# The procurement vocabularies (section names, column catalogues, status, method, category,
# language and placeholder words) are data, kept beside the code in procurement_vocab.json;
# clean_procurement reads them from VOCAB too.
VOCAB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement_vocab.json")
with open(VOCAB_FILE, encoding="utf-8") as _fh:
    VOCAB = json.load(_fh)

SECTION_NAMES = tuple(VOCAB["plan_sections"])
SECTION_RE = re.compile(
    r"^\s*(" + "|".join(n.replace(" ", r"[- ]") for n in SECTION_NAMES) + r")\s*$",
    re.I)
ANCHOR_RE = re.compile(r"Activity Reference No")

# A borrower reference, the one grammar the plan reader uses everywhere: an agency prefix,
# then up to seven fields joined by one or two dashes.
REF_SEP = r"\s?[-\u2013\u2014]{1,2}\s?"
REF_FIELD = rf"(?:[A-Z0-9]+ [A-Z0-9]+(?={REF_SEP})|[A-Z0-9]+(?:\.[0-9]+)*|\([0-9.]+\)?)"
REF_TOKEN = rf"[A-Z][A-Z0-9]{{1,}}(?:{REF_SEP}{REF_FIELD}){{1,7}}"

# A data row begins where the first column holds a borrower reference.
ROW_START_RE = re.compile(rf"^\s*({REF_TOKEN})\s*(/|$)")

# The STEP columns, in print order, per section.
_BASE, _GOODS_TAIL, _CONSULTING_TAIL, _MONEY = (
    [tuple(c) for c in VOCAB["plan_columns"][k]]
    for k in ("base", "goods_tail", "consulting_tail", "money"))
# The first milestone heading of each section.
_MILESTONES = VOCAB["plan_milestones"]


def catalogue(section):
    """The ordered column catalogue for a section: a superset, not a schema."""
    section = section.upper()
    tail = _CONSULTING_TAIL if section in ("CONSULTING FIRMS", "INDIVIDUAL CONSULTANTS") \
        else _GOODS_TAIL + _CONSULTING_TAIL
    milestones = [(f"_milestone_{i}", m)
                  for i, m in enumerate(_MILESTONES.get(section, []))]
    return _BASE + tail + _MONEY + milestones


class Table:
    """One section's table in one rendition: its heading band and its rows."""

    def __init__(self, section, start, header, data):
        self.section = section
        self.start = start
        self.header = header
        self.data = data

    def __repr__(self):
        return (f"<Table {self.section} @{self.start} "
                f"header={len(self.header)} data={len(self.data)}>")


HEADER_MAX_LINES = 22    # a section's heading band is at most this many lines
ANCHOR_WITHIN = 3        # the column heading follows a section name within this many lines
WIDE_HEADER_GAPS = 3     # a heading line with this many column gaps proves the columns survived
LABEL_MIN_PREFIX = 5     # characters of a column label that locate it in the heading band
ALIGNED_FLOOR = 0.8      # share of collapsed records carrying a figure for their columns to be trusted
REF_CHAIN_MAX = 3        # segments of a 'REF / REF / description' chain read as references


def find_tables(lines, max_header=HEADER_MAX_LINES):
    """Every STEP table in the rendition, in document order."""
    tables = []
    heads = [i for i, l in enumerate(lines)
             if SECTION_RE.match(l)
             and any(ANCHOR_RE.search(x) for x in lines[i + 1:i + 1 + ANCHOR_WITHIN])]
    for n, i in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        # The heading band runs to the first data row; everything after it, up to the next
        # heading, is rows.
        split = None
        for j in range(i + 1, min(i + max_header, end)):
            if ROW_START_RE.match(lines[j]):
                split = j
                break
        if split is None:
            split = min(i + max_header, end)
        section = re.sub(r"\s+", " ", SECTION_RE.match(lines[i]).group(1).upper())
        section = section.replace("NON-CONSULTING", "NON CONSULTING")
        tables.append(Table(section, i, lines[i:split], lines[split:end]))
    return tables


def is_collapsed(table):
    """Has this rendition thrown the column positions away?"""
    wide = sum(1 for l in table.header if len(re.findall(r"\S {2,}\S", l)) >= WIDE_HEADER_GAPS)
    return wide < 1


def _label_positions(header, label, min_prefix=LABEL_MIN_PREFIX):
    """Every x at which `label` might begin in the heading band."""
    for n in range(len(label), min_prefix - 1, -1):
        frag = label[:n]
        hits = []
        for line in header:
            k = line.find(frag)
            while k >= 0:
                hits.append((k, n))
                k = line.find(frag, k + 1)
        if hits:
            return sorted(set(hits))
    return []


def solve_columns(header, section):
    """Assign catalogue columns to x positions, left to right."""
    cols = catalogue(section)
    candidates = [(field, _label_positions(header, label)) for field, label in cols]
    best = {}          # x of last placed column -> (placed, matched_chars, [(field, x)])
    best[-1] = (0, 0, [])
    for field, positions in candidates:
        nxt = dict(best)
        for last_x, (placed, chars, chosen) in best.items():
            for x, n in positions:
                if x <= last_x:
                    continue
                cand = (placed + 1, chars + n, chosen + [(field, x)])
                if x not in nxt or cand[:2] > nxt[x][:2]:
                    nxt[x] = cand
        best = nxt
    placed, chars, chosen = max(best.values(), key=lambda v: (v[0], v[1]))
    return chosen


def has_column(table, label):
    """Does this table print the column headed `label`?"""
    return bool(_label_positions(["\n".join(table.header)], label))


ESTIMATED_LABEL = "Estimated Amount (US$)"


def present_columns(table):
    """Which of the section's catalogue columns this table actually prints."""
    if is_collapsed(table):
        # No meaningful x to order by - every heading sits at the left margin on its own
        # line - so presence is all that can be asked.
        band = ["\n".join(table.header)]
        found = [field for field, label in catalogue(table.section)
                 if _label_positions(band, label)]
    else:
        # Ordered, because order is what resolves an ambiguous heading: "Contract Type" and
        # "Contract Completion" share nine characters, and a bare presence test reports a
        # Contract Type column in a GOODS table that only has the Contract Completion
        # milestone.
        found = [field for field, _ in solve_columns(table.header, table.section)]
    return [f for f in found if not f.startswith("_milestone")]


def table_span(table):
    """The [start, end) line range this table occupies in the rendition."""
    return table.start, table.start + len(table.header) + len(table.data)


# A collapsed rendition has no column positions, so the cells have to be told apart by what
# they contain.
LOAN_WORDS = r"(?:IDA|IBRD|TF|GRANT|CREDIT|DON|PRET|COFN)"
LOAN_RE = re.compile(rf"^\s*{LOAN_WORDS}\s*/\s*\S", re.I)
AMOUNT_TOKEN_RE = re.compile(r"[\d,]+\.\d{2}")
LOAN_INLINE = re.compile(rf"\b{LOAN_WORDS}\s*/\s*[A-Z]?\s?\d[\dA-Z-]*")
# The first cell after the description in a collapsed row: the review type, the method or
# the market approach, in the languages the plans use.
CELL_OPEN = re.compile(
    r"^(?:Post|Prior|A posteriori|A priori)$|^(?:Post|Prior|A posteriori|A priori)\s+"
    r"(?=Request|Direct|Quality|Consultant|Least|Individual|S[eé]lection|Demande|"
    r"Appel|Entente|Passation|Individuel|Open|Limited)"
    r"|^(?:Request for (?:Bids|Quota|Propos)|Direct Selection|Quality And Cost|"
    r"Quality Based|Consultant Qualification|Least Cost Selection|"
    r"Individual Consult|Single Source Selection|Framework Agreement|"
    r"S[eé]lection fond|S[eé]lection au moindre|Demande de prix|Appel d'offres|"
    r"Entente directe|Passation de march|Consultant individuel|Individuel$|"
    r"(?:Open|Limited|Direct)\s*[-/]\s*(?:National|Internationa)|Single Stage)", re.I)
DATE_TOKEN_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


# Lines belonging to a column-heading band that the rendition reprints at every page break.
HEADER_NOISE_RE = re.compile(
    r"^\s*(Activity Reference No|Loan / Credit|Component\s*$|Review Type|Method\s*$|"
    r"Market Approac|Procurement Pro|Procureme\b|Prequalification|High SEA/SH|"
    r"Estimated Am|Actual Am|Process St|Draft Pre-qualification|Draft Bidding|"
    r"Specific Procurement|Bidding Documents|Proposal Submission|Bid Evaluation|"
    r"Signed Contract\s*$|Contract Complet|Terms of Reference\s*$|Expression of Interest|"
    r"Short List|Invitation to Identifie|Draft Negotiated|"
    r"(Planned|Actual)(\s+(Planned|Actual))*\s*$|Page \d+\s*$|"
    r"nt Docume|ount \(US\$|atus\s*$|cess\s*$|\(Y/N\)\s*$|isk\s*$|ation R)",
    re.I)


def _atoms(band):
    """A record's lines as a stream of typed tokens, in print order."""
    split = re.compile(r"([\d,]+\.\d{2}|\d{4}-\d{2}-\d{2})")
    for line in band:
        if not line.strip():
            continue
        for piece in split.split(line):
            piece = piece.strip()
            if not piece:
                continue
            if AMOUNT_TOKEN_RE.fullmatch(piece):
                yield "amount", piece
            elif DATE_TOKEN_RE.fullmatch(piece):
                yield "date", piece
            else:
                yield "text", piece


def read_collapsed_table(table, has_estimated=None):
    """Rows out of a rendition that threw its column positions away."""
    if has_estimated is None:
        # Presence, not position. solve_columns orders the headings by x and a collapsed
        # band has no meaningful x - every label sits at the left margin on its own line -
        # so the increasing-x constraint there rejects almost every column and would report
        # the Estimated Amount heading missing on a table that prints it.
        band = "\n".join(table.header)
        has_estimated = bool(_label_positions([band], "Estimated Amount (US$)"))

    bands, cur = [], None
    for line in table.data:
        if ROW_START_RE.match(line):
            if cur:
                bands.append(cur)
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur:
        bands.append(cur)

    rows = []
    for raw_band in bands:
        # A heading band reprinted inside a record means the page turned there, which is
        # where collapsed columns come apart: such a record gives up its figures.
        interleaved = any(HEADER_NOISE_RE.match(l) for l in raw_band)
        band = [l for l in raw_band if not HEADER_NOISE_RE.match(l)]
        stream = list(_atoms(band))
        amounts = [i for i, (k, _) in enumerate(stream) if k == "amount"]
        row = {"section": table.section, "estimated_amount": "", "actual_amount": "",
               "status_raw": "", "loan": "", "dates": [], "block": "\n".join(band)}
        row["interleaved"] = interleaved
        if amounts and not interleaved:
            values = [stream[i][1] for i in amounts[:2]]
            if has_estimated and len(values) >= 2:
                row["estimated_amount"], row["actual_amount"] = values
            elif has_estimated:
                row["estimated_amount"] = values[0]
            else:
                row["actual_amount"] = values[0]
            # The status is the text between the last amount and the first date after it.
            for kind, text in stream[amounts[-1] + 1:]:
                if kind == "date":
                    break
                if kind == "text" and text:
                    row["status_raw"] = text
                    break
        row["dates"] = [] if interleaved else [t for k, t in stream if k == "date"]
        loan = [t for k, t in stream if k == "text" and LOAN_RE.match(t)]
        row["loan"] = loan[0] if loan else ""
        # The description runs from the reference to the first structural cell.
        pieces = []
        for kind, text in stream:
            if kind != "text":
                break
            for j, part in enumerate(LOAN_INLINE.split(text)):
                if j:
                    pieces.append(None)            # where a loan number stood
                if part.strip():
                    pieces.append(part.strip())
        desc, cells, in_desc = [], [], True
        for i, piece in enumerate(pieces):
            if piece is None:
                if in_desc:
                    nxt = next((p for p in pieces[i + 1:] if p), "")
                    in_desc = nxt[:1].islower()
                continue
            if in_desc and CELL_OPEN.match(piece):
                in_desc = False
            (desc if in_desc else cells).append(piece)
        row["cells"] = " ".join(cells)
        row["description"] = " ".join(desc)
        rows.append(row)
    return rows


def collapsed_is_aligned(rows, floor=ALIGNED_FLOOR):
    """Did the rendition keep its rows together, or scatter the columns?"""
    if not rows:
        return True
    with_money = sum(1 for r in rows if r["estimated_amount"] or r["actual_amount"])
    return with_money / len(rows) >= floor


def split_ref_chain(desc, fallback):
    """Recover the package reference from the joined first column."""
    parts = desc.split(" / ")
    ref, i = fallback, 0
    while i < len(parts) and i < REF_CHAIN_MAX:
        tok = parts[i].strip()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9\u2013\u2014-]{1,40}", tok):
            break
        if tok.isdigit():
            break               # a year or a number ('.../2023 / ...') is not a reference
        if re.search(r"\d", tok):
            ref = tok
        elif i > 0:
            break
        i += 1
    rest = " / ".join(parts[i:]).strip()
    return ref, (rest or desc)


def chain_ref(ref):
    """The package reference inside a printed reference chain, by the same rule
    as split_ref_chain: 'BJ-UCP / PADA-43641-GO-RFQ' gives 'PADA-43641-GO-RFQ'.
    Notices and awards print the chain the plan's reference was cut from."""
    return split_ref_chain(ref or "", ref or "")[0]

