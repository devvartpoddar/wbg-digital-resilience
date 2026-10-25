"""Text rules shared by the PDF reader and the cleaning stages: character folds, line
fingerprints, heading, annex and numbered-paragraph shapes, and the tabular test.
Their constants are part of the PDF reader's version (clean.parser_version).
"""
import re
import unicodedata

ROMAN = re.compile(r"^\s*((?:I|V|X)[IVX]*)\.\s+([A-Z][^a-z]*(?:[A-Z].*)?)\s*$")
LETTER = re.compile(r"^\s*([A-Z])\.\s+(\S.*)$")
ANNEX = re.compile(r"^\s*(ANNEX|Annex|APPENDIX|Appendix)\s+([0-9]{1,2}|[IVX]{1,4})\s*(?:[:.\-\u2013]\s*)?(.*)$")
NUMBERED_PARA = re.compile(r"^\s*(\d{1,3})\.\s+(\S)")
TOKEN = re.compile(r"[\w'’-]+", re.UNICODE)
NUMERIC_TOKEN = re.compile(r"^[\d.,%()$-]+$")
ANNEX_HEADING_MAX_CHARS = 110  # an annex heading line is no longer than this
TABULAR_MIN_TOKENS = 6         # fewer tokens than this is never judged tabular
TABULAR_NUMERIC_SHARE = 0.35   # tabular: at least this share of tokens are figures...
TABULAR_DIGIT_SHARE = 0.20     # ...or at least this share of characters are digits...
TABULAR_MAX_WORDY = 0.45       # ...and fewer than this share are words of four letters or more
WORDY_MIN_CHARS = 3            # a token longer than this, not a number, is a word

# Character folds.
CHAR_MAP = {
    " ": " ", " ": " ", " ": " ", " ": " ", "​": "",
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "«": '"', "»": '"',
    "\u2013": "-", "\u2212": "-", "\u00ad": "",
    # U+2010 HYPHEN and U+2011 NON-BREAKING HYPHEN are what a renderer emits for an ordinary
    # hyphen in "Non-Consulting" or "Sub-component".
    "\u2010": "-", "\u2011": "-",
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "\u25cf": "\u2022", "\u27a2": "\u2022", "\u25aa": "\u2022",
    # Private Use Area.
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
    if rest.startswith(")") or len(line.strip()) > ANNEX_HEADING_MAX_CHARS:
        return None
    if re.search(r"[.!?]\s+[A-Z]", rest) or rest.endswith("."):
        return None
    return m


def looks_tabular(body):
    """A flattened table reads as prose to everything downstream but carries
    almost no language: mostly figures, dates and currency. Label it rather than
    drop it - the results framework and disbursement tables hold real numbers -
    but keep it out of the narrative block so a text search does not hit it."""
    toks = TOKEN.findall(body)
    if len(toks) < TABULAR_MIN_TOKENS:
        return False
    numeric = sum(1 for t in toks if NUMERIC_TOKEN.match(t) or t.isdigit())
    wordy = sum(1 for t in toks if len(t) > WORDY_MIN_CHARS and not t.isdigit())
    digits = sum(c.isdigit() for c in body)
    return (numeric / len(toks) >= TABULAR_NUMERIC_SHARE
            or digits / max(len(body), 1) >= TABULAR_DIGIT_SHARE) and \
        wordy / len(toks) < TABULAR_MAX_WORDY
