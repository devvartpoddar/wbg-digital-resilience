"""What is sent to the decision model, for every decision alike: the label
sets, the two question forms, the one score, and the corpus a sentence's
state is built from.

Two forms of question (docs/method/decisions.md). A classification (asset,
measure) asks graded_question on the set's 0-3 scale, scored as the
probability of the grades the panel counts (read.grades). A check made after
it (type, package to asset, package to measure) asks
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
SENTENCE_FIELD = "the sentence in the 'sentence' field"     # the digital and asset questions' plain subject
# Said once, at the end of every question about an asset (first pass, second look, package).
ASSET_RULE = "Equipment, training or a policy counts for the asset it is for."
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
    return active, read_scale(path)


def read_scale(path):
    """[grade 0 text, ...] of a scale file (grade, description)."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = sorted(csv.DictReader(fh), key=lambda r: int(r["grade"]))
    scale = [" ".join(r["description"].split()) for r in rows]
    if not 2 <= len(scale) <= 5:
        raise SystemExit(f"{path} needs 2 to 5 grades")
    return scale


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


CONTEXT_NOTE = ("The 'context' field is the paragraph around the sentence. It is there only to show what "
                "the sentence refers to. ")
# The policy question keeps the note it was first asked with, so its stored answers stand
# (it was not part of the 2026-11-21 rewrite of the questions).
POLICY_NOTE = ("Only the sentence in the 'sentence' field is judged; the 'context' field is the "
               "paragraph around it, for reference. ")


def in_context(question, note=CONTEXT_NOTE):
    """The same question for a state that carries a 'context' field (the
    paragraph): a note says what the paragraph is for. A different question,
    so a different hash and its own stored answers."""
    return dict(question, instructions=note + question["instructions"])


# ------------------------------------------------- the digital and asset questions
#
# Graded on the taxonomy's one scale (inputs/taxonomy/assets_scale.csv: 0 not at all, 1 in passing,
# 2 partly or as the means, 3 what the sentence is about), scored as the probability of the grades
# the panel counts (read.grades), the same way as a measure. A package question reads the same
# scale with "package" where it says "sentence".

def taxonomy_scale():
    """The scale of the asset set, shared by the digital question."""
    return load(ASSETS)[1]


def package_scale(scale):
    return [g.replace("sentence", "package") for g in scale]


def asset_question(lab, scale, package=False):
    """How far a sentence is about an asset (the first pass), or how far a package is for it."""
    ask = f"How far is {PACKAGE} for" if package else f"How far is {SENTENCE_FIELD} about"
    return {"type": "score",
            "instructions": f"{ask} {lab['name']}? {lab['name']} are: {lab['definition']} {ASSET_RULE}",
            "criteria": [g.replace("{name}", lab["name"]) for g in (package_scale(scale) if package else scale)]}


def digital_question():
    """How far a sentence is about a digital technology (the digital flag of inputs/taxonomy/flags.csv),
    asked with the paragraph beside it."""
    f = labels.flag("digital")
    return in_context({"type": "score",
                       "instructions": f"How far is {SENTENCE_FIELD} about {f['name']}? "
                                       f"{f['name'][:1].upper() + f['name'][1:]} is: {f['definition']}",
                       "criteria": taxonomy_scale()})


# ---------------------------------------------- the chat model's questions (second looks)

CHAT_FORMS = ("grade", "yesno", "choice", "labels", "fields")


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
        f"{f['name'][:1].upper() + f['name'][1:]} means: {f['definition']}", "choice", ["investment", "policy"]),
        POLICY_NOTE)


def asset_catalogue():
    """The four assets with their types, as the chat questions show them:
    ([asset {id, name, definition}], {asset_id: [subtype {id, name, definition}]})."""
    assets, _ = load(ASSETS)
    return assets, labels.load_subtypes(ASSETS)


def asset_second_question(assets):
    """Which asset (one, a second only when the sentence names two, or none); the type is asked
    after, by asset_subtype_question. The list is in the order of the ids, not of the file, so
    reordering rows of assets.csv does not change the question."""
    assets = sorted(assets, key=lambda a: a["id"])
    return in_context(chat_question(
        f"Which digital asset is {SENTENCE_FIELD} about? Pick one, or none. Pick a second only if the "
        f"sentence itself names two.\n" + "\n".join(f"- {a['id']}: {a['name']}: {a['definition']}" for a in assets)
        + f"\n{ASSET_RULE}",
        "labels", [a["id"] for a in assets], 2))


UNSPECIFIED = "unspecified"


def asset_subtype_question(asset, types, package=False):
    """Which type of an asset a sentence (with its paragraph) or a package is about: the types by
    name, in the order of their ids, then unspecified (not stated)."""
    types = sorted((t for t in types if t["id"] != UNSPECIFIED), key=lambda t: t["id"])
    subject = PACKAGE if package else SENTENCE_FIELD
    q = chat_question(
        f"Which type of {asset['name']} is {subject} about?\n"
        + "".join(f"- {t['id']}: {t['name']}\n" for t in types) + f"- {UNSPECIFIED}: not stated",
        "choice", [t["id"] for t in types] + [UNSPECIFIED])
    return q if package else in_context(q)


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


# ------------------------------------- the evaluation's graders (docs/method/evaluation.md)
#
# The wording of the trial ruler the earlier model-graded trials used, so a grade here reads like
# theirs: a system line, the item laid out in the prompt, the 0-3 scale, a short JSON reply. The
# measure grade is asked alone, exactly as the ruler asked it; the digital and general-aim questions
# go in a second request. Each question is a template whose {places} the item's fields fill
# (chat.prompt); text from the label files is escaped so its braces are never read as places.

GRADER_SYSTEM = ("You are a careful, strict annotator for World Bank project documents. Output ONLY the JSON "
                 "object, with no text before or after it.")
VERDICT = ["yes", "unsure", "no"]
_SENTENCE_PART = ("\nDocument section: {section}\nProject component: {component}\n{lead_in_line}"
                  "\nParagraph (the sentence to judge is marked >>> <<<):\n{paragraph}\n")


def _esc(text):
    return str(text).replace("{", "{{").replace("}", "}}")


def grader_question(template, fields):
    """A grader's question: the template, the system line and the named fields of the reply
    (chat.parse, form "fields")."""
    return {"type": "chat", "system": GRADER_SYSTEM, "template": template,
            "reply": {"form": "fields", "fields": [list(f) for f in fields]}}


def grader_measure_question(lab, scale):
    """The ruler's measure question, alone in its request: the 0-3 grade on the shared scale, the
    sentence marked in its paragraph, worded and replied to exactly as the trial ruler asked it."""
    name = _esc(lab["name"])
    sc = "\n".join(f"{i}: {_esc(g.replace('{name}', lab['name']))}" for i, g in enumerate(scale))
    t = f"Measure: {name}\nDefinition: {_esc(lab['definition'])}\n" + _SENTENCE_PART
    t += (f"\nHow does the marked sentence (with its lead-in) relate to the measure? Judge that sentence only; "
          f"the paragraph is context.\nScale:\n{sc}\n\n")
    t += 'Reply with JSON only: {{"grade": 0-3}}'
    return grader_question(t, [("grade", {"max": len(scale) - 1})])


def grader_measure_extra_question():
    """The two further questions on a measure item, in a request of their own so the grade is asked as the
    trial asked it: how far the marked sentence is about a digital technology (the pipeline's digital
    definition and four-grade scale) and whether it is only a general aim."""
    dig, scale = labels.flag("digital"), taxonomy_scale()
    sc = "\n".join(f"{i}: {_esc(g)}" for i, g in enumerate(scale))
    name = dig["name"][:1].upper() + dig["name"][1:]
    t = "A sentence from a World Bank project appraisal document, in its paragraph.\n" + _SENTENCE_PART
    t += (f"\nTwo questions about the marked sentence (with its lead-in). Judge that sentence only; the paragraph "
          f"is context.\n- digital: How far is the marked sentence about {_esc(dig['name'])}? {_esc(name)} is: "
          f"{_esc(dig['definition'])}\nScale:\n{sc}\n"
          f"- general_aim: Is the marked sentence only a general aim, with no specific thing to be done?\n\n")
    t += 'Reply with JSON only: {{"digital": 0-3, "general_aim": "yes" | "no"}}'
    return grader_question(t, [("digital", {"max": len(scale) - 1}), ("general_aim", {"options": ["yes", "no"]})])


def grader_types_question(lab, types):
    """Which of the measure's types the marked sentence describes (any, or none)."""
    types = sorted(types, key=lambda t: t["type_id"])
    t = f"Measure: {_esc(lab['name'])}\nDefinition: {_esc(lab['definition'])}\n" + _SENTENCE_PART
    t += ("\nTypes of the measure:\n" + "\n".join(
        f"- {_esc(x['type_id'])}: {_esc(x['type_name'])}. {_esc(x['definition'])}" for x in types))
    t += ("\n\nQuestion: which of these types does the marked sentence (with its lead-in) describe? Judge that "
          "sentence only; the paragraph is context. Choose every type that applies, or none.\n"
          'Reply with JSON only: {{"types": [<type ids>]}}')
    return grader_question(t, [("types", {"options": [x["type_id"] for x in types], "many": True})])


def grader_asset_question(assets, subtypes):
    """Which digital asset the marked sentence is about (one, a second only when the sentence names two, or
    none), each with its type: the pipeline's assets, types and shared rule."""
    assets = sorted(assets, key=lambda a: a["id"])
    listing = []
    for a in assets:
        listing.append(f"- {_esc(a['id'])}: {_esc(a['name'])}: {_esc(a['definition'])}")
        listing.extend(f"    type {_esc(s['id'])}: {_esc(s['name'])}"
                       for s in sorted(subtypes.get(a["id"], []), key=lambda s: s["id"]))
    types = sorted({s["id"] for v in subtypes.values() for s in v} | {UNSPECIFIED})
    t = "A sentence from a World Bank project appraisal document, in its paragraph.\n" + _SENTENCE_PART
    t += "\nDigital assets, each with its types:\n" + "\n".join(listing) + f"\n{_esc(ASSET_RULE)}"
    t += ("\n\nQuestion: which digital asset is the marked sentence (with its lead-in) about? Judge that sentence "
          "only; the paragraph is context. Pick one, or none. Pick a second only if the sentence itself names two. "
          "For each, give the type of that asset; unspecified when the type is not stated.\n"
          'Reply with JSON only: {{"asset": "<asset id>" | "none", "type": "<type id of that asset>" | "none", '
          '"second_asset": "<asset id>" | "none", "second_type": "<type id of that asset>" | "none"}}')
    ids = [a["id"] for a in assets] + ["none"]
    return grader_question(t, [("asset", {"options": ids}), ("type", {"options": types + ["none"]}),
                               ("second_asset", {"options": ids}), ("second_type", {"options": types + ["none"]})])


def grader_package_measure_question(lab):
    """The ruler's package question, asked of a measure: does the package procure it."""
    name = _esc(lab["name"])
    t = ("A procurement package from a World Bank project procurement plan:\nDescription: {package}\n"
         "Plan component: {package_component}\nCategory: {category}\n\n"
         f"Measure: {name}\nDefinition: {_esc(lab['definition'])}\n\n"
         f"Question: does the package procure {name}, or equipment, works, systems or services for it, named or "
         "clearly implied? yes = clearly applies; unsure = the text does not say enough; no = does not apply.\n"
         'Reply with JSON only: {{"verdict": "yes" | "unsure" | "no"}}')
    return grader_question(t, [("verdict", {"options": VERDICT})])


def grader_link_question(lab):
    """The ruler's link question: does carrying out this package deliver the commitment."""
    t = ("A commitment sentence from a World Bank project appraisal document, and a procurement package from the "
         "same project's procurement plan.\n\n"
         f"Commitment measure: {_esc(lab['name'])}\nMeasure definition: {_esc(lab['definition'])}\n"
         "Appraisal component: {sentence_component}\nLead-in: {lead_in}\nSentence: {sentence}\n\n"
         "Procurement package: {package}\nPlan component: {package_component}\n"
         "Category: {category}; method: {method}; estimated amount USD: {amount}\n\n"
         "Question: does carrying out this package deliver the commitment? Answer yes when the package procures, "
         "builds, installs or contracts the asset, works, equipment, system or service the commitment concerns, or "
         "the study or service the commitment itself requires, so the commitment is delivered or applied through "
         "what the package delivers. Belonging to the same project is not enough. Answer unsure when the text does "
         "not say enough to tell, no when it does not apply.\n"
         'Reply with JSON only: {{"verdict": "yes" | "unsure" | "no"}}')
    return grader_question(t, [("verdict", {"options": VERDICT})])


def graded_questions(name):
    """{label_id: graded question} of a set, and its scale (the assets: asset_question)."""
    labs, scale = load(name)
    build = asset_question if name == ASSETS else graded_question
    return {lab["id"]: build(lab, scale) for lab in labs}, scale


OTH = "OTH."            # a proposed type's id and stored name start with this


def type_question(measure, t):
    """The one type question: does a type of the measure apply to a sentence already counted as a
    commitment to it (fit: yes, unsure, no). A proposed type's name is sent without its "OTH." prefix
    (the id keeps it, so the reply still maps to the type)."""
    name = t["type_name"]
    if name.startswith(OTH):
        name = name[len(OTH):].strip()
    return fit_question({"id": t["type_id"], "name": name, "definition": t["definition"]},
                        given=f"The sentence is already counted as a commitment to {measure['name']}; this asks "
                              f"only which type it is. ")


def type_questions(name):
    """{label_id: {type_id: type question}} for the measure's types (a person's list, else the
    proposed list the types step wrote)."""
    labs, _ = load(name)
    return {lab["id"]: {t["type_id"]: type_question(lab, t) for t in labels.load_types(name)
                        if t["label_id"] == lab["id"]} for lab in labs}


def oth_types(name, data=None):
    """The OTH types the gap step proposed for a measure (data/types/<set>_oth_types.csv), [] when none."""
    from wbg_portfolio.paths import data_root
    path = where(data or data_root(), "types", f"{name}_oth_types.csv")
    return labels.rows(path) if os.path.exists(path) else []


def oth_type_questions(name, data=None):
    """{label_id: {type_id: type question}} for the measure's OTH types."""
    labs = {lab["id"]: lab for lab in load(name)[0]}
    out = {}
    for t in oth_types(name, data):
        lab = labs.get(t.get("label_id"))
        if lab:
            out.setdefault(lab["id"], {})[t["type_id"]] = type_question(lab, t)
    return out


def package_asset_questions():
    """{asset_id: graded question}: how far the package is for the asset, on the package scale."""
    labs, scale = load(ASSETS)
    return {a["id"]: asset_question(a, scale, package=True) for a in labs}


def package_subtype_questions():
    """{asset_id: chat question}: which type of the asset the package is for (B2)."""
    assets, subtypes = asset_catalogue()
    return {a["id"]: asset_subtype_question(a, subtypes[a["id"]], package=True)
            for a in assets if subtypes.get(a["id"])}


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


def included_projects(path=None):
    """The project ids of the cohort file's `included = true` rows: the one scope of
    every paid question (Corpus.in_scope, checks.load_packages)."""
    from wbg_portfolio.paths import cohort_path
    with open(path or cohort_path(), newline="", encoding="utf-8") as fh:
        return {r["project_id"].strip() for r in csv.DictReader(fh) if r["included"].strip().lower() == "true"}


def window(length, start, end, chars):
    """(lo, hi): the stretch of a text of `length` characters that a question shows around the
    sentence at [start, end): `chars` characters centred on the sentence, moved inward at either end
    of the text, and never cutting the sentence (a sentence longer than `chars` is shown whole). The one
    window of the pipeline's context questions (Corpus.paragraph_window) and the graders' paragraph
    (sampling.mark)."""
    lo = max(0, min(start - (chars - (end - start)) // 2, length - chars))
    lo = min(lo, start)
    hi = min(length, max(lo + chars, end))
    return lo, hi


class Corpus:
    """The embedded sentences, read once per process: the index, the texts
    as embedded (lead-in included), the paragraphs, the component names and
    the vectors. state(unit_id) is the one state every sentence-level
    question sends (ask.context: sentence | paragraph)."""

    def __init__(self, data, vectors=True, included=None):
        from wbg_portfolio import units
        self.data = data
        self.included = set(included) if included is not None else included_projects()
        self.context = str(panel.require("ask.context"))
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
        self._docs, self.texts, self.spans = {}, {}, {}
        for r in self.index:
            u = every.get(r["unit_id"])
            if u is None:
                continue
            t = self._doc(u["doc_id"])
            self.spans[r["unit_id"]] = (int(u["char_start"]), int(u["char_end"]))
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

    def paragraph_window(self, unit_id, chars):
        """At most `chars` characters of the sentence's paragraph, centred on the sentence (window):
        the sentence itself is always inside."""
        p = self.paras.get(self.row(unit_id)["paragraph_id"])
        if not p:
            return ""
        text = self.paragraph_text(p["paragraph_id"])
        start, end = self.spans.get(unit_id, (None, None))
        if start is not None and int(p["char_start"]) <= start and end <= int(p["char_end"]):
            start, end = start - int(p["char_start"]), end - int(p["char_start"])
        else:
            body = self.texts.get(unit_id, "").split("\n")[-1]
            start = text.find(body)
            if start < 0:
                return text[:chars]
            end = start + len(body)
        lo, hi = window(len(text), start, end, chars)
        return text[lo:hi]

    def context_state(self, unit_id):
        """state() with the paragraph in a field named 'context', second.context_chars of it centred
        on the sentence, for the questions that read the sentence with its paragraph beside it
        (in_context). state() is not changed: a first-pass answer already stored stays what it was."""
        state = self.state(unit_id)
        state.pop("paragraph", None)
        state["context"] = self.paragraph_window(unit_id, int(panel.require("second.context_chars")))
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
        """A sentence is asked about when at least one of its projects is an included
        row of the cohort file (and, in a run over named projects, one of those).
        projects None or empty: every included project."""
        ids = set(self.projects(unit_id))
        return bool(ids & self.included) and (not projects or bool(ids & set(projects)))

    def scope_note(self):
        """One line for the start of a paid stage: sentences in scope and left out."""
        n = sum(1 for r in self.index if self.in_scope(r["unit_id"], None))
        return (f"scope: {n:,} sentence(s) in scope (a project of the cohort file, included = true); "
                f"{len(self.index) - n:,} left out because none of their projects is included")
