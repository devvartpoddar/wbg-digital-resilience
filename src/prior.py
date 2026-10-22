"""The prior inputs that need no grading: what is known about an item before
the decision model is asked (docs/method/decisions.md).

Every question to the decision model stores the same columns next to its
answers, so one fit can later read them all:

  prior_phrase_cosine   best embedding cosine to the label's search phrases
  prior_phrase_match    the best phrase's share of information found in the
                        text: each word weighted by its inverse document
                        frequency over every search unit (a rare word counts
                        for more than a common one)
  prior_table_row       the unit is a prose table row
  prior_position        the sentence's place in its paragraph (0: a table
                        row or a whole unit)
  prior_words           its length in words
  prior_has_component   it sits under a known component
  prior_para_assets     other sentences in the paragraph that look like an
                        asset: their best cosine over every asset's phrases
                        reaches the asset gate (control panel
                        asset.gate_cosine)
  prior_para_measures   other sentences in the paragraph that look like a
                        measure: their best cosine over every measure's
                        phrases reaches the measure gate (measure.gate_cosine)

A paragraph about data centres with two measure sentences raises every one of
its sentences, whichever measure or asset is asked about; a measure added to
analysis/labels/ counts in the paragraphs from its first search.

Only text, structure and embeddings go in: never a model answer, so no item
depends on the order others were asked. Nothing reads these yet; the weights
and cut-offs that use them are fitted after a sheet is graded.
"""
import hashlib
import json
import math
import os
import re
from collections import defaultdict

from paths import where

# Part of every run key that stores these columns: a change here is a new run
# (its answers come from the store).
VERSION = "1"
COLS = ["prior_phrase_cosine", "prior_phrase_match", "prior_table_row", "prior_position",
        "prior_words", "prior_has_component", "prior_para_assets", "prior_para_measures"]
ASSET_SET = "assets"
WORD = re.compile(r"[a-z]{2,}")
TABLE_ROW = re.compile(r":t\d+$")
SENTENCE = re.compile(r":s(\d+)$")


def words(text):
    """The text's words, lower case, letters only, a plural's final s dropped
    (towers, tower; not process or gas), the same way for the corpus counts
    and the phrases."""
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in WORD.findall((text or "").lower())]


# ------------------------------------------------------- document frequency

def word_df(data):
    """(units, {word: units containing it}) over every embedded search unit,
    computed once per state of the sentence index and kept in
    data/prior/word_df-<key>.json."""
    from paths import file_sha256
    index_path = where(data, "embeddings", "sentence_index.csv")
    key = hashlib.sha256(f"{file_sha256(index_path)}\t{VERSION}".encode()).hexdigest()[:16]
    cache = where(data, "prior", f"word_df-{key}.json")
    try:
        with open(cache, encoding="utf-8") as fh:
            got = json.load(fh)
        return got["units"], got["df"]
    except (OSError, ValueError, KeyError):
        pass
    import csv
    import measure
    with open(index_path, newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    df = defaultdict(int)
    for text in measure.unit_texts(data, index).values():
        for w in set(words(text)):
            df[w] += 1
    out = {"units": len(index), "df": dict(sorted(df.items()))}
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache + ".part", "w", encoding="utf-8") as fh:
        json.dump(out, fh, sort_keys=True)
    os.replace(cache + ".part", cache)
    return out["units"], out["df"]


class Weights:
    """Inverse document frequency of a word: log(units / (units with it + 1)),
    at least 0."""

    def __init__(self, units, df):
        self.units, self.df = units, df

    def __call__(self, word):
        return max(0.0, math.log(self.units / (self.df.get(word, 0) + 1)))


def phrases(texts, weight):
    """Phrases ready to match: [(set of words, {word: weight}, total weight)],
    phrases of no weight left out."""
    out = []
    for t in texts:
        ws = set(words(t))
        wt = {w: weight(w) for w in ws}
        total = sum(wt.values())
        if total > 0:
            out.append((ws, wt, total))
    return out


def phrase_match(text_words, prepared):
    """The best phrase's share of weight found among the text's words (a
    set), 0 to 1; 0 with no phrases."""
    best = 0.0
    for ws, wt, total in prepared:
        best = max(best, sum(wt[w] for w in ws & text_words) / total)
    return best


# ---------------------------------------------------------------- structure

def structure(unit_id, text, component_number):
    """{prior_table_row, prior_position, prior_words, prior_has_component}."""
    m = SENTENCE.search(unit_id or "")
    return {"prior_table_row": "true" if TABLE_ROW.search(unit_id or "") else "false",
            "prior_position": int(m.group(1)) if m else 0,
            "prior_words": len((text or "").split()),
            "prior_has_component": "true" if component_number else "false"}


# --------------------------------------------------------- paragraph context

def paragraph_key(data, sets=None):
    """(key, gates, [(set, search run folder)]): what the paragraph counts are
    made from, without counting (a run key needs only this)."""
    import labels
    import panel
    import search
    from paths import file_sha256
    gates = {"asset": float(panel.get("asset.gate_cosine", 0.25)),
             "measure": float(panel.get("measure.gate_cosine", 0.45))}
    index_path = where(data, "embeddings", "sentence_index.csv")
    if not os.path.exists(index_path):          # nothing embedded: nothing to count
        return "", gates, []
    runs = []
    for name in sorted(sets or labels.label_sets()):
        try:
            folder, _ = search.run_folder(name, data)
        except SystemExit:
            continue
        if os.path.exists(os.path.join(folder, "manifest.json")):
            runs.append((name, folder))
    key = hashlib.sha256(json.dumps([VERSION, gates, file_sha256(index_path),
                                     [os.path.basename(f) for _, f in runs]]).encode()
                         ).hexdigest()[:16]
    return key, gates, runs


def paragraph_counts(data, sets=None):
    """(key, {unit_id: (other sentences in its paragraph that look like an
    asset, that look like a measure)}) over every embedded unit, from every
    label set's current search run (or `sets`). Kept in
    data/prior/paragraphs-<key>.json; the key covers the search runs, the two
    gates and the sentence index, so a new phrase or measure is a new count
    and a new key in every stage that stores it."""
    import csv
    import numpy
    import search
    key, gates, runs = paragraph_key(data, sets)
    if not key:
        return "", {}
    index_path = where(data, "embeddings", "sentence_index.csv")
    cache = where(data, "prior", f"paragraphs-{key}.json")
    try:
        with open(cache, encoding="utf-8") as fh:
            return key, {u: tuple(v) for u, v in json.load(fh).items()}
    except (OSError, ValueError):
        pass
    with open(index_path, newline="", encoding="utf-8") as fh:
        index = [(r["unit_id"], r["paragraph_id"]) for r in csv.DictReader(fh)]
    looks = {"asset": numpy.zeros(len(index), dtype=bool),
             "measure": numpy.zeros(len(index), dtype=bool)}
    for name, folder in runs:
        kind = "asset" if name == ASSET_SET else "measure"
        _, scores, _ = search.load_run(folder, data)
        for s in range(0, len(index), search.BLOCK):
            block = numpy.asarray(scores[s:s + search.BLOCK]).max(axis=1)
            looks[kind][s:s + len(block)] |= block >= gates[kind]
    per = defaultdict(lambda: [0, 0])
    for (uid, pid), a, m in zip(index, looks["asset"], looks["measure"]):
        per[pid][0] += int(a)
        per[pid][1] += int(m)
    out = {uid: (per[pid][0] - int(a), per[pid][1] - int(m))
           for (uid, pid), a, m in zip(index, looks["asset"], looks["measure"])}
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache + ".part", "w", encoding="utf-8") as fh:
        json.dump(out, fh, sort_keys=True)
    os.replace(cache + ".part", cache)
    return key, out


def paragraph_inputs(counts, unit_id):
    """{prior_para_assets, prior_para_measures} for a unit (0 and 0 when the
    unit is not in the index)."""
    a, m = counts.get(unit_id, (0, 0))
    return {"prior_para_assets": a, "prior_para_measures": m}


def fmt(x):
    return "" if x is None else f"{x:.4f}"
