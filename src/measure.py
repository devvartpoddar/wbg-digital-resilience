#!/usr/bin/env python3
"""Grade a measure's units with the decision model, through the funnel, and
deepen the search while it still yields.

The funnel, each level cheaper than the next and each one's loss measurable:

  1. The pool: the units a measure's word patterns match (<set>_patterns.csv),
     plus every unit whose best cosine over the measure's phrases reaches
     the gate: the skip rule's fitted gate (src/skip.py), else the control
     panel's measure.gate_cosine (or its embedding top N, measure.top). No
     model reads anything here.
  2. The sentence asset map (src/classify.py on the assets set): every
     sentence's score for each asset, asked with the same graded question
     form as the measure's.
  3. The decision model reads each unit once: the measure's graded question
     about the sentence. Nothing else is asked here. Each score (the probabilities of the grades the control panel counts,
     2 and 3) goes through the probability layer (src/posterior.py) when one
     is fitted, and the measure's floor (control panel) reads the result:
     the one decision (is_committed).
  4. Relevance feedback (src/feedback.py, the asset map's too): the vectors
     of the committed units are averaged
     into a centroid (minus feedback.negative_weight times the centroid of
     the units that do not count); the corpus is re-ranked by cosine to it;
     the next feedback.batch unseen units are graded in one parallel batch;
     and this repeats until a round yields fewer committed units than
     feedback.min_yield, or feedback.max_rounds is reached. The
     stopping rule is a yield rule for retrieval depth, not a recall
     guarantee: what the search never reaches stays unmeasured until a
     person grades a sample.
  5. The asset of a unit (asset_link, asset_link_level, asset_link_p): the
     sentence's own best asset when it reaches the control panel's
     asset.floor (level unit); else the asset with the highest sum of its own
     score and every earlier sentence's in its paragraph, each weighted by
     asset.decay per sentence back, when that reaches the floor (level
     paragraph); else none.

Every threshold here is a control-panel setting
(inputs/config/control_panel.csv, src/panel.py).

The measure's question names the measure and its definition and never the
asset: the decision model reads literally, and a question about "climate
risk assessment of digital infrastructure" scored a tower-siting assessment
at 0.17 where the same question without the asset phrase scored it 0.97.

Nothing is gated on the asset: every unit in the pool is asked about the
sentence alone, the same question form as the asset map's. Every row carries the wording's firmness (activity, outcome, none): a
column to filter on, never a cut.

Reads  data/search/<set>-<version>/{scores.npy,queries.csv}
       data/classify/assets-<version>/scores.csv        the sentence asset map (optional)
       data/labels/<set>_posterior.json, assets_posterior.json   probability layers (optional)
       inputs/config/control_panel.csv                   every threshold
       data/embeddings/{sentence_emb.npy,sentence_index.csv}
       data/appraisal/{paragraphs,components}.csv, data/appraisal/text/
       analysis/labels/<set>.csv, <set>_scale.csv, <set>_patterns.csv;
       inputs/taxonomy/assets.csv
Writes data/classify/answers/<model>/...                 every answer, once
       data/measures/<set>-<version>/units.csv           one row per unit seen
       data/measures/<set>-<version>/manifest.json       rounds, calls, cost, time
       data/reports/measure_<set>.txt
       and, with --publish (the default from the command line), its rows in
       Postgres (src/store.py)

<version> is a SHA-256 over the search run, the patterns, the asset map
version, the exact questions, the model, the prior inputs' version and the
feedback settings. The units a feedback round reads depend on the
stored answers, which are the record, so an unchanged run reads the store
and asks nothing.

  ./wbg measure cra                      cosine pool, feedback rounds
  ./wbg measure cra --dry-run            round 0 only, no request
"""
import argparse, csv, hashlib, json, os, re, sys, time
from collections import defaultdict

import numpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
import labels as labels_mod  # noqa: E402
import panel  # noqa: E402
import posterior  # noqa: E402
import prior  # noqa: E402
import search  # noqa: E402

# The dated snapshot, not the alias: the alias moves when the vendor ships a
# release, and a run must be repeatable. The response names the snapshot that
# served it; a mismatch is reported.
MODEL = "typesafe/jev-1.13-20260917"
ASSET_SET = "assets"
# The asset comes from the sentence asset map (src/classify.py).

# Firmness of the wording, read by fixed word lists: 'activity' (the project
# finances, builds, requires, includes...), 'outcome' ("will enable", "is
# expected to": what should follow, not what is done) or 'none'. A column to
# filter on, never a cut. An activity phrase anywhere wins.
ACTIVITY = re.compile(
    r"\b(?:will|shall|to) (?:also |further |specifically |be )?(?:be )?"
    r"(?:financ|fund|support|install|deploy|build|construct|establish|develop|procur|"
    r"integrat|link|connect|upgrad|rehabilitat|conduct|carr(?:y|ied) out|undertak|prepar|"
    r"design|implement|introduc|provid|acquir|equip|strengthen(?:ed|ing)? (?:the|its)|"
    r"set up|put in place|creat|adopt|requir|includ|incorporat|expand|digiti[sz]|"
    r"mainstream|assess|map|bur(?:y|ied)|elevat|reinforc|harden|relocat|backup|back up)\w*"
    r"|\b(?:is|are) (?:being |to be )?(?:financed|supported|required|included|integrated|"
    r"deployed|installed|built|procured|conducted|undertaken|planned|designed)\b"
    r"|\b(?:activities|investments) (?:will )?includ\w*|\bthe following\b|\bincludes?\b"
    r"|\bsupported by the (?:project|component)|\bunder (?:this |the )?(?:sub-?)?component\b"
    r"|\bthe (?:project|component|program(?:me)?) (?:will )?(?:finances?|supports?)\b"
    r"|^\(?[a-z0-9ivx]{1,4}[).]\s", re.I)
OUTCOME = re.compile(
    r"\bwill (?:also |further |thus |therefore |thereby |in turn |greatly )?"
    r"(?:enable|allow|help|contribut|improv|enhanc|increas|reduc|mitigat|facilitat|"
    r"make it|lead|result|benefit|boost|promot|foster|minimi[sz]|lower|empower)\w*"
    r"|\bby enabling\b|\b(?:is|are) expected to\b", re.I)


def firmness(text):
    """'activity', 'outcome' or 'none'."""
    if ACTIVITY.search(text):
        return "activity"
    if OUTCOME.search(text):
        return "outcome"
    return "none"
# Every number below is a control-panel setting (inputs/config/control_panel.csv).
LINK_MIN = float(panel.get("asset.floor", 0.3))   # a sentence has an asset at or above this
DECAY = float(panel.get("asset.decay", 0.4))      # per sentence back, for a sentence naming none
TOP = int(panel.get("measure.top", 0))            # round 0 by count; 0 = off (the cosine gate is the pool)
MIN_COSINE = float(panel.get("measure.gate_cosine", 0.3))  # the starting gate, until the
                      # skip rule (src/skip.py) has fitted the measure's own
BATCH = int(panel.get("feedback.batch", 500))      # units per feedback round
MIN_YIELD = int(panel.get("feedback.min_yield", 10))  # a round yielding fewer is the last
MAX_ROUNDS = int(panel.get("feedback.max_rounds", 6))
NEGATIVE_WEIGHT = float(panel.get("feedback.negative_weight", 0.5))  # Rocchio: rejected share


def score_of(u):
    """A unit's score: the model's probability of the grades the control panel
    counts (read.grades, 2 and 3)."""
    return sum(float(u.get(f"p{g}") or 0) for g in panel.grades())


def is_committed(u, floor=None):
    """The one decision: a unit counts for the measure when its posterior
    probability (p_committed_calibrated: the probability layer's reading of
    the model's score for grades 2 and 3, or that score itself before a layer
    is fitted) is at or above the measure's floor (control panel floor.<set>,
    else floor.default). A row that carries its own `committed` column says
    so."""
    if u.get("committed") in ("true", "false"):
        return u["committed"] == "true"
    if u.get("sent") != "true":
        return False
    floor = DEFAULT_FLOOR if floor is None else floor
    p = u.get("p_committed_calibrated", "")
    return (float(p) if p != "" else score_of(u)) >= floor


DEFAULT_FLOOR = float(panel.get("floor.default", 0.5))
# grade, expected and p0-p3 are the model's raw answer, kept as the record;
# no decision reads them (is_committed reads the posterior).
UNIT_COLS = ["label_id", "unit_id", "kind", "paragraph_id", "doc_id", "project_ids",
             "component_number", "subcomponent_number", "component_key", "copies", "route",
             "round", "rank", "score", "patterns", "firmness",
             "asset_para", "asset_p_para",
             "asset_unit", "asset_p_unit", "assets_yes", "asset_link", "asset_link_level",
             "asset_link_p", "template_projects", "template_share",
             *prior.COLS, "sent", "committed",
             "grade", "expected", "confidence", "p0", "p1", "p2", "p3",
             "p_committed_calibrated",
             "model_served", "cost", "text"]
# Asset thresholds the report tabulates (nothing is chosen here).
REPORT_THRESHOLDS = (0.3, 0.5, 0.7, 0.9)


def questions(labels, scale):
    """The measure's graded question(s) about the sentence: the same question
    form as the asset map's (src/classify.py)."""
    return {lab["id"]: classify.graded_question(lab, scale) for lab in labels}


# ------------------------------------------------------------------ the inputs

def asset_map(data, projects=None, vector_of=None, projects_of=None, folder=None):
    """({unit_id: {asset: probability}}, run name) from the current sentence
    asset map over the same scope: each asked sentence's score for each
    asset (read.grades), through the asset's probability layer when one has
    been fitted (data/labels/assets_posterior.json). Empty without a run."""
    folder = folder or classify.current_run(ASSET_SET, data, projects=projects)
    if not folder:
        return {}, ""
    layers = classify.asset_layers(data, folder, vector_of, projects_of) if vector_of else {}
    out = {}
    with open(os.path.join(folder, "scores.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("asked") not in ("", None, "true") or not r.get("unit_id"):
                continue
            p = score_of(r)
            if r["label_id"] in layers:
                p = layers[r["label_id"]].probability(r["unit_id"], p)
            out.setdefault(r["unit_id"], {})[r["label_id"]] = p
    return out, os.path.basename(folder)


def sentence_asset(own, earlier, decay=None, floor=None):
    """(asset, probability, level) for a sentence: its own best asset when
    that reaches LINK_MIN (level 'unit'); else the best asset of its own
    scores plus each earlier sentence's in the same paragraph, the k-th one
    back weighted by decay**k (level 'paragraph'); else ('', best, 'none'). own and earlier[i] are
    {asset: probability}; earlier is nearest first."""
    decay = DECAY if decay is None else decay
    floor = LINK_MIN if floor is None else floor
    best = max(own, key=own.get) if own else ""
    if best and own[best] >= floor:
        return best, own[best], "unit"
    total = dict(own)
    for k, prev in enumerate(earlier, 1):
        for a, p in prev.items():
            total[a] = total.get(a, 0.0) + (decay ** k) * p
    if total:
        a = max(total, key=total.get)
        if total[a] >= floor and decay:
            return a, min(total[a], 1.0), "paragraph"
        return "", total[a], "none"
    return "", 0.0, "none"


def unit_texts(data, index):
    """{unit_id: text as embedded} (a list item with its lead-in)."""
    import units
    every = {u["unit_id"]: u for u in units.search_units(data)}
    cache, out = {}, {}
    for r in index:
        u = every[r["unit_id"]]
        d = u["doc_id"]
        if d not in cache:
            with open(where(data, "text", f"{d}.txt"), encoding="utf-8") as fh:
                cache[d] = fh.read()
        body = cache[d][int(u["char_start"]):int(u["char_end"])]
        if u.get("lead_in_start"):
            body = cache[d][int(u["lead_in_start"]):int(u["lead_in_end"])] + "\n" + body
        out[r["unit_id"]] = body
    return out


def paragraph_rows(data):
    with open(where(data, "appraisal", "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        return {r["paragraph_id"]: r for r in csv.DictReader(fh)}


def _text(cache, data, doc_id):
    if doc_id not in cache:
        with open(where(data, "text", f"{doc_id}.txt"), encoding="utf-8") as fh:
            cache[doc_id] = fh.read()
    return cache[doc_id]


def component_names(data):
    names = {}
    path = where(data, "appraisal", "components.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in sorted(csv.DictReader(fh), key=lambda r: r["source"] == "heading"):
                names.setdefault((r["doc_id"], r["number"]), r["name"])
    return names


def state_for(row, para, unit_text, names):
    sub = row.get("subcomponent_number") or ""
    comp = sub or row.get("component_number") or ""
    name = names.get((row["doc_id"], comp)) or \
        (para.get("subcomponent_name") if sub else para.get("component_name")) or ""
    state = {"section": para.get("section_title", ""), "sentence": unit_text}
    if comp:
        state["component"] = f"{comp} {name}".strip()
    return state


# ------------------------------------------------------------------- the run

class Run:
    """Everything one measure run needs, loaded once."""

    def __init__(self, name, data, *, model=MODEL, top=TOP, min_cosine=None, batch=BATCH,
                 min_yield=MIN_YIELD, max_rounds=MAX_ROUNDS, build=True, log=print,
                 projects=None):
        self.name, self.data, self.log = name, data, log
        self.search_run, _ = search.run_folder(name, data)
        self.labels, self.scale = classify.load_labels(name, None)
        self.qs = questions(self.labels, self.scale)
        self.question_sha = classify.question_shas(self.qs)[self.labels[0]["id"]]
        # The skip rule (src/skip.py): the gate the evaluation fitted for this
        # question and search run, else the panel's starting gate.
        self.gate_fitted = None
        if min_cosine is None:
            import skip
            self.gate_fitted = skip.read(data, name, classify.question_shas(self.qs),
                                         self.search_run).get(self.labels[0]["id"])
            min_cosine = self.gate_fitted if self.gate_fitted is not None else MIN_COSINE
        self.settings = {"model": model, "top": top, "min_cosine": min_cosine,
                         "batch": batch,
                         "min_yield": min_yield, "max_rounds": max_rounds,
                         "negative_weight": NEGATIVE_WEIGHT,
                         # the prior inputs stored with every unit (src/prior.py)
                         "prior": prior.VERSION,
                         "projects": sorted(projects) if projects else None,
                         # the control panel's readings this run applies
                         "panel": {**panel.items("read."), **panel.items("asset."),
                                   **panel.items("posterior.")}}
        self.projects = sorted(projects) if projects else None
        # The floor is a reading, not a question (control panel, floor.*).
        self.floor = panel.floor(name)
        if self.floor is None:
            self.floor = DEFAULT_FLOOR
        self.settings["floor"] = self.floor
        if not os.path.exists(os.path.join(self.search_run, "manifest.json")):
            raise SystemExit(f"measure: {name} has no search run for the current index; "
                             f"run ./wbg search {name} first")
        self.patterns = labels_mod.load_patterns(name)
        self.assets = [a for a in labels_mod.load_set(ASSET_SET) if a["active"] == "true"]
        asset_folder = classify.current_run(ASSET_SET, data, projects=self.projects)
        self.asset_run = os.path.basename(asset_folder) if asset_folder else ""
        # The asset layers change each sentence's asset, so they are part of the key.
        self.asset_layers = classify.asset_layers_digest(data, asset_folder)
        # The probability layer (src/posterior.py) fitted by the evaluation of a
        # graded sheet (./wbg check <set> --evaluate) on the same question:
        # applied to every score as p_committed_calibrated, which the floor
        # reads. Part of the version, so new grades give a new run (from the
        # store, free). The fit is keyed on the exact question (AGENTS.md rule
        # 9): a revised definition starts without one.
        self.layer_path = where(data, "labels", f"{name}_posterior.json")
        self.calibration = posterior.digest(posterior.read_spec(self.layer_path, self.question_sha))
        h = hashlib.sha256()
        h.update(f"{os.path.basename(self.search_run)}\t{self.asset_run}\t{self.asset_layers}\t"
                 f"{json.dumps(self.settings, sort_keys=True)}\n".encode())
        for pt in self.patterns:
            h.update(f"p\t{pt['label_id']}\t{pt['pattern_id']}\t{pt['pattern']}\n".encode())
        h.update(json.dumps(self.qs, sort_keys=True).encode())
        h.update(json.dumps(self.calibration, sort_keys=True).encode())
        self.out = where(data, "measures", f"{name}-{h.hexdigest()[:12]}")
        # The run folder is known from the inputs' names alone. A reader
        # (the load, the types stage, the evaluation) stops here; what
        # follows reads the corpus and is only for a run or a check sheet.
        self._loaded = False
        if build:
            self.load_corpus()

    def load_corpus(self):
        """The paragraphs, the index, the matrix, the texts, the commitment
        scores and the asset map's two levels: everything a run reads."""
        if self._loaded:
            return
        data = self.data
        self.paras = paragraph_rows(data)
        self.names = component_names(data)
        self.queries, self.scores, self.index = search.load_run(self.search_run, data)
        self.row_of = {r["unit_id"]: i for i, r in enumerate(self.index)}
        self.matrix = numpy.load(where(data, "embeddings", "sentence_emb.npy"), mmap_mode="r")
        self.vector_of = lambda u: (numpy.asarray(self.matrix[self.row_of[u]], dtype="float64")
                                    if u in self.row_of else None)
        proj = {r["unit_id"]: r.get("project_ids", "") for r in self.index}
        self.projects_of = lambda u: proj.get(u, "")
        self.unit_assets, _ = asset_map(data, self.projects, self.vector_of, self.projects_of)
        # Each paragraph's sentences in reading order, for the asset of a
        # sentence that names none (the decayed sum over those before it).
        self.before = {}
        seen = {}
        for r in self.index:
            prior = seen.setdefault(r["paragraph_id"], [])
            self.before[r["unit_id"]] = list(reversed(prior))
            prior.append(r["unit_id"])
        self.layer = posterior.load(self.layer_path, self.question_sha, self.vector_of,
                                    self.projects_of)
        self.texts = unit_texts(data, self.index)
        self.load_prior()
        self._loaded = True

    def load_prior(self):
        """What the prior inputs read (src/prior.py): each unit's best cosine
        over the measure's phrases, and the phrases weighted by how rare their
        words are. (The paragraph counts are made after every stage, from the
        classification.)"""
        lid = self.labels[0]["id"]
        cols = [j for j, q in enumerate(self.queries) if q["label_id"] == lid]
        best = numpy.asarray(self.scores[:, cols]).max(axis=1) if cols else \
            numpy.zeros(len(self.index), dtype="float32")
        self.best_cosine = {r["unit_id"]: float(b) for r, b in zip(self.index, best)}
        weight = prior.Weights(*prior.word_df(self.data))
        self.phrases = prior.phrases([q["text"] for q in self.queries
                                      if q["label_id"] == lid and q["kind"] == "phrase"], weight)

    def prior_of(self, r, text):
        """The unit's prior inputs (src/prior.py)."""
        uid = r["unit_id"]
        return {"prior_phrase_cosine": prior.fmt(self.best_cosine.get(uid)),
                "prior_phrase_match": prior.fmt(prior.phrase_match(set(prior.words(text)),
                                                                   self.phrases))}

    # ---- units

    def base_row(self, r, route, rnd, rank="", score="", patterns=""):
        """A units.csv row for index row r, without the model's answers."""
        para = self.paras[r["paragraph_id"]]
        text = self.texts[r["unit_id"]]
        row = {**{c: r.get(c, "") for c in search.CONTEXT}, "label_id": self.labels[0]["id"],
                "route": route, "round": rnd, "rank": rank, "score": score,
                "patterns": patterns, "firmness": firmness(text),
                "asset_para": "", "asset_p_para": "",
                "asset_unit": "", "asset_p_unit": "", "assets_yes": "", "asset_link": "",
                "asset_link_level": "", "asset_link_p": "",
                "template_projects": r.get("template_projects", ""),
                "template_share": r.get("template_share", ""), "p_committed_calibrated": "",
                "sent": "false", "committed": "false",
                **self.prior_of(r, text),
                "text": text, "_para": para}
        self.link_into(row)
        return row

    def candidate_rows(self):
        """Round 0's pool: every unit a word pattern matches, plus every unit
        whose best cosine over the measure's phrases reaches min_cosine (or
        ranks in the top N when that is on). No model reads anything here;
        a cosine threshold carries between a rare measure and a common one
        where a count does not."""
        lid = self.labels[0]["id"]
        cols = [j for j, q in enumerate(self.queries) if q["label_id"] == lid]
        best = numpy.asarray(self.scores[:, cols]).max(axis=1) if cols else \
            numpy.zeros(len(self.index), dtype="float32")
        order = numpy.lexsort((numpy.arange(len(best)), -best))   # best first, ties by row
        rank = numpy.empty(len(best), dtype=int)
        rank[order] = numpy.arange(1, len(best) + 1)
        top, floor = self.settings["top"], self.settings["min_cosine"]
        mine = [p for p in self.patterns if p["label_id"] == lid]
        rows = []
        for i in order.tolist():
            r = self.index[i]
            if not classify.in_scope(r, self.projects):
                continue
            hit = [p["pattern_id"] for p in mine if p["regex"].search(self.texts[r["unit_id"]])]
            near = (top and rank[i] <= top) or (floor and best[i] >= floor)
            if not hit and not near:
                continue
            route = "both" if hit and near else ("pattern" if hit else "embedding")
            rows.append(self.base_row(r, route, 0, int(rank[i]), f"{best[i]:.6f}", "|".join(hit)))
        return rows

    def state_of(self, row):
        return state_for(row, row["_para"], row["text"], self.names)

    def grade_into(self, row, entries):
        """The measure's grade from the model's answer."""
        lab = self.labels[0]["id"]
        a = entries[lab]["answer"]
        probs = [float(a["probabilities"][k]) for k in sorted(a["probabilities"], key=int)]
        total = sum(probs) or 1.0
        row.update({"grade": max(range(len(probs)), key=lambda i: probs[i]),
                    "expected": f"{sum(i * q for i, q in enumerate(probs)) / total:.3f}",
                    "confidence": f"{float(a.get('confidence', 0)):.3f}",
                    **{f"p{i}": (f"{probs[i]:.4f}" if i < len(probs) else "") for i in range(4)},
                    "model_served": entries[lab]["model"],
                    "cost": f"{sum(float(e.get('cost_usd') or 0) for e in entries.values()):.6f}"})
        # The score (read.grades) through the probability layer, when one is
        # fitted; the floor reads the result.
        sc = sum(probs[g] for g in panel.grades() if g < len(probs)) / total
        if self.layer:
            sc = self.layer.probability(row["unit_id"], sc)
        row["p_committed_calibrated"] = f"{sc:.4f}"
        row["committed"] = "true" if is_committed(
            {**row, "sent": "true", "committed": ""}, self.floor) else "false"

    def link_into(self, row):
        """The sentence's asset from the sentence asset map: its own best
        (asset_unit, asset_p_unit; level 'unit'), else the decayed sum over
        the earlier sentences of its paragraph
        (asset_para, asset_p_para; level 'paragraph'), else none."""
        uid = row["unit_id"]
        own = self.unit_assets.get(uid, {})
        earlier = [self.unit_assets.get(u, {}) for u in self.before.get(uid, [])]
        best = max(own, key=own.get) if own else ""
        a, p, level = sentence_asset(own, earlier)
        row.update({"asset_unit": best, "asset_p_unit": f"{own[best]:.4f}" if best else "",
                    "assets_yes": best if level == "unit" else "",
                    "asset_para": a if level == "paragraph" else "",
                    "asset_p_para": f"{p:.4f}" if level == "paragraph" else "",
                    "asset_link": a, "asset_link_level": level,
                    "asset_link_p": f"{p:.4f}" if a else ""})

    # ---- a batch to the model

    def grade(self, rows, rnd, api_key, dry_run, workers):
        """The measure's question, through the answer store, for the rows
        in the pool. Returns (calls, cost, failures)."""
        import answers
        model = self.settings["model"]
        sent = []
        for row in rows:
            row["sent"] = "true"
            row["_state"] = self.state_of(row)
            sent.append(row)
        requests_ = [(r["_state"], self.qs) for r in sent]
        got, st = answers.ask(model, requests_, self.data,
                              api_key=api_key, log=self.log, workers=workers, dry_run=True)
        self.log(f"  round {rnd}: {len(rows):,} units, {len(sent):,} asked; measure "
                 f"question: {st['to_ask']:,} to ask (about US${st['estimate_usd']:.3f})")
        if dry_run:
            return 0, 0.0, []
        calls, cost = 0, 0.0
        if st["to_ask"]:
            if not api_key:
                raise SystemExit("measure: no credential: set $WBG_OPENROUTER_KEY")
            got, st = answers.ask(model, requests_, self.data,
                                  api_key=api_key, log=self.log, workers=workers)
            calls, cost = st["calls"], st["cost_usd"]
            if st["failures"] or not all(got):
                return calls, cost, st["failures"] or [("", "an answer is missing")]
        for row, g in zip(sent, got):
            self.grade_into(row, g)
        return calls, cost, []

    # ---- relevance feedback

    def feedback_rows(self, graded, rnd):
        """The next batch: the unseen units nearest the Rocchio query, in
        index order on ties. The query is the centroid of the committed
        units' vectors minus NEGATIVE_WEIGHT times the centroid of the units
        asked and not committed: the first run showed the raw centroid pulls in the
        measure's neighbourhood ("resilient infrastructure" in general), and
        the rejected units are exactly what that neighbourhood looks like."""
        import feedback
        pos = [self.row_of[r["unit_id"]] for r in graded
               if r["sent"] == "true" and r["committed"] == "true"]
        neg = [self.row_of[r["unit_id"]] for r in graded
               if r["sent"] == "true" and r["committed"] != "true"]
        q = feedback.query(self.matrix, pos, neg, NEGATIVE_WEIGHT)
        if q is None:
            return []
        (order, sims), = feedback.ranked(self.matrix, [q])
        seen = {r["unit_id"] for r in graded}
        pick = feedback.next_rows(
            order, lambda i: self.index[i]["unit_id"] not in seen and
            classify.in_scope(self.index[i], self.projects), self.settings["batch"])
        return [self.base_row(self.index[i], "feedback", rnd, k + 1, f"{float(sims[i]):.6f}")
                for k, i in enumerate(pick)]


def report_lines(run, rows, rounds, calls, cost, seconds, served):
    s = run.settings
    sent = [r for r in rows if r["sent"] == "true"]
    com = [r for r in sent if r["committed"] == "true"]
    floor = run.floor if run.floor is not None else DEFAULT_FLOOR
    bands = [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.01]
    post = [float(r["p_committed_calibrated"] or 0) for r in sent]
    lines = [f"{run.name}: {len(rows):,} units seen, {len(sent):,} graded by {s['model']} "
             f"(pool: cosine >= {s['min_cosine']}"
             + (" (fitted by the skip rule)" if run.gate_fitted is not None else
                " (the panel's starting gate)")
             + (f" or top {s['top']}" if s["top"] else "")
             + f", batch {s['batch']}, min yield {s['min_yield']})",
             f"  search run {os.path.basename(run.search_run)}; {len(run.patterns)} pattern(s); "
             f"asset map {run.asset_run or 'none'}; probability layer "
             f"{'fitted' if run.calibration else 'none (the model score stands)'}",
             f"  this run: {calls:,} calls, US${cost:.4f}, {seconds:.0f}s; models served: {served}",
             "  rounds (units graded / committed): " + ", ".join(
                 f"{k}: {v['graded']}/{v['committed']}" for k, v in rounds.items()),
             "\n  graded units by posterior probability: " + ", ".join(
                 f"{lo:.1f}-{min(hi, 1):.1f} {sum(1 for p in post if lo <= p < hi)}"
                 for lo, hi in zip(bands, bands[1:])),
             f"  committed (posterior >= {floor}): {len(com):,} units, "
             f"{len({p for r in com for p in r['project_ids'].split('|')})} projects; "
             f"prevalence among graded {len(com) / max(len(sent), 1):.1%}"]
    by_route = defaultdict(lambda: [0, 0])
    for r in sent:
        by_route[r["route"]][0] += 1
        by_route[r["route"]][1] += r["committed"] == "true"
    lines.append("  by route (graded / committed): " + ", ".join(
        f"{k} {v[0]:,}/{v[1]:,}" for k, v in sorted(by_route.items())))
    lines.append("  committed units by the unit-level asset answer (best asset at >= " +
                 ", ".join(f"{t}: {sum(1 for r in com if r['asset_p_unit'] and float(r['asset_p_unit']) >= t)}"
                           for t in REPORT_THRESHOLDS) + f") of {len(com)}; no asset of its own at {LINK_MIN}: "
                 f"{sum(1 for r in com if not r['assets_yes'])}")
    levels = defaultdict(int)
    for r in com:
        levels[r["asset_link_level"]] += 1
    lines.append("  committed units by asset link level (the sentence's own asset, else "
                 f"an earlier sentence of its paragraph, at {LINK_MIN}): " + ", ".join(
                     f"{k} {v}" for k, v in sorted(levels.items(), key=lambda x: -x[1])))
    linked = defaultdict(int)
    for r in com:
        if r["asset_link"]:
            linked[r["asset_link"]] += 1
    lines.append("  committed units by linked asset: " + ", ".join(
        f"{a} {n}" for a, n in sorted(linked.items(), key=lambda x: -x[1])))
    assets = defaultdict(int)
    for r in com:
        for a in r["assets_yes"].split("|"):
            if a:
                assets[a] += 1
    lines.append(f"  committed units by their own asset (at {LINK_MIN}): " + ", ".join(
        f"{a} {n}" for a, n in sorted(assets.items(), key=lambda x: -x[1])))
    proj = defaultdict(int)
    for r in com:
        for p in r["project_ids"].split("|"):
            proj[p] += 1
    lines.append("  committed units by project: " + ", ".join(
        f"{p} {n}" for p, n in sorted(proj.items(), key=lambda x: (-x[1], x[0]))))
    return lines


def measure(name, data, *, api_key="", dry_run=False, log=print, workers=classify.WORKERS,
            publish=False, **kw):
    """One measure through the funnel; with publish=True its rows go to
    Postgres as the last step (src/store.py)."""
    run = Run(name, data, log=log, **kw)
    if os.path.exists(os.path.join(run.out, "manifest.json")):
        log(f"{name}: already written: {run.out}")
        if publish:
            import store
            store.try_publish(data, sets=[name], projects=run.projects, log=log)
        return run.out
    log(f"{name}: {len(run.labels)} label(s) x {len(run.scale)} grades -> {run.out}")
    t0 = time.time()
    rows = run.candidate_rows()
    rounds, calls, cost = {}, 0, 0.0
    top = len(run.scale) - 1
    rnd = 0
    while True:
        c, k, failures = run.grade(rows if rnd == 0 else batch, rnd, api_key, dry_run, workers)
        calls += c
        cost += k
        if failures:
            # Paid calls are paid whether or not the run completes: the
            # ledger must match the provider's bill.
            classify.record_spend(data, "measure", name, run.settings["model"], calls,
                                  cost, time.time() - t0, "failed")
            log(f"  {len(failures)} request(s) failed; nothing written. Run again to resume.")
            for sha, err in failures[:5]:
                log(f"    {sha[:12]}: {err}")
            return None
        if dry_run:
            log("  --dry-run: round 0 only; no request made, nothing written")
            return None
        this = rows if rnd == 0 else batch
        got = sum(1 for r in this if r["sent"] == "true" and r["committed"] == "true")
        rounds[str(rnd)] = {"graded": sum(1 for r in this if r["sent"] == "true"),
                            "committed": got}
        log(f"    committed this round: {got}")
        if rnd > 0 and got < run.settings["min_yield"]:
            break
        if rnd >= run.settings["max_rounds"]:
            break
        rnd += 1
        batch = run.feedback_rows(rows, rnd)
        if not batch:
            break
        rows.extend(batch)
    seconds = time.time() - t0
    classify.record_spend(data, "measure", name, run.settings["model"], calls, cost, seconds,
                          os.path.basename(run.out))
    served = defaultdict(int)
    for r in rows:
        if r["sent"] == "true":
            served[r["model_served"]] += 1
        else:
            r.update({"grade": "", "expected": "", "confidence": "", "model_served": "",
                      "cost": "", **{f"p{i}": "" for i in range(4)}})
    part = run.out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "units.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=UNIT_COLS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    lines = report_lines(run, rows, rounds, calls, cost, seconds, dict(served))
    manifest = dict(run.settings, label_set=name, search_run=os.path.basename(run.search_run),
                    asset_map=run.asset_run, patterns=len(run.patterns), calibration=run.calibration, version=os.path.basename(run.out).rsplit("-", 1)[1],
                    units_seen=len(rows), graded=sum(1 for r in rows if r["sent"] == "true"),
                    rounds=rounds, questions=run.qs, question_sha256=classify.question_shas(run.qs),
                    scale=run.scale, models_served=dict(served), calls_this_run=calls,
                    cost_this_run_usd=round(cost, 6), seconds_this_run=round(seconds, 1),
                    cost_stored_usd=round(sum(float(r["cost"] or 0) for r in rows), 6),
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False, sort_keys=True)
    os.replace(part, run.out)
    report = where(data, "reports", f"measure_{name}.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + f"\n  -> {run.out}\n")
    for line in lines:
        log(line)
    if any(m != run.settings["model"] for m in served):
        log(f"  warning: a snapshot other than the one requested served some units: {dict(served)}")
    log(f"  wrote {run.out}")
    if publish:
        import store
        store.try_publish(data, sets=[name], projects=run.projects, log=log)
    return run.out


def current_run(name, data, **kw):
    """The run folder for the set as it stands now, or None."""
    out = Run(name, data, log=lambda *_: None, build=False, **kw).out
    return out if os.path.exists(os.path.join(out, "manifest.json")) else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=classify.WORKERS)
    ap.add_argument("--projects", default="",
                    help="comma-separated project ids: only their units, with the asset map "
                         "over the same scope (./wbg measure --set digital fills this)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-publish", action="store_true",
                    help="leave Postgres alone (the run folder is written either way)")
    args = ap.parse_args()
    import embed
    key = "" if args.dry_run else embed.read_key("")
    out = measure(args.label_set, args.data, api_key=key, dry_run=args.dry_run,
                  workers=args.workers, model=args.model,
                  publish=not (args.dry_run or args.no_publish),
                  projects=classify.scope(args.projects))
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
