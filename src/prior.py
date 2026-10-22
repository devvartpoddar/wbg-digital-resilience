"""The prior inputs that need no grading: what is known about an item before
the decision model is asked (docs/method/decisions.md).

Every question to the decision model stores the same columns next to its
answers, so one fit can later read them all:

  prior_phrase_cosine   best embedding cosine to the label's search phrases
  prior_phrase_match    the best phrase's share of information found in the
                        text: each word weighted by its inverse document
                        frequency over every search unit (a rare word counts
                        for more than a common one)

Two more are made once every stage has run, from what the pipeline
classified (paragraph_classified, loaded as wbg.unit_paragraph_context):

  prior_para_assets     other sentences in the paragraph the asset map gives
                        an asset (its best asset at asset.floor or more)
  prior_para_measures   other sentences in the paragraph committed for any
                        measure (each measure's floor)

They move when the classification is tuned, as they should: a paragraph
about data centres with two committed measure sentences raises each of its
sentences, whichever asset or measure is asked about.

The first two read only the text and its embedding; the paragraph counts read
the finished classification of the other sentences, so no item depends on the
order the others were asked. Nothing reads these yet; the weights
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
VERSION = "2"     # 2: structure dropped, paragraph counts from the classification
COLS = ["prior_phrase_cosine", "prior_phrase_match"]
PARA_COLS = ["prior_para_assets", "prior_para_measures"]
ASSET_SET = "assets"
WORD = re.compile(r"[a-z]{2,}")


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


# ------------------------------------------- paragraph counts (classified)

def paragraph_classified(data, projects=None):
    """(key, [{unit_id, paragraph_id, prior_para_assets, prior_para_measures}])
    for every embedded unit in scope: how many OTHER sentences of its
    paragraph the current asset map gives an asset (the best asset's
    probability at the control panel's asset.floor or more) and how many are
    committed for any measure (each measure run's `committed`). key names the
    runs read; (None, []) with no run at all."""
    import csv
    import labels
    import measure
    import panel
    floor = float(panel.get("asset.floor", 0.3))
    index_path = where(data, "embeddings", "sentence_index.csv")
    if not os.path.exists(index_path):            # nothing embedded: nothing classified
        return None, []
    import posterior
    # The same reading of the map the measure stage makes: through the asset
    # layers when they are fitted.
    vector_of, projects_of = posterior.lookups(data)
    unit_assets, asset_run = measure.asset_map(data, projects, vector_of, projects_of)
    with_asset = {u for u, ps in unit_assets.items() if ps and max(ps.values()) >= floor}
    committed, runs = set(), [asset_run] if asset_run else []
    for name in labels.label_sets():
        if name == ASSET_SET:
            continue
        try:
            folder = measure.current_run(name, data, projects=projects)
        except SystemExit:                         # no search run for the set yet
            continue
        if not folder:
            continue
        runs.append(os.path.basename(folder))
        with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
            committed |= {u["unit_id"] for u in csv.DictReader(fh) if u.get("committed") == "true"}
    if not runs:
        return None, []
    with open(index_path, newline="", encoding="utf-8") as fh:
        index = [(r["unit_id"], r["paragraph_id"], r.get("project_ids", ""))
                 for r in csv.DictReader(fh)]
    per = defaultdict(lambda: [0, 0])
    for uid, pid, _ in index:
        per[pid][0] += uid in with_asset
        per[pid][1] += uid in committed
    out = []
    for uid, pid, proj in index:
        if projects and not set(proj.split("|")) & set(projects):
            continue
        out.append({"unit_id": uid, "paragraph_id": pid,
                    "prior_para_assets": per[pid][0] - (uid in with_asset),
                    "prior_para_measures": per[pid][1] - (uid in committed)})
    key = f"{VERSION}:{floor}:" + "|".join(sorted(runs))
    return key, out


def fmt(x):
    return "" if x is None else f"{x:.4f}"
