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
FOOTNOTE_BODY = re.compile(r"^\s{0,6}(\d{1,3})\s+[A-Za-z“\"(]")
PAGE_FOOTER = re.compile(r"^\s*(?:Page\s+)?\d{1,4}\s*$", re.I)
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

PARA_COLS = ["paragraph_id", "doc_id", "project_id", "ordinal", "section_path",
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
    lower = idx[len(idx) // 2:]
    nums, where = set(), set()
    for i in lower:
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
    """Indices of heading-shaped lines that belong to the contents page.

    A per-line test on the trailing page number is not enough on its own: one
    Tanzania contents entry ends '...digital commitments 81', a single space
    before the number, which reads exactly like prose ending in a figure. What
    separates a contents page from the body is that its entries are packed
    together. So: find runs of nearby heading-shaped lines, and if most of a run
    carries a trailing page number, the whole run is contents.
    """
    heads = [i for i, l in enumerate(lines) if ROMAN.match(l) or annex_match(l)]
    if not heads:
        return set()
    runs, cur = [], [heads[0]]
    for a, b in zip(heads, heads[1:]):
        if b - a <= 4:
            cur.append(b)
        else:
            runs.append(cur)
            cur = [b]
    runs.append(cur)
    out = set()
    for run in runs:
        if len(run) < 4:
            continue
        tailed = sum(1 for i in run if TOC_TAIL.search(lines[i].rstrip()))
        if tailed >= len(run) / 2:
            out.update(run)
    return out


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
    stripped_pages = [[l for i, l in enumerate(pg) if i not in kill]
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

    state = {"last": 0, "used": set(), "hits": 0, "misses": 0}
    kept_pages = []
    for n, page in enumerate(stripped_pages):
        allowed = bodies[n] | (bodies[n + 1] if n + 1 < len(bodies) else set())
        body_line_idx = scanned[n][1]
        lines = []
        for i, line in enumerate(page):
            # A footnote body keeps its own leading number; only inline
            # references inside prose are stripped.
            if i not in body_line_idx:
                line = strip_footnote_marks(line, allowed, state)
            lines.append(line)
        kept_pages.append(lines)

    flat = []
    for page in kept_pages:
        flat.extend(rejoin(page))

    toc_lines = find_contents_lines(flat)
    body_start = max(toc_lines) + 1 if toc_lines else 0

    blocks, cur = [], []
    sec_path, sec_title, marker = "", "", ""
    annex_seen = False
    spans = []
    clean_parts, cursor = [], 0

    def flush():
        nonlocal cur, cursor
        if not cur:
            return
        body = "\n".join(cur).strip("\n")
        cur = []
        if not body.strip():
            return
        start = cursor
        clean_parts.append(body + "\n\n")
        cursor += len(body) + 2
        block = ("template:" + marker if marker else
                 "frontmatter" if (start_idx[0] < body_start or not sec_path) else
                 "annex" if annex_seen else "narrative")
        spans.append((start, start + len(body), sec_path, sec_title, block))

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
            if m_annex:
                annex_seen = True
                sec_path = f"{m_annex.group(1).upper()} {m_annex.group(2)}"
                sec_title = m_annex.group(3).strip(" .:-")
            else:
                sec_path = m_roman.group(1)
                sec_title = m_roman.group(2).strip(" .:-")
            start_idx = [i]
            cur = [line.strip()]
            flush()
            continue

        m_letter = LETTER.match(line)
        if m_letter and not is_toc and i >= body_start and not annex_seen and sec_path:
            flush()
            sec_path = f"{sec_path.split('.')[0]}.{m_letter.group(1)}"
            sec_title = m_letter.group(2).strip(" .:-")
            start_idx = [i]
            cur = [line.strip()]
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
                "project_id": doc["project_id"], "ordinal": ordinal,
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
