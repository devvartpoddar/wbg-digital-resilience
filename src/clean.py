#!/usr/bin/env python3
"""Turn appraisal documents into clean, addressable paragraphs and sentences.

Reads  data/raw/pdf/{doc_id}.pdf   the PDF, read by layout (src/pdf_layout.py)
       data/raw/text/{doc_id}.txt  the Bank's text rendition: the fallback, and
                                   the reference the review sheet compares with
       data/raw/documents.csv
Writes data/appraisal/text/{doc_id}.txt          the cleaned reference text
       data/appraisal/cache/{doc_id}.layout.json cached PDF read, keyed on checksums
       data/appraisal/paragraphs.csv  one row per paragraph, offsets into the clean file
       data/appraisal/sentences.csv   one row per sentence, offsets into the same file
       data/appraisal/rejected.csv    everything dropped, with a reason
       data/reports/clean.txt

Paragraph text is not stored in the table. To read a paragraph, slice its clean
file with char_start:char_end. The raw files stay on disk untouched.

Which reader. The PDF carries what the text rendition throws away - font size,
position, bold, raised characters - so it is read first. The text-rendition
cleaner below (clean_document) is kept for any document whose PDF is missing,
unreadable or image-only, and the row's `source` column says which was used.

The text-rendition cleaner handles two formats:
  - Documents disclosed from about 2022 carry '@#&OPS...#doctemplate' markers
    naming each structured block (results framework, risk matrix, financing).
  - Older ones carry no markers. Form feeds, roman headings and a dot-leader
    contents page are present in both, so structure detection works either way.
"""
import argparse, csv, hashlib, os, re, sys, unicodedata
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402

# A contents-page line ends in dot leaders and a page number, or in a run of
# spaces and a page number. A body heading ends in neither. Verified against
# nine documents; this replaces an earlier cluster heuristic that missed the
# first contents entry when it sat further from the rest.
TOC_TAIL = re.compile(r"(\.{2,}\s*\d{1,4}|\s{2,}\d{1,4})\s*$")

ROMAN = re.compile(r"^\s*((?:I|V|X)[IVX]*)\.\s+([A-Z][^a-z]*(?:[A-Z].*)?)\s*$")
LETTER = re.compile(r"^\s*([A-Z])\.\s+(\S.*)$")
ANNEX = re.compile(r"^\s*(ANNEX|Annex|APPENDIX|Appendix)\s+([0-9]{1,2}|[IVX]{1,4})\s*(?:[:.\-\u2013]\s*)?(.*)$")
MARKER = re.compile(r"@#&OPS.*?@(\w+)#doctemplate")

NUMBERED_PARA = re.compile(r"^\s*(\d{1,3})\.\s+(\S)")
BULLET = re.compile(r"^\s*(?:[•●➢▪\-]|\(?[ivxlc]{1,5}[\).]|\(?[a-z][\).])\s+\S")
# A footnote body starts with its number, in three shapes: number and text on
# one line, number running straight into the text with no space ("10Supporting
# climate resilient agriculture"), and the number alone above its text.
# Requiring whitespace missed the second, and those were exactly the ones that
# then leaked into the narrative as if they were body prose.
FOOTNOTE_BODY = re.compile(r"^\s{0,8}(\d{1,3})(?:\s*[A-Za-z“\"(]|\s*$)")
PAGE_FOOTER = re.compile(r"^\s*(?:Page\s+)?\d{1,4}(?:\s+of\s+\d{1,4})?(?:\s+of)?\s*$", re.I)
TOKEN = re.compile(r"[\w'’-]+", re.UNICODE)

# Inline footnote reference glued to the preceding word or to a closing period.
# The lookbehind is deliberately lowercase-only. A digit after an UPPERCASE
# letter belongs to an abbreviation - FY01, IDA19, SOP1, EUR25, HLO2, XOF603 -
# and stripping it silently rewrites a fiscal year or a currency figure. A real
# footnote marker attaches to the end of an ordinary word: project34, shocks46.
MARK_A = re.compile(r"(?<=[a-z\)\]\"’])(\d{1,3})(?=[\s,;:.\)\]\"]|$)")
MARK_B = re.compile(r"(?<=[a-z\)\]\"]\.)(\d{1,3})(?=[\s,;:\)\]\"]|$)")

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

NUMERIC_TOKEN = re.compile(r"^[\d.,%()$-]+$")
FOOTNOTE_OPEN = re.compile(r"^(\d{1,3})\s*(?=[A-Z\"(])")
# Marks a line as belonging to a page's footnote region while pages are being
# flattened. Never reaches stored text.
FOOTNOTE_SENTINEL = "\x01"


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


PARA_COLS = ["paragraph_id", "doc_id", "project_ids", "ordinal", "section_path",
             "section_title", "block", "char_start", "char_end", "n_tokens",
             "text_sha256", "page_from", "page_to", "source", "parser",
             "for_model", "list_item", "lead_in_id", "table_id", "raw_start", "raw_end",
             "component", "subcomponent", "component_mentions"]
REJ_COLS = ["unit_id", "doc_id", "reason", "n_chars"]


def decode_raw(blob):
    """Bytes of a rendition -> text, discarding bytes that form no character.

    The renditions are UTF-8, but a handful are not quite: the World Bank's own
    converter drops one byte out of a three-byte sequence, so a closing quote
    arrives as b'\\xe2\\x80' followed by '?' instead of b'\\xe2\\x80\\x9d', and
    one document carries CESU-8 surrogate pairs. Decoding with errors='replace'
    turns every one of those into U+FFFD, which is noise in an embedding and is
    not recoverable - the missing byte is gone at the source (re-fetching the
    same URL returns byte-identical corruption). A byte that forms no character
    is not content, so dropping the fragment loses nothing that "precision over
    recall" protects; it keeps the text the source could actually express.

    Returns (text, bytes_dropped). The count is reported in the clean run so a
    rendition that is broken wholesale shows as a number rather than a silent
    difference.
    """
    text = blob.decode("utf-8", errors="ignore")
    return text, len(blob) - len(text.encode("utf-8"))


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


def find_running_lines(pages):
    """Line indices, per page, that repeat at the same edge across the document."""
    top, bottom = Counter(), Counter()
    for page in pages:
        idx = [i for i, l in enumerate(page) if l.strip()]
        for slot, i in enumerate(idx[:3]):
            top[(slot, fingerprint(page[i]))] += 1
        for slot, i in enumerate(reversed(idx[-2:])):
            bottom[(slot, fingerprint(page[i]))] += 1
    need = max(3, int(0.5 * len(pages)))
    drop = []
    for page in pages:
        idx = [i for i, l in enumerate(page) if l.strip()]
        kill = set()
        for slot, i in enumerate(idx[:3]):
            if top[(slot, fingerprint(page[i]))] >= need:
                kill.add(i)
        for slot, i in enumerate(reversed(idx[-2:])):
            fp = fingerprint(page[i])
            if bottom[(slot, fp)] >= need or PAGE_FOOTER.match(page[i]):
                kill.add(i)
        drop.append(kill)
    return drop


HEADER_BANK = re.compile(r"^\s*The World Bank\s*$")
HEADER_PID = re.compile(r"\(P\d{6}\)")


def strip_header_pairs(page_lines):
    """Remove the 'The World Bank' / '<Project Title> (P123456)' header pair.

    The repetition test needs a document with enough pages to establish what
    repeats; a short one slips through. This pair is structural, not lexical -
    the bank line followed within two lines by a project identifier in brackets -
    so it can be removed wherever it appears."""
    kill = set()
    for i, line in enumerate(page_lines):
        if not HEADER_BANK.match(line):
            continue
        for j in range(i + 1, min(i + 3, len(page_lines))):
            if HEADER_PID.search(page_lines[j]):
                kill.update(range(i, j + 1))
                break
        else:
            kill.add(i)
    return [l for i, l in enumerate(page_lines) if i not in kill]


def footnote_bodies(page_lines):
    """Footnote numbers defined at the foot of a page, with the line indices.

    Used to decide whether an inline number is a footnote reference or part of a
    figure: 'EUR25.8', 'US$1', 'SOP1' and 'FY33' all look identical to a naive
    rule, so a marker is only stripped when this page actually defines a footnote
    with that number.

    Scanning strictly upward from the last line does not work, because a footnote
    body wraps onto continuation lines that do not start with a number. Instead
    look anywhere in the lower part of the page.
    """
    idx = [i for i, l in enumerate(page_lines) if l.strip()]
    if not idx:
        return set(), set()
    # The region must OPEN on a blank line. Without that a wrapped prose line
    # beginning with a figure - "100 and 911 will coexist..." - reads as
    # "footnote 100" and drags the rest of the page into the footnote block.
    start = None
    for i in idx[len(idx) // 2:]:
        if FOOTNOTE_BODY.match(page_lines[i]) and (i == 0 or not page_lines[i - 1].strip()):
            start = i
            break
    if start is None:
        return set(), set()
    # From there to the foot of the page is footnote material: the bodies and
    # the continuation lines their text wraps onto.
    nums, where = set(), set()
    for i in range(start, len(page_lines)):
        if not page_lines[i].strip():
            continue
        m = FOOTNOTE_BODY.match(page_lines[i])
        if m:
            nums.add(int(m.group(1)))
        where.add(i)
    return nums, where


def strip_footnote_marks(text, allowed, state):
    """Remove an inline footnote number only when this page (or the next) defines
    a footnote body with that number, and the numbers keep increasing."""
    def repl(m):
        val = int(m.group(1))
        if val in allowed and val > state["last"] and val not in state["used"]:
            state["last"] = val
            state["used"].add(val)
            state["hits"] += 1
            return ""
        state["misses"] += 1
        return m.group(0)
    return MARK_A.sub(repl, MARK_B.sub(repl, text))


def is_block_start(line):
    return bool(NUMBERED_PARA.match(line) or BULLET.match(line)
                or ROMAN.match(line) or annex_match(line) or LETTER.match(line))


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


def find_contents_lines(lines):
    """Indices of heading-shaped lines belonging to the contents page.

    Entries that carry a trailing page number are certain. Take the span they
    occupy and claim every heading-shaped line inside it, which picks up the
    stragglers a per-line test misses: one Tanzania entry ends '...digital
    commitments 81' with a single space before the number, indistinguishable
    from prose ending in a figure.

    Span rather than run-length, because contents entries are not evenly
    spaced - an Ethiopia contents page leaves gaps of seven lines between
    top-level entries, which a gap-based rule splits into fragments, leaving
    body_start in the middle of the contents and the remaining entries read as
    real headings.
    """
    heads = [i for i, l in enumerate(lines) if ROMAN.match(l) or annex_match(l)]
    tailed = [i for i in heads if TOC_TAIL.search(lines[i].rstrip())]
    if len(tailed) < 3:
        return set()
    lo, hi = min(tailed), max(tailed)
    # Absorb only an IMMEDIATELY adjacent further entry - one whose title wrapped
    # so its page number landed on the next line. Reaching further than this
    # swallows the first real body heading, which is far more damaging than
    # missing a contents entry: the annex-before-Section-I rule in
    # clean_document() is the backstop for anything this does not catch.
    for i in sorted(heads):
        if i > hi and i - hi <= 1:
            hi = i
    return {i for i in heads if lo <= i <= hi}


def is_heading(line):
    return bool(ROMAN.match(line) or annex_match(line) or LETTER.match(line))


def rejoin(lines):
    """Join a line to the next when the first does not end a sentence and the
    second does not open a new block. Undoes page-layout line wrapping.

    Body text and footnotes are rejoined as SEPARATE streams. A page's footnote
    region sits physically between a paragraph that ran off the foot of the page
    and its continuation overleaf, so joining strictly to the previous line left
    every such paragraph split. Tracking the last body line and the last footnote
    line independently lets the body flow across the interruption while keeping
    both in their original order.

    A heading is never joined onto: headings carry no terminal punctuation, so
    without that guard each one absorbs the paragraph beneath it.
    """
    out = []
    last_body = -1
    last_fn = -1
    last_was_footnote = False

    def can_extend(target, line):
        return (target >= 0 and out[target].strip()
                and not is_block_start(line)
                and not is_heading(out[target].replace(FOOTNOTE_SENTINEL, ""))
                and not re.search(r"[.!?:;]\s*$", out[target]))

    for line in lines:
        s = line.rstrip()
        if not s.strip():
            out.append(s)
            last_fn = -1
            # A blank after a footnote region separates the footnotes from what
            # follows, not one body paragraph from the next. Resetting the body
            # stream there is what kept a paragraph interrupted by footnotes from
            # ever rejoining with its continuation overleaf.
            if not last_was_footnote:
                last_body = -1
            continue
        if line.startswith(FOOTNOTE_SENTINEL):
            if can_extend(last_fn, line):
                out[last_fn] = out[last_fn].rstrip() + " " + s.lstrip().replace(
                    FOOTNOTE_SENTINEL, "")
            else:
                out.append(s)
                last_fn = len(out) - 1
            last_was_footnote = True
            continue
        if can_extend(last_body, line):
            out[last_body] = out[last_body].rstrip() + " " + s.lstrip()
        else:
            out.append(s)
            last_body = len(out) - 1
        last_was_footnote = False
    return out


def clean_document(raw):
    """raw text -> (clean text, list of (start, end, section_path, title, block))."""
    text = fold_chars(raw.replace("\r\n", "\n").replace("\r", "\n"))
    pages = [p.split("\n") for p in text.split("\f")]
    drops = find_running_lines(pages)
    # Footnote bodies must be found AFTER the running header and page-number
    # lines are out of the way: they sit at the very foot of the page, so a
    # 'Page 7' line below them aborts the upward scan and finds nothing.
    stripped_pages = [strip_header_pairs([l for i, l in enumerate(pg) if i not in kill])
                      for pg, kill in zip(pages, drops)]
    # A few renditions carry no form feeds at all, so the whole document is one
    # "page". Page-foot logic is meaningless there: the footnote scan would range
    # over half the document and strip digits out of tables. Leaving markers in
    # is the lesser harm, so skip stripping and count it.
    paged = len(stripped_pages) >= 3
    if paged:
        scanned = [footnote_bodies(p) for p in stripped_pages]
    else:
        scanned = [(set(), set()) for _ in stripped_pages]
    bodies = [n for n, _ in scanned]
    all_footnotes = set().union(*bodies) if bodies else set()

    state = {"last": 0, "used": set(), "hits": 0, "misses": 0}
    kept_pages = []
    for n, page in enumerate(stripped_pages):
        allowed = bodies[n] | (bodies[n + 1] if n + 1 < len(bodies) else set())
        body_line_idx = scanned[n][1]
        # Footnotes sit at the foot of the page, and their text wraps onto
        # continuation lines that do not start with a number. Treating only the
        # opening line as a footnote orphaned every continuation into the
        # narrative as a paragraph starting mid-sentence. From the first
        # footnote line to the end of the page is footnote material.
        fn_from = min(body_line_idx) if body_line_idx else None
        lines = []
        for i, line in enumerate(page):
            in_fn = fn_from is not None and i >= fn_from
            if not in_fn:
                line = strip_footnote_marks(line, allowed, state)
            elif line.strip():
                line = FOOTNOTE_SENTINEL + line
            lines.append(line)
        kept_pages.append(lines)

    # Rejoin across the whole document, not page by page: the running headers
    # and page numbers are already gone, so a paragraph that runs off the foot
    # of one page sits directly above its continuation. Rejoining per page left
    # every one of those split, as a fragment starting mid-sentence.
    merged = []
    for page in kept_pages:
        lines = list(page)
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        lines = [l for i, l in enumerate(lines)
                 if l.strip() or not (i + 1 < len(lines)
                                      and lines[i + 1].startswith(FOOTNOTE_SENTINEL))]
        if merged and lines:
            prev = merged[-1].rstrip()
            # Blank lines at the page edges are layout, not paragraph breaks.
            # Keep a separator only where the previous page actually finished a
            # sentence or the next page opens a new block; otherwise let the two
            # halves meet so rejoin can mend the paragraph.
            if (not prev or re.search(r"[.!?:;]\s*$", prev)
                    or is_block_start(lines[0])
                    or lines[0].startswith(FOOTNOTE_SENTINEL)
                    or prev.startswith(FOOTNOTE_SENTINEL)):
                merged.append("")
        merged.extend(lines)
    flat = rejoin(merged)

    toc_lines = find_contents_lines(flat)
    body_start = max(toc_lines) + 1 if toc_lines else 0

    blocks, cur = [], []
    heading_flush = [False]
    sec_path, sec_title, marker = "", "", ""
    annex_seen = False
    # Some documents are a standalone annex, disclosed on their own - a technical
    # note pulled out of a PAD, for instance. They have no Section I, so the
    # "an annex cannot precede Section I" rule would suppress their only heading
    # and leave the document with no body at all. Where the document contains no
    # body roman heading anywhere, that rule has nothing to protect.
    roman_seen = not any(
        ROMAN.match(l) for i, l in enumerate(flat) if i not in toc_lines)
    spans = []
    clean_parts, cursor = [], 0

    def flush():
        nonlocal cur, cursor
        if not cur:
            return
        raw_body = "\n".join(cur).strip("\n")
        cur = []
        if not raw_body.strip():
            return
        # Decide tabular on the laid-out text, then store prose with whitespace
        # collapsed: a paragraph is one line, with no page-layout spacing left
        # in it to reach the embedding.
        tabular = looks_tabular(raw_body)
        from_footnote = FOOTNOTE_SENTINEL in raw_body
        raw_body = raw_body.replace(FOOTNOTE_SENTINEL, "")
        body = re.sub(r"\s+", " ", raw_body).strip()
        if not body:
            return
        # A footnote body flattened into a paragraph: it opens with the number it
        # defines, e.g. '38Vietnam provides a good example'. Label it rather than
        # let it sit in the narrative as if it were body prose.
        m_fn = FOOTNOTE_OPEN.match(body)
        is_footnote = from_footnote or (bool(m_fn) and int(m_fn.group(1)) in all_footnotes)
        if is_footnote and m_fn:
            # Drop the leading number only where the paragraph actually opens
            # with one; a continuation line carries no number of its own.
            body = body[m_fn.end(1):].lstrip()
        if is_footnote and not body:
            return
        # A DETACHED footnote marker - '...infrastructure 17 and low capacity...'
        # - is deliberately NOT stripped. Nothing separates it from a quantity:
        # an A/B test over the corpus showed five of six removals destroyed real
        # content ("SDGs 9 and 11", "Component 1 will build", "Lines 100 and
        # 911"), against one genuine marker. A stray digit costs far less than a
        # silently deleted figure.
        start = cursor
        clean_parts.append(body + "\n\n")
        cursor += len(body) + 2
        block = ("heading" if heading_flush[0] else
                 "footnote" if is_footnote else
                 "template:" + marker if marker else
                 "frontmatter" if (start_idx[0] < body_start or not sec_path) else
                 "annex" if annex_seen else "narrative")
        if block in ("narrative", "annex") and tabular:
            block = "table"
        spans.append((start, start + len(body), sec_path, sec_title, block))
        heading_flush[0] = False

    start_idx = [0]
    for i, line in enumerate(flat):
        m_mark = MARKER.search(line)
        if m_mark:
            flush()
            marker = m_mark.group(1)
            start_idx = [i]
            continue

        m_roman, m_annex = ROMAN.match(line), annex_match(line)
        is_toc = i in toc_lines or bool(TOC_TAIL.search(line.rstrip()))

        if (m_roman or m_annex) and not is_toc and i >= body_start:
            flush()
            marker = ""
            # An annex heading cannot precede Section I of the body. Where one
            # appears to, it is a contents entry whose page number wrapped onto
            # the next line and so escaped the contents span. Treating it as a
            # real annex latches every later paragraph into the annex block and
            # leaves the document with no narrative at all.
            if m_annex and not roman_seen:
                continue
            if m_annex:
                annex_seen = True
                sec_path = f"{m_annex.group(1).upper()} {m_annex.group(2)}"
                sec_title = m_annex.group(3).strip(" .:-")
            else:
                roman_seen = True
                sec_path = m_roman.group(1)
                sec_title = m_roman.group(2).strip(" .:-")
            start_idx = [i]
            cur = [line.strip()]
            heading_flush[0] = True
            flush()
            continue

        m_letter = LETTER.match(line)
        if m_letter and not is_toc and i >= body_start and not annex_seen and sec_path:
            flush()
            sec_path = f"{sec_path.split('.')[0]}.{m_letter.group(1)}"
            sec_title = m_letter.group(2).strip(" .:-")
            start_idx = [i]
            cur = [line.strip()]
            heading_flush[0] = True
            flush()
            continue

        if not line.strip():
            flush()
            start_idx = [i + 1]
            continue
        if is_block_start(line) and cur:
            flush()
            start_idx = [i]
        if not cur:
            start_idx = [i]
        cur.append(line)
    flush()

    state["unpaged"] = 0 if paged else 1
    return "".join(clean_parts), spans, state


PARSER_TXT = "clean-txt-1"
# A PDF whose text layer holds fewer characters than this is a scan, not a
# document that can be read by layout.
PDF_MIN_CHARS = 2000
# Footnotes are mostly references ('ITU. 2021. Global Cybersecurity Index
# 2020.'), which a sentence splitter can only cut into nonsense.
SENTENCE_BLOCKS = {"narrative", "annex"}
# What goes to embedding and to the model test. Front matter (cover, data
# sheet, acronyms, contents) and headings are kept and cleaned, but are too
# short and too repetitive to be worth scoring.
# What the embedding and the decision model are given: the body prose and
# its footnotes. Front matter and tables stay in the tables for reading, but
# are not cleaned to the standard prose is and add little the prose does not
# say; component names and costs are read from the front matter separately
# (src/components.py), straight from the PDF.
MODEL_BLOCKS = {"narrative", "annex", "footnote"}


def citation_only(text, block):
    """A footnote that is a reference and a link, and nothing else: 'World
    Bank. 2021. Climate Risk Profile. [link]'. It is a bibliography entry, so it
    is kept for reading and left out of what the model is given."""
    if block != "footnote" or "[link]" not in text:
        return False
    return len(re.findall(r"[^\W\d_]{2,}", text.replace("[link]", ""))) < 15


def write_if_changed(path, text):
    """Write a file only when its content differs, so a re-run touches nothing."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(path + ".part", path)
SENT_COLS = ["sentence_id", "paragraph_id", "doc_id", "ordinal", "char_start",
             "char_end", "n_tokens", "text_sha256"]


def parser_version():
    """The PDF reader's identity: pdfplumber's version plus a checksum of the
    layout code itself, so any change to the rules invalidates the cache and is
    recorded on every row it produced - no version number to forget to bump."""
    import pdfplumber
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    # Only the reader: what is cached is pdf_layout.extract's output, and an
    # edit elsewhere in clean.py must not cost an hour of re-reading.
    with open(os.path.join(here, "pdf_layout.py"), "rb") as fh:
        h.update(fh.read())
    # ...plus the helpers from this module that the reader calls.
    import inspect
    import pdf_layout
    names = sorted(set(re.findall(r"\bC\.([A-Za-z_]\w*)", inspect.getsource(pdf_layout)))
                   | {"CHAR_MAP"})   # what fold_chars folds
    for name in names:
        obj = globals()[name]
        h.update(obj.pattern.encode() if hasattr(obj, "pattern") else
                 inspect.getsource(obj).encode() if callable(obj) else repr(obj).encode())
    return f"pdf-layout-{h.hexdigest()[:10]}/pdfplumber-{pdfplumber.__version__}"


def _layout_job(args):
    """Worker: read one PDF. Runs in a separate process."""
    doc_id, path = args
    import pdfplumber
    import pdf_layout
    try:
        raw = []
        with pdfplumber.open(path) as pdf:
            text, spans, stats = pdf_layout.extract(pdf, raw)
        return doc_id, text, [list(s) for s in spans], dict(stats), raw[0], ""
    except Exception as exc:                       # a broken PDF is a fallback, not a crash
        return doc_id, "", [], {}, "", f"{type(exc).__name__}: {exc}"


def layout_cached(data, docs, workers):
    """{doc_id: (text, spans, stats, error, raw)} for every document with a PDF;
    raw is the PDF's text as read, before cleaning.

    Each result is cached in data/appraisal/cache/{doc_id}.layout.json, keyed on the PDF's
    SHA-256 and the parser version, so a re-run reads the cache and only a
    changed PDF or a changed parser costs a re-read."""
    import json
    from concurrent.futures import ProcessPoolExecutor
    version = parser_version()
    out, todo = {}, []
    for doc in docs:
        pdf = where(data, "pdf", f"{doc['doc_id']}.pdf")
        if not os.path.exists(pdf):
            continue
        sha = doc.get("pdf_sha256") or hashlib.sha256(open(pdf, "rb").read()).hexdigest()
        cache = where(data, "cache", f"{doc['doc_id']}.layout.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as fh:
                c = json.load(fh)
            if c.get("pdf_sha256") == sha and c.get("parser") == version:
                out[doc["doc_id"]] = (c["text"], c["spans"], c["stats"], c.get("error", ""),
                                      c.get("raw", ""))
                continue
        todo.append((doc["doc_id"], pdf, sha, cache))
    if todo:
        print(f"  reading {len(todo)} PDFs with {workers} workers "
              f"({len(out)} cached)", flush=True)
        meta = {d: (sha, cache) for d, _, sha, cache in todo}
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for n, (did, text, spans, stats, raw, err) in enumerate(
                    ex.map(_layout_job, [(d, p) for d, p, _, _ in todo]), 1):
                sha, cache = meta[did]
                tmp = cache + ".part"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"pdf_sha256": sha, "parser": version, "text": text,
                               "spans": spans, "stats": stats, "error": err,
                               "raw": raw}, fh)
                os.replace(tmp, cache)
                out[did] = (text, spans, stats, err, raw)
                if n % 10 == 0:
                    print(f"    ...{n}/{len(todo)} PDFs read", flush=True)
    return out


def main():
    import sentences
    import components
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-tokens", type=int, default=5)
    ap.add_argument("--workers", type=int, default=3,
                    help="PDF reading processes; the box has four cores")
    ap.add_argument("--txt-only", action="store_true",
                    help="ignore PDFs and use the text renditions (the old path)")
    args = ap.parse_args()

    raw_dir = where(args.data, "rendition")
    clean_dir = where(args.data, "text")
    for d in (clean_dir, where(args.data, "cache"), where(args.data, "reports")):
        os.makedirs(d, exist_ok=True)

    with open(where(args.data, "documents"), newline="", encoding="utf-8") as fh:
        docs = list(csv.DictReader(fh))
    if args.limit:
        docs = docs[:args.limit]

    layouts = {} if args.txt_only else layout_cached(args.data, docs, args.workers)
    version = None if args.txt_only else parser_version()

    paras, rejected, sents, stats = [], [], [], Counter()
    per_doc = []

    for n, doc in enumerate(docs, 1):
        raw = ""
        source, parser = "txt", PARSER_TXT
        lay = layouts.get(doc["doc_id"])
        if lay and not lay[3]:
            text, spans, lstats, _, raw = lay
            if len(re.sub(r"\s", "", raw)) >= PDF_MIN_CHARS:
                clean, source, parser = text, "pdf", version
                spans = [tuple(s) + (({},) if len(s) == 7 else ()) for s in spans]
                for k, v in lstats.items():
                    stats["pdf_" + k] += v
                write_if_changed(where(args.data, "pdf_text", f"{doc['doc_id']}.txt"), raw)
            else:
                stats["pdf_too_little_text"] += 1
                raw = ""
        elif lay and lay[3]:
            stats["pdf_unreadable"] += 1
        if source == "txt":
            # The Bank's text rendition is no longer fetched; one already on
            # disk is used only for a document whose PDF is missing or holds no
            # text (a scan).
            src = os.path.join(raw_dir, f"{doc['doc_id']}.txt")
            if os.path.exists(src):
                with open(src, "rb") as fh:
                    raw, undecodable = decode_raw(fh.read())
                stats["undecodable_bytes_dropped"] += undecodable
            if not raw:
                stats["missing_raw"] += 1
                continue
            clean, spans, fn = clean_document(raw)
            spans = [tuple(s) + ("", "", {}) for s in spans]
            stats["footnote_marks_stripped"] += fn["hits"]
            stats["footnote_marks_rejected"] += fn["misses"]
            stats["docs_without_page_breaks"] += fn.get("unpaged", 0)
        stats["source_" + source] += 1

        with open(os.path.join(clean_dir, f"{doc['doc_id']}.txt"), "w", encoding="utf-8") as fh:
            fh.write(clean)

        kept = 0
        tagger = components.Tagger()
        for ordinal, (a, b, path, title, block, p0, p1, meta) in enumerate(spans, 1):
            body = clean[a:b]
            pid = f"{doc['doc_id']}:p{ordinal:05d}"
            comp, sub = tagger.see(body, block, path)
            ntok = len(TOKEN.findall(body))
            if ntok < args.min_tokens and block != "heading" \
                    and not (ROMAN.match(body) or annex_match(body)):
                rejected.append({"unit_id": pid, "doc_id": doc["doc_id"],
                                 "reason": "too_short", "n_chars": len(body)})
                stats["too_short"] += 1
                continue
            paras.append({
                "paragraph_id": pid, "doc_id": doc["doc_id"],
                "project_ids": doc["project_ids"], "ordinal": ordinal,
                "section_path": path, "section_title": title[:120], "block": block,
                "char_start": a, "char_end": b, "n_tokens": ntok,
                "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "page_from": p0, "page_to": p1, "source": source, "parser": parser,
                "for_model": str(block in MODEL_BLOCKS
                                 and not citation_only(body, block)).lower(),
                "list_item": str(bool(meta.get("list_item"))).lower(),
                "lead_in_id": (f"{doc['doc_id']}:p{meta['lead_in'] + 1:05d}"
                               if "lead_in" in meta else ""),
                "table_id": (f"{doc['doc_id']}:t{meta['table']:03d}"
                             if "table" in meta else ""),
                "raw_start": (meta.get("src") or ["", ""])[0],
                "raw_end": (meta.get("src") or ["", ""])[1],
                "component": comp, "subcomponent": sub,
                "component_mentions": "|".join(components.mentions(body)),
            })
            kept += 1
            if block in SENTENCE_BLOCKS:
                for k, (sa, sb) in enumerate(sentences.split(body), 1):
                    st = body[sa:sb]
                    sents.append({
                        "sentence_id": f"{pid}:s{k:03d}", "paragraph_id": pid,
                        "doc_id": doc["doc_id"], "ordinal": k,
                        "char_start": a + sa, "char_end": a + sb,
                        "n_tokens": len(TOKEN.findall(st)),
                        "text_sha256": hashlib.sha256(st.encode("utf-8")).hexdigest(),
                    })
        # Retention is the cheapest honest signal that cleaning has not eaten
        # something it should have kept. Headers, page numbers and contents
        # lines are a few per cent; anything much lower wants looking at.
        rn = len(re.sub(r"\s", "", raw))
        cn = len(re.sub(r"\s", "", clean))
        per_doc.append((doc["doc_id"], doc["doc_kind"], len(spans), kept,
                        sum(1 for s in spans if s[4] == "narrative"),
                        100.0 * cn / rn if rn else 0.0, source))
        if n % 25 == 0:
            print(f"  ...{n}/{len(docs)} documents, {len(paras)} paragraphs", flush=True)

    for name, rows, cols in (("paragraphs.csv", paras, PARA_COLS),
                             ("rejected.csv", rejected, REJ_COLS),
                             ("sentences.csv", sents, SENT_COLS)):
        with open(where(args.data, "appraisal", name), "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    # A shared regional document serves several projects. If it ever lands in
    # documents.csv once per project again, every one of its paragraphs is
    # emitted several times under the same identifier - which is silent, and
    # poisons any count or join downstream. Fail loudly instead.
    dupes = [k for k, v in Counter(p["paragraph_id"] for p in paras).items() if v > 1]
    if dupes:
        raise SystemExit(
            f"{len(dupes)} duplicate paragraph_id values, e.g. {dupes[:3]}. "
            "documents.csv must hold one row per doc_id, with project_ids pipe-delimited.")

    blocks = Counter(p["block"] for p in paras)
    sections = Counter(p["section_path"] for p in paras)
    with open(where(args.data, "reports", "clean.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"documents cleaned: {len(per_doc)}\n")
        fh.write(f"  read from PDF:   {stats['source_pdf']}\n")
        fh.write(f"  read from text:  {stats['source_txt']}\n")
        fh.write(f"paragraphs kept:   {len(paras)}\n")
        fh.write(f"paragraphs dropped:{len(rejected)}\n")
        fh.write(f"sentences:         {len(sents)}\n\n")
        fh.write("counters:\n")
        for k in sorted(stats):
            fh.write(f"  {k}: {stats[k]}\n")
        fh.write("\nby block:\n")
        for k, v in blocks.most_common():
            fh.write(f"  {v:>7}  {k}\n")
        fh.write("\ntop section paths:\n")
        for k, v in sections.most_common(30):
            fh.write(f"  {v:>7}  {k or '(none)'}\n")
        fh.write("\ndocuments with no narrative paragraphs:\n")
        for d, kind, total, kept, narr, pct, src in per_doc:
            if narr == 0:
                fh.write(f"  {d}  kind={kind}  source={src}  spans={total} kept={kept}\n")
        fh.write("\ndocuments read from the text rendition:\n")
        for d, kind, total, kept, narr, pct, src in per_doc:
            if src == "txt":
                fh.write(f"  {d}  kind={kind}\n")
        low = sorted((p, d, k, src) for d, k, t, kp, n, p, src in per_doc)
        fh.write("\ntext retention against the text rendition, lowest 15:\n")
        for pct, d, kind, src in low[:15]:
            fh.write(f"  {pct:5.1f}%  {d}  {kind}  {src}\n")
        if low:
            fh.write(f"  median {sorted(p for p, *_ in low)[len(low)//2]:.1f}%\n")

    print(f"\n{len(paras)} paragraphs, {len(sents)} sentences from {len(per_doc)} documents "
          f"({len(rejected)} dropped; {stats['source_pdf']} from PDF, "
          f"{stats['source_txt']} from text)")
    print("  blocks: " + "  ".join(f"{k}={v}" for k, v in blocks.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
