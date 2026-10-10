"""Split a cleaned paragraph into sentences, deterministically, with no model.

The paragraph stays the unit for deciding what a passage is ABOUT: an asset is
often named in one sentence and qualified in the next, and a paragraph keeps
them together. The sentence is the unit for deciding what a passage COMMITS
TO: "will finance fiber" and "sites are expected to avoid flood zones" are two
claims with two degrees of firmness, and a paragraph-level judgement blurs
them. So both are kept, and a sentence is addressed by offsets inside its
paragraph rather than stored as a copy.

Appraisal documents are full of periods that do not end a sentence, and each
rule below exists because one of them did:

  abbreviations         e.g. i.e. etc. No. para. U.S. Rs. approx. vs.
  initials              "J. Smith", "P. 2"
  numbering             "38. Component 3 seeks ..." - the paragraph number
  decimals              "US$3.2 million" - no whitespace after the period
  enumerations          "(a) ...; (b) ..." are clauses of ONE sentence
  fragments             "ITU. 2021. Global ..." - under three words is not a
                        sentence, so no boundary is placed after one

A boundary is a ., ! or ? followed by whitespace and then something that can
open a sentence: an uppercase letter, a digit, an opening quote or bracket.
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
# src/sensitivity.py varies it, and the abbreviation list.
MIN_WORDS = 3
BOUNDARY = re.compile(r"[.!?][\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")
PARA_NUMBER = re.compile(r"^\s*\(?\d{1,3}(?:\.\d{1,2})*[.)]\s+")


def split(text):
    """Paragraph text -> list of (start, end) offsets, one per sentence."""
    spans, start = [], 0
    # A leading paragraph number belongs to the first sentence; it is not one.
    m = PARA_NUMBER.match(text)
    floor = m.end() if m else 0
    for b in BOUNDARY.finditer(text):
        end = b.start() + 1
        if end <= floor:
            continue
        before = text[start:b.start()]
        # Too short to be a sentence: 'ITU.', '2021.' in a reference, 'Annex
        # 2.' as a label. Fewer than three words do not stand alone.
        if len(re.findall(r"\w+", text[max(start, floor):b.start()])) < MIN_WORDS:
            continue
        w = WORD_BEFORE.search(before)
        if w:
            word = w.group(1).lower().rstrip(".")
            if word in ABBREV or len(word) == 1:
                continue            # an abbreviation or an initial
        spans.append((start, b.start() + len(b.group(0).rstrip())))
        start = b.end()
    if start < len(text) and text[start:].strip():
        spans.append((start, len(text.rstrip())))
    return [(a, z) for a, z in spans if text[a:z].strip()]
