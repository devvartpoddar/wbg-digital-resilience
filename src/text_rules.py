"""The text rules shared by the PDF reader and the cleaning stages: how
characters are folded, how a running line is fingerprinted, how a heading,
annex or numbered paragraph is recognised, and how a table row is told from
prose. Kept apart from clean.py so the PDF reader's cache key can cover exactly
the code that shapes its output (clean.parser_version)."""
import re
import unicodedata

ROMAN = re.compile(r"^\s*((?:I|V|X)[IVX]*)\.\s+([A-Z][^a-z]*(?:[A-Z].*)?)\s*$")
LETTER = re.compile(r"^\s*([A-Z])\.\s+(\S.*)$")
ANNEX = re.compile(r"^\s*(ANNEX|Annex|APPENDIX|Appendix)\s+([0-9]{1,2}|[IVX]{1,4})\s*(?:[:.\-\u2013]\s*)?(.*)$")
NUMBERED_PARA = re.compile(r"^\s*(\d{1,3})\.\s+(\S)")
TOKEN = re.compile(r"[\w'’-]+", re.UNICODE)
NUMERIC_TOKEN = re.compile(r"^[\d.,%()$-]+$")

# Character folds. Deliberately not NFKC: that would turn a comparison operator
# into two characters and change meaning. The risk-matrix glyphs are left alone
# because they are the best signal that a line came out of a table.
CHAR_MAP = {
    " ": " ", " ": " ", " ": " ", " ": " ", "​": "",
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "«": '"', "»": '"',
    "\u2013": "-", "\u2212": "-", "\u00ad": "",
    # U+2010 HYPHEN and U+2011 NON-BREAKING HYPHEN are what a renderer emits
    # for an ordinary hyphen in "Non-Consulting" or "Sub-component". They are
    # not dashes and carry no meaning the ASCII hyphen lacks, but they tokenise
    # as something else entirely. 665 of them survived the first full corpus.
    "\u2010": "-", "\u2011": "-",
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "\u25cf": "\u2022", "\u27a2": "\u2022", "\u25aa": "\u2022",
    # Private Use Area. These are Symbol and Wingdings glyphs that a converter
    # carried across as raw font code points: U+F0B7 is Symbol's bullet, U+F06C
    # Wingdings'. Outside the font that defined them they mean nothing at all,
    # so they are noise in an embedding rather than characters. Mapped to the
    # character they were drawn as. Any OTHER private-use character is caught by
    # the audit rather than guessed at here.
    #
    # The six below were found by the "private use area character" check on the
    # first full corpus, not by inspection. The code points are U+F000 + the
    # glyph's byte position in the font's own encoding, which is the convention
    # the two above already assume, and the reading is from Alan Wood's
    # Symbol/Wingdings tables:
    #
    #   U+F0A7  Wingdings 0xA7, black small square  -> bullet. 28 occurrences,
    #           and \u25aa (the same square, unfolded) is already mapped above.
    #   U+F0D8  Wingdings 0xD8, rightwards arrowhead -> bullet. 95 occurrences,
    #           and its sibling \u27a2 is already mapped above. Both of these
    #           are list markers, so BULLET at line 38 now splits the glued
    #           runs they left behind: the same segmentation bug adb2592 fixed
    #           for U+F0B7/U+F06C, still open for these two.
    #   U+F05B  Symbol 0x5B "["   \
    #   U+F05D  Symbol 0x5D "]"   /  read as brackets because that is what they
    #           spell in context ("\uf05bCFI-CIRAS\uf05d"). Through Wingdings
    #           they would be astronomically unrelated glyphs.
    #   U+F020  Symbol/Wingdings 0x20, space. Carries no glyph at all, so it
    #           folds to the space it stands for rather than to nothing.
    #   U+F02E  Symbol 0x2E, a period. One occurrence, and it sits in a line
    #           clean.py drops as too_short, so it moves no number today; it
    #           folds so that a re-rendition of that page cannot reintroduce it.
    "\uf0b7": "\u2022", "\uf06c": "\u2022",
    "\uf0a7": "\u2022", "\uf0d8": "\u2022",
    "\uf05b": "[", "\uf05d": "]", "\uf020": " ", "\uf02e": ".",
    # Wingdings 0x76, a diamond bullet: found in two award supplier names.
    "\uf076": "\u2022",
}



def fold_chars(text):
    out = []
    for ch in text:
        if ch in CHAR_MAP:
            out.append(CHAR_MAP[ch])
        elif unicodedata.category(ch) == "Cf":   # zero-width / directional marks
            continue
        else:
            out.append(ch)
    return "".join(out)


def fingerprint(line):
    """Whitespace *deleted*, not collapsed, and digits dropped. The same running
    header appears as both 'Project (P179138)' and 'Project(P179138)', and the
    page number in a footer changes every page."""
    return re.sub(r"[\s\d]", "", line).casefold()



def annex_match(line):
    """ANNEX at the start of a line is usually a heading, but not always: a
    wrapped cross-reference can leave 'Annex 1). The one-time CAPEX subsidies
    will support broadband...' sitting at the head of a line. A heading is short,
    carries no sentence boundary, and is not followed by a closing bracket."""
    m = ANNEX.match(line)
    if not m:
        return None
    rest = m.group(3).strip()
    if rest.startswith(")") or len(line.strip()) > 110:
        return None
    if re.search(r"[.!?]\s+[A-Z]", rest) or rest.endswith("."):
        return None
    return m




GUTTER = re.compile(r"\S {6,}\S")


def looks_tabular(body):
    """A flattened table reads as prose to everything downstream but carries
    almost no language: mostly figures, dates and currency. Label it rather than
    drop it - the results framework and disbursement tables hold real numbers -
    but keep it out of the narrative block so a text search does not hit it.

    Must run BEFORE whitespace is collapsed: the column gutters are the single
    most reliable signal, and collapsing destroys them. Two or more wide gutters
    means the line was a table row whatever its wording, which catches the
    results-framework tables that the numeric test misses because their
    indicator names are wordy."""
    if len(GUTTER.findall(body)) >= 2:
        return True
    toks = TOKEN.findall(body)
    if len(toks) < 6:
        return False
    numeric = sum(1 for t in toks if NUMERIC_TOKEN.match(t) or t.isdigit())
    wordy = sum(1 for t in toks if len(t) > 3 and not t.isdigit())
    digits = sum(c.isdigit() for c in body)
    return (numeric / len(toks) >= 0.35 or digits / max(len(body), 1) >= 0.20) and \
        wordy / len(toks) < 0.45
