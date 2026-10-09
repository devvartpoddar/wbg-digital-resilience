"""Read an appraisal document from its PDF, using layout rather than guesswork.

The World Bank's own text rendition of a PDF throws away everything that says
what a line IS: its font size, its position on the page, whether it is bold,
whether a character is raised. src/clean.py has to infer all of that back from
line patterns, and its residual defects are exactly the places where the
inference fails - a footnote number fused to the word it annotated
("202024"), a running header inside a paragraph, a footnote body read as prose.

The PDF still has that information, so this reads it directly:

  running header / footer   lines repeating at the same height on many pages,
                            plus page-number lines and the rotated
                            "Public Disclosure Authorized" watermark
  footnote marker           a raised digit set smaller than the line it sits on
  footnote body             the run of small-type lines at the foot of a page
  heading                   a short line set in bold, or larger than body text
  table                     a region pdfplumber finds ruled as a table; each
                            row becomes one unit, its cells joined by " | "
  paragraph                 a vertical gap larger than the line pitch, or a
                            numbered paragraph or bullet opening a new line

Everything is decided from measurements the document gives, relative to its
own body font size and line pitch, so no threshold depends on one template.

Output matches clean.clean_document: the cleaned text, and one span per unit,
so every stage downstream is unchanged. Spans also carry the pages they came
from, so a reader can open the PDF at the right place.
"""
import re
import statistics
from collections import Counter

import clean as C

WATERMARK_MAX_SIZE = 6.0      # the rotated disclosure stamp is set at ~4.7pt
SUPERSCRIPT_RATIO = 0.80      # a footnote marker is set at most this size of its line
SMALL_RATIO = 0.93            # footnote body type: smaller than body text by this much
HEADER_REPEAT = 0.30          # a line at the same height on this share of pages is furniture
TOC_LINE = re.compile(r"(\.{3,}|\s\.\s\.\s)\s*\d{1,4}\s*$")
PAGE_NUM = re.compile(r"^\s*(?:Page\s+)?\d{1,4}(?:\s+of\s+\d{1,4})?\s*$", re.I)
# Page furniture recognisable by its words alone, wherever on the page it sits:
# the classification banner some restructuring papers repeat, and the hidden
# template markers the Bank's authoring system leaves in the PDF text layer.
FURNITURE_TEXT = re.compile(
    r"^(?:for\s+)?official\s+use\s+only(?:\s+page\s+\d+(?:\s+of\s+\d+)?|\s+[ivxlc]{1,6})?$"
    r"|@#&OPS"
    # Hidden template codes left in the text layer: RESULT_FRAME_TBL_PDO.
    r"|^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}$"
    # A dated footer: 'Oct 12, 2023 Page 27 of 29'.
    r"|^[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}\s+Page \d+ of \d+$", re.I)
# A footnote reference that was not set raised: digits run onto the end of a
# word or a closing bracket, 'hoped44', 'process104.'. Only removed when the
# page's own footnotes define that number.
INLINE_MARK = re.compile(r"(?<=[a-z\)\]\"'])(\d{1,3})(?=[\s,.;:)]|$)")
# A web address, including the pieces a line break left after a space
# ('https://www.example.org/en/ country/report.pdf'). Replaced by "[link]": it
# costs the model many tokens and says nothing about assets or measures.
URL = re.compile(r"(?:https?://|www\.)\S+(?:\s(?=[\w.\-]*[/=])[\w./?=&%#~:+\-]+)*", re.I)
TITLE_LINE = re.compile(r"^(?:Sub-?component|Component|Figure|Table|Box|Map|Chart)\s*[\dA-Z]")
BULLET_OPEN = re.compile(r"^\s*(?:[•●▪■◦\-–]|\(?[ivxlc]{1,5}[\).]|\(?[a-z][\).]|\d{1,2}\))\s+\S")


TEMPLATE_CODE = re.compile(r"\s*\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}\b|\s*\b[A-Z]{2,} Table SPACE\b")


def _norm(text):
    return re.sub(r"\s+", " ", TEMPLATE_CODE.sub("", C.fold_chars(text))).strip()


def _line_text(line, chars, line_size):
    """A line's text as pdfplumber spaced it, minus raised small digits.

    pdfplumber's own word spacing is kept - rebuilding it from character gaps
    runs justified text together. Its line text is the line's characters in
    order with spaces inserted, so the two are walked in step and only the
    characters flagged as footnote markers are left out."""
    full = [c["bottom"] for c in chars if c["size"] >= SUPERSCRIPT_RATIO * line_size]
    baseline = statistics.median(full) if full else None
    drop = set()
    # The number a footnote body opens with is its own, not a marker: keep the
    # whole leading run of digits, not just the first one ('43', not '4').
    lead = 0
    while lead < len(chars) and (chars[lead]["text"].isdigit() or chars[lead]["text"].isspace()):
        lead += 1
    for k, ch in enumerate(chars):
        t = ch["text"]
        if k < lead:
            continue
        # A footnote marker is small AND raised: its bottom sits well above the
        # line's baseline. Small alone is not enough - the 2 in CO2 is small too,
        # but lowered, and must survive.
        if (k and baseline is not None and ch["size"] < SUPERSCRIPT_RATIO * line_size
                and (t.isdigit() or t in ",*")
                and ch["bottom"] < baseline - 0.1 * line_size):
            drop.add(k)
    # A list bullet drawn from a symbol font arrives as whatever letter sits at
    # that code point in the font's own table - Wingdings 'y', Courier 'o',
    # Symbol 'r' - and reads as a stray word. At the head of a line, in such a
    # font, it is a bullet.
    first = next((k for k, ch in enumerate(chars) if not ch["text"].isspace()), None)
    bullet_at = None
    if first is not None:
        f = chars[first]["fontname"]
        if any(n in f for n in ("Wingdings", "Symbol", "Dingbat")) or \
                ("Courier" in f and chars[first]["text"] == "o"):
            bullet_at = first
    if not drop and bullet_at is None:
        return line["text"]
    out, j, text = [], 0, line["text"]
    i = 0
    while i < len(text):
        if j < len(chars) and chars[j]["text"] and text.startswith(chars[j]["text"], i):
            n = len(chars[j]["text"])
            if j == bullet_at:
                out.append("•")
            elif j not in drop:
                out.append(text[i:i + n])
            i += n
            j += 1
        else:
            out.append(text[i])
            i += 1
    return re.sub(r" {2,}", " ", "".join(out))


def _not_rotated(obj):
    """Drop characters set at an angle: the 'Public Disclosure Authorized'
    stamp down the page margin. pdfplumber still calls them upright, so the
    text matrix is read directly; its shear terms are zero for level text."""
    if obj.get("object_type") != "char":
        return True
    m = obj.get("matrix") or (1, 0, 0, 1)
    return abs(m[1]) < 0.1 and abs(m[2]) < 0.1


def _page_lines(page):
    """(lines, raw): the level lines on a page as dicts (top, bottom, x0, x1,
    size, bold, italic, text, raw_i), and every line exactly as the PDF gives
    it, before anything is removed; raw_i points a line at its raw line."""
    lines, raw = [], []
    for ln in page.extract_text_lines(return_chars=True, strip=True):
        raw.append(ln["text"])
        if not any(c.get("upright", True) and c["size"] >= WATERMARK_MAX_SIZE
                   for c in ln["chars"]):
            continue                        # the rotated disclosure stamp
        chars = ln["chars"]
        sizes = [round(c["size"], 1) for c in chars if not c["text"].isspace()]
        if not sizes:
            continue
        size = Counter(sizes).most_common(1)[0][0]
        bold = sum("Bold" in c["fontname"] or "Black" in c["fontname"] for c in chars) > 0.6 * len(chars)
        italic = sum("Italic" in c["fontname"] or "Oblique" in c["fontname"]
                     for c in chars) > 0.6 * len(chars)
        text = _norm(_line_text(ln, chars, size))
        if text:
            lines.append({"top": ln["top"], "bottom": ln["bottom"], "x0": ln["x0"],
                          "x1": ln["x1"], "size": size, "bold": bold, "italic": italic,
                          "text": text, "raw_i": len(raw) - 1})
    return lines, raw


def _tables(page):
    """Ruled tables on the page, ignoring the page-sized frame some covers draw."""
    out = []
    area = page.width * page.height
    for tb in page.find_tables():
        x0, top, x1, bottom = tb.bbox
        rows = tb.extract()
        if (x1 - x0) * (bottom - top) > 0.8 * area:
            continue
        if len(rows) < 2 or max(len(r) for r in rows) < 2:
            continue
        out.append((tb.bbox, rows))
    return out


NUMERIC_CELL = re.compile(r"^[\s\d.,%$()+\-–/:]*\d[\s\d.,%$()+\-–/:]*[A-Za-z]{0,4}$")


def _is_numeric(cell):
    return bool(NUMERIC_CELL.match(cell))


def _clean_table(rows, vocab):
    """A pdfplumber table -> (header or None, data rows, caption or None).

    - a cell that wraps keeps its line breaks in pdfplumber's output; they are
      joined, with a word broken at a hyphen mended as in prose
    - hidden template text in a cell ('@#&OPS...', 'RESULT_FRAME_TBL_PDO') goes
    - columns empty in every row go: merged cells leave many of them
    - the header is the first row of two or more labels that some later row
      answers with a figure; single-cell rows above it are titles, and the last
      of them is the table's caption ('Project Development Objective
      Indicators')
    - a row of small numbers under the header is a sub-header: 'Intermediate
      Targets' over '1  2' becomes 'Intermediate Targets 1', '... 2'
    - a table ruled only around its edge yields one row per printed line, so a
      label wrapping over three lines with its figures on the middle one is
      three rows. They are regrouped: label fragments and figures gather into
      one row until a new label opens with a capital after figures were seen
    """
    def cell(c):
        if not c:
            return ""
        parts = [p for p in (c or "").split("\n") if "@#&OPS" not in p]
        out = ""
        for p in parts:
            p = _norm(p)
            if not p:
                continue
            out = _dehyphenate(out, p, vocab) if out else p
        return out

    grid = [[cell(c) for c in r] for r in rows]
    grid = [r for r in grid if any(r)]
    if not grid:
        return None, [], None
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    keep = [j for j in range(width) if any(r[j] for r in grid)]
    grid = [[r[j] for j in keep] for r in grid]

    def numeric_cells(r):
        return [j for j, v in enumerate(r) if v and _is_numeric(v)]

    def text_cells(r):
        return [j for j, v in enumerate(r) if v and not _is_numeric(v)]

    h = None
    for i, r in enumerate(grid[:12]):
        if len(text_cells(r)) >= 2 and not numeric_cells(r) and \
                any(numeric_cells(x) for x in grid[i + 1:]):
            h = i
            break
    if h is None:
        # No header: only fold single-cell continuation rows into the row above.
        merged = []
        for r in grid:
            filled = [j for j, v in enumerate(r) if v]
            if merged and len(filled) == 1 and not _is_numeric(r[filled[0]]) \
                    and merged[-1][filled[0]]:
                j = filled[0]
                merged[-1][j] = _dehyphenate(merged[-1][j], r[j], vocab)
                continue
            merged.append(list(r))
        return None, [(r, None) for r in merged], None

    titles = [r for r in grid[:h] if len([v for v in r if v]) == 1]
    caption = next((v for v in titles[-1] if v), None) if titles else None
    header_row = list(grid[h])
    header = list(header_row)
    body = grid[h + 1:]

    def is_subheader(r):
        return bool(r) and all(len(v) <= 3 for v in r if v) and not text_cells(r) \
            and len([v for v in r if v]) >= 2

    if body and is_subheader(body[0]):
        sub = body.pop(0)
        top = list(header)
        for j, v in enumerate(sub):
            if v:
                parent = next((top[k] for k in range(j, -1, -1) if top[k]), "")
                header[j] = f"{parent} {v}".strip()

    # A frame can hold several tables with the same columns, each reprinting the
    # header under its own title ('Intermediate Results Indicators by
    # Components'). Split there; the single-cell rows just above a repeated
    # header are the next table's title, not part of the last indicator.
    segments, seg, cap = [], [], caption
    for r in body:
        if r == header_row:
            titles = []
            while seg and len([v for v in seg[-1] if v]) == 1 and not numeric_cells(seg[-1]):
                titles.insert(0, seg.pop())
            segments.append((seg, cap))
            cap = next((v for v in titles[-1] if v), cap) if titles else cap
            seg = []
            continue
        if not seg and is_subheader(r):
            continue
        seg.append(r)
    segments.append((seg, cap))

    out = [(list(r), caption) for r in grid[:h] if len([v for v in r if v]) > 1]
    for seg, cap in segments:
        groups = []
        for r in seg:
            has_num = bool(numeric_cells(r))
            labels = text_cells(r)
            cur = groups[-1] if groups else None
            first = r[labels[0]] if labels else ""
            # An objective heading inside the framework, '1. to increase access
            # ...', stands on its own row and closes the indicator before it.
            if re.match(r"^\d{1,2}\.\s+\S", first) and not has_num:
                groups.append({"cells": list(r), "has_num": False, "closed": True})
                continue
            starts_new = labels and cur is not None and cur["has_num"] and \
                first[:1].isupper()
            if cur is None or starts_new or cur.get("closed"):
                cur = {"cells": [""] * len(r), "has_num": False}
                groups.append(cur)
            for j, v in enumerate(r):
                if not v:
                    continue
                if cur["cells"][j] and not _is_numeric(v):
                    cur["cells"][j] = _dehyphenate(cur["cells"][j], v, vocab)
                elif not cur["cells"][j]:
                    cur["cells"][j] = v
                else:
                    # A second figure for a cell already filled: a new record.
                    cur = {"cells": [""] * len(r), "has_num": False}
                    groups.append(cur)
                    cur["cells"][j] = v
            cur["has_num"] = cur["has_num"] or has_num
        out += [(g["cells"], cap) for g in groups]
    return header, out, caption


def _row_text(cells, header, caption):
    """'Caption - Header: value · Header: value', or the cells joined by ' | '
    when the table has no header to name them."""
    if header and len(header) == len(cells):
        parts = [f"{h}: {v}" if h else v for h, v in zip(header, cells) if v]
        body = " · ".join(parts)
    else:
        body = " | ".join(v for v in cells if v)
    if caption and body:
        return f"{caption} — {body}"
    return body


def _in_box(line, box):
    x0, top, x1, bottom = box
    mid = (line["top"] + line["bottom"]) / 2
    return top - 1 <= mid <= bottom + 1 and line["x0"] >= x0 - 2 and line["x1"] <= x1 + 2


def _furniture(pages):
    """Fingerprints of lines repeating at the same height across the document."""
    seen = Counter()
    for lines in pages:
        for fp in {(round(l["top"] / 4), C.fingerprint(l["text"])) for l in lines}:
            seen[fp] += 1
    need = max(3, int(HEADER_REPEAT * len(pages)))
    return {fp for fp, n in seen.items() if n >= need and fp[1]}


def _dehyphenate(left, right, vocab):
    """Join a word broken across lines. 'infra-' + 'structure' -> 'infrastructure'
    when the document uses that word unbroken somewhere; otherwise the hyphen is
    real ('climate-' + 'resilient') and is kept."""
    m = re.search(r"([A-Za-z]+)-$", left)
    n = re.match(r"([a-z]+)", right)
    if m and n:
        whole = (m.group(1) + n.group(1)).lower()
        if whole in vocab:
            return left[:-1] + right
        return left + right
    return left + " " + right


def _contents_entry(text):
    """A contents line without dot leaders: a section or annex heading that
    ends in a bare page number, 'V. KEY RISKS 25'. A body heading does not end
    in a number, and taking a contents entry for a real heading files the whole
    document under the wrong section - one Togo PAD went entirely to annex."""
    if not re.search(r"\s\d{1,3}$", text):
        return False
    return bool(C.ROMAN.match(text) or C.ANNEX.match(text) or C.LETTER.match(text))


GLYPH_BULLET = re.compile(r"^\s*[•●▪■◦](?:\s+|(?=[A-Z]))")


def _opens_block(line, before):
    """Does this line start a new paragraph or list item, rather than continue
    the one above? A bullet glyph always does. A number or enumerator only does
    when what came before had reached a stopping point: a cross-reference that
    wrapped onto a new line - "... as described in sub-part" / "(ii) above" -
    looks exactly like an enumerator but follows mid-sentence."""
    if GLYPH_BULLET.match(line):
        return True
    if not (C.NUMBERED_PARA.match(line) or BULLET_OPEN.match(line)):
        return False
    tail = before.rstrip()
    return bool(re.search(r"[.:;!?)\"]$", tail) or re.search(r"\b(?:and|or)$", tail))


def extract(pdf, raw_out=None):
    """An open pdfplumber PDF -> (clean_text, spans, stats).

    raw_out, a list, receives the PDF's text exactly as read - every line of
    every page, a form feed between pages - and each span's meta carries `src`,
    the (start, end) of the lines it was built from in that text. So the review
    sheet shows a paragraph's source without searching for it.

    spans: (start, end, section_path, section_title, block, page_from, page_to,
            meta) - meta holds, where they apply: list_item, lead_in (index of
            the span that introduces the list), and for a table row its table
            number, row, cells, header and caption
    """
    stats = Counter()
    pages, tables = [], []
    raw_parts, cursor = [], 0
    for page in pdf.pages:
        page = page.filter(_not_rotated)
        lines, raw = _page_lines(page)
        offs = []
        for r in raw:
            offs.append((cursor, cursor + len(r)))
            raw_parts.append(r + "\n")
            cursor += len(r) + 1
        raw_parts.append("\f")
        cursor += 1
        for l in lines:
            l["src"] = offs[l.pop("raw_i")]
        tabs = _tables(page)
        pages.append(lines)
        tables.append(tabs)
    if raw_out is not None:
        raw_out.append("".join(raw_parts))
    body_sizes = Counter(l["size"] for lines in pages for l in lines for _ in range(len(l["text"])))
    if not body_sizes:
        return "", [], stats
    body = body_sizes.most_common(1)[0][0]
    furniture = _furniture(pages)
    # The width of a full body line, per page: landscape annex pages run wider
    # than portrait ones. A heading is short; a line running the full measure
    # is prose, even when it is set in bold for emphasis.
    def _measure(lines):
        w = sorted(l["x1"] - l["x0"] for l in lines if abs(l["size"] - body) < 0.3)
        return w[len(w) // 2] if len(w) >= 5 else None
    page_measure = [_measure(lines) for lines in pages]
    known = sorted(m for m in page_measure if m)
    doc_measure = known[len(known) // 2] if known else 0
    page_measure = [m or doc_measure for m in page_measure]

    # Vocabulary for dehyphenation, from every line as printed.
    vocab = set()
    for lines in pages:
        for l in lines:
            vocab.update(w.lower() for w in re.findall(r"[A-Za-z]{3,}", l["text"]))

    # 1. Label every line: furniture, footnote, table, heading or body.
    items = []          # (kind, page_no, line or table-row text, line dict)
    pitches = []
    # Pass 1, per page: drop furniture, find where the footnotes start, and
    # note which footnote numbers the page defines.
    kept_pages, fn_starts, defined_by_page = [], [], []
    for lines in pages:
        keep = []
        for l in lines:
            if (round(l["top"] / 4), C.fingerprint(l["text"])) in furniture:
                stats["furniture_lines"] += 1
                continue
            if PAGE_NUM.match(l["text"]):
                stats["page_number_lines"] += 1
                continue
            if FURNITURE_TEXT.search(l["text"]):
                stats["furniture_lines"] += 1
                continue
            keep.append(l)
        # Footnotes: the unbroken run of small-type lines at the foot of the page.
        fn_from = len(keep)
        for i in range(len(keep) - 1, -1, -1):
            if keep[i]["size"] < SMALL_RATIO * body:
                fn_from = i
            else:
                break
        kept_pages.append(keep)
        fn_starts.append(fn_from)
        defined_by_page.append({int(m.group(1)) for x in keep[fn_from:]
                                for m in [re.match(r"^(\d{1,3})", x["text"])] if m})

    table_no = [0]
    last_header = [None, ""]     # (header, caption) of the last headed table
    last_caption = ["", 0]       # the latest heading or short title line, and its page
    for pno, (keep, tabs) in enumerate(zip(kept_pages, tables), 1):
        fn_from = fn_starts[pno - 1]
        # Footnote numbers for markers that were not set raised: this page's,
        # and the next page's, where a footnote that did not fit is carried.
        defined = defined_by_page[pno - 1] | (
            defined_by_page[pno] if pno < len(defined_by_page) else set())

        def unmark(text):
            def repl(m):
                if int(m.group(1)) in defined:
                    stats["inline_marks_removed"] += 1
                    return ""
                return m.group(0)
            return re.sub(r" (?=[,.;:])", "", INLINE_MARK.sub(repl, text)) \
                if defined else text

        emitted_tables = set()
        for i, l in enumerate(keep):
            box = next((t for t in tabs if _in_box(l, t[0])), None)
            if box is not None:
                key = id(box)
                if key not in emitted_tables:
                    emitted_tables.add(key)
                    table_no[0] += 1
                    header, data, own_caption = _clean_table(box[1], vocab)
                    width = len(data[0][0]) if data else 0
                    # A table running on from the previous page reprints no
                    # header: borrow the last one when the column count agrees
                    # and the table starts the page's content.
                    if header is None and last_header[0] and \
                            len(last_header[0]) == width and i <= 2:
                        header = last_header[0]
                        caption = last_header[1]
                        stats["table_headers_carried"] += 1
                    else:
                        # A title more than a page back belongs to something else.
                        recent = last_caption[0] if pno - last_caption[1] <= 1 else ""
                        caption = own_caption or recent
                    if header:
                        last_header[0], last_header[1] = header, caption
                        stats["tables_with_header"] += 1
                    stats["tables"] += 1
                    inside = [x["src"] for x in keep if _in_box(x, box[0])]
                    tsrc = [min(a for a, _ in inside), max(b for _, b in inside)] \
                        if inside else None
                    for r_i, (row, row_cap) in enumerate(data):
                        row_caption = row_cap or caption
                        text = _row_text(row, header, row_caption)
                        if text:
                            items.append(("table", pno, text, {
                                "table": table_no[0], "row": r_i, "cells": row,
                                "header": header, "caption": row_caption,
                                "src": tsrc}))
                stats["table_lines"] += 1
                continue
            if i >= fn_from:
                items.append(("footnote", pno, l["text"], l))
                continue
            if TOC_LINE.search(l["text"]) or _contents_entry(l["text"]):
                stats["contents_lines"] += 1
                continue
            heading = (l["bold"] or l["italic"] or l["size"] > body + 0.5) \
                and len(l["text"].split()) <= 14 and not re.search(r"[.;,:]$", l["text"]) \
                and (l["x1"] - l["x0"]) < 0.8 * page_measure[pno - 1]
            text = unmark(l["text"])
            if heading or TITLE_LINE.match(text):
                last_caption[0], last_caption[1] = text, pno
                last_header[0] = None   # a new title ends the previous table
            items.append(("heading" if heading else "body", pno, text, l))
            if i and keep[i - 1] is not None:
                gap = l["top"] - keep[i - 1]["top"]
                if 0 < gap < 3 * body:
                    pitches.append(round(gap, 1))
    pitch = statistics.median(pitches) if pitches else 1.2 * body

    # 2. Group lines into units.
    units = []          # [kind, text, page_from, page_to]
    prev = None         # previous line item of the same stream, for gap tests
    last_body = None    # index in units of the last body unit
    last_body_line = None

    def start(kind, text, pno, meta=None, line=None):
        meta = dict(meta or {})
        if line is not None:
            meta["src"] = list(line["src"])
        units.append([kind, text, pno, pno, meta])

    def extend(u, line):
        if "src" in u[4]:
            u[4]["src"][0] = min(u[4]["src"][0], line["src"][0])
            u[4]["src"][1] = max(u[4]["src"][1], line["src"][1])

    for kind, pno, text, l in items:
        if kind == "table":
            start(kind, text, pno, l)
            prev = kind
            continue
        if kind == "heading":
            # A figure caption, a table or a sub-heading can interrupt a
            # paragraph that carries on below it. last_body is kept so that a
            # lowercase continuation can still find its paragraph; prev is
            # cleared so an ordinary next line does not join across it.
            start(kind, text, pno, line=l)
            prev = kind
            continue
        if kind == "footnote":
            opens = re.match(r"^\d{1,3}\s", text) or re.match(r"^\d{1,3}[A-Z\"(]", text)
            if units and units[-1][0] == "footnote" and not opens and prev == "footnote":
                u = units[-1]
                u[1] = _dehyphenate(u[1], text, vocab)
                u[3] = pno
                extend(u, l)
            else:
                start("footnote", re.sub(r"^\d{1,3}\s*", "", text), pno, line=l)
            prev = "footnote"
            continue
        # Body. A paragraph continues when the next line follows at the normal
        # line pitch on the same page, or - across a page turn - when the
        # paragraph had not finished a sentence. Footnotes and the running
        # header between the two halves are already out of the body stream, so
        # the continuation joins the last BODY unit, whatever came between.
        joined = False
        if last_body is not None and last_body_line is not None:
            ppno, pl = last_body_line
            opens_block = _opens_block(text, units[last_body][1])
            same_page = pno == ppno and prev == "body" and \
                l["top"] - pl["top"] <= 1.45 * pitch
            unfinished = not re.search(r"[.!?:]\"?$", units[last_body][1])
            page_turn = pno == ppno + 1 and unfinished and prev != "heading"
            # Past an interruption (figure, table, caption) only a line that
            # plainly continues a sentence - it opens in lower case - rejoins.
            resumed = prev in ("table", "heading", "footnote") and unfinished \
                and pno - ppno <= 1 and re.match(r"[a-z]", text)
            # A wider gap than usual between two lines of one sentence: the
            # document set this paragraph at looser spacing. The sentence was
            # not finished and the line opens in lower case, so it continues.
            loose = pno == ppno and prev == "body" and unfinished \
                and re.match(r"[a-z]", text) and l["top"] - pl["top"] <= 3 * pitch
            if (same_page or page_turn or resumed or loose) and not opens_block:
                u = units[last_body]
                u[1] = _dehyphenate(u[1], text, vocab)
                u[3] = pno
                extend(u, l)
                joined = True
        if not joined:
            start("body", text, pno, line=l)
            last_body = len(units) - 1
        last_body_line = (pno, l)
        prev = "body"

    # 3. Sections, front matter and blocks, then the clean text.
    clean_parts, spans, cursor = [], [], 0
    sec_path, sec_title = "", ""
    annex_seen = roman_seen = False
    lead_in = None                  # index in spans of the sentence that opens a list
    for kind, text, p0, p1, meta in units:
        text = text.strip()
        meta = dict(meta)
        # A bullet glyph is layout, not text: strip it and record that the
        # unit is a list item, with the sentence that introduced the list.
        if GLYPH_BULLET.match(text):
            text = GLYPH_BULLET.sub("", text, count=1).strip()
            meta["list_item"] = True
        elif kind == "body" and BULLET_OPEN.match(text):
            meta["list_item"] = True
        text, n_links = URL.subn("[link]", text)
        stats["links_replaced"] += n_links
        if n_links:
            meta["links"] = n_links
        # A lone symbol is a logo or an ornament, not text: '$'.
        if not re.search(r"[^\W_]", text):
            stats["symbol_only_units"] += 1
            continue
        block = kind
        if kind == "heading" or kind == "body":
            m_roman, m_annex = C.ROMAN.match(text), C.annex_match(text)
            m_letter = C.LETTER.match(text)
            if m_roman and (kind == "heading" or (len(text) < 90 and
                                                  m_roman.group(2).isupper())):
                block = "heading"
                roman_seen = True
                sec_path, sec_title = m_roman.group(1), m_roman.group(2).strip(" .:-")
            elif m_annex and roman_seen and len(text) < 110:
                annex_seen = True
                sec_path = f"{m_annex.group(1).upper()} {m_annex.group(2)}"
                sec_title = m_annex.group(3).strip(" .:-")
                block = "heading"
            elif m_letter and kind == "heading" and sec_path and not annex_seen:
                sec_path = f"{sec_path.split('.')[0]}.{m_letter.group(1)}"
                sec_title = m_letter.group(2).strip(" .:-")
        if block == "body" and TITLE_LINE.match(text) and len(text.split()) <= 30 \
                and not re.search(r"[.;]$", text):
            block = "heading"
        if block == "body":
            block = ("frontmatter" if not roman_seen else
                     "annex" if annex_seen else "narrative")
            if C.looks_tabular(text):
                block = "table"
        elif block == "table" and not roman_seen:
            block = "frontmatter"
        if meta.get("list_item") and lead_in is not None:
            meta["lead_in"] = lead_in
        elif kind == "body" and not meta.get("list_item"):
            lead_in = len(spans) if re.search(r":$", text) else None
        if block == "heading":
            lead_in = None
        clean_parts.append(text + "\n\n")
        spans.append((cursor, cursor + len(text), sec_path, sec_title[:120], block, p0, p1,
                      meta))
        cursor += len(text) + 2
    stats["units"] = len(spans)
    return "".join(clean_parts), spans, stats
