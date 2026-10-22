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
  prior_para_assets_mean, prior_para_assets_max
                        among the other sentences of the paragraph, the
                        average and the highest of their best cosine over
                        every asset's phrases: how asset-dense the paragraph
                        is, and whether one neighbour is clearly an asset
  prior_para_measures_mean, prior_para_measures_max
                        the same over every measure's phrases

No cut-off goes into these: the fit decides later what level counts.
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
PARA_COLS = ["prior_para_assets_mean", "prior_para_assets_max", "prior_para_measures_mean",
             "prior_para_measures_max"]
COLS = ["prior_phrase_cosine", "prior_phrase_match", "prior_table_row", "prior_position",
        "prior_words", "prior_has_component", *PARA_COLS]
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
    """(key, [(set, search run folder)]): what the paragraph context is made
    from, without computing it (a run key needs only this)."""
    import labels
    import search
    from paths import file_sha256
    index_path = where(data, "embeddings", "sentence_index.csv")
    if not os.path.exists(index_path):          # nothing embedded: no context
        return "", []
    runs = []
    for name in sorted(sets or labels.label_sets()):
        try:
            folder, _ = search.run_folder(name, data)
        except SystemExit:
            continue
        if os.path.exists(os.path.join(folder, "manifest.json")):
            runs.append((name, folder))
    key = hashlib.sha256(json.dumps([VERSION, file_sha256(index_path),
                                     [os.path.basename(f) for _, f in runs]]).encode()
                         ).hexdigest()[:16]
    return key, runs


def paragraph_context(data, sets=None):
    """(key, {unit_id: (assets mean, assets max, measures mean, measures max)})
    over every embedded unit: among the OTHER sentences of its paragraph, the
    average and the highest of their best cosine over every asset's phrases,
    and over every measure's phrases (None for a sentence alone in its
    paragraph). No cut-off: the fit decides what level counts. From every
    label set's current search run (or `sets`); kept in
    data/prior/paragraphs-<key>.json, the key covering the search runs and the
    sentence index, so a new phrase or measure is a new context."""
    import csv
    import numpy
    import search
    key, runs = paragraph_key(data, sets)
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
    best = {"asset": numpy.full(len(index), numpy.nan), "measure": numpy.full(len(index), numpy.nan)}
    for name, folder in runs:
        kind = "asset" if name == ASSET_SET else "measure"
        _, scores, _ = search.load_run(folder, data)
        for s in range(0, len(index), search.BLOCK):
            block = numpy.asarray(scores[s:s + search.BLOCK], dtype="float64").max(axis=1)
            best[kind][s:s + len(block)] = numpy.fmax(best[kind][s:s + len(block)], block)
    rows = defaultdict(list)
    for i, (_, pid) in enumerate(index):
        rows[pid].append(i)
    out = {}
    for pid, members in rows.items():
        stats = {}
        for kind, b in best.items():
            vals = [(b[i], i) for i in members if not numpy.isnan(b[i])]
            total = sum(v for v, _ in vals)
            top = sorted(vals, reverse=True)[:2]
            stats[kind] = (total, len(vals), top)
        for i in members:
            row = []
            for kind in ("asset", "measure"):
                total, n, top = stats[kind]
                own = best[kind][i]
                has = not numpy.isnan(own)
                others = n - has
                mean = (total - (own if has else 0.0)) / others if others else None
                high = next((v for v, j in top if j != i), None)
                row += [None if mean is None else round(float(mean), 4),
                        None if high is None else round(float(high), 4)]
            out[index[i][0]] = tuple(row)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache + ".part", "w", encoding="utf-8") as fh:
        json.dump(out, fh, sort_keys=True)
    os.replace(cache + ".part", cache)
    return key, out


def paragraph_inputs(context, unit_id):
    """The four paragraph inputs of a unit (blank when it is alone in its
    paragraph or not in the index)."""
    v = context.get(unit_id, (None, None, None, None))
    return dict(zip(PARA_COLS, (fmt(x) for x in v)))


def fmt(x):
    return "" if x is None else f"{x:.4f}"
