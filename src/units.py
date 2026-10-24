"""The search units, each carrying where it sits: the sentences of the narrative and
annex paragraphs given to the model, each such footnote whole, and each table row
that reads as prose (with its caption and column names).

Each unit has its paragraph, document, projects, block, section, component
(component_key from keys.name_key), offsets, n_tokens, text_sha256 and, for a list
item, the offsets of its lead-in. left_out says why a unit is not searched
(reference, fragment, indicator); copy_of names an earlier unit with the same
embedded text; template_projects and template_share measure how far its words are
shared with other projects. Nothing is deleted: search_units() lists every unit.
"""
import json
import hashlib
import os
import re

from keys import name_key
from paths import where
from sentences import ITEM_MARKER
from sentences import split as split_sentences

UNIT_COLS = ["unit_id", "kind", "paragraph_id", "doc_id", "project_ids", "block",
             "section_path", "component_number", "subcomponent_number", "component_key",
             "char_start", "char_end", "n_tokens", "text_sha256", "lead_in_start",
             "lead_in_end", "left_out", "copy_of", "template_projects", "template_share"]
import panel  # noqa: E402
TEMPLATE_SHINGLE = int(panel.get("text.shingle_words", 6))  # words per run, shared with the views
TEMPLATE_MIN_PROJECTS = 2   # a shingle is template wording when this many other projects print it

WORD = re.compile(r"[^\W\d_]{2,}")
CITATION = re.compile(r"\b(?:19|20)\d\d[a-z]?\b|\[link\]|\bhttps?:|www\.", re.I)
SOURCE = re.compile(r"\b(?:Report|Paper|Profile|Database|Portal|Note|Study|Brief|Review|Survey|"
                    r"Outlook|Index|Series|Washington|Retrieved|Available|accessed|et al|Journal|"
                    r"Working|ITU|GSMA|OECD|World Bank|Bank)\b")
VERB = re.compile(r"\b(?:will|shall|would|should|must|is|are|was|were|be|been|being|has|"
                  r"have|had|can|could|may|might|do|does|did)\b", re.I)
FIRM = re.compile(r"\b(?:will|shall)\b", re.I)
NUMBERED = re.compile(r"^(?:\d+(?:\.\d+)*\.?|\(?[ivxlc]+\)|\(?[a-z]\)|[A-Z]\.)\s")
MINOR = {"and", "or", "of", "the", "for", "to", "in", "on", "a", "an", "with", "by", "at", "from"}
MIN_WORDS = 4           # fewer words than this is a scrap
TITLE_WORDS = 10        # a title is at most this many words, nearly all capitalised
TITLE_CAPITALISED = 0.8 # ...this share of its content words capitalised
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
    # A title: short, nearly every word capitalised, and numbered or unpunctuated ('14.
    # Project Risk Assessment and Mitigation.').
    content = [w for w in words if w.lower() not in MINOR]
    titled = NUMBERED.match(text.strip()) or not text.strip().endswith((".", ";", ":"))
    if n <= TITLE_WORDS and content and titled and \
            sum(w[0].isupper() for w in content) >= TITLE_CAPITALISED * len(content):
        return "fragment"
    return ""


# A table row is searched when its cells read as prose: a box of text in an annex ('Climate
# Action Supported by ...') holds commitments, a row of costs or targets does not.
PROSE_WORDS = 15         # the cells hold at least this many words
PROSE_MAX_NUMBERS = 0.2  # and numbers are under this share of their tokens
NUMBER = re.compile(r"^[\d.,%$()\-–]+$")
ROW_VERB = re.compile(r"\b(?:will|shall|would|should|must|is|are|was|were|be|been|has|have|"
                      r"include[sd]?|support[sd]?|finance[sd]?|provide[sd]?|ensure[sd]?)\b", re.I)
# Sections of standard text printed in every document.
BOILERPLATE_SECTION = re.compile(r"grievance", re.I)
# Results-framework and indicator tables: a row there is a target with a baseline and a date
# ("campaign launched (Yes/No)"), not a commitment.
RESULTS_SECTION = re.compile(r"results? (?:framework|matrix|indicators?)\b|\bindicators?\b|"
                             r"monitoring (?:and|&) evaluation", re.I)


def prose_row(text):
    """Whether a table row's cells read as prose."""
    cells = text.split(" — ", 1)[-1]
    tokens = cells.split()
    return (len(WORD.findall(cells)) >= PROSE_WORDS and bool(ROW_VERB.search(cells))
            and sum(bool(NUMBER.match(t)) for t in tokens) < PROSE_MAX_NUMBERS * len(tokens))


TABLE_SPLIT_WORDS = 60   # a prose row longer than this is searched sentence by sentence
# Inside a row, where one cell ends and the next begins (' · ' between named cells, ' | '
# between unnamed ones, pdf_layout._row_text), and a bullet glyph left inside a cell: a
# stopping point even with no full stop, since cells and bullets are rarely punctuated.
CELL_BREAK = re.compile(r"\s+[·|]\s+|\s*[•●▪■◦]\s*")


def _row_sentences(row):
    """(start, end) of each sentence of a table row: the row is cut at its
    cell boundaries and bullets, then each piece by sentence."""
    out, at = [], 0
    for m in list(CELL_BREAK.finditer(row)) + [None]:
        end = m.start() if m else len(row)
        piece = row[at:end]
        out += [(at + s, at + e) for s, e in split_sentences(piece)]
        if m:
            at = m.end()
    return out


def table_units(p, text):
    """A prose table row as search units: whole if short; else one unit per
    sentence, each read with the row's caption in front (as a list item is
    read with its lead-in). A row holding a column of bullets ('TA will be
    provided to...', 'A mapping atlas...') states several commitments, and
    one vector for all of them matches none of them well."""
    a, z = int(p["char_start"]), int(p["char_end"])
    row = text[a:z]
    whole = {"unit_id": p["paragraph_id"], "kind": "table", "char_start": p["char_start"],
             "char_end": p["char_end"], "n_tokens": p["n_tokens"],
             "text_sha256": p["text_sha256"], "lead_in_start": "", "lead_in_end": ""}
    if len(WORD.findall(row)) <= TABLE_SPLIT_WORDS:
        return [whole]
    cut = row.find(" — ")
    caption = (a, a + cut) if cut > 0 else None
    out = []
    for n, (s, e) in enumerate(_row_sentences(row), 1):
        body = row[s:e]
        lead = caption if caption and s > cut else None
        out.append({"unit_id": f"{p['paragraph_id']}:t{n:03d}", "kind": "table",
                    "char_start": a + s, "char_end": a + e,
                    "n_tokens": len(body.split()),
                    "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    "lead_in_start": lead[0] if lead else "",
                    "lead_in_end": lead[1] if lead else ""})
    return out or [whole]


def _same_text(text):
    return " ".join(WORD.findall(text.lower()))


from common import read_csv as _read  # noqa: E402


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
    """Every search unit, in document and paragraph order, cached in
    data/appraisal/cache/search_units-<key>.json keyed on the paragraphs,
    sentences, components and the code that shapes a unit."""
    from paths import file_sha256
    inputs = [where(data, "appraisal", f) for f in ("paragraphs.csv", "sentences.csv",
                                                     "components.csv")]
    # The code that shapes a unit: this file and what it calls on.
    here = os.path.dirname(os.path.abspath(__file__))
    inputs += [os.path.join(here, f) for f in ("units.py", "sentences.py", "keys.py",
                                                "common.py")]
    key = hashlib.sha256(("\n".join(file_sha256(f, remember=not f.endswith(".py"))
                                    if os.path.exists(f) else "-"
                                    for f in inputs) + f"\nshingle={TEMPLATE_SHINGLE}")
                         .encode()).hexdigest()[:16]
    cache = where(data, "cache", f"search_units-{key}.json")
    try:
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        pass
    out = _search_units(data)
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + ".part", "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
        os.replace(cache + ".part", cache)
    except OSError:
        pass
    return out


def _search_units(data):
    every = _read(where(data, "appraisal", "paragraphs.csv"))
    paras = {p["paragraph_id"]: p for p in every if p.get("for_model") == "true"}
    keys = component_keys(data)

    def context(p):
        return {"paragraph_id": p["paragraph_id"], "doc_id": p["doc_id"],
                "project_ids": p["project_ids"], "block": p["block"],
                "section_path": p["section_path"],
                "component_number": p.get("component_number", ""),
                "subcomponent_number": p.get("subcomponent_number", ""),
                # The data sheet's name first; else the title the heading printed (a run-in
                # title the data sheet does not list).
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
    indicator = set()
    for p in every:
        if p["block"] == "table" and not BOILERPLATE_SECTION.search(p.get("section_title", "")):
            text = _text(texts, data, p["doc_id"])
            if text is not None and prose_row(text[int(p["char_start"]):int(p["char_end"])]):
                rows = [dict(context(p), **u) for u in table_units(p, text)]
                out.extend(rows)
                if RESULTS_SECTION.search(p.get("section_title", "")):
                    indicator.update(u["unit_id"] for u in rows)
    out.sort(key=lambda u: (u["doc_id"], u["unit_id"]))
    first = {}
    for u in out:
        text = _text(texts, data, u["doc_id"]) or ""
        body = text[int(u["char_start"]):int(u["char_end"])]
        lead = text[int(u["lead_in_start"]):int(u["lead_in_end"])] if u["lead_in_start"] else ""
        # A table sentence's caption is context, not a list lead-in: a scrap of a cell
        # ('(Adaptation)') is still left out.
        u["left_out"] = "indicator" if u["unit_id"] in indicator else \
            left_out(u["kind"], body, bool(lead) and u["kind"] != "table")
        u["copy_of"] = ""
        if not u["left_out"]:
            same = _same_text(lead + "\n" + body)
            u["copy_of"] = first.setdefault(same, u["unit_id"])
            if u["copy_of"] == u["unit_id"]:
                u["copy_of"] = ""
    template_scores(out, lambda d: _text(texts, data, d) or "")
    return out


def template_scores(units, texts):
    """Fill template_projects and template_share on every unit: exact and
    shingle word-run frequencies across projects, computed with numpy (one 64-bit
    hash and one project index per shingle) so memory stays flat; deterministic."""
    import numpy
    from array import array
    project_index = {}
    # three flat arrays, not a list of tuples: there are tens of millions of rows
    row_unit, row_hash, row_proj = array("L"), array("Q"), array("L")
    exact = {}                    # exact-words hash -> set of project indices
    per_unit = []                 # (own project indices, shingle count)
    for n, u in enumerate(units):
        text = texts(u["doc_id"])
        body = text[int(u["char_start"]):int(u["char_end"])]
        if u["lead_in_start"]:      # what was embedded and graded: the item with its lead-in
            body = text[int(u["lead_in_start"]):int(u["lead_in_end"])] + "\n" + body
        words = WORD.findall(body.lower())
        projects = [project_index.setdefault(p, len(project_index))
                    for p in u["project_ids"].split("|") if p]
        key = hashlib.sha256(" ".join(words).encode()).digest()
        exact.setdefault(key, set()).update(projects)
        per_unit.append((projects, key, max(0, len(words) - TEMPLATE_SHINGLE + 1)))
        for i in range(per_unit[-1][2]):
            h = int.from_bytes(hashlib.blake2b(" ".join(words[i:i + TEMPLATE_SHINGLE]).encode(),
                                               digest_size=8).digest(), "big")
            for p in projects:
                row_unit.append(n)
                row_hash.append(h)
                row_proj.append(p)
    unit_hashes, projects_of = {}, {}
    if len(row_unit):
        arr = numpy.column_stack([numpy.frombuffer(row_unit, dtype=numpy.uint64 if row_unit.itemsize == 8 else numpy.uint32).astype(numpy.uint64),
                                  numpy.frombuffer(row_hash, dtype=numpy.uint64),
                                  numpy.frombuffer(row_proj, dtype=numpy.uint64 if row_proj.itemsize == 8 else numpy.uint32).astype(numpy.uint64)])
        # projects per shingle: distinct (hash, project) pairs, counted by hash
        pairs = numpy.unique(arr[:, 1:], axis=0)
        hashes, counts = numpy.unique(pairs[:, 0], return_counts=True)
        projects_of = dict(zip(hashes.tolist(), counts.tolist()))
        # per unit: its shingles' hashes (distinct per unit)
        for n, h in numpy.unique(arr[:, :2], axis=0).tolist():
            unit_hashes.setdefault(n, []).append(h)
    for n, u in enumerate(units):
        projects, key, count = per_unit[n]
        u["template_projects"] = len(exact[key] - set(projects))
        own = len(set(projects))
        hit = sum(1 for h in unit_hashes.get(n, ())
                  if projects_of[h] - own >= TEMPLATE_MIN_PROJECTS) if count else 0
        u["template_share"] = f"{hit / count:.2f}" if count else "0.00"


def copies(units):
    """{unit_id: [unit_id of each copy]} for the searched units that have
    copies, so a hit can be reported in every document that prints it."""
    out = {}
    for u in units:
        if u["copy_of"]:
            out.setdefault(u["copy_of"], []).append(u["unit_id"])
    return out
