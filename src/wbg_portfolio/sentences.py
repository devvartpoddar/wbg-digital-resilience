"""Split a cleaned paragraph into sentences, deterministically, with no model.

A boundary is '.', '!' or '?' followed by whitespace and something that can open
a sentence. Abbreviations, initials, paragraph numbers and decimals are not
boundaries; each item of an enumerated list ('(a) ...; (b) ...') is its own
sentence; a scrap under MIN_WORDS words is not a sentence; a lone form answer
('N/A') is left out. Sentences are addressed by offsets in their paragraph.
"""
import re

ABBREV = {
    "e.g", "i.e", "etc", "no", "nos", "para", "paras", "vs", "cf", "approx",
    "mr", "ms", "mrs", "dr", "prof", "st", "fig", "figs", "al", "inc", "ltd",
    "co", "corp", "dept", "art", "sec", "vol", "p", "pp", "ch", "op", "rev",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "u.s", "u.k", "u.n", "rs", "est", "min", "max", "incl",
    "govt", "intl", "natl", "ref", "resp", "viz", "ca",
}
# A stretch with fewer words than this does not stand alone as a sentence.
# src/wbg_portfolio/sensitivity.py varies it, and the abbreviation list.
MIN_WORDS = 3
BOUNDARY = re.compile(r"[.!?][\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
# A list marker, in every form the documents print: '(a)', '(iv)', '(1)', and without the
# opening bracket or with a full stop: 'a)', 'iv)', '1)', 'a.', 'ii.'. A letter is one
# lowercase letter; a numeral is a roman numeral from i to xx. A number and a full stop
# ('38.') is a paragraph number (PARA_NUMBER), never a marker. A marker is followed by
# whitespace, so 'U.S.', 'e.g.' and '3.2' are not markers, and a capital is never one ('J.
# Smith'). The bare forms count only where a list can open: the start of a line or sentence,
# or after ':' or ';'.
ROMAN = r"(?:xx|x?(?:ix|iv|v?i{1,3}|v)|x)"
MARK = rf"(?:[a-z]|{ROMAN}|\d{{1,2}})"
PAREN_MARKER = rf"\((?:{MARK})\)"             # (a) (iv) (1)
CLOSE_MARKER = rf"(?:{MARK})\)"               # a) iv) 1)
DOT_MARKER = rf"(?:[a-z]|{ROMAN})\."          # a. ii.   (never '1.')
MARKER = rf"(?:{PAREN_MARKER}|{CLOSE_MARKER}|{DOT_MARKER})"
# A sentence or line opening with a marker is a list item: the one definition, used here and
# by units.lead_ins.
ITEM_MARKER = re.compile(rf"^\s*{MARKER}\s")
# An inline list: '... on: (a) civil registration; b) national ID; and c) ...'. Each item is
# its own sentence, so a search finds the item that names an asset or a measure. The
# boundary is a colon or semicolon (with a trailing 'and' or 'or', kept with the item
# before) followed by a list marker.
LIST_BOUNDARY = re.compile(rf"[:;](?:\s+(?:and|or))?\s+(?={MARKER}\s)")
# A list whose items are separated by commas only: '(a) setting tariffs, (b) running toll
# stations, and (c) collecting revenue'.
SEQ_BOUNDARY = re.compile(rf"[,.](?:\s+(?:and|or))?\s+(?=(?:{PAREN_MARKER}|{CLOSE_MARKER})\s)")
# A list can also begin mid-sentence with a bracketed first marker: 'The project will (a)
# build towers, (b) ...'.
OPEN_MARKER = re.compile(rf"(?<!\S){PAREN_MARKER}\s")
MARKER_VALUE = re.compile(rf"\(?({MARK})[.)]")
FIRST_MARKERS = {"a", "i", "1"}
ROMAN_SEQ = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii",
             "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx"]
WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")
PARA_NUMBER = re.compile(r"^\s*\(?\d{1,3}(?:\.\d{1,2})*[.)]\s+")
NO_ANSWER = re.compile(r"^\W*(?:n\s*/\s*a|n\.a|none|not applicable)\W*$", re.I)


def split(text):
    """Paragraph text -> list of (start, end) offsets, one per sentence. Each
    line is split on its own: a paragraph holds a newline only where a list
    is set out line by line under its lead-in."""
    out, at = [], 0
    for line in text.split("\n"):
        out += [(at + a, at + z) for a, z in _split_line(line, first=at == 0)
                if not NO_ANSWER.match(line[a:z])]
        at += len(line) + 1
    return out


def next_markers(marker):
    """The markers that follow this one in a list: 'a' -> b, 'ii' -> iii,
    '3' -> 4. 'i', 'v' and 'x' are letters and numerals, so both successors."""
    out = set()
    if marker.isdigit():
        out.add(str(int(marker) + 1))
    if len(marker) == 1 and marker.isalpha() and marker < "z":
        out.add(chr(ord(marker) + 1))
    if marker in ROMAN_SEQ[:-1]:
        out.add(ROMAN_SEQ[ROMAN_SEQ.index(marker) + 1])
    return out


def _depth(text, at):
    """Brackets open at text[at]: a marker inside '(see (a) above)' is a
    cross-reference, not a list."""
    return text.count("(", 0, at) - text.count(")", 0, at)


def _list_boundaries(text, floor):
    """The inline-list boundaries of one line, in order. A colon or semicolon
    before a marker is always one. A comma or full stop before a marker is
    one only when that marker follows, in sequence, the last marker of a list
    already begun in the line with its first marker ('a', 'i' or '1')."""
    events = [(m.end(), 0, m) for m in LIST_BOUNDARY.finditer(text)] + \
        [(m.end(), 1, m) for m in SEQ_BOUNDARY.finditer(text)] + \
        [(m.start(), 2, None) for m in OPEN_MARKER.finditer(text)]
    head = ITEM_MARKER.match(text[floor:])
    if head:
        events.append((floor + len(text[floor:]) - len(text[floor:].lstrip()), 2, None))
    out, last = [], None
    for at, kind, m in sorted(events, key=lambda e: (e[0], e[1])):
        v = MARKER_VALUE.match(text, at)
        if v is None:
            continue
        value = v.group(1)
        if kind == 0:
            out.append(m)
            last = value
        elif kind == 1:
            if last is not None and value in next_markers(last) and _depth(text, at) == 0:
                out.append(m)
                last = value
        elif value in FIRST_MARKERS and _depth(text, at) == 0 and \
                (last is None or value not in next_markers(last)):
            last = value
    return out


def _split_line(text, first):
    spans, start = [], 0
    # A leading paragraph number belongs to the first sentence; it is not one.
    m = PARA_NUMBER.match(text) if first else None
    floor = m.end() if m else 0
    found = [(b, False) for b in BOUNDARY.finditer(text)] + \
        [(b, True) for b in _list_boundaries(text, floor)]
    found.sort(key=lambda x: x[0].start())
    for b, is_list in found:
        end = b.start() + 1
        if end <= floor or b.start() < start:
            continue
        before = text[start:b.start()]
        # Too short to be a sentence: 'ITU.', '2021.' in a reference, 'Annex 2.' as a label.
        seg = text[max(start, floor):b.start()]
        item = ITEM_MARKER.match(seg)
        if item:
            if not re.search(r"\w", seg[item.end():]):
                continue
        elif len(re.findall(r"\w+", seg)) < MIN_WORDS:
            continue
        w = None if is_list else WORD_BEFORE.search(before)
        if w:
            word = w.group(1).lower().rstrip(".")
            if word in ABBREV or len(word) == 1:
                continue            # an abbreviation or an initial
        spans.append((start, b.start() + len(b.group(0).rstrip())))
        start = b.end()
    if start < len(text) and text[start:].strip():
        spans.append((start, len(text.rstrip())))
    return [(a + len(text[a:z]) - len(text[a:z].lstrip()), z)
            for a, z in spans if text[a:z].strip()]
