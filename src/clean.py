#!/usr/bin/env python3
"""Turn raw appraisal-document text renditions into clean, addressable paragraphs.

Reads  data/raw/{doc_id}.txt      (as fetched, never modified)
       data/documents.csv
Writes data/clean/{doc_id}.txt    the cleaned reference text
       data/paragraphs.csv        one row per paragraph, offsets into the clean file
       data/rejected.csv          everything dropped, with a reason
       data/clean_report.txt

Paragraph text is not stored in the table. To read a paragraph, slice its clean
file with char_start:char_end. The raw file stays on disk untouched, so nothing
needs an offset map back to it.

Two document formats exist and both are handled:
  - Documents disclosed from about 2022 carry '@#&OPS...#doctemplate' markers
    naming each structured block (results framework, risk matrix, financing).
  - Older ones carry no markers. Form feeds, roman headings and a dot-leader
    contents page are present in both, so structure detection works either way.
"""
import argparse, csv, hashlib, os, re, sys, unicodedata
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    "–": "-", "−": "-", "­": "",
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "●": "•", "➢": "•", "▪": "•",
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
             "text_sha256"]
REJ_COLS = ["unit_id", "doc_id", "reason", "n_chars"]


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

    A heading is never joined onto: headings carry no terminal punctuation, so
    without this guard every heading absorbs the paragraph beneath it - which
    corrupts the heading text and, worse, turns a mid-document sentence into a
    false ANNEX match that mislabels everything after it."""
    out = []
    for line in lines:
        s = line.rstrip()
        if (out and out[-1].strip() and not is_block_start(line)
                and line.startswith(FOOTNOTE_SENTINEL) == out[-1].startswith(FOOTNOTE_SENTINEL)
                and not is_heading(out[-1])
                and not re.search(r"[.!?:;]\s*$", out[-1])
                and not re.match(r"^\s*$", s)):
            out[-1] = out[-1].rstrip() + " " + s.lstrip()
        else:
            out.append(s)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-tokens", type=int, default=5)
    args = ap.parse_args()

    raw_dir = os.path.join(args.data, "raw")
    clean_dir = os.path.join(args.data, "clean")
    os.makedirs(clean_dir, exist_ok=True)

    with open(os.path.join(args.data, "documents.csv"), newline="", encoding="utf-8") as fh:
        docs = list(csv.DictReader(fh))
    if args.limit:
        docs = docs[:args.limit]

    paras, rejected, stats = [], [], Counter()
    per_doc = []

    for n, doc in enumerate(docs, 1):
        src = os.path.join(raw_dir, f"{doc['doc_id']}.txt")
        if not os.path.exists(src):
            stats["missing_raw"] += 1
            continue
        raw = open(src, encoding="utf-8", errors="replace").read()
        clean, spans, fn = clean_document(raw)

        with open(os.path.join(clean_dir, f"{doc['doc_id']}.txt"), "w", encoding="utf-8") as fh:
            fh.write(clean)

        kept = 0
        for ordinal, (a, b, path, title, block) in enumerate(spans, 1):
            body = clean[a:b]
            pid = f"{doc['doc_id']}:p{ordinal:05d}"
            ntok = len(TOKEN.findall(body))
            if ntok < args.min_tokens and not (ROMAN.match(body) or annex_match(body)):
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
            })
            kept += 1
        stats["footnote_marks_stripped"] += fn["hits"]
        stats["footnote_marks_rejected"] += fn["misses"]
        stats["docs_without_page_breaks"] += fn.get("unpaged", 0)
        # Retention is the cheapest honest signal that cleaning has not eaten
        # something it should have kept. Headers, page numbers and contents
        # lines are a few per cent; anything much lower wants looking at.
        rn = len(re.sub(r"\s", "", raw))
        cn = len(re.sub(r"\s", "", clean))
        per_doc.append((doc["doc_id"], doc["doc_kind"], len(spans), kept,
                        sum(1 for s in spans if s[4] == "narrative"),
                        100.0 * cn / rn if rn else 0.0))
        if n % 25 == 0:
            print(f"  ...{n}/{len(docs)} documents, {len(paras)} paragraphs", flush=True)

    for name, rows, cols in (("paragraphs.csv", paras, PARA_COLS),
                             ("rejected.csv", rejected, REJ_COLS)):
        with open(os.path.join(args.data, name), "w", newline="", encoding="utf-8") as fh:
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
    with open(os.path.join(args.data, "clean_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"documents cleaned: {len(per_doc)}\n")
        fh.write(f"paragraphs kept:   {len(paras)}\n")
        fh.write(f"paragraphs dropped:{len(rejected)}\n\n")
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
        for d, kind, total, kept, narr, pct in per_doc:
            if narr == 0:
                fh.write(f"  {d}  kind={kind}  spans={total} kept={kept}\n")
        low = sorted((p, d, k) for d, k, t, kp, n, p in per_doc)
        fh.write("\ntext retention, lowest 15 (non-whitespace chars kept):\n")
        for pct, d, kind in low[:15]:
            fh.write(f"  {pct:5.1f}%  {d}  {kind}\n")
        if low:
            fh.write(f"  median {sorted(p for p, _, _ in low)[len(low)//2]:.1f}%\n")

    print(f"\n{len(paras)} paragraphs from {len(per_doc)} documents "
          f"({len(rejected)} dropped)")
    print("  blocks: " + "  ".join(f"{k}={v}" for k, v in blocks.most_common()))
    print(f"  footnote marks: {stats['footnote_marks_stripped']} stripped, "
          f"{stats['footnote_marks_rejected']} left alone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
