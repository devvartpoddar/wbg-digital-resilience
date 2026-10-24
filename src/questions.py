"""What is sent to the decision model, for every decision alike: the label
sets, the two question forms, the one score, and the corpus a sentence's
state is built from.

Two forms of question (docs/method/decisions.md). A classification (asset,
measure) asks graded_question on the set's 0-3 scale, scored as the
probability of the grades the panel counts (read.grades). A check made after
it (type, pair check, package to asset, commitment to package) asks
fit_question, yes / unsure / no, told what is already decided, scored as
P(yes). Every sentence-level question sends the same state (sentence_state):
the sentence with its list lead-in, the section title, the component, and
with ask.context = paragraph the paragraph too.
"""
import csv
import hashlib
import json
import os

import numpy

import labels
import panel
from paths import where

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = "assets"
MODEL = "typesafe/jev-1.13-20260917"
ALIASES = ("typesafe/jev-1.13",)
PRICE_PER_MTOK = 0.042          # US$ per million input tokens, for estimates only
SENTENCE = "the sentence from a World Bank project appraisal document in the 'sentence' field"
PACKAGE = "the procurement package in the 'package' field"
FIT_SCALE = ["No: {name} does not apply.",
             "Unsure: the text does not say enough to tell whether {name} applies.",
             "Yes: {name} clearly applies."]


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ------------------------------------------------------------------ label sets

def load(name, folder=None):
    """(active labels [{id, name, definition}], scale [grade 0 text, ...]) of
    a set. The scale is <set>_scale.csv beside it, else the shared
    analysis/labels/scale.csv."""
    folder = folder or labels.folder(name)
    active = [{"id": r["id"], "name": r["name"], "definition": r["definition"]}
              for r in labels.load_set(name, taxonomy=folder) if r["active"] == "true"]
    if not active:
        raise SystemExit(f"{name}.csv has no active label")
    path = os.path.join(folder, f"{name}_scale.csv")
    if not os.path.exists(path):
        path = os.path.join(ROOT, "analysis", "labels", "scale.csv")
    if not os.path.exists(path):
        raise SystemExit(f"{name}: no grade scale ({path} is missing)")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = sorted(csv.DictReader(fh), key=lambda r: int(r["grade"]))
    scale = [" ".join(r["description"].split()) for r in rows]
    if not 2 <= len(scale) <= 5:
        raise SystemExit(f"{path} needs 2 to 5 grades")
    return active, scale


def boundary(name):
    """(label name, boundary text) of a measure: its boundary column, else its
    definition."""
    for r in labels.rows(os.path.join(labels.folder(name), f"{name}.csv")):
        if labels.is_active(r):
            nm = next(r[c] for c in r if c.endswith("_name")).strip()
            return nm, " ".join((r.get("boundary") or r["definition"]).split())
    raise SystemExit(f"{name} has no active label")


# ------------------------------------------------------------------ questions

def graded_question(lab, scale, subject=SENTENCE):
    return {"type": "score",
            "instructions": f"How does {subject}, relate to {lab['name']}? {lab['name']} "
                            f"means: {lab['definition']}",
            "criteria": [g.replace("{name}", lab["name"]) for g in scale]}


def fit_question(lab, subject=SENTENCE, given=""):
    return {"type": "score",
            "instructions": f"Does {lab['name']} apply to {subject}? {given}"
                            f"{lab['name']} means: {lab['definition']}",
            "criteria": [g.replace("{name}", lab["name"]) for g in FIT_SCALE]}


def graded_questions(name):
    """{label_id: graded question} of a set, and its scale."""
    labs, scale = load(name)
    return {lab["id"]: graded_question(lab, scale) for lab in labs}, scale


def type_questions(name):
    """{label_id: {type_id: fit question}} for the measure's approved types,
    told the sentence is already a commitment to the measure."""
    labs, _ = load(name)
    out = {}
    for lab in labs:
        given = (f"The sentence is already counted as a commitment to {lab['name']}; this asks "
                 f"only which kind it is. ")
        out[lab["id"]] = {t["type_id"]: fit_question(
            {"id": t["type_id"], "name": t["type_name"], "definition": t["definition"]},
            given=given) for t in labels.load_types(name) if t["label_id"] == lab["id"]}
    return out


def pair_questions(a, b):
    """{'keep_a', 'keep_b'}: does each measure apply, the other's boundary beside it."""
    out = {}
    for key, s, o in (("keep_a", a, b), ("keep_b", b, a)):
        lab = load(s)[0][0]
        on, ob = boundary(o)
        out[key] = fit_question(lab, given=(
            f"The sentence is counted for both {lab['name']} and {on} ({ob}); this asks only "
            f"whether it commits the project to {lab['name']}, whatever it does for {on}. "))
    return out


def package_asset_questions():
    """{asset_id: fit question}: does the package buy, build, install, connect,
    operate or design the asset."""
    return {a["id"]: fit_question(a, PACKAGE, given=(
        f"It applies when what the package procures is {a['name']}, or equipment, works, "
        f"systems or services for it, named or clearly implied. ")) for a in load(ASSETS)[0]}


LINK_QUESTION = fit_question(
    {"id": "pair", "name": "carrying out the commitment in the 'commitment' field",
     "definition": ("the package procures, builds, installs or contracts the asset, works, "
                    "equipment, system or service the commitment concerns, or the study or "
                    "service the commitment itself requires, so the commitment is delivered or "
                    "applied through what the package delivers. Both come from the same World "
                    "Bank project: the commitment from its appraisal document, the package from "
                    "its procurement plan. Belonging to the same project is not enough.")},
    PACKAGE)


def score(answer, fit=False):
    """The one score of an answer, off the raw probabilities (never off
    rounded columns): P(yes) for a fit question, else the probability of the
    grades the panel counts (read.grades), each out of the total."""
    probs = {int(k): float(v) for k, v in answer["probabilities"].items()}
    total = sum(probs.values()) or 1.0
    if fit:
        return probs.get(len(FIT_SCALE) - 1, 0.0) / total
    return sum(probs.get(g, 0.0) for g in panel.grades()) / total


# ------------------------------------------------------------------- states

def package_state(p):
    state = {"package": p["text"]}
    if p.get("component"):
        state["component"] = p["component"]
    return state


def pair_state(unit_text, unit_component, p):
    state = {"commitment": unit_text, "package": p["text"]}
    if unit_component:
        state["commitment_component"] = unit_component
    if p.get("component"):
        state["package_component"] = p["component"]
    return state


class Corpus:
    """The embedded sentences, read once per process: the index, the texts
    as embedded (lead-in included), the paragraphs, the component names and
    the vectors. state(unit_id) is the one state every sentence-level
    question sends (ask.context: sentence | paragraph)."""

    def __init__(self, data, vectors=True):
        import units
        self.data = data
        self.context = str(panel.get("ask.context", "sentence"))
        with open(where(data, "embeddings", "sentence_index.csv"), newline="", encoding="utf-8") as fh:
            self.index = list(csv.DictReader(fh))
        self.row_of = {r["unit_id"]: i for i, r in enumerate(self.index)}
        with open(where(data, "appraisal", "paragraphs.csv"), newline="", encoding="utf-8") as fh:
            self.paras = {r["paragraph_id"]: r for r in csv.DictReader(fh)}
        self.names = {}
        path = where(data, "appraisal", "components.csv")
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as fh:
                for r in sorted(csv.DictReader(fh), key=lambda r: r["source"] == "heading"):
                    self.names.setdefault((r["doc_id"], r["number"]), r["name"])
        every = {u["unit_id"]: u for u in units.search_units(data)}
        self._docs, self.texts = {}, {}
        for r in self.index:
            u = every.get(r["unit_id"])
            if u is None:
                continue
            t = self._doc(u["doc_id"])
            body = t[int(u["char_start"]):int(u["char_end"])]
            if u.get("lead_in_start"):
                body = t[int(u["lead_in_start"]):int(u["lead_in_end"])] + "\n" + body
            self.texts[r["unit_id"]] = body
        self.matrix = None
        npy = where(data, "embeddings", "sentence_emb.npy")
        if vectors and os.path.exists(npy):
            self.matrix = numpy.load(npy, mmap_mode="r")

    def _doc(self, doc_id):
        if doc_id not in self._docs:
            with open(where(self.data, "text", f"{doc_id}.txt"), encoding="utf-8") as fh:
                self._docs[doc_id] = fh.read()
        return self._docs[doc_id]

    def row(self, unit_id):
        return self.index[self.row_of[unit_id]]

    def projects(self, unit_id):
        return [p for p in self.row(unit_id).get("project_ids", "").split("|") if p]

    def component(self, unit_id):
        r = self.row(unit_id)
        sub = r.get("subcomponent_number") or ""
        comp = sub or r.get("component_number") or ""
        if not comp:
            return ""
        para = self.paras.get(r["paragraph_id"], {})
        name = self.names.get((r["doc_id"], comp)) or \
            (para.get("subcomponent_name") if sub else para.get("component_name")) or ""
        return f"{comp} {name}".strip()

    def paragraph_text(self, paragraph_id):
        p = self.paras.get(paragraph_id)
        if not p:
            return ""
        return self._doc(p["doc_id"])[int(p["char_start"]):int(p["char_end"])]

    def state(self, unit_id):
        r = self.row(unit_id)
        para = self.paras.get(r["paragraph_id"], {})
        state = {"section": para.get("section_title", ""), "sentence": self.texts[unit_id]}
        comp = self.component(unit_id)
        if comp:
            state["component"] = comp
        if self.context == "paragraph":
            state["paragraph"] = self.paragraph_text(r["paragraph_id"])
        return state

    def vector(self, unit_id):
        i = self.row_of.get(unit_id)
        return None if i is None or self.matrix is None else numpy.asarray(self.matrix[i], dtype="float64")

    def in_scope(self, unit_id, projects):
        return not projects or bool(set(self.projects(unit_id)) & set(projects))
