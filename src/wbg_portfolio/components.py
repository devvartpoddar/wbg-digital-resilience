#!/usr/bin/env python3
"""Collect each project's components and tag paragraphs with the component they
belong to.

Reads  data/raw/documents.csv, data/appraisal/paragraphs.csv and text/,
       data/raw/pdf/{doc_id}.pdf (only the pages holding a component table)
Writes data/appraisal/components.csv (one row per component or sub-component, per
       document and source: datasheet, restructuring or heading),
       data/reports/components.txt

Sources are not reconciled here. Tagger and mentions() are applied by clean.py:
the component a paragraph sits under, and the component numbers it names.
"""
import argparse, csv, hashlib, json, os, re, sys
from collections import Counter, defaultdict

from wbg_portfolio.paths import data_root, file_sha256, where  # noqa: E402
from wbg_portfolio import common  # noqa: E402
from wbg_portfolio.common import write_csv  # noqa: E402
from wbg_portfolio.keys import (name_key, project_parts, project_at, number_key,  # noqa: E402
                                normal_number, uses_roman, COMPONENT_PREFIX)
from wbg_portfolio.pdf_layout import _title_line  # noqa: E402

COMP_WORD = r"(?:Component|Composante|Componente|Komponen)"
# A component's number: digits, a capital letter (A, B, C) or a run of I, V, X (II, III);
# 'ICT' or 'CERC' is no number. A letter is the number only when no digit or '.N' follows it
# ('Component B.1' and 'Component A1' are sub-components of B and A).
_LET = r"(?-i:[A-Z](?![A-Za-z\d]|\.\d)|[IVX]{2,4}(?![A-Za-z\d]|\.\d))"
# The number is followed by punctuation ('Component 2: ...') or, in some templates, by the
# title alone ('Component 1 Extending digital connectivity'). A letter is followed by a colon,
# full stop, closing bracket, dash or spaced hyphen, or by the title alone; never by a hyphen
# that runs into the next word ('Component E-Government Services' is no component E).
_COMP_NUM_HEAD = re.compile(
    rf"^\s*{COMP_WORD}\s*(\d{{1,2}})(?:\s*[:.\-–)]\s*|\s+(?=(?-i:[A-Z(])))(.+?)\s*$", re.I)
_COMP_LET_HEAD = re.compile(
    rf"^\s*{COMP_WORD}\s*({_LET})(?:\s*[:.–—)]\s*|\s*-\s+|\s+(?=(?-i:[A-Z(])))(.+?)\s*$", re.I)
# Sub-component number: 2.1, or lettered 'A.1' / 'A1', or roman 'II.1'.
_SUBID = r"((?-i:\d{1,2}(?=\.)|[A-Z]|[IVX]{2,4}))(?:\.|(?<=[A-Z])(?=\d))(\d{1,2})"
SUB_HEAD = re.compile(
    r"^\s*(?:Sub-?component|Sous-composante|Subcomponente)?\s*"
    r"((?-i:\d{1,2}|[A-Z]|[IVX]{2,4}))\.(\d{1,2})\.?"
    r"\s*[:.\-–)]?\s*([A-ZÀ-Þ].+?)\s*$", re.I)
SUB_WORD_HEAD = re.compile(
    rf"^\s*(?:Sub-?component|Sous-composante|Subcomponente)\s*{_SUBID}\.?"
    r"\s*[:.\-–)]?\s*(.+?)\s*$", re.I)
# 'Component B.1: ...' and 'Component A1: ...': only a letter before the sub-number.
SUB_COMP_HEAD = re.compile(
    rf"^\s*{COMP_WORD}\s*(?!\d){_SUBID}\.?\s*[:.\-–)]?\s*(.+?)\s*$", re.I)
_ROMAN_OK = re.compile(r"^X{0,3}(?:IX|IV|V?I{0,3})$")


def _ok(m):
    """The match, unless its component number is letters that are no letter or numeral."""
    if m is None:
        return None
    n = m.group(1)
    return m if n.isdigit() or len(n) == 1 or _ROMAN_OK.match(n) else None


def comp_match(text):
    return _ok(_COMP_NUM_HEAD.match(text)) or _ok(_COMP_LET_HEAD.match(text))


class _Head:
    """COMP_HEAD.match, for what reads the heading pattern directly."""
    match = staticmethod(lambda text: comp_match(text))


COMP_HEAD = _Head


def sub_word_match(text):
    """'Sub-component 2.1: ...', 'Subcomponent A.2', 'Component B.1: ...'."""
    return _ok(SUB_WORD_HEAD.match(text)) or _ok(SUB_COMP_HEAD.match(text))


def sub_bare_match(text):
    return _ok(SUB_HEAD.match(text))


COST_IN_TEXT = re.compile(
    r"\(\s*(?:US\$|USD|\$|EUR|€|SDR)\s*([\d.,]+)\s*(million|m|billion)?", re.I)
_SEP = r"\s*(?:,|and|&|et|e|y|or|to|-|–)\s*"
_LID = r"(?-i:[A-Z])(?:\.?\d{1,2})?(?![A-Za-z\d])"
MENTION = re.compile(
    r"\b(?:Sub-?components?|Components?|Composantes?|Sous-composantes?|Componentes?|"
    r"Subcomponentes?)\s+((?:\d{1,2}(?:\.\d{1,2})?)"
    rf"(?!\d)(?:{_SEP}\d{{1,2}}(?:\.\d{{1,2}})?(?![\d.]?\d))*"
    rf"|{_LID}(?:{_SEP}{_LID})*)", re.I)
RUN_IN_NUMBER = re.compile(r"^\s*\d{1,3}\.\s+")
RUN_IN_COMP = re.compile(rf"^\s*{COMP_WORD}\s*(?:(\d{{1,2}})\s*[:.\-\u2013)]\s*|({_LET})(?:\s*[:.\u2013\u2014)]\s*|\s*-\s+))[A-Z]")


def run_in_match(text):
    """A run-in 'Component 3: Title' or 'Component C: Title' (a letter never runs into a hyphenated word);
    the match's group(1) is the number."""
    m = RUN_IN_COMP.match(text)
    if m is None:
        return None
    return _Run(m)


class _Run:
    def __init__(self, m):
        self.m = m

    def group(self, _=1):
        return self.m.group(1) or self.m.group(2)


# A document's lettered components are real only when its lettered headings form a sequence that
# starts A, B (or I, II): a lone 'Component E-Government' or 'Component X Activities' is not one.
_ROMANS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def _is_letter(n):
    return not n[:1].isdigit()


def sequence_letters(found):
    """The component letters a document may use, from the letters its headings carry: the run from
    A (or the roman run from I) that is unbroken and holds at least two; empty otherwise."""
    found = {str(n).split(".")[0].upper() for n in found if _is_letter(str(n))}
    out = set()
    for seq in (list(map(chr, range(65, 91))), _ROMANS):
        run = []
        for x in seq:
            if x not in found:
                break
            run.append(x)
        if len(run) >= 2:
            out.update(run)
    return out


def document_letters(units):
    """The component letters a document's headings may use: units are (text, block) in order.
    Reads the same headings the Tagger reads, with no letter filter, and returns sequence_letters
    of what it finds."""
    found = set()
    for text, block in units:
        if block not in TAGGED_BLOCKS:
            continue
        short = len(text.split()) <= 30
        if not short and block in ("narrative", "annex"):
            head = RUN_IN_NUMBER.sub("", text, count=1)
            for m in (sub_word_match(head[:200]), _ok(run_in_match(head))):
                if m:
                    found.add(m.group(1))
        if block == "heading" or (short and block in ("narrative", "annex")):
            for m in (comp_match(text), sub_word_match(text)):
                if m:
                    found.add(m.group(1))
    return sequence_letters(found)



def _reader_version():
    """The table reader's identity: this file, the text rules and the
    pdfplumber version, which decides how a table is read."""
    import pdfplumber
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for name in ("components.py", "text_rules.py"):
        with open(os.path.join(here, name), "rb") as fh:
            h.update(fh.read())
    h.update(pdfplumber.__version__.encode())
    return h.hexdigest()[:10]


READER_VERSION = _reader_version()
TAGGED_BLOCKS = {"narrative", "annex", "table", "heading", "footnote"}


def mentions(text):
    """Component numbers a paragraph names: 'Components 1 and 3',
    'Sub-component 2.1', 'Composante 2', 'Components A and B'. Sorted, unique."""
    out = set()
    for m in MENTION.finditer(text):
        out.update(normal_number(v) for v in
                   re.findall(r"\d{1,2}(?:\.\d{1,2})?|[A-Z](?:\.?\d{1,2})?", m.group(1)))
    return sorted(out, key=number_key)


class Tagger:
    """Walk a document's units in order and say which component each sits
    under. A component heading opens a component, and a sub-component heading
    a sub-component inside it. Both close at the first section that is not the
    one the component opened in, or inside it: after the last component of
    'II.B Project Components', 'II.C Project Beneficiaries' belongs to none."""

    def __init__(self, close_at_section=True, letters=None):
        # close_at_section=False keeps a component open until the next component heading;
        # src/wbg_portfolio/sensitivity.py compares the two.
        # letters: the component letters this document may use (document_letters of its units);
        # None applies no document test (a Tagger fed unit by unit, in a test).
        self.close_at_section = close_at_section
        self.letters = letters
        self._close()

    def _allowed(self, m):
        """The match, unless its number is a letter this document's headings do not support."""
        if m is None or self.letters is None or not _is_letter(m.group(1)):
            return m
        return m if m.group(1).split(".")[0].upper() in self.letters else None

    def _close(self):
        self.comp, self.sub, self.home = "", "", None
        # The titles as the headings print them, so a paragraph under a component the data
        # sheet did not list still carries its name.
        self.comp_name, self.sub_name = "", ""

    def _open(self, comp, name, path):
        self.comp, self.sub, self.home = comp, "", path
        self.comp_name, self.sub_name = clean_name(name), ""
        return self.comp, ""

    def _open_sub(self, m, name, path):
        # A sub-component heading N.M belongs to component N: if another component is open,
        # the heading that would have opened N was missed, so N is opened here rather than
        # the sub-component ignored.
        if m.group(1) != self.comp:
            self.comp, self.comp_name, self.home = m.group(1), "", path
        self.sub, self.sub_name = f"{m.group(1)}.{m.group(2)}", clean_name(name)
        return self.comp, self.sub

    def see(self, text, block, section_path):
        path = section_path or ""
        if self.close_at_section and self.home is not None and path != self.home \
                and not path.startswith(self.home + "."):
            self._close()
        if block not in TAGGED_BLOCKS:
            return "", ""
        short = len(text.split()) <= 30
        # A run-in title opening a long numbered paragraph: '31. Subcomponent 2.2: Data
        # centre. This subcomponent will finance ...'.
        if not short and block in ("narrative", "annex"):
            head = RUN_IN_NUMBER.sub("", text, count=1)
            m = self._allowed(sub_word_match(head[:200]))
            # A run-in title ends at its first full stop.
            # (after the number: 'Component 1. Title. ...' has its first stop behind 'Component 1').
            title = re.split(r"\.\s+", COMPONENT_PREFIX.sub("", head[:200], count=1))[0]
            if m:
                return self._open_sub(m, title, path)
            m = self._allowed(_ok(run_in_match(head)))
            if m:
                return self._open(m.group(1), title, path)
        # A short footnote or table row that names a component ("Component 2: see Annex 3",
        # a cost table's row) refers to it; it does not open it.
        if block == "heading" or (short and block in ("narrative", "annex")):
            m = self._allowed(comp_match(text))
            if m:
                return self._open(m.group(1), m.group(2), path)
            # A numbered sub-component title is a heading even when set in plain type, if it
            # carries a cost: '1.2. Infrastructure ... (US$15.3 million equivalent)'.
            titled = block == "heading" or bool(COST_IN_TEXT.search(text))
            m = self._allowed(sub_word_match(text))
            if m:
                return self._open_sub(m, text, path)
            # A bare 'N.M Title' is a sub-component only under component N: elsewhere it is
            # as likely a numbered paragraph or a table row.
            m = sub_bare_match(text) if titled else None
            if m and self.comp and m.group(1) == self.comp:
                return self._open_sub(m, text, path)
        return self.comp, self.sub


# ------------------------------------------------------------- extraction

# name_key (keys.name_key) is what the same component shares across documents and with
# procurement: a paragraph and a package under one component meet on it even when a
# restructuring renumbered it.
COMP_COLS = ["project_ids", "doc_id", "doc_kind", "disclosure_date", "source", "level",
             "number", "name", "name_key", "cost_usd_m", "cost_before_usd_m", "action",
             "page"]
NUM = re.compile(r"^\s*\$?\s*([\d,]+(?:\.\d+)?)\s*$")
# A row that ends the component list: what the data sheet prints after it.
STOP_ROW = re.compile(r"^(?:implementing agency|organizations|project financing|total"
                      r"|borrower|financing|summary|front.end fee|(?:ida|ibrd)\s*$)", re.I)


def _has_name(name):
    """Does a cell hold a name at all? '+2.5 +3.3' read from the wrong column
    does not: a name has a word of three letters or more."""
    return bool(re.search(r"[A-Za-z\u00C0-\u024F]{3}", name or ""))


def _num(v):
    # A figure wrapped inside its cell ('125,000,000.0' over '0') comes back with a space in
    # it; digits on both sides of a space are one figure.
    v = re.sub(r"(?<=[\d,.])\s+(?=\d)", "", v or "") if re.fullmatch(r"[\d,.\s$]+", v or "") else v
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
    from wbg_portfolio import text_rules as C
    parts = [p for p in (c or "").split("\n") if "@#&OPS" not in p]
    return re.sub(r"\s+", " ", C.fold_chars(" ".join(parts))).strip()


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_CNUM = rf"(?:\d{{1,2}}|{'|'.join(NUMBER_WORDS)}|{_LET})"
COMP_START = re.compile(rf"^\s*{COMP_WORD}\s*{_CNUM}(?![A-Za-z\d])", re.I)


def _split_number(name, order):
    """('2', 'Digital Connectivity') from 'Component 2: Digital Connectivity',
    'Component Two - Digital Connectivity' or '2. Digital Connectivity'; the
    row order when the name carries no number. A name never runs into the next
    component: the 2024 data sheet prints every name in one cell."""
    m = re.match(rf"^\s*(?:{COMP_WORD}\s*)?(\d{{1,2}}|{'|'.join(NUMBER_WORDS)})"
                 r"\s*[.:)\-–]?\s+(.+)$", name, re.I)
    # A letter or numeral is a number only after the word 'Component' ('A. Plan' may be a name).
    ml = _ok(re.match(rf"^\s*{COMP_WORD}\s*({_LET})\s*[.:)\-–]?\s+(.+)$", name, re.I))
    if ml and not (m and m.group(1).isdigit()):
        number, rest = ml.group(1), ml.group(2).strip()
        m = ml
    elif m:
        n = m.group(1).lower()
        number, rest = str(NUMBER_WORDS.get(n, n)), m.group(2).strip()
    else:
        number, rest = str(order), name
    nxt = re.search(rf"\s{COMP_WORD}\s*{_CNUM}(?![A-Za-z\d])", rest, re.I)
    if nxt:
        rest = rest[:nxt.start()]
    return number, rest.strip(" -–:"), bool(m)


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


_CID = r"(?:\d|(?-i:[A-Z](?![A-Za-z\d])))"


def read_datasheet(grid):
    """Components from a data-sheet table grid, or []."""
    rows = [[_cell(c) for c in r] for r in _explode(grid)]
    start, header_text = None, ""
    for i, r in enumerate(rows):
        joined = " ".join(r)
        if re.search(r"component\s*name", joined, re.I) and not \
                re.search(r"current component", joined, re.I):
            start, header_text = i + 1, joined
            # The 2024 template packs the first component into the header row.
            if re.search(rf"{COMP_WORD}\s*(?:1\b|(?-i:A)(?![A-Za-z\d]))", joined, re.I):
                start = i
            break
        if any(re.match(rf"^{COMP_WORD}\s*(?:1|(?-i:A)(?![A-Za-z\d]))\s*[.:]", v, re.I)
               for v in r):
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
        # In the 2024 template a header cell repeats every name; take the cell that names
        # one component.
        name = next((t for t in texts if re.match(rf"^{COMP_WORD}\s*{_CID}", t, re.I)),
                    texts[-1] if texts else "")
        # ... unless it opens with the next component's number ("3. Increasing access ...":
        # a row whose cost is printed on the next page).
        opens_next = re.match(rf"^(?:{COMP_WORD}\s*)?(?:{len(out) + 1}|(?-i:{chr(65 + len(out) % 26)}))"
                              r"\s*[.:]\s", name, re.I)
        if not nums and out and name and not opens_next \
                and not re.match(rf"^{COMP_WORD}\s*{_CID}", name, re.I):
            out[-1]["name"] = (out[-1]["name"] + " " + name).strip()
            continue
        if not name:
            continue
        number, clean_name, printed = _split_number(name, len(out) + 1)
        out.append({"number": number, "name": clean_name, "number_printed": printed,
                    "cost_usd_m": _to_millions(nums[-1] if nums else None, header_text),
                    "action": ""})
    return out


def read_restructuring(grid):
    """Components from a 'Current Component Name | Current Cost | Action |
    Proposed Component Name | Proposed Cost' table, or []. The proposed name
    and cost are what stand after the paper, the current cost is kept as
    cost_before_usd_m, and the action ('Revised', 'New', 'Marked for
    Deletion', 'No Change') says what happened."""
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
        # Merged cells put a figure one column off its heading, so figures are placed by
        # side of the Action column: before it the current cost, after it the proposed one.
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
        number, clean_name, printed = _split_number(name, len(out) + 1)
        out.append({"number": number, "name": clean_name, "number_printed": printed,
                    "cost_usd_m": _to_millions(cost, "millions"),
                    # The cost before the paper, when it prints one beside the proposed
                    # cost: a restructuring that cuts or adds funds.
                    "cost_before_usd_m": _to_millions(before[-1], "millions")
                    if before and after else "",
                    "action": action})
    return out


def clean_name(name):
    """A component's name without what the heading or cell prints around it:
    its number, its cost in brackets, a sentence that follows, figures from
    the next columns. '' when what is left is not a name (it opens in lower
    case: a sentence picked up as a heading)."""
    n = COMPONENT_PREFIX.sub("", name or "", count=1)
    m = re.search(r"\s*\((?=[^)]*(?:US\$|U\$|US%|IDA|IBRD|SDR|Estimated|[Uu]p to|"
                  r"million|xxx|GoM|PCM))", n)
    if m:
        n = n[:m.start()]
    n = n.split("**")[0]
    n = re.split(r"\.\s+(?=(?:This|The|It|These|Under|Proposed)\b)", n)[0]
    # A cost printed as ' - $30.00m)' rather than in brackets.
    n = re.sub(r"\s*[-\u2013]?\s*(?:US)?\$\s*[\d.,]+\s*(?:m|million)?\)?\s*$", "", n)
    n = re.sub(r"(?:\s+(?:[\d.,]+|N/A))+$", "", n)
    n = re.sub(r"\s+The World Bank$", "", n)     # the page footer, read into the cell
    n = n.strip(" :-\u2013(.;,")
    # A cell read twice over: 'National Digital Connectivity Infrastructure Digital
    # Connectivity Infrastructure' is the name and the tail of it again.
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
        # Budget lines, not components.
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
    letters = document_letters((text[int(r["char_start"]):int(r["char_end"])], r["block"]) for r in rows)
    for r in rows:
        s = text[int(r["char_start"]):int(r["char_end"])]
        if r["block"] != "heading":
            # A title that leads in a list ('Component 3: Growing the Digital Economy. The
            # proposed changes include:' then its items) heads the paragraph that holds the
            # list: its first line is read.
            first = s.split("\n", 1)[0]
            if r["block"] not in ("narrative", "annex") or first == s \
                    or not _title_line(first):
                continue
            s = first
        m = comp_match(s)
        if m and _is_letter(m.group(1)) and m.group(1).upper() not in letters:
            m = None
        level, number, name = None, None, None
        if m:
            level, number, name = "component", m.group(1), m.group(2)
        else:
            # 'Sub-component 2.1: ...' always; a bare '2.1 Title' only once component 2 has
            # been seen, else it is a numbered section.
            m = sub_word_match(s)
            if m and _is_letter(m.group(1)) and m.group(1).upper() not in letters:
                m = None
            if not m:
                m = sub_bare_match(s)
                if m and ("component", m.group(1)) not in seen:
                    m = None
            if m:
                level, number, name = "subcomponent", f"{m.group(1)}.{m.group(2)}", m.group(3)
        if not level or (level, number) in seen:
            continue
        # A results indicator numbered like a component ("2. Number of servers planned ...")
        # is not one, nor an exchange rate read as a sub-component number ("5.72 BRL =
        # US$1").
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
    """The component list from the first of these pages that holds one."""
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


def _same_component(a, b):
    """Whether two name keys name one component: equal, or one the start of
    the other ('projectmanagement' and 'projectmanagementandinstitutional...'),
    the shorter at least 12 characters."""
    short, long_ = sorted((a, b), key=len)
    return bool(short) and (short == long_ or (len(short) >= 12 and long_.startswith(short)))


def number_by_name(rows):
    """Number the rows of a table that printed no numbers (row order may differ
    from the project's): each takes the number the project's headings and numbered
    tables give the same name, preferring the same document; else its position, or
    the smallest number free. Printed numbers are left alone."""
    by_doc = defaultdict(list)
    for r in rows:
        by_doc[(r["doc_id"], r["project_ids"])].append(r)
    for (did, owners), rs in by_doc.items():
        table = [r for r in rs if r.get("source") in ("datasheet", "restructuring")
                 and r.get("level", "component") == "component" and r["number"]
                 and r.get("number_printed") is False]
        if not table:
            continue
        known = []          # (rank, name_key, number): this document first
        for r in rows:
            if r.get("level", "component") == "component" and r["number"] \
                    and set(r["project_ids"].split("|")) & set(owners.split("|")) \
                    and (r.get("source") == "heading" or r.get("number_printed")):
                known.append((0 if r["doc_id"] == did else 1, r["name_key"], r["number"]))
        known.sort()
        taken, unmatched = set(), []
        for r in table:
            hit = next((n for _, k, n in known if _same_component(r["name_key"], k)), None)
            if hit and hit not in taken:
                r["number"] = hit
                taken.add(hit)
            else:
                unmatched.append(r)
        for r in unmatched:
            if r["number"] in taken:
                r["number"] = str(next(i for i in range(1, 100) if str(i) not in taken))
            taken.add(r["number"])


def _documents(data, docs, paras):
    """For each document and each operation it covers (a combined document is split at its data
    sheets, keys.project_parts): (doc id, document row, its paragraph rows, its cleaned text,
    the parts, the row base, the operation's project ids, the pages holding a component table,
    the cache key of that read, the PDF's path)."""
    for did in sorted(docs):
        doc = docs[did]
        rows = paras.get(did, [])
        clean_path = where(data, "text", f"{did}.txt")
        if not rows or not os.path.exists(clean_path):
            continue
        with open(clean_path, encoding="utf-8") as fh:
            text = fh.read()
        base = {"doc_id": did, "doc_kind": doc["doc_kind"],
                "disclosure_date": doc["disclosure_date"]}
        parts = project_parts(text, doc["project_ids"], doc.get("doc_kind"))
        # Pages holding a component table, found in the cleaned text so the PDF is opened
        # only at those pages.
        starts = sorted((int(r["char_start"]), int(r["page_from"] or 0)) for r in rows)
        pages_by_part = defaultdict(set)
        for m in re.finditer(r"component\s*name|current component", text, re.I):
            before = [pg for s, pg in starts if s <= m.start()]
            if before and before[-1]:
                pages_by_part[project_at(parts, m.start())].add(before[-1])
        pdf_path = where(data, "pdf", f"{did}.pdf")
        sha = doc.get("pdf_sha256") or (file_sha256(pdf_path, remember=False)
                                        if os.path.exists(pdf_path) else "")
        for owners in dict.fromkeys(p[2] for p in parts):
            pages = sorted(pages_by_part.get(owners, ()))
            # Keyed on the PDF, the pages searched and this module's code, so a change to
            # any of them reads the tables again.
            key = ":".join([sha, ",".join(map(str, pages)), READER_VERSION])
            yield did, doc, rows, text, parts, base, owners, pages, key, pdf_path


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

    # The component tables to read from the PDFs, each in a child process with a time limit
    # (common.limited); the cache is written as each is read, so a stop keeps them. A read that
    # fails or runs out of time is reported, finds nothing this run and is tried again next run.
    todo = {}
    for did, *_, pages, key, pdf_path in _documents(args.data, docs, paras):
        if pages and os.path.exists(pdf_path) and key not in cache:
            todo.setdefault(key, (pdf_path, pages, did))
    progress = common.Progress("components: PDF tables read", len(todo))
    for key, found, err in common.limited(lambda k: read_table_pages(*todo[k][:2]), sorted(todo),
                                          lambda k: common.pdf_seconds(todo[k][0])):
        if err:
            progress.skip(todo[key][2], err[:120])
            continue
        cache[key] = found
        common.write_atomic(cache_path, json.dumps(cache, sort_keys=True))
        progress.step()
    progress.end()

    out, stats, used = [], Counter(), {}
    for did, doc, rows, text, parts, base, owners, pages, key, pdf_path in _documents(args.data, docs, paras):
        found = []
        if pages and os.path.exists(pdf_path):
            found = cache.get(key, [])
            if key in cache:
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

    # The cache keeps what this corpus uses, as before.
    common.write_atomic(cache_path, json.dumps(used, sort_keys=True))

    for r in out:
        r["name_key"] = name_key(r["name"])
    number_by_name(out)
    roman = {}
    for r in out:
        roman.setdefault((r["doc_id"], r["project_ids"]), []).append(r["number"])
    roman = {k: uses_roman(v) for k, v in roman.items()}
    out.sort(key=lambda r: (r["doc_id"], r["project_ids"], r["source"], r["level"],
                            number_key(r["number"], roman[(r["doc_id"], r["project_ids"])])))
    dest = where(args.data, "appraisal", "components.csv")
    write_csv(dest, COMP_COLS, out)

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
    sys.exit(common.exit_status(main()))
