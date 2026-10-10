#!/usr/bin/env python3
"""Collect each project's components, and tag paragraphs with the component
they belong to.

Reads  data/raw/documents.csv, data/appraisal/paragraphs.csv, data/appraisal/text/{doc_id}.txt,
       data/raw/pdf/{doc_id}.pdf (only the pages that hold a component table)
Writes data/appraisal/components.csv  one row per component or sub-component,
                                    per document that states it
       data/reports/components.txt   coverage, per project

A component is how an appraisal document divides what a project finances, and
the procurement plan files every package under one. Knowing the components is
what lets a paragraph about "Component 2" be read against the packages filed
under Component 2.

No interface publishes components as data; the World Bank projects interface
carries only project totals. So they are read from the documents, from three
places, each recorded with its source:

  datasheet      the data sheet's 'Component Name | Cost' table at the front of
                 a Project Appraisal Document. Numbered or not (the row order is
                 the number), in US$ millions or, in the 2024 template, in full
                 dollars
  restructuring  the 'Current Component Name ... Proposed Cost' table of a
                 restructuring or additional-financing paper, which revises
                 names and costs
  heading        the body's own headings, 'Component 1: Digital Ecosystem
                 (US$28.7 million equivalent)', and sub-component headings
                 '1.1 Digital Enabling Environment (US$13.4 million)'

Nothing is merged or reconciled across sources here: a later stage picks what
it needs, and a disagreement between the data sheet and the body stays visible.

The paragraph tags (`Tagger`, `mentions`) are applied by clean.py as it writes
paragraphs.csv:

  component / subcomponent   structural - the paragraph sits under that
                             component's heading, until the next component
                             heading or the next section
  component_mentions         textual - the component numbers the paragraph
                             itself names, wherever it sits

  python3 src/components.py
"""
import argparse, csv, hashlib, json, os, re, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
from keys import name_key, project_parts, project_at  # noqa: E402

COMP_WORD = r"(?:Component|Composante|Componente|Komponen)"
COMP_HEAD = re.compile(
    rf"^\s*{COMP_WORD}\s*(\d{{1,2}})\s*[:.\-–)]\s*(.+?)\s*$", re.I)
SUB_HEAD = re.compile(
    r"^\s*(?:Sub-?component|Sous-composante|Subcomponente)?\s*(\d{1,2})\.(\d{1,2})\.?"
    r"\s*[:.\-–)]?\s*([A-ZÀ-Þ].+?)\s*$", re.I)
SUB_WORD_HEAD = re.compile(
    r"^\s*(?:Sub-?component|Sous-composante|Subcomponente)\s*(\d{1,2})\.(\d{1,2})"
    r"\s*[:.\-–)]?\s*(.+?)\s*$", re.I)
COST_IN_TEXT = re.compile(
    r"\(\s*(?:US\$|USD|\$|EUR|€|SDR)\s*([\d.,]+)\s*(million|m|billion)?", re.I)
MENTION = re.compile(
    r"\b(?:Sub-?components?|Components?|Composantes?|Sous-composantes?|Componentes?|"
    r"Subcomponentes?)\s+((?:\d{1,2}(?:\.\d{1,2})?)"
    r"(?:\s*(?:,|and|&|et|e|y|or|to|-|–)\s*\d{1,2}(?:\.\d{1,2})?)*)", re.I)
RUN_IN_NUMBER = re.compile(r"^\s*\d{1,3}\.\s+")
RUN_IN_COMP = re.compile(rf"^\s*{COMP_WORD}\s*(\d{{1,2}})\s*[:.\-\u2013)]\s*[A-Z]")
def _reader_version():
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for name in ("components.py", "text_rules.py"):
        with open(os.path.join(here, name), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


READER_VERSION = _reader_version()
TAGGED_BLOCKS = {"narrative", "annex", "table", "heading", "footnote"}


def mentions(text):
    """Component numbers a paragraph names: 'Components 1 and 3',
    'Sub-component 2.1', 'Composante 2'. Sorted, unique."""
    out = set()
    for m in MENTION.finditer(text):
        out.update(re.findall(r"\d{1,2}(?:\.\d{1,2})?", m.group(1)))
    return sorted(out, key=lambda v: [int(x) for x in v.split(".")])


class Tagger:
    """Walk a document's units in order and say which component each sits
    under. A component heading opens a component, and a sub-component heading
    a sub-component inside it. Both close at the first section that is not the
    one the component opened in, or inside it: after the last component of
    'II.B Project Components', 'II.C Project Beneficiaries' belongs to none."""

    def __init__(self):
        self.comp, self.sub, self.home = "", "", None

    def _close(self):
        self.comp, self.sub, self.home = "", "", None

    def see(self, text, block, section_path):
        path = section_path or ""
        if self.home is not None and path != self.home \
                and not path.startswith(self.home + "."):
            self._close()
        if block not in TAGGED_BLOCKS:
            return "", ""
        short = len(text.split()) <= 30
        # A run-in title opening a long numbered paragraph: '31. Subcomponent
        # 2.2: Data centre. This subcomponent will finance ...'. Only with the
        # word itself, so an ordinary '1.2' numbered paragraph is not read as one.
        if not short and block in ("narrative", "annex"):
            head = RUN_IN_NUMBER.sub("", text, count=1)
            m = SUB_WORD_HEAD.match(head[:200])
            if m and (not self.comp or m.group(1) == self.comp):
                if not self.comp:
                    self.home = path
                self.comp, self.sub = m.group(1), f"{m.group(1)}.{m.group(2)}"
                return self.comp, self.sub
            m = RUN_IN_COMP.match(head)
            if m:
                self.comp, self.sub, self.home = m.group(1), "", path
                return self.comp, ""
        if block == "heading" or short:
            m = COMP_HEAD.match(text)
            if m:
                self.comp, self.sub, self.home = m.group(1), "", path
                return self.comp, ""
            # A numbered sub-component title is a heading even when set in
            # plain type, if it carries a cost: '1.2. Infrastructure ... (US$15.3
            # million equivalent)'.
            titled = block == "heading" or bool(COST_IN_TEXT.search(text))
            m = SUB_WORD_HEAD.match(text) or (SUB_HEAD.match(text) if titled else None)
            if m and (not self.comp or m.group(1) == self.comp):
                if not self.comp:
                    self.home = path
                self.comp, self.sub = m.group(1), f"{m.group(1)}.{m.group(2)}"
                return self.comp, self.sub
        return self.comp, self.sub


# ------------------------------------------------------------- extraction

# name_key (keys.name_key) is what the same component shares across
# documents and with procurement: a paragraph and a package under one
# component meet on it even when a restructuring renumbered it.
COMP_COLS = ["project_ids", "doc_id", "doc_kind", "disclosure_date", "source", "level",
             "number", "name", "name_key", "cost_usd_m", "action", "page"]
NUM = re.compile(r"^\s*\$?\s*([\d,]+(?:\.\d+)?)\s*$")
# A row that ends the component list: what the data sheet prints after it.
# The lender's name ("IDA") and the front-end fee are financing lines.
STOP_ROW = re.compile(r"^(?:implementing agency|organizations|project financing|total"
                      r"|borrower|financing|summary|front.end fee|(?:ida|ibrd)\s*$)", re.I)


def _has_name(name):
    """Does a cell hold a name at all? '+2.5 +3.3' read from the wrong column
    does not: a name has a word of three letters or more."""
    return bool(re.search(r"[A-Za-z\u00C0-\u024F]{3}", name or ""))


def _num(v):
    m = NUM.match(v or "")
    return float(m.group(1).replace(",", "")) if m else None


def _to_millions(value, header_text):
    """Data sheets state costs in US$ millions, except the 2024 template, which
    prints full dollars ('58,000,000.00'). A figure over 10,000 cannot be a
    component cost in millions, so it is read as dollars."""
    if value is None:
        return ""
    if "million" in (header_text or "").lower() and value < 10_000:
        return f"{value:.2f}"
    return f"{value / 1e6:.2f}" if value >= 10_000 else f"{value:.2f}"


def _cell(c):
    import text_rules as C
    parts = [p for p in (c or "").split("\n") if "@#&OPS" not in p]
    return re.sub(r"\s+", " ", C.fold_chars(" ".join(parts))).strip()


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}
COMP_START = re.compile(
    rf"^\s*{COMP_WORD}\s*(\d{{1,2}}|{'|'.join(NUMBER_WORDS)})\b", re.I)


def _split_number(name, order):
    """('2', 'Digital Connectivity') from 'Component 2: Digital Connectivity',
    'Component Two - Digital Connectivity' or '2. Digital Connectivity'; the
    row order when the name carries no number. A name never runs into the next
    component: the 2024 data sheet prints every name in one cell."""
    m = re.match(rf"^\s*(?:{COMP_WORD}\s*)?(\d{{1,2}}|{'|'.join(NUMBER_WORDS)})"
                 r"\s*[.:)\-–]?\s+(.+)$", name, re.I)
    if m:
        n = m.group(1).lower()
        number, rest = str(NUMBER_WORDS.get(n, n)), m.group(2).strip()
    else:
        number, rest = str(order), name
    nxt = re.search(rf"\s{COMP_WORD}\s*(?:\d{{1,2}}|{'|'.join(NUMBER_WORDS)})\b", rest, re.I)
    if nxt:
        rest = rest[:nxt.start()]
    return number, rest.strip(" -–:")


def _explode(grid):
    """Split a row that packs several components into one, one line per
    component inside each cell - restructuring tables without row rules. Done
    only when the name lines and the figure lines agree in number."""
    out = []
    for row in grid:
        cells = [(c or "") for c in row]
        lines = [[l for l in c.split("\n") if l.strip() and "@#&OPS" not in l] for c in cells]
        starts = [sum(1 for l in ls if COMP_START.match(l)) for ls in lines]
        j = max(range(len(cells)), key=lambda k: starts[k]) if cells else 0
        k = starts[j] if cells else 0
        if k < 2:
            out.append(row)
            continue
        groups, cur = [], []
        for l in lines[j]:
            if COMP_START.match(l) and cur:
                groups.append(cur)
                cur = []
            cur.append(l)
        groups.append(cur)
        numeric = {c: [l for l in lines[c] if _num(l) is not None] for c in range(len(cells))}
        new_rows = []
        for g_i, g in enumerate(groups):
            r = []
            for c in range(len(cells)):
                if c == j:
                    r.append(" ".join(g))
                elif len(numeric[c]) == len(groups):
                    r.append(numeric[c][g_i])
                elif c != j and lines[c] and not numeric[c] and len(lines[c]) == len(groups):
                    r.append(lines[c][g_i])
                else:
                    r.append("" if g_i else cells[c].replace("\n", " "))
            new_rows.append(r)
        out.extend(new_rows)
    return out


def read_datasheet(grid):
    """Components from a data-sheet table grid, or [].

    Rows start after the 'Component Name' header cell (or, in the 2024
    template, at the first 'Component 1.' cell) and run until a row that opens
    another part of the data sheet. A row with a name and no cost continues
    the name above it."""
    rows = [[_cell(c) for c in r] for r in _explode(grid)]
    start, header_text = None, ""
    for i, r in enumerate(rows):
        joined = " ".join(r)
        if re.search(r"component\s*name", joined, re.I) and not \
                re.search(r"current component", joined, re.I):
            start, header_text = i + 1, joined
            # The 2024 template packs the first component into the header row.
            if re.search(rf"{COMP_WORD}\s*1\b", joined, re.I):
                start = i
            break
        if any(re.match(rf"^{COMP_WORD}\s*1\s*[.:]", v, re.I) for v in r):
            start = i
            break
    if start is None:
        return []
    out = []
    for r in rows[start:]:
        texts = [v for v in r if v and _num(v) is None
                 and not re.search(r"component\s*name|cost \(", v, re.I)]
        nums = [_num(v) for v in r if _num(v) is not None]
        if texts and STOP_ROW.match(texts[0]):
            break
        if not texts and not nums:
            if out:
                break
            continue
        # In the 2024 template a header cell repeats every name; take the cell
        # that names one component.
        name = next((t for t in texts if re.match(rf"^{COMP_WORD}\s*\d", t, re.I)),
                    texts[-1] if texts else "")
        # ... unless it opens with the next component's number ("3. Increasing
        # access ...": a row whose cost is printed on the next page).
        opens_next = re.match(rf"^(?:{COMP_WORD}\s*)?{len(out) + 1}\s*[.:]\s", name, re.I)
        if not nums and out and name and not opens_next \
                and not re.match(rf"^{COMP_WORD}\s*\d", name, re.I):
            out[-1]["name"] = (out[-1]["name"] + " " + name).strip()
            continue
        if not name:
            continue
        number, clean_name = _split_number(name, len(out) + 1)
        out.append({"number": number, "name": clean_name,
                    "cost_usd_m": _to_millions(nums[-1] if nums else None, header_text),
                    "action": ""})
    return out


def read_restructuring(grid):
    """Components from a 'Current Component Name | Current Cost | Action |
    Proposed Component Name | Proposed Cost' table, or []. The proposed name
    and cost are what stand after the paper; the action ('Revised', 'New',
    'Marked for Deletion', 'No Change') says what happened."""
    rows = [[_cell(c) for c in r] for r in _explode(grid)]
    for i, r in enumerate(rows):
        if re.search(r"current component", " ".join(r), re.I):
            head = [v.lower() for v in r]
            break
    else:
        return []

    def col(*words):
        for j, h in enumerate(head):
            if all(w in h for w in words):
                return j
        return None
    a_col = col("action")
    if a_col is None:
        a_col = len(head) // 2
    ACTION = re.compile(r"^(?:revised|new|no change|marked for deletion|dropped|"
                        r"cancelled|unchanged)$", re.I)
    out, side = [], None
    for r in rows[i + 1:]:
        if not any(r):
            continue
        # Merged cells put a figure one column off its heading, so figures are
        # placed by side of the Action column: before it the current cost,
        # after it the proposed one.
        nums = [(j, _num(v)) for j, v in enumerate(r) if v and _num(v) is not None]
        before = [v for j, v in nums if j < a_col]
        after = [v for j, v in nums if j > a_col]
        texts = [(j, v) for j, v in enumerate(r)
                 if v and _num(v) is None and not ACTION.match(v)]
        action = next((v for v in r if v and ACTION.match(v)), "")
        current = " ".join(v for j, v in texts if j < a_col)
        proposed = " ".join(v for j, v in texts if j > a_col)
        if re.fullmatch(r"(?:component\s*)?name|cost", current or proposed, re.I):
            continue                    # a stray heading fragment
        cost = after[-1] if after else (before[-1] if before else None)
        if cost is None and not out:
            continue                    # header fragments before the first row
        if cost is None and out:
            # A continuation line: extend the name from the side it came from.
            extra = proposed if side == "proposed" else current
            if extra:
                out[-1]["name"] = (out[-1]["name"] + " " + extra).strip()
            continue
        # A proposed name cut short ('Component One -') is no name.
        bare = re.sub(rf"^{COMP_WORD}\s*\S+\s*[-–:.]?", "", proposed, flags=re.I).strip()
        if len(bare) > 3:
            name, side = proposed, "proposed"
        else:
            name, side = current, "current"
        if not name:
            continue
        if STOP_ROW.match(name):
            break
        number, clean_name = _split_number(name, len(out) + 1)
        out.append({"number": number, "name": clean_name,
                    "cost_usd_m": _to_millions(cost, "millions"), "action": action})
    return out


def clean_name(name):
    """A component's name without what the heading or cell prints around it:
    its number, its cost in brackets, a sentence that follows, figures from
    the next columns. '' when what is left is not a name (it opens in lower
    case: a sentence picked up as a heading)."""
    n = re.sub(r"^(?:Component|Sub-?component)\s*\d+(?:\.\d+)?\s*[.:\-\u2013]?\s*", "",
               name or "", flags=re.I)
    m = re.search(r"\s*\((?=[^)]*(?:US\$|U\$|US%|IDA|IBRD|SDR|Estimated|[Uu]p to|"
                  r"million|xxx|GoM|PCM))", n)
    if m:
        n = n[:m.start()]
    n = n.split("**")[0]
    n = re.split(r"\.\s+(?=(?:This|The|It|These|Under)\b)", n)[0]
    n = re.sub(r"(?:\s+(?:[\d.,]+|N/A))+$", "", n)
    n = re.sub(r"\s+The World Bank$", "", n)     # the page footer, read into the cell
    n = n.strip(" :-\u2013(.;,")
    # A cell read twice over: 'National Digital Connectivity Infrastructure
    # Digital Connectivity Infrastructure' is the name and the tail of it again.
    words = n.split()
    # 'Project Management Management' is the same with one word.
    for k in range(len(words) // 2, 0, -1):
        if words[-k:] == words[:-k][-k:]:
            n = " ".join(words[:-k])
            break
    return "" if not n or n[:1].islower() else n


def _tidy(found):
    """Collapse rows read twice (the 2024 template prints each name in a cell
    of its own and again in the header cell), and take budget lines that are
    not components - unallocated funds, contingency - out of the numbering."""
    out, seen = [], {}
    for c in found:
        # Budget lines, not components. The Contingent Emergency Response
        # Component (CERC) is a real, numbered component and keeps its number.
        if re.match(r"^(?:unallocated|contingenc(?:y|ies)\s+(?:and|for|reserve)\b)",
                    c["name"], re.I):
            c = dict(c, number="")
            out.append(c)
            continue
        if c["number"] in seen:
            prev = seen[c["number"]]
            if not prev["cost_usd_m"] and c["cost_usd_m"]:
                prev["cost_usd_m"] = c["cost_usd_m"]
            if len(c["name"]) > len(prev["name"]):
                prev["name"] = c["name"]
            continue
        seen[c["number"]] = c
        out.append(c)
    return out


def read_headings(rows, text):
    """Components and sub-components from body headings in Section II and the
    annexes."""
    out, seen = [], set()
    for r in rows:
        if r["block"] != "heading":
            continue
        s = text[int(r["char_start"]):int(r["char_end"])]
        m = COMP_HEAD.match(s)
        level, number, name = None, None, None
        if m:
            level, number, name = "component", m.group(1), m.group(2)
        else:
            m = SUB_WORD_HEAD.match(s) or SUB_HEAD.match(s)
            if m:
                level, number, name = "subcomponent", f"{m.group(1)}.{m.group(2)}", m.group(3)
        if not level or (level, number) in seen:
            continue
        # A results indicator numbered like a component ("2. Number of
        # servers planned ...") is not one, nor an exchange rate read as a
        # sub-component number ("5.72 BRL = US$1").
        if re.match(r"(?:Number|Percentage|Share|Proportion)\s+of\b", name, re.I) \
                or "=" in name:
            continue
        seen.add((level, number))
        cost = COST_IN_TEXT.search(name)
        value = ""
        if cost:
            v = float(cost.group(1).replace(",", "").rstrip("."))
            value = f"{v * 1000:.2f}" if (cost.group(2) or "").lower() == "billion" else f"{v:.2f}"
        name = COST_IN_TEXT.split(name)[0].strip(" :-–(")
        out.append({"level": level, "number": number, "name": name,
                    "cost_usd_m": value, "action": "", "page": r["page_from"]})
    return out


def read_table_pages(pdf_path, pages):
    """The component list from the first of these pages that holds one.

    A data sheet is often drawn as one small table per row, and runs onto the
    next page: every piece on a page and the next is read, top to bottom, as
    one grid. Rows with no name are a misread grid and are dropped, so a table
    that yields only those is passed over."""
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        for pno in pages:
            grid = []
            for q in (pno, pno + 1):
                if q <= len(pdf.pages):
                    for t in sorted(pdf.pages[q - 1].find_tables(), key=lambda t: t.bbox[1]):
                        grid += t.extract()
            width = max((len(r) for r in grid), default=0)
            grid = [list(r) + [None] * (width - len(r)) for r in grid]
            rs = [c for c in read_restructuring(grid) if _has_name(c["name"])]
            src = "restructuring"
            if not rs:
                rs = [c for c in read_datasheet(grid) if _has_name(c["name"])]
                src = "datasheet"
            if rs:
                return [dict(c, source=src, page=pno) for c in _tidy(rs)]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()

    with open(where(args.data, "documents"), newline="", encoding="utf-8") as fh:
        docs = {r["doc_id"]: r for r in csv.DictReader(fh)}
    paras = defaultdict(list)
    with open(where(args.data, "appraisal", "paragraphs.csv"), newline="",
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            paras[r["doc_id"]].append(r)
    # Page lookups are cached per document against the PDF checksum.
    cache_path = where(args.data, "cache", "components_cache.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}

    out, stats, used = [], Counter(), {}
    for did in sorted(docs):
        doc = docs[did]
        rows = paras.get(did, [])
        clean_path = where(args.data, "text", f"{did}.txt")
        if not rows or not os.path.exists(clean_path):
            continue
        with open(clean_path, encoding="utf-8") as fh:
            text = fh.read()
        base = {"doc_id": did, "doc_kind": doc["doc_kind"],
                "disclosure_date": doc["disclosure_date"]}
        # A combined appraisal document is split at its data sheets, each part
        # belonging to its own operation (keys.project_parts); a document of
        # one project is one part. Each part's tables and headings are read
        # on their own, so one country's "Component 1" does not hide another's.
        parts = project_parts(text, doc["project_ids"])

        # Pages holding a component table, found in the cleaned text so the
        # PDF is opened only at those pages. Searched over the whole text, not
        # paragraph by paragraph: a table's heading line can fall between two
        # kept paragraphs, and then belongs to the page of the one before it.
        starts = sorted((int(r["char_start"]), int(r["page_from"] or 0)) for r in rows)
        pages_by_part = defaultdict(set)
        for m in re.finditer(r"component\s*name|current component", text, re.I):
            before = [pg for s, pg in starts if s <= m.start()]
            if before and before[-1]:
                pages_by_part[project_at(parts, m.start())].add(before[-1])
        pdf_path = where(args.data, "pdf", f"{did}.pdf")
        for owners in dict.fromkeys(p[2] for p in parts):
            pages = sorted(pages_by_part.get(owners, ()))
            found = []
            # Keyed on the PDF, the pages searched and this module's code, so a
            # change to any of them reads the tables again.
            key = ":".join([doc.get("pdf_sha256") or "", ",".join(map(str, pages)),
                            READER_VERSION])
            if pages and os.path.exists(pdf_path):
                if key in cache:
                    found = cache[key]
                else:
                    found = read_table_pages(pdf_path, pages)
                    cache[key] = found
                used[key] = found
            for c in found:
                c = dict(c, name=clean_name(c["name"]) or c["name"])
                out.append(dict(base, project_ids=owners, level="component", **c))
                stats[f"rows_{c['source']}"] += 1
            if found:
                stats[f"docs_{found[0]['source']}"] += 1
            part_rows = [r for r in rows if project_at(parts, int(r["char_start"])) == owners]
            heads = read_headings(part_rows, text)
            heads = [dict(c, name=clean_name(c["name"])) for c in heads]
            heads = [c for c in heads if c["name"]]
            for c in heads:
                out.append(dict(base, project_ids=owners, source="heading", **c))
            if heads:
                stats["docs_heading"] += 1

    with open(cache_path + ".part", "w", encoding="utf-8") as fh:
        json.dump(used, fh, sort_keys=True)
    os.replace(cache_path + ".part", cache_path)

    for r in out:
        r["name_key"] = name_key(r["name"])
    out.sort(key=lambda r: (r["doc_id"], r["project_ids"], r["source"], r["level"],
                            [int(x) for x in r["number"].split(".") if x.isdigit()]))
    with open(where(args.data, "appraisal", "components.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COMP_COLS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    projects = defaultdict(set)
    for r in out:
        for p in r["project_ids"].split("|"):
            projects[p].add(r["source"])
    all_projects = {p for d in docs.values() for p in d["project_ids"].split("|")}
    with open(where(args.data, "reports", "components.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"component rows: {len(out)}\n")
        for k in sorted(stats):
            fh.write(f"  {k}: {stats[k]}\n")
        fh.write(f"\nprojects with components from any source: {len(projects)} of "
                 f"{len(all_projects)}\n")
        by = Counter("+".join(sorted(v)) for v in projects.values())
        for k, v in by.most_common():
            fh.write(f"  {v:>4}  {k}\n")
        fh.write("\nprojects with none:\n")
        for p in sorted(all_projects - set(projects)):
            kinds = Counter(d["doc_kind"] for d in docs.values() if p in d["project_ids"].split("|"))
            fh.write(f"  {p}  {dict(kinds)}\n")
    print(f"{len(out)} component rows; {len(projects)} of {len(all_projects)} projects covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
