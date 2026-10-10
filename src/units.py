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

The units are the sentences of the narrative and annex paragraphs given to
the model, and each footnote given to the model, whole: footnotes are mostly
references, which a sentence splitter would cut into nonsense (clean.py does
not split them).
"""
import csv
import os

from paths import where

UNIT_COLS = ["unit_id", "kind", "paragraph_id", "doc_id", "project_ids", "block",
             "section_path", "component_number", "subcomponent_number", "component_key",
             "char_start", "char_end", "n_tokens", "text_sha256"]


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
    for s in _read(where(data, "appraisal", "sentences.csv")):
        p = paras.get(s["paragraph_id"])
        if p is None:
            continue
        out.append(dict(context(p), unit_id=s["sentence_id"], kind="sentence",
                        char_start=s["char_start"], char_end=s["char_end"],
                        n_tokens=s["n_tokens"], text_sha256=s["text_sha256"]))
    for p in paras.values():
        if p["block"] == "footnote":
            out.append(dict(context(p), unit_id=p["paragraph_id"], kind="footnote",
                            char_start=p["char_start"], char_end=p["char_end"],
                            n_tokens=p["n_tokens"], text_sha256=p["text_sha256"]))
    out.sort(key=lambda u: (u["doc_id"], u["unit_id"]))
    return out
