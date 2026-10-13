"""The units the search runs over, each carrying where it sits.

Sentences are where we search, paragraphs where we work: a sentence states
one thing, so a measure or an asset is found in a sentence; the paragraph
around it is what a model or a person reads to decide. So every search unit
carries its paragraph and the paragraph's context, and nothing downstream has
to re-join:

  unit_id              the sentence id ('D:p00042:s003'), or the paragraph id
                       for a footnote
  kind                 sentence | footnote
  paragraph_id, doc_id, project_ids (the project of the document part it sits
                       in), block, section_path
  component_number, subcomponent_number
                       the component heading the paragraph sits under, in its
                       own document's numbering
  component_key        that component's name_key (keys.name_key): the same
                       component in another document, even renumbered
  char_start, char_end offsets into data/appraisal/text/<doc_id>.txt
  n_tokens, text_sha256
  lead_in_start, lead_in_end
                       for a list item, the offsets of the sentence that
                       introduces the list ('Sub-component 1.1 will
                       finance:'); blank otherwise. '(b) towers;' alone says
                       nothing about what the towers are for, so embed.py
                       embeds the item with its lead-in in front. The unit is
                       still the item: its id, offsets and hash are its own.

The units are the sentences of the narrative and annex paragraphs given to
the model, and each footnote given to the model, whole: footnotes are mostly
references, which a sentence splitter would cut into nonsense (clean.py does
not split them).
"""
import csv
import os
import re

from paths import where

UNIT_COLS = ["unit_id", "kind", "paragraph_id", "doc_id", "project_ids", "block",
             "section_path", "component_number", "subcomponent_number", "component_key",
             "char_start", "char_end", "n_tokens", "text_sha256", "lead_in_start",
             "lead_in_end"]
ITEM_MARKER = re.compile(r"^\s*(?:\((?:[a-z]|[ivx]{1,4}|\d{1,2})\)|[a-z]\.|[ivx]{1,4}\.)\s")


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
        cache[doc_id] = open(path, encoding="utf-8").read() if os.path.exists(path) else None
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
    paras = {p["paragraph_id"]: p for p in _read(where(data, "appraisal", "paragraphs.csv"))
             if p.get("for_model") == "true"}
    keys = component_keys(data)

    def context(p):
        return {"paragraph_id": p["paragraph_id"], "doc_id": p["doc_id"],
                "project_ids": p["project_ids"], "block": p["block"],
                "section_path": p["section_path"],
                "component_number": p.get("component_number", ""),
                "subcomponent_number": p.get("subcomponent_number", ""),
                "component_key": keys.get((p["doc_id"], p["component_number"]), "")
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
    out.sort(key=lambda u: (u["doc_id"], u["unit_id"]))
    return out
