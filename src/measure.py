#!/usr/bin/env python3
"""Grade a measure's units with the decision model, through the funnel, and
deepen the search while it still yields.

The funnel, each level cheaper than the next and each one's loss measurable:

  1. The pool: the units a measure's word patterns match (<set>_patterns.csv),
     plus every unit whose best cosine over the measure's phrases reaches
     --min-cosine (or its embedding top N, when --top is on). No model reads
     anything here; a cosine threshold carries between a rare measure and a
     common one where a count does not.
  2. The asset map (src/classify.py on the assets set), as columns on every
     unit: the best asset in its paragraph and in its component.
  3. The decision model reads each unit in two passes: the measure's graded
     question for every unit, then, for the units graded 2 or more, one
     choice question for the asset (which one digital asset the commitment
     concerns, or that it names none, or that it is not digital), one yes/no
     question per activity kind (govern, build, sustain, use) and
     the facets (conditional; who carries it out), so the asset is answered
     for the sentence itself and the question tokens, which are most of the
     cost, are spent only where they matter. The second pass runs in the
     background while the next round's first pass goes out.
  4. Relevance feedback: the vectors of the units the model graded committed
     are averaged into a centroid (minus half the rejected units' centroid);
     the corpus is re-ranked by cosine to it; the next --batch unseen units
     are graded in one parallel batch; and this repeats until a round yields
     fewer committed units than --min-yield, or --max-rounds is reached. The
     stopping rule is a yield rule for retrieval depth, not a recall
     guarantee: what the search never reaches stays unmeasured until a
     person grades a sample.
  5. The asset link of a unit, the three levels combined (asset_link,
     asset_link_level, asset_link_p): the unit's chosen asset when it chose
     one at LINK_MIN or more (the sentence names it), else the paragraph map
     at LINK_MIN, else the component map, else none. A unit that chose "not
     a digital asset" takes no link from its paragraph or component
     (asset_link_level not_digital): the paragraph's asset would be wrong for
     it, as with streetlights in a paragraph about towers.

The measure's question names the measure and its definition and never the
asset: the decision model reads literally, and a question about "climate
risk assessment of digital infrastructure" scored a tower-siting assessment
at 0.17 where the same question without the asset phrase scored it 0.97.

Nothing is gated by default. --gate link|unit|paragraph|component with
--asset-min sends only the units the chosen asset link scores at least that
high. Every row carries the wording's firmness (activity, outcome, none): a
column to filter on, never a cut.

Reads  data/search/<set>-<version>/{scores.npy,queries.csv}
       data/classify/assets-<version>/scores.csv        the asset map (optional)
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
version, the exact questions, the model, the context design, the gate and
the feedback settings. The units a feedback round reads depend on the
stored answers, which are the record, so an unchanged run reads the store
and asks nothing.

  ./wbg measure cra                      sentence context, cosine pool, feedback rounds
  ./wbg measure cra --context paragraph
  ./wbg measure cra --gate unit --asset-min 0.5
  ./wbg measure cra --dry-run            round 0 only, no request
"""
import argparse, csv, hashlib, json, os, re, sys, time
from collections import defaultdict

import numpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
import labels as labels_mod  # noqa: E402
import search  # noqa: E402

# The dated snapshot, not the alias: the alias moves when the vendor ships a
# release, and a run must be repeatable. The response names the snapshot that
# served it; a mismatch is reported.
MODEL = "typesafe/jev-1.13-20260917"
ASSET_SET = "assets"
# How a commitment is delivered (govern, build, sustain, use): a hand-kept
# list in inputs/config/, not a label set, so nothing searches or grades it
# on its own; the measure stage asks it alongside the measure.
KINDS_FILE = os.path.join(labels_mod.ROOT, "inputs", "config", "activity_kinds.csv")
# Two more things a TTL conversation turns on, asked of the same units as
# the kinds: whether the commitment is conditional (group "condition") and
# who carries it out (group "actor": the Bank, the borrower, an operator, a
# contractor; one yes/no each, several allowed).
FACETS_FILE = os.path.join(labels_mod.ROOT, "inputs", "config", "commitment_facets.csv")
CONTEXTS = ("sentence", "paragraph")
# The second pass (asset, kind, facet questions) reads the sentence with its
# paragraph: a climate-annex sentence says "the sites" or "the infrastructure"
# and the paragraph says which asset that is. The measure's own question
# stays on the sentence alone (the paragraph doubled its false positives).
# On the first graded sheet the sentence-alone asset answer agreed with the
# grader on 13 of 28 committed units; the grader had read the paragraph.
EXTRAS_CONTEXT = "paragraph"
GATES = ("none", "link", "unit", "paragraph", "component")

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
LINK_MIN = 0.5        # an asset answer or map score at or above this makes a link
TOP = 0               # embedding depth of round 0 by count; 0 = off (the cosine gate is the pool)
MIN_COSINE = 0.45     # round 0 pool: every unit whose best cosine over the measure's phrases
                      # reaches this (on climate risk assessment, top 2,000 was 0.473 and
                      # 0.45 kept 168 of 171 committed units from a pool of 3,070)
BATCH = 500           # units per feedback round
MIN_YIELD = 10        # a round yielding fewer committed units than this is the last
MAX_ROUNDS = 6
NEGATIVE_WEIGHT = 0.5  # Rocchio: how much of the rejected units' centroid is subtracted
EXTRAS_MIN_GRADE = 2   # asset and kind questions are asked of units graded at least this
UNIT_COLS = ["label_id", "unit_id", "kind", "paragraph_id", "doc_id", "project_ids",
             "component_number", "subcomponent_number", "component_key", "copies", "route",
             "round", "rank", "score", "patterns", "firmness",
             "asset_para", "asset_p_para", "asset_comp", "asset_p_comp",
             "asset_unit", "asset_p_unit", "assets_yes", "asset_link", "asset_link_level",
             "asset_link_p", "kinds_yes", "kind_best", "kind_p", "conditional_p",
             "actors_yes", "actor_best", "actor_p", "template_projects", "template_share",
             "sent",
             "grade", "expected", "confidence", "p0", "p1", "p2", "p3", "p4",
             "p_committed_calibrated",
             "model_served", "cost", "text"]
# Asset thresholds the report tabulates (nothing is chosen here).
REPORT_THRESHOLDS = (0.3, 0.5, 0.7, 0.9)
ASSET_YES = 0.5       # a kind or actor the unit answer puts at or above this is listed
# The asset question is one choice over the assets and these two (2026-10-21):
# as one yes/no question per asset the model said yes to two or more assets
# for 70 per cent of committed units and "digital systems" to most of them;
# as a choice, on 167 test units, digital systems fell from 50 to 4 and 49
# chose "no asset named".
ASSET_NONE = "no_asset_named"
ASSET_NOT_DIGITAL = "not_digital"


def questions(labels, scale, context, assets, kinds=(), facets=()):
    """The measure's graded question(s), one yes/no question per asset, one
    per activity kind (how the commitment is delivered: governing, building,
    sustaining, using) and one per facet (is it conditional; who carries it
    out). The measure's question is about the sentence (the paragraph is
    context when the design says so); the others read the sentence with its
    paragraph (EXTRAS_CONTEXT)."""
    where_ = ("the 'sentence' field, read with the 'paragraph' it sits in,"
              if context == "paragraph" else "the 'sentence' field,")
    where_x = ("the 'sentence' field, read with the 'paragraph' it sits in,"
               if EXTRAS_CONTEXT == "paragraph" else "the 'sentence' field,")
    out = {}
    for lab in labels:
        out[lab["id"]] = {
            "type": "score",
            "instructions": (f"How does the sentence from a World Bank project appraisal "
                             f"document in {where_} relate to {lab['name']}? "
                             f"{lab['name']} means: {lab['definition']}"),
            "criteria": [g.replace("{name}", lab["name"]) for g in scale],
        }
    if assets:
        crit = {a["id"]: f"{a['name']}: {a['definition']}" for a in assets}
        crit[ASSET_NONE] = ("No asset named: the sentence names no specific digital asset, "
                            "or speaks of the project's digital investments in general.")
        crit[ASSET_NOT_DIGITAL] = ("Not a digital asset: the commitment is about something "
                                   "that is not digital infrastructure or a digital system "
                                   "(buildings, roads, power for health facilities, "
                                   "streetlights, households).")
        out["asset:choice"] = {
            "type": "choice",
            "instructions": (f"Which one digital asset does the commitment in {where_x} "
                             f"concern? Choose exactly one: the most specific asset it names "
                             f"or clearly implies."),
            "criteria": crit}
    for k in kinds:
        out[f"kind:{k['id']}"] = {
            "type": "noul",
            "instructions": (f"Is what the sentence in {where_x} commits the project to delivered "
                             f"by {k['name'].lower()}? {k['name']} means: {k['definition']}"),
            "criteria": {"true": f"Yes: the commitment is delivered by {k['name'].lower()}, "
                                 f"as defined.",
                         "false": f"No: it is not delivered by {k['name'].lower()}."}}
    for f in facets:
        if f["group"] == "condition":
            text = (f"Is what the sentence in {where_x} commits the project to "
                    f"{f['name'].lower()}? {f['name']} means: {f['definition']}")
            crit = {"true": f"Yes: the commitment is {f['name'].lower()}, as defined.",
                    "false": f"No: the sentence states it as something that will be done."}
        else:
            text = (f"Is {f['name']} who carries out what the sentence in {where_x} commits "
                    f"the project to? {f['name']} means: {f['definition']}")
            crit = {"true": f"Yes: {f['name']} carries it out, named or clearly implied.",
                    "false": f"No: the sentence does not have {f['name']} carry it out."}
        out[f"facet:{f['id']}"] = {"type": "noul", "instructions": text, "criteria": crit}
    return out


def load_facets(path=FACETS_FILE):
    """The active facets of inputs/config/commitment_facets.csv:
    [{id, group, name, definition}]."""
    if not os.path.exists(path):
        return []
    return [{"id": f["facet_id"].strip(), "group": f["group"].strip(),
             "name": f["facet_name"].strip(), "definition": " ".join(f["definition"].split())}
            for f in labels_mod.rows(path) if labels_mod.is_active(f)]


# ------------------------------------------------------------------ the inputs

def asset_map(data, projects=None):
    """({paragraph_id: (label, p_at_least_2)}, {(doc_id, component_number):
    (label, p)}, run name) from the current assets classification over the
    same scope. Empty without a run."""
    folder = classify.current_run(ASSET_SET, data, projects=projects)
    if not folder:
        return {}, {}, ""
    para, comp = {}, {}
    with open(os.path.join(folder, "scores.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("asked") not in ("", None, "true"):
                continue
            p = float(r["p2"] or 0) + float(r["p3"] or 0)
            pid = r["paragraph_id"]
            if p > para.get(pid, ("", -1.0))[1]:
                para[pid] = (r["label_id"], p)
            key = (r["doc_id"], r.get("component_number") or "")
            if key[1] and p > comp.get(key, ("", -1.0))[1]:
                comp[key] = (r["label_id"], p)
    return para, comp, os.path.basename(folder)


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


def state_for(row, para, unit_text, para_text, names, context):
    sub = row.get("subcomponent_number") or ""
    comp = sub or row.get("component_number") or ""
    name = names.get((row["doc_id"], comp)) or \
        (para.get("subcomponent_name") if sub else para.get("component_name")) or ""
    state = {"section": para.get("section_title", ""), "sentence": unit_text}
    if comp:
        state["component"] = f"{comp} {name}".strip()
    if context == "paragraph" and para_text != unit_text:
        state["paragraph"] = para_text
    return state


# ------------------------------------------------------------------- the run

class Run:
    """Everything one measure run needs, loaded once."""

    def __init__(self, name, data, *, model=MODEL, context="sentence", gate="none",
                 asset_min=0.0, top=TOP, min_cosine=MIN_COSINE, batch=BATCH,
                 min_yield=MIN_YIELD, max_rounds=MAX_ROUNDS, build=True, log=print,
                 projects=None):
        if context not in CONTEXTS or gate not in GATES:
            raise SystemExit(f"measure: --context is one of {CONTEXTS}, --gate one of {GATES}")
        self.name, self.data, self.log = name, data, log
        self.settings = {"model": model, "context": context, "gate": gate,
                         "asset_min": asset_min, "top": top, "min_cosine": min_cosine,
                         "batch": batch,
                         "min_yield": min_yield, "max_rounds": max_rounds,
                         "negative_weight": NEGATIVE_WEIGHT,
                         "extras_min_grade": EXTRAS_MIN_GRADE,
                         "extras_context": EXTRAS_CONTEXT,
                         "projects": sorted(projects) if projects else None}
        self.projects = sorted(projects) if projects else None
        self.search_run, _ = search.run_folder(name, data)
        if not os.path.exists(os.path.join(self.search_run, "manifest.json")):
            raise SystemExit(f"measure: {name} has no search run for the current index; "
                             f"run ./wbg search {name} first")
        self.patterns = labels_mod.load_patterns(name)
        self.labels, self.scale = classify.load_labels(name, None)
        self.assets = [a for a in labels_mod.load_set(ASSET_SET) if a["active"] == "true"]
        self.kinds = [{"id": k["kind_id"].strip(), "name": k["kind_name"].strip(),
                       "definition": " ".join(k["definition"].split())}
                      for k in (labels_mod.rows(KINDS_FILE) if os.path.exists(KINDS_FILE) else [])
                      if labels_mod.is_active(k)]
        self.facets = load_facets()
        self.qs = questions(self.labels, self.scale, context, self.assets, self.kinds,
                            self.facets)
        asset_folder = classify.current_run(ASSET_SET, data, projects=self.projects)
        self.asset_run = os.path.basename(asset_folder) if asset_folder else ""
        if gate in ("paragraph", "component") and not self.asset_run:
            raise SystemExit(f"measure: --gate {gate} needs the asset map: run ./wbg classify "
                             f"{ASSET_SET} first")
        # Platt scaling from a graded sheet (./wbg check <set> --evaluate), when
        # one exists: applied to P(committed) as p_committed_calibrated. Part
        # of the version, so new grades give a new run (from the store, free).
        self.calibration = None
        cpath = where(data, "labels", f"{name}_calibration.json")
        if os.path.exists(cpath):
            with open(cpath, encoding="utf-8") as fh:
                cal = json.load(fh)
            # Grades are keyed on the exact question (AGENTS.md rule 9), and so
            # is the scaling fitted to them: a revised definition starts without one.
            if cal.get("question_sha256") == classify.question_shas(self.qs)[self.labels[0]["id"]]:
                self.calibration = cal.get("grades", {}).get(str(len(self.scale) - 1))
        h = hashlib.sha256()
        h.update(f"{os.path.basename(self.search_run)}\t{self.asset_run}\t"
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
        self.para_assets, self.comp_assets, _ = asset_map(data, self.projects)
        self.paras = paragraph_rows(data)
        self.names = component_names(data)
        self.queries, self.scores, self.index = search.load_run(self.search_run, data)
        self.row_of = {r["unit_id"]: i for i, r in enumerate(self.index)}
        self.matrix = numpy.load(where(data, "embeddings", "sentence_emb.npy"), mmap_mode="r")
        self.texts = unit_texts(data, self.index)
        self.doc_texts = {}
        self.pool, self.pending = None, []
        self._loaded = True

    # ---- units

    def base_row(self, r, route, rnd, rank="", score="", patterns=""):
        """A units.csv row for index row r, without the model's answers."""
        para = self.paras[r["paragraph_id"]]
        a_para = self.para_assets.get(r["paragraph_id"], ("", ""))
        a_comp = self.comp_assets.get((r["doc_id"], r.get("component_number") or ""), ("", ""))
        text = self.texts[r["unit_id"]]
        return {**{c: r.get(c, "") for c in search.CONTEXT}, "label_id": self.labels[0]["id"],
                "route": route, "round": rnd, "rank": rank, "score": score,
                "patterns": patterns, "firmness": firmness(text),
                "asset_para": a_para[0],
                "asset_p_para": "" if a_para[1] == "" else f"{a_para[1]:.4f}",
                "asset_comp": a_comp[0],
                "asset_p_comp": "" if a_comp[1] == "" else f"{a_comp[1]:.4f}",
                "asset_unit": "", "asset_p_unit": "", "assets_yes": "", "asset_link": "",
                "asset_link_level": "", "asset_link_p": "", "kinds_yes": "", "kind_best": "",
                "kind_p": "", "conditional_p": "", "actors_yes": "", "actor_best": "",
                "actor_p": "", "template_projects": r.get("template_projects", ""),
                "template_share": r.get("template_share", ""), "p_committed_calibrated": "",
                "sent": "false",
                "text": text, "_para": para}

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

    def gated(self, row, rnd):
        s = self.settings
        if s["gate"] == "none" or (s["gate"] in ("unit", "link") and rnd == 0):
            return True
        if s["gate"] == "link":
            if row["asset_link_level"] == "":          # not graded yet: link from the maps alone
                self.link_into(row)
            return row["asset_link"] != "" and float(row["asset_link_p"]) >= s["asset_min"]
        p = {"unit": row["asset_p_unit"], "paragraph": row["asset_p_para"],
             "component": row["asset_p_comp"]}[s["gate"]]
        return p != "" and float(p) >= s["asset_min"]

    def state_of(self, row, context=None):
        para = row["_para"]
        doc = _text(self.doc_texts, self.data, row["doc_id"])
        para_text = doc[int(para["char_start"]):int(para["char_end"])]
        return state_for(row, para, row["text"], para_text, self.names,
                         context or self.settings["context"])

    def grade_into(self, row, entries):
        """The measure's grade from the first pass's answer."""
        lab = self.labels[0]["id"]
        a = entries[lab]["answer"]
        probs = [float(a["probabilities"][k]) for k in sorted(a["probabilities"], key=int)]
        total = sum(probs) or 1.0
        row.update({"grade": max(range(len(probs)), key=lambda i: probs[i]),
                    "expected": f"{sum(i * q for i, q in enumerate(probs)) / total:.3f}",
                    "confidence": f"{float(a.get('confidence', 0)):.3f}",
                    **{f"p{i}": (f"{probs[i]:.4f}" if i < len(probs) else "") for i in range(5)},
                    "model_served": entries[lab]["model"],
                    "cost": f"{sum(float(e.get('cost_usd') or 0) for e in entries.values()):.6f}"})
        if self.calibration:
            import math
            p = min(max(probs[-1] / total, 1e-4), 1 - 1e-4)
            z = self.calibration["a"] * math.log(p / (1 - p)) + self.calibration["b"]
            z = max(-30.0, min(30.0, z))
            row["p_committed_calibrated"] = f"{1 / (1 + math.exp(-z)):.4f}"
        self.link_into(row)

    def extras_into(self, row, entries):
        """The asset and activity-kind answers from the second pass."""
        ps = {k: float(v) for k, v in entries["asset:choice"]["answer"]["probabilities"].items()}
        best = max(ps, key=ps.get) if ps else ""
        named = best not in ("", ASSET_NONE, ASSET_NOT_DIGITAL)
        row.update({"asset_unit": best if best != ASSET_NONE else "",
                    "asset_p_unit": f"{ps[best]:.3f}" if best and best != ASSET_NONE else "",
                    "assets_yes": best if named else ""})
        self.link_into(row)
        ks = {k["id"]: float(entries[f"kind:{k['id']}"]["answer"]["noul"]) for k in self.kinds}
        kb = max(ks, key=ks.get) if ks else ""
        row.update({"kinds_yes": "|".join(k for k in ks if ks[k] >= ASSET_YES),
                    "kind_best": kb, "kind_p": f"{ks[kb]:.3f}" if kb else "",
                    "cost": f"{float(row['cost']) + sum(float(e.get('cost_usd') or 0) for e in entries.values()):.6f}"})
        fs = {f["id"]: (f["group"], float(entries[f"facet:{f['id']}"]["answer"]["noul"]))
              for f in self.facets}
        cond = [p for g, p in fs.values() if g == "condition"]
        actors = {k: p for k, (g, p) in fs.items() if g == "actor"}
        ab = max(actors, key=actors.get) if actors else ""
        row.update({"conditional_p": f"{max(cond):.3f}" if cond else "",
                    "actors_yes": "|".join(k for k in actors if actors[k] >= ASSET_YES),
                    "actor_best": ab, "actor_p": f"{actors[ab]:.3f}" if ab else ""})

    def link_into(self, row):
        """asset_link, asset_link_level, asset_link_p from the three levels."""
        if row["asset_unit"] == ASSET_NOT_DIGITAL and row["asset_p_unit"] != "" \
                and float(row["asset_p_unit"]) >= LINK_MIN:
            row.update({"asset_link": "", "asset_link_level": "not_digital",
                        "asset_link_p": row["asset_p_unit"]})
            return
        for level, a, p in (("unit", row["asset_unit"], row["asset_p_unit"]),
                            ("paragraph", row["asset_para"], row["asset_p_para"]),
                            ("component", row["asset_comp"], row["asset_p_comp"])):
            if p != "" and float(p) >= LINK_MIN:
                row.update({"asset_link": a, "asset_link_level": level, "asset_link_p": p})
                return
        row.update({"asset_link": "", "asset_link_level": "none", "asset_link_p": ""})

    # ---- a batch to the model

    def grade(self, rows, rnd, api_key, dry_run, workers):
        """Two passes through the answer store for the rows that pass the
        gate: the measure's question for all of them, then the asset and
        activity-kind questions only for those graded EXTRAS_MIN_GRADE or
        more. The questions are most of a request's tokens, so asking them
        of the 8 per cent that matter cuts a measure's cost by about four
        fifths. Returns (calls, cost, failures)."""
        import answers
        model = self.settings["model"]
        sent = []
        for row in rows:
            if self.gated(row, rnd):
                row["sent"] = "true"
                row["_state"] = self.state_of(row)
                sent.append(row)
        first = {k: v for k, v in self.qs.items() if ":" not in k}
        extras = {k: v for k, v in self.qs.items() if ":" in k}
        got, st = answers.ask(model, [(r["_state"], first) for r in sent], self.data,
                              api_key=api_key, log=self.log, workers=workers, dry_run=True)
        self.log(f"  round {rnd}: {len(rows):,} units, {len(sent):,} pass the gate; measure "
                 f"question: {st['to_ask']:,} to ask (about US${st['estimate_usd']:.3f})")
        if dry_run:
            return 0, 0.0, []
        calls, cost = 0, 0.0
        if st["to_ask"]:
            if not api_key:
                raise SystemExit("measure: no credential: set $WBG_OPENROUTER_KEY")
            got, st = answers.ask(model, [(r["_state"], first) for r in sent], self.data,
                                  api_key=api_key, log=self.log, workers=workers)
            calls, cost = st["calls"], st["cost_usd"]
            if st["failures"] or not all(got):
                return calls, cost, st["failures"] or [("", "an answer is missing")]
        for row, g in zip(sent, got):
            self.grade_into(row, g)
        need = [r for r in sent if int(r["grade"]) >= EXTRAS_MIN_GRADE] if extras else []
        if need:
            ctx = self.settings["extras_context"]
            states = [self.state_of(r, ctx) for r in need]
            if self.pool is not None:
                # The asset, kind and facet answers are not read by the next
                # round (the centroid uses grades alone), so they are asked
                # in the background while the next round's measure
                # questions go out; collect_extras() gathers them.
                self.pending.append((need, self.pool.submit(
                    answers.ask, model, [(s, extras) for s in states], self.data,
                    api_key=api_key, log=self.log, workers=workers)))
                return calls, cost, []
            got2, st2 = answers.ask(model, [(s, extras) for s in states], self.data,
                                    api_key=api_key, log=self.log, workers=workers)
            calls += st2["calls"]
            cost += st2["cost_usd"]
            self.log(f"    asset and kind questions for {len(need):,} units graded >= "
                     f"{EXTRAS_MIN_GRADE}: {st2['calls']:,} asked, US${st2['cost_usd']:.3f}")
            if st2["failures"] or not all(got2):
                return calls, cost, st2["failures"] or [("", "an answer is missing")]
            for row, g in zip(need, got2):
                self.extras_into(row, g)
        return calls, cost, []

    def collect_extras(self):
        """Wait for the background asset, kind and facet answers and write
        them into their rows. Returns (calls, cost, failures)."""
        calls, cost, failures = 0, 0.0, []
        for need, future in self.pending:
            got2, st2 = future.result()
            calls += st2["calls"]
            cost += st2["cost_usd"]
            if st2["failures"] or not all(got2):
                failures.extend(st2["failures"] or [("", "an answer is missing")])
                continue
            for row, g in zip(need, got2):
                self.extras_into(row, g)
        self.pending = []
        return calls, cost, failures

    # ---- relevance feedback

    def feedback_rows(self, graded, rnd):
        """The next batch: the unseen units nearest the Rocchio query, in
        index order on ties. The query is the centroid of the committed
        units' vectors minus NEGATIVE_WEIGHT times the centroid of the units
        graded 0: the first run showed the raw centroid pulls in the
        measure's neighbourhood ("resilient infrastructure" in general), and
        the rejected units are exactly what that neighbourhood looks like."""
        top = len(self.scale) - 1
        pos = [self.row_of[r["unit_id"]] for r in graded
               if r["sent"] == "true" and int(r["grade"]) == top]
        neg = [self.row_of[r["unit_id"]] for r in graded
               if r["sent"] == "true" and int(r["grade"]) == 0]
        if not pos:
            return []
        centroid = numpy.asarray(self.matrix[sorted(pos)], dtype="float32").mean(axis=0)
        if neg and NEGATIVE_WEIGHT:
            centroid -= NEGATIVE_WEIGHT * numpy.asarray(self.matrix[sorted(neg)],
                                                        dtype="float32").mean(axis=0)
        norm = numpy.linalg.norm(centroid)
        if not norm:
            return []
        centroid /= norm
        n = self.matrix.shape[0]
        sims = numpy.empty(n, dtype="float32")
        for s in range(0, n, search.BLOCK):
            block = numpy.asarray(self.matrix[s:s + search.BLOCK], dtype="float32")
            sims[s:s + block.shape[0]] = block @ centroid
        seen = {r["unit_id"] for r in graded}
        order = numpy.lexsort((numpy.arange(n), -sims))
        rows = []
        for i in order.tolist():
            uid = self.index[i]["unit_id"]
            if uid in seen or not classify.in_scope(self.index[i], self.projects):
                continue
            rows.append(self.base_row(self.index[i], "feedback", rnd, len(rows) + 1,
                                      f"{float(sims[i]):.6f}"))
            if len(rows) >= self.settings["batch"]:
                break
        return rows


def report_lines(run, rows, rounds, calls, cost, seconds, served):
    s, scale = run.settings, run.scale
    top = len(scale) - 1
    sent = [r for r in rows if r["sent"] == "true"]
    com = [r for r in sent if int(r["grade"]) == top]
    lines = [f"{run.name}: {len(rows):,} units seen, {len(sent):,} graded by {s['model']} "
             f"(context {s['context']}, gate {s['gate']}"
             + (f" >= {s['asset_min']}" if s["gate"] != "none" else "")
             + f", pool: cosine >= {s['min_cosine']}" + (f" or top {s['top']}" if s["top"] else "")
             + f", batch {s['batch']}, min yield {s['min_yield']})",
             f"  search run {os.path.basename(run.search_run)}; {len(run.patterns)} pattern(s); "
             f"asset map {run.asset_run or 'none'}",
             f"  this run: {calls:,} calls, US${cost:.4f}, {seconds:.0f}s; models served: {served}",
             "  rounds (units graded / committed): " + ", ".join(
                 f"{k}: {v['graded']}/{v['committed']}" for k, v in rounds.items()),
             f"\n  graded units by most likely grade {[sum(1 for r in sent if int(r['grade']) == g) for g in range(len(scale))]}; "
             f"grade {top} (committed) in {len(com):,} units, "
             f"{len({p for r in com for p in r['project_ids'].split('|')})} projects; "
             f"prevalence among graded {len(com) / max(len(sent), 1):.1%}"]
    by_route = defaultdict(lambda: [0, 0])
    for r in sent:
        by_route[r["route"]][0] += 1
        by_route[r["route"]][1] += int(r["grade"]) == top
    lines.append("  by route (graded / committed): " + ", ".join(
        f"{k} {v[0]:,}/{v[1]:,}" for k, v in sorted(by_route.items())))
    lines.append("  committed units by the unit-level asset answer (best asset at >= " +
                 ", ".join(f"{t}: {sum(1 for r in com if r['asset_p_unit'] and float(r['asset_p_unit']) >= t)}"
                           for t in REPORT_THRESHOLDS) + f") of {len(com)}; no asset at 0.5: "
                 f"{sum(1 for r in com if not r['assets_yes'])}")
    if run.asset_run:
        lines.append("  committed units kept by a gate on the paragraph / the component map at " +
                     ", ".join(f"{t}: {sum(1 for r in com if r['asset_p_para'] and float(r['asset_p_para']) >= t)}"
                               f"/{sum(1 for r in com if r['asset_p_comp'] and float(r['asset_p_comp']) >= t)}"
                               for t in REPORT_THRESHOLDS)
                     + f"; committed units with no paragraph score: "
                       f"{sum(1 for r in com if not r['asset_p_para'])}")
    levels = defaultdict(int)
    for r in com:
        levels[r["asset_link_level"]] += 1
    lines.append("  committed units by asset link level (unit answer, else paragraph map, else "
                 f"component map, at {LINK_MIN}): " + ", ".join(
                     f"{k} {v}" for k, v in sorted(levels.items(), key=lambda x: -x[1])))
    linked = defaultdict(int)
    for r in com:
        if r["asset_link"]:
            linked[r["asset_link"]] += 1
    lines.append("  committed units by linked asset: " + ", ".join(
        f"{a} {n}" for a, n in sorted(linked.items(), key=lambda x: -x[1])))
    kinds = defaultdict(int)
    for r in com:
        for k in (r["kinds_yes"].split("|") if r["kinds_yes"] else ["none"]):
            kinds[k] += 1
    lines.append("  committed units by activity kind (yes at 0.5, several allowed): " + ", ".join(
        f"{k} {n}" for k, n in sorted(kinds.items(), key=lambda x: -x[1])))
    actors = defaultdict(int)
    for r in com:
        for k in (r["actors_yes"].split("|") if r["actors_yes"] else ["none"]):
            actors[k] += 1
    lines.append("  committed units by who carries it out (yes at 0.5, several allowed): "
                 + ", ".join(f"{k} {n}" for k, n in sorted(actors.items(), key=lambda x: -x[1]))
                 + f"; conditional at 0.5: "
                   f"{sum(1 for r in com if r['conditional_p'] and float(r['conditional_p']) >= ASSET_YES)}")
    assets = defaultdict(int)
    for r in com:
        for a in r["assets_yes"].split("|"):
            if a:
                assets[a] += 1
    lines.append("  committed units by asset (unit answer >= 0.5, several allowed): " + ", ".join(
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
    from concurrent.futures import ThreadPoolExecutor
    run.pool = ThreadPoolExecutor(max_workers=2)
    log(f"{name}: {len(run.labels)} label(s) x {len(run.scale)} grades + {len(run.assets)} asset "
        f"questions per unit; context {run.settings['context']}, gate {run.settings['gate']} "
        f"-> {run.out}")
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
            run.pool.shutdown(wait=True)
            c, k, _ = run.collect_extras()
            # Paid calls are paid whether or not the run completes: the
            # ledger must match the provider's bill.
            classify.record_spend(data, "measure", name, run.settings["model"], calls + c,
                                  cost + k, time.time() - t0, "failed")
            log(f"  {len(failures)} request(s) failed; nothing written. Run again to resume.")
            for sha, err in failures[:5]:
                log(f"    {sha[:12]}: {err}")
            return None
        if dry_run:
            run.pool.shutdown(wait=True)
            log("  --dry-run: round 0 only; no request made, nothing written")
            return None
        this = rows if rnd == 0 else batch
        got = sum(1 for r in this if r["sent"] == "true" and int(r["grade"]) == top)
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
    c, k, failures = run.collect_extras()
    run.pool.shutdown(wait=True)
    calls += c
    cost += k
    if failures:
        classify.record_spend(data, "measure", name, run.settings["model"], calls, cost,
                              time.time() - t0, "failed")
        log(f"  {len(failures)} request(s) failed; nothing written. Run again to resume.")
        for sha, err in failures[:5]:
            log(f"    {sha[:12]}: {err}")
        return None
    seconds = time.time() - t0
    classify.record_spend(data, "measure", name, run.settings["model"], calls, cost, seconds,
                          os.path.basename(run.out))
    served = defaultdict(int)
    for r in rows:
        if r["sent"] == "true":
            served[r["model_served"]] += 1
        else:
            r.update({"grade": "", "expected": "", "confidence": "", "model_served": "",
                      "cost": "", **{f"p{i}": "" for i in range(5)}})
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
    ap.add_argument("--context", default="sentence", choices=CONTEXTS)
    ap.add_argument("--gate", default="none", choices=GATES)
    ap.add_argument("--asset-min", type=float, default=0.0,
                    help="with --gate: the asset probability a unit, paragraph or component "
                         "needs for the unit to be sent")
    ap.add_argument("--top", type=int, default=TOP, help="round 0 pool by count (0 = off)")
    ap.add_argument("--min-cosine", type=float, default=MIN_COSINE,
                    help="round 0 pool: units whose best cosine reaches this (0 = off)")
    ap.add_argument("--batch", type=int, default=BATCH, help="units per feedback round")
    ap.add_argument("--min-yield", type=int, default=MIN_YIELD,
                    help="a round yielding fewer committed units than this is the last")
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
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
                  context=args.context,
                  gate=args.gate, asset_min=args.asset_min, top=args.top,
                  min_cosine=args.min_cosine, batch=args.batch,
                  min_yield=args.min_yield, max_rounds=args.max_rounds,
                  projects=classify.scope(args.projects))
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
