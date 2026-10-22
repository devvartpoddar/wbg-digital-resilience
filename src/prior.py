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
  prior_para_likely     other sentences in the paragraph whose best phrase
                        cosine over the label set reaches the set's gate
  prior_para_best       the best cosine to this label among those other
                        sentences

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
        "prior_words", "prior_has_component", "prior_para_likely", "prior_para_best"]
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

def paragraph_likely(rows, best_any, gate):
    """{unit_id: other units in its paragraph whose best cosine over the label
    set reaches gate}. rows: [(unit_id, paragraph_id)]; best_any: {unit_id:
    best cosine over every label of the set}."""
    likely = defaultdict(int)
    for uid, pid in rows:
        if best_any.get(uid, -1.0) >= gate:
            likely[pid] += 1
    return {uid: likely[pid] - (best_any.get(uid, -1.0) >= gate) for uid, pid in rows}


def paragraph_best(rows, cosine):
    """{unit_id: the best cosine among the other units of its paragraph} for
    one label (None when it has none). cosine: {unit_id: cosine}."""
    top = defaultdict(list)
    for uid, pid in rows:
        if uid in cosine:
            top[pid].append((cosine[uid], uid))
    for pid in top:
        top[pid] = sorted(top[pid], reverse=True)[:2]
    out = {}
    for uid, pid in rows:
        others = [c for c, u in top.get(pid, []) if u != uid]
        out[uid] = others[0] if others else None
    return out


def fmt(x):
    return "" if x is None else f"{x:.4f}"
