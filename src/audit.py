#!/usr/bin/env python3
"""Scan every cleaned paragraph for known text defects and count them.

Reads  data/paragraphs.csv, data/clean/{doc_id}.txt
Writes data/audit_report.txt

Sampling a handful of paragraphs finds obvious defects and misses rare ones.
This checks all of them, so a defect affecting 0.2% of the corpus is a number
rather than a thing nobody happened to look at.

Each check reports a count, a share of the block it applies to, and a few
example paragraph ids so any hit can be pulled up with review.py.

  python3 src/audit.py
  python3 src/audit.py --examples 8
  python3 src/audit.py --block narrative
"""
import argparse, csv, os, re, sys, unicodedata
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Blocks that are meant to read as prose. Table and template blocks are
# deliberately not prose, so prose checks would only produce noise there.
PROSE_BLOCKS = ("narrative", "annex")

CHECKS = []


def check(name, blocks=PROSE_BLOCKS, severity="defect"):
    def wrap(fn):
        CHECKS.append((name, blocks, severity, fn))
        return fn
    return wrap


# ---------------------------------------------------------------- footnotes

@check("footnote marker glued to a word", severity="defect")
def _glued(t):
    """project34, shocks46 - a reference number fused onto the preceding word."""
    return re.findall(r"\b[a-z]{3,}(\d{1,3})\b", t)


@check("paragraph opens as a footnote body", severity="defect")
def _body_leak(t):
    """'38Vietnam provides a good example...' - page-foot footnote text emitted
    as a paragraph, with its number fused to the first word."""
    return re.findall(r"^\s*(\d{1,3})[A-Z][a-z]", t)


# ---------------------------------------------------------------- layout

@check("page number left inline", severity="defect")
def _pageno(t):
    return re.findall(r"(?:^|\s)Page \d{1,4}(?:\s|$)", t)


@check("whitespace run of 3-5 spaces", severity="defect")
def _small_gap(t):
    return re.findall(r"\S {3,5}\S", t)


@check("whitespace run of 6+ spaces (column gutter)", severity="defect")
def _gutters(t):
    """A wide run is a column gutter that survived, so the paragraph is table
    content rather than prose."""
    return re.findall(r"\S {6,}\S", t)


@check("tabular: numeric tokens over a third", severity="defect")
def _numeric(t):
    toks = re.findall(r"[\w'’-]+", t)
    if len(toks) < 12:
        return []
    numeric = sum(1 for x in toks if re.fullmatch(r"[\d.,%()$-]+", x))
    return ["ratio"] if numeric / len(toks) >= 0.33 else []


# ---------------------------------------------------------------- fragments

@check("starts mid-sentence", severity="soft")
def _midsentence(t):
    """A paragraph whose first character is lowercase is usually the tail of one
    split across a page or a table cell."""
    s = t.lstrip()
    if not s or not s[0].islower():
        return []
    # A list item legitimately starts lowercase: "a. Mobile broadband ..." under
    # a stem ending in a colon. That is structure, not a broken split.
    if re.match(r"^(?:\(?[a-z]|\(?[ivxlc]{1,4})[\.\)]\s+\S", s):
        return []
    return ["frag"]


@check("no sentence-ending punctuation", severity="soft")
def _unterminated(t):
    s = t.rstrip()
    return ["open"] if s and s[-1] not in ".!?:;\"')]" else []


# ---------------------------------------------------------------- encoding

@check("unicode replacement character", blocks=None, severity="defect")
def _replacement(t):
    return re.findall("�", t)


@check("control or format character", blocks=None, severity="defect")
def _control(t):
    return [c for c in t if unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\t"]


@check("smart quote or ligature left unfolded", blocks=None, severity="defect")
def _unfolded(t):
    return re.findall("[‘’“”–­ﬁﬂ ]", t)




def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--examples", type=int, default=4)
    ap.add_argument("--block", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with open(os.path.join(args.data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if args.block:
        rows = [r for r in rows if r["block"] == args.block]

    cache = {}

    def text_of(row):
        did = row["doc_id"]
        if did not in cache:
            with open(os.path.join(args.data, "clean", f"{did}.txt"), encoding="utf-8") as fh:
                cache[did] = fh.read()
        return cache[did][int(row["char_start"]):int(row["char_end"])]

    hits = defaultdict(list)
    totals = Counter()
    blocks = Counter(r["block"] for r in rows)

    for row in rows:
        body = text_of(row)
        for name, only, severity, fn in CHECKS:
            if only and row["block"] not in only:
                continue
            totals[name + "__scope"] += 1
            found = fn(body)
            if found:
                flat = re.sub(r"\s+", " ", body)
                needle = str(found[0]) if isinstance(found[0], str) else ""
                span = flat.find(needle) if needle and len(needle) > 1 else None
                hits[name].append((row["paragraph_id"], row["block"], row["section_path"],
                                   str(found[:3]), body, span))

    prose = sum(v for k, v in blocks.items() if k in PROSE_BLOCKS)
    out = []
    out.append(f"paragraphs scanned: {len(rows)}   prose blocks (narrative+annex): {prose}")
    out.append("")

    # Volume, so the cost of embedding rests on measured counts. Words and
    # characters are exact; model tokens are not, and are reported as a RANGE.
    #
    # The low end is 1.3 model tokens per word, the usual figure for ordinary
    # English. The high end is one token per four characters. They disagree here
    # because this text is not ordinary English: it runs 6.8 characters per word
    # against roughly 5.3 for general prose, so words break into more subword
    # pieces than the per-word rule assumes. On this corpus the two ends differ
    # by about 30%, and the character rule is the safer one to budget against.
    #
    # Neither is a measurement. Only the embedding model's own tokeniser settles
    # the bill, so re-derive this once a model is pinned rather than trusting
    # either end of the range.
    words = Counter()
    chars = Counter()
    for row in rows:
        words[row["block"]] += int(row["n_tokens"])
        chars[row["block"]] += int(row["char_end"]) - int(row["char_start"])
    def tok_range(w, c):
        return f"{int(w * 1.3):,} - {int(c / 4):,}"

    out.append(f"{'block':<34}{'paras':>8}{'words':>12}{'chars':>12}"
               f"{'model tokens (range)':>26}")
    out.append("-" * 92)
    for blk in sorted(blocks, key=lambda b: -words[b]):
        out.append(f"{blk:<34}{blocks[blk]:>8}{words[blk]:>12,}{chars[blk]:>12,}"
                   f"{tok_range(words[blk], chars[blk]):>26}")
    pw = sum(words[b] for b in PROSE_BLOCKS)
    pc = sum(chars[b] for b in PROSE_BLOCKS)
    out.append("-" * 92)
    out.append(f"{'EMBEDDABLE (narrative+annex)':<34}{prose:>8}{pw:>12,}{pc:>12,}"
               f"{tok_range(pw, pc):>26}")
    tw, tc = sum(words.values()), sum(chars.values())
    out.append(f"{'ALL BLOCKS':<34}{len(rows):>8}{tw:>12,}{tc:>12,}"
               f"{tok_range(tw, tc):>26}")
    out.append("")
    out.append(f"chars per word: {pc / max(pw, 1):.2f} over the embeddable prose "
               f"(ordinary English is nearer 5.3, so budget the upper end)")
    out.append("")
    out.append(f"{'check':<44}{'hits':>8}{'% of scope':>12}  severity")
    out.append("-" * 82)
    clean_prose = set(r["paragraph_id"] for r in rows if r["block"] in PROSE_BLOCKS)
    for name, only, severity, _ in CHECKS:
        n = len(hits[name])
        scope = totals[name + "__scope"] or 1
        out.append(f"{name:<44}{n:>8}{100.0 * n / scope:>11.2f}%  {severity}")
        if severity == "defect":
            for row_hit in hits[name]:
                clean_prose.discard(row_hit[0])
    out.append("-" * 82)
    out.append(f"prose paragraphs with NO defect-severity hit: {len(clean_prose)} "
               f"of {prose} ({100.0 * len(clean_prose) / max(prose, 1):.1f}%)")

    for name, only, severity, _ in CHECKS:
        if not hits[name]:
            continue
        out.append("")
        out.append("=" * 82)
        out.append(f"{name}  ({len(hits[name])} hits, {severity})")
        out.append("=" * 82)
        for pid, block, sec, found, body, span in hits[name][:args.examples]:
            flat = re.sub(r"\s+", " ", body)
            if span is not None:
                lo = max(0, span - 90)
                snippet = ("..." if lo else "") + flat[lo:span + 120]
            else:
                snippet = flat[:210]
            out.append(f"  {pid}  block={block} section={sec or '-'}  matched={found}")
            out.append(f"    {snippet}")

    body = "\n".join(out)
    dest = args.out or os.path.join(args.data, "audit_report.txt")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    print(body)
    print(f"\nwrote {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
