"""The units the search runs over, each carrying where it sits.

Sentences are where we search, paragraphs where we work: a sentence states
one thing, so a measure or an asset is found in a sentence; the paragraph
around it is what a model or a person reads to decide. So every search unit
carries its paragraph and the paragraph's context, and nothing downstream has
to re-join:

  unit_id              the sentence id ('D:p00042:s003'), or the paragraph id
                       for a footnote or a table row
  kind                 sentence | footnote | table
  paragraph_id, doc_id, project_ids (the project of the document part it sits
                       in), block, section_path
  component_number, subcomponent_number
                       the component heading the paragraph sits under, in its
                       own document's numbering
  component_key        that component's name_key (keys.name_key): the same
                       component in another document, even renumbered. From
                       components.csv, or failing that from the title the
                       heading printed (paragraphs.csv component_name)
  char_start, char_end offsets into data/appraisal/text/<doc_id>.txt
  n_tokens, text_sha256
  lead_in_start, lead_in_end
                       for a list item, the offsets of the sentence that
                       introduces the list ('Sub-component 1.1 will
                       finance:'); blank otherwise. '(b) towers;' alone says
                       nothing about what the towers are for, so embed.py
                       embeds the item with its lead-in in front. The unit is
                       still the item: its id, offsets and hash are its own.
  left_out             why the unit is not searched, or blank: 'reference' (a
                       footnote that only cites a source or titles a table),
                       'fragment' (a title or a scrap of a few words). A list
                       item with a lead-in, and anything saying 'will' or
                       'shall', is always searched.
  copy_of              the first unit (in document and unit order) whose
                       embedded text, lead-in included, is the same word for
                       word, or blank. An additional financing or a
                       restructuring paper repeats its appraisal document; a
                       copy is not searched, and copies() maps each searched
                       unit to the copies it stands for.

The units are the sentences of the narrative and annex paragraphs given to
the model, each footnote given to the model, whole (footnotes are mostly
references, which a sentence splitter would cut into nonsense; clean.py does
not split them), and each table row whose cells read as prose (prose_row),
whole, with its caption and column names: about one in six activities an
expert listed sits in a table.

embed.py embeds and indexes every unit that is neither left out nor a copy.
Nothing is deleted: search_units() still lists them all.
"""
import csv
import os
import re

from keys import name_key
from paths import where
from sentences import ITEM_MARKER

UNIT_COLS = ["unit_id", "kind", "paragraph_id", "doc_id", "project_ids", "block",
             "section_path", "component_number", "subcomponent_number", "component_key",
             "char_start", "char_end", "n_tokens", "text_sha256", "lead_in_start",
             "lead_in_end", "left_out", "copy_of"]

WORD = re.compile(r"[^\W\d_]{2,}")
CITATION = re.compile(r"\b(?:19|20)\d\d[a-z]?\b|\[link\]|\bhttps?:|www\.", re.I)
SOURCE = re.compile(r"\b(?:Report|Paper|Profile|Database|Portal|Note|Study|Brief|Review|Survey|"
                    r"Outlook|Index|Series|Washington|Retrieved|Available|accessed|et al|Journal|"
                    r"Working|ITU|GSMA|OECD|World Bank|Bank)\b")
VERB = re.compile(r"\b(?:will|shall|would|should|must|is|are|was|were|be|been|being|has|"
                  r"have|had|can|could|may|might|do|does|did)\b", re.I)
FIRM = re.compile(r"\b(?:will|shall)\b", re.I)
MINOR = {"and", "or", "of", "the", "for", "to", "in", "on", "a", "an", "with", "by", "at", "from"}
MIN_WORDS = 4           # fewer words than this is a scrap
TITLE_WORDS = 10        # a title is at most this many words, nearly all capitalised
UNVERBED_NOTE = 12      # a footnote this short with no verb is a citation or a caption
CITATION_WORDS = 40     # a citation (a year or a link, and a source) is shorter than this


def left_out(kind, text, has_lead_in):
    """Why a unit is not searched ('reference' or 'fragment'), or ''. The
    rules lean to keeping: a unit saying 'will' or 'shall' is searched,
    and so is a list item with a lead-in ('(b) towers;' after
    'Sub-component 1.1 will finance:')."""
    words = WORD.findall(text)
    n = len(words)
    if kind == "footnote":
        if n < MIN_WORDS:
            return "reference"
        if FIRM.search(text):
            return ""
        if n < UNVERBED_NOTE and not VERB.search(text):
            return "reference"
        if n < CITATION_WORDS and CITATION.search(text) and SOURCE.search(text):
            return "reference"
        return ""
    if has_lead_in or FIRM.search(text):
        return ""
    if n < MIN_WORDS:
        return "fragment"
    content = [w for w in words if w.lower() not in MINOR]
    if n <= TITLE_WORDS and content and \
            sum(w[0].isupper() for w in content) >= 0.8 * len(content):
        return "fragment"
    return ""


# A table row is searched when its cells read as prose: a box of text in an
# annex ('Climate Action Supported by ...') holds commitments, a row of costs
# or targets does not. The parser writes a row as 'caption — header: cells'.
PROSE_WORDS = 15         # the cells hold at least this many words
PROSE_MAX_NUMBERS = 0.2  # and numbers are under this share of their tokens
NUMBER = re.compile(r"^[\d.,%$()\-–]+$")
ROW_VERB = re.compile(r"\b(?:will|shall|would|should|must|is|are|was|were|be|been|has|have|"
                      r"include[sd]?|support[sd]?|finance[sd]?|provide[sd]?|ensure[sd]?)\b", re.I)
# Sections of standard text printed in every document.
BOILERPLATE_SECTION = re.compile(r"grievance", re.I)


def prose_row(text):
    """Whether a table row's cells read as prose."""
    cells = text.split(" — ", 1)[-1]
    tokens = cells.split()
    return (len(WORD.findall(cells)) >= PROSE_WORDS and bool(ROW_VERB.search(cells))
            and sum(bool(NUMBER.match(t)) for t in tokens) < PROSE_MAX_NUMBERS * len(tokens))


def _same_text(text):
    return " ".join(WORD.findall(text.lower()))


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def component_keys(data):
    """(doc_id, number) -> name_key, for components and sub-components. A
    document that lists a number twice (data sheet and heading) gives the
    data sheet's name, read first."""
    keys = {}
    rows = _read(where(data, "appraisal", "components.csv"))
    rows.sort(key=lambda r: (r["source"] == "heading", r["doc_id"], r["number"]))
    for r in rows:
        if r.get("name_key"):
            keys.setdefault((r["doc_id"], r["number"]), r["name_key"])
    return keys


def _text(cache, data, doc_id):
    if doc_id not in cache:
        path = where(data, "text", f"{doc_id}.txt")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                cache[doc_id] = fh.read()
        else:
            cache[doc_id] = None
    return cache[doc_id]


def lead_ins(para, sents, text):
    """{sentence_id: (start, end)} of the lead-in for each list item in a
    paragraph. An item is a sentence on a line after the paragraph's first
    (a list set out line by line) or one opening with an enumerator (an
    inline list). Its lead-in is the last sentence before it that is not
    itself an item; a paragraph that opens with a list has none."""
    if text is None or para.get("list_item") == "true":
        return {}
    start = int(para["char_start"])
    first_break = text.find("\n", start, int(para["char_end"]))
    out, lead = {}, None
    for s in sorted(sents, key=lambda s: int(s["char_start"])):
        a, b = int(s["char_start"]), int(s["char_end"])
        item = (first_break != -1 and a > first_break) or ITEM_MARKER.match(text[a:b])
        if not item:
            lead = (a, b)
        elif lead:
            out[s["sentence_id"]] = lead
    return out


def search_units(data):
    """Every search unit, in document and paragraph order."""
    every = _read(where(data, "appraisal", "paragraphs.csv"))
    paras = {p["paragraph_id"]: p for p in every if p.get("for_model") == "true"}
    keys = component_keys(data)

    def context(p):
        return {"paragraph_id": p["paragraph_id"], "doc_id": p["doc_id"],
                "project_ids": p["project_ids"], "block": p["block"],
                "section_path": p["section_path"],
                "component_number": p.get("component_number", ""),
                "subcomponent_number": p.get("subcomponent_number", ""),
                # The data sheet's name first; else the title the heading
                # printed (a run-in title the data sheet does not list).
                "component_key": (keys.get((p["doc_id"], p["component_number"]))
                                  or name_key(p.get("component_name", "")))
                if p.get("component_number") else ""}

    out = []
    by_para = {}
    for s in _read(where(data, "appraisal", "sentences.csv")):
        if s["paragraph_id"] in paras:
            by_para.setdefault(s["paragraph_id"], []).append(s)
    texts = {}
    for pid, sents in by_para.items():
        p = paras[pid]
        leads = lead_ins(p, sents, _text(texts, data, p["doc_id"]))
        for s in sents:
            lead = leads.get(s["sentence_id"])
            out.append(dict(context(p), unit_id=s["sentence_id"], kind="sentence",
                            char_start=s["char_start"], char_end=s["char_end"],
                            n_tokens=s["n_tokens"], text_sha256=s["text_sha256"],
                            lead_in_start=lead[0] if lead else "",
                            lead_in_end=lead[1] if lead else ""))
    for p in paras.values():
        if p["block"] == "footnote":
            out.append(dict(context(p), unit_id=p["paragraph_id"], kind="footnote",
                            char_start=p["char_start"], char_end=p["char_end"],
                            n_tokens=p["n_tokens"], text_sha256=p["text_sha256"],
                            lead_in_start="", lead_in_end=""))
    for p in every:
        if p["block"] == "table" and not BOILERPLATE_SECTION.search(p.get("section_title", "")):
            text = _text(texts, data, p["doc_id"])
            if text is not None and prose_row(text[int(p["char_start"]):int(p["char_end"])]):
                out.append(dict(context(p), unit_id=p["paragraph_id"], kind="table",
                                char_start=p["char_start"], char_end=p["char_end"],
                                n_tokens=p["n_tokens"], text_sha256=p["text_sha256"],
                                lead_in_start="", lead_in_end=""))
    out.sort(key=lambda u: (u["doc_id"], u["unit_id"]))
    first = {}
    for u in out:
        text = _text(texts, data, u["doc_id"]) or ""
        body = text[int(u["char_start"]):int(u["char_end"])]
        lead = text[int(u["lead_in_start"]):int(u["lead_in_end"])] if u["lead_in_start"] else ""
        u["left_out"] = left_out(u["kind"], body, bool(lead))
        u["copy_of"] = ""
        if not u["left_out"]:
            same = _same_text(lead + "\n" + body)
            u["copy_of"] = first.setdefault(same, u["unit_id"])
            if u["copy_of"] == u["unit_id"]:
                u["copy_of"] = ""
    return out


def copies(units):
    """{unit_id: [unit_id of each copy]} for the searched units that have
    copies, so a hit can be reported in every document that prints it."""
    out = {}
    for u in units:
        if u["copy_of"]:
            out.setdefault(u["copy_of"], []).append(u["unit_id"])
    return out
