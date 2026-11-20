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

from wbg_portfolio import labels
from wbg_portfolio import panel
from wbg_portfolio.paths import where

from wbg_portfolio.paths import REPO as ROOT
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


CONTEXT_NOTE = ("Only the sentence in the 'sentence' field is judged; the 'context' field is the "
                "paragraph around it, for reference. ")


def in_context(question):
    """The same question for a state that carries a 'context' field (the
    paragraph): it says that only the sentence is judged. A different question,
    so a different hash and its own stored answers."""
    return dict(question, instructions=CONTEXT_NOTE + question["instructions"])


def flag_question(flag_id):
    """The fit question of a flag of inputs/taxonomy/flags.csv (digital): does
    the flag apply to the sentence, asked with the paragraph beside it."""
    f = labels.flag(flag_id)
    return in_context(fit_question({"id": f["id"], "name": f["name"], "definition": f["definition"]}))


# ---------------------------------------------- the chat model's questions (second looks, activities)

CHAT_FORMS = ("grade", "yesno", "choice", "labels", "assets", "activities")


def chat_question(instructions, form, options=None, maximum=None):
    """A chat question: what to judge, and how to reply (src/wbg_portfolio/chat.py asks it and
    reads the reply)."""
    if form not in CHAT_FORMS:
        raise ValueError(f"chat: unknown reply form {form!r}")
    reply = {"form": form}
    if options is not None:
        reply["options"] = list(options)
    if maximum is not None:
        reply["max"] = int(maximum)
    return {"type": "chat", "instructions": instructions, "reply": reply}


def second_grade_question(lab, scale):
    """The 0-n grade of a measure on the shared scale (the measure's second look)."""
    grades = "\n".join(f"{i}: {t.replace('{name}', lab['name'])}" for i, t in enumerate(scale))
    return chat_question(
        f"How does {SENTENCE}, relate to {lab['name']}? {lab['name']} means: {lab['definition']}\nGrades:\n{grades}",
        "grade", maximum=len(scale) - 1)


def policy_question():
    """Investment or policy instrument, for a committed sentence (flag policy)."""
    f = labels.flag("policy")
    return in_context(chat_question(
        f"Does the sentence commit the project to an investment or to a policy instrument? "
        f"{f['name'][:1].upper() + f['name'][1:]} means: {f['definition']}", "choice", ["investment", "policy"]))


def asset_catalogue():
    """The four assets with their finer kinds, as the chat questions show them:
    ([asset {id, name, definition}], {asset_id: [subtype {id, name, definition}]})."""
    assets, _ = load(ASSETS)
    return assets, labels.load_subtypes(ASSETS)


def asset_second_question(assets, subtypes):
    """Up to two of the four assets, or none, each with one of its kinds."""
    listing = []
    for a in assets:
        listing.append(f"- {a['id']}: {a['name']}. {a['definition']}")
        listing.extend(f"    kind {s['id']}: {s['name']}. {s['definition']}" for s in subtypes.get(a["id"], []))
    return in_context(chat_question(
        "Which digital asset or assets, if any, does the sentence in the 'sentence' field commit the project to "
        "financing, building, buying or running? Choose none when the sentence concerns no digital asset, or "
        "only digital policy, strategy, training or equipment with no system or network named; otherwise choose at most two assets from the list, "
        "and for each the one kind of it that fits best. "
        "Assets, with their kinds:\n" + "\n".join(listing),
        "assets", [a["id"] for a in assets], 2))


def asset_subtype_question(asset, kinds):
    """Which kind of an asset a sentence already counted for it concerns."""
    return in_context(chat_question(
        f"{SENTENCE[:1].upper() + SENTENCE[1:]} is already counted as concerning {asset['name']}; this asks only which kind "
        f"of it. {asset['name']} means: {asset['definition']}\nKinds:\n" +
        "\n".join(f"- {s['id']}: {s['name']}. {s['definition']}" for s in kinds),
        "choice", [s["id"] for s in kinds]))


def package_asset_second_question(asset):
    """Does the package procure the asset, as the chat model's yes or no."""
    return chat_question(
        f"Does {PACKAGE} procure {asset['name']}, or equipment, works, "
        f"systems or services for it, named or clearly implied in the package's own description? A package for "
        f"something else (vehicles, audits, general works, unrelated consultancy) does not; when the description does not "
        f"make it clear, answer no. {asset['name']} means: {asset['definition']}", "yesno")


def package_measure_question(lab):
    """Does the measure apply to the package alone: it applies when what the
    package procures is the measure, named or clearly implied in the package's
    own description. Asked of the decision model as a fit question (yes,
    unsure, no)."""
    return fit_question(lab, PACKAGE, given=(
        f"It applies when what the package procures is {lab['name']}, named or clearly implied in the "
        f"package's own description. "))


def activity_question(lab):
    """Group the numbered sentences of one project and measure into distinct activities."""
    return chat_question(
        f"Each numbered sentence in the 'sentences' field comes from the World Bank documents of one project and "
        f"commits the project to {lab['name']} ({lab['definition']}). Group the sentences into the distinct "
        f"activities they describe: one activity is one investment, study or action, even when several "
        f"sentences or documents say it again or describe it in different words; two different things are two "
        f"activities. Every sentence belongs to exactly one activity. Give each activity a short label.",
        "activities")


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
        from wbg_portfolio import units
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

    def context_state(self, unit_id):
        """state() with the paragraph in a field named 'context', cut to
        second.context_chars, for the questions that read the sentence with
        its paragraph beside it (in_context). state() is not changed: a
        first-pass answer already stored stays what it was."""
        state = self.state(unit_id)
        state.pop("paragraph", None)
        text = self.paragraph_text(self.row(unit_id)["paragraph_id"])
        state["context"] = text[:int(panel.require("second.context_chars"))]
        return state

    def chat_state(self, unit_id):
        """state() without any paragraph: what the chat model's second look at a
        measure is shown (section, the sentence with its lead-in, component)."""
        state = self.state(unit_id)
        state.pop("paragraph", None)
        return state

    def vector(self, unit_id):
        i = self.row_of.get(unit_id)
        return None if i is None or self.matrix is None else numpy.asarray(self.matrix[i], dtype="float64")

    def in_scope(self, unit_id, projects):
        return not projects or bool(set(self.projects(unit_id)) & set(projects))
