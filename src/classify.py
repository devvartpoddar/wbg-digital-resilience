#!/usr/bin/env python3
"""The asset map: grade every sentence the search reads against each asset
with a decision model.

For each sentence, with its section and component, one request asks, for
every active label in the set, the graded question every stage asks (the
measures' form, graded_question): how does the sentence relate to the
label? Which asset a sentence has is decided where the map is read
(src/measure.py sentence_asset: its own posterior at asset.floor, else the
decayed sum over the earlier sentences of its paragraph). The grades are
the label set's scale (inputs/taxonomy/<set>_scale.csv). The model is a
decision model: it returns a probability for each grade, not text. Default:
TypeSafe's Jev, pinned to a dated snapshot, through OpenRouter's decisions
endpoint with the project's own key (embed.read_key). Every answer is stored
per question (src/answers.py), so a changed definition re-asks one question.
What is asked follows the measures' method: a label's question goes to the
sentences whose cosine to the label's phrases reaches its gate (the skip
rule's fitted gate, src/skip.py, else the control panel's asset.gate_cosine;
0 asks every label of every sentence) or that a word pattern of the label
matches (<set>_patterns.csv, optional); then feedback rounds (src/feedback.py)
look below the gate, asking the sentences nearest those whose posterior
reaches asset.floor, until a round yields too few (asset_feedback.* on the
control panel, apart from the measures' feedback.*).

Reads  data/appraisal/{paragraphs,components}.csv, data/appraisal/text/
       inputs/taxonomy/<set>.csv, <set>_scale.csv, <set>_patterns.csv (optional)
       data/embeddings/sentence_emb.npy (the feedback rounds)
Writes data/classify/answers/<model>/<aa>/<sha>.json one stored answer per question
       data/classify/<set>-<version>/scores.csv      sentence x label: the
                                                     probability of each grade
                                                     and the model's confidence
       data/classify/<set>-<version>/manifest.json
       data/reports/classify_<set>.txt

Repeatable: the model's probabilities move a little from call to call, so a
response is requested once and stored under the SHA-256 of the exact request;
every later run reads the store. The stored responses are the record.
Resumable: an interrupted run asks only for what is not stored.
Versioned: <version> is a SHA-256 over the model, the questions and the
sentences; changing a definition, a grade or the text gives a new version.

  python3 src/classify.py assets --dry-run    requests and cost, no network
  python3 src/classify.py assets --limit 20   smoke test, under data/classify/smoke_*
  python3 src/classify.py assets
"""
import argparse, csv, hashlib, json, os, sys, time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import embed  # noqa: E402
import labels  # noqa: E402
import panel  # noqa: E402
import prior  # noqa: E402
import skip  # noqa: E402
from search import TAXONOMY, _active, _rows  # noqa: E402

# The dated snapshot, as the measure stage uses (measure.MODEL): the alias
# moves when the vendor ships a release. Answers stored under the alias
# (ALIASES) are reused where the snapshot that served them is this one.
MODEL = "typesafe/jev-1.13-20260917"
ALIASES = ("typesafe/jev-1.13",)
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
# US$ per million input tokens (output is free), for the dry-run estimate only.
PRICE_PER_MTOK = 0.042
# Above about 32,000 bytes the endpoint switches to a long-context mode the
# vendor describes as slower and less accurate; a request that would pass
# that is split over several (answers.bodies_for), never truncated.
MAX_REQUEST_CHARS = 30_000
# A label's question is put to a sentence only when it scores at least this
# against the label's phrases (control panel asset.gate_cosine; 0 = ask
# every label of every sentence). The skip rule (src/skip.py) replaces it
# per asset once fitted.
MIN_COSINE = float(panel.get("asset.gate_cosine", 0.3))
# The map grades each search unit (a sentence) with its section and
# component, asked the same graded question the measures ask.
WORKERS = 10
SCORE_COLS = ["unit_id", "paragraph_id", "doc_id", "project_ids", "component_number", "label_id",
              "asked", "route", "round", "best_cosine", "grade", "expected", "confidence", "p0", "p1", "p2",
              "p3", "model_served", *prior.COLS]


# What each run actually asked for is appended to data/reports/spend.csv, so
# the day's total is a pipeline fact, not a memory.
SPEND_COLS = ["at", "stage", "label_set", "model", "calls", "cost_usd", "seconds", "run"]


def record_spend(data, stage, label_set, model, calls, cost, seconds, run=""):
    """One line per run that asked anything, in data/reports/spend.csv."""
    if not calls:
        return
    path = where(data, "reports", "spend.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SPEND_COLS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stage": stage,
                    "label_set": label_set, "model": model, "calls": calls,
                    "cost_usd": f"{cost:.4f}", "seconds": f"{seconds:.0f}", "run": run})


def spend_today(data):
    """(calls, US$) recorded today, for the log line before a run."""
    path = where(data, "reports", "spend.csv")
    today = time.strftime("%Y-%m-%d", time.gmtime())
    calls, cost = 0, 0.0
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["at"].startswith(today):
                    # A ledger row can hold "33.0": older link runs recorded a
                    # float count, and the ledger is never rewritten.
                    calls += int(float(r["calls"]))
                    cost += float(r["cost_usd"])
    return calls, cost


class TransportError(RuntimeError):
    """A request did not yield a usable answer; it is retried on a later run."""


class Refused(RuntimeError):
    """The endpoint refused the request outright (bad key, no credit, bad
    payload): every other request would be refused too, so the run stops."""


# ------------------------------------------------------------------ the labels

def load_labels(name, folder=TAXONOMY):
    """(labels, scale): the active labels [{id, name, definition}] and the
    grade descriptions, lowest first, with {name} standing for the label."""
    folder = folder or labels.folder(name)
    rows = _rows(os.path.join(folder, f"{name}.csv"))
    id_col = next(c for c in rows[0] if c.endswith("_id"))
    name_col = next(c for c in rows[0] if c.endswith("_name"))
    active = [{"id": r[id_col].strip(), "name": r[name_col].strip(),
               "definition": " ".join(r["definition"].split())}
              for r in rows if _active(r)]
    if not active:
        raise SystemExit(f"classify: {name}.csv has no active label")
    path = os.path.join(folder, f"{name}_scale.csv")
    if not os.path.exists(path):
        raise SystemExit(f"classify: {path} is missing: the grades are a person's "
                         f"judgement and are not assumed")
    scale = [" ".join(r["description"].split())
             for r in sorted(_rows(path), key=lambda r: int(r["grade"]))]
    if not 2 <= len(scale) <= 5:
        raise SystemExit(f"classify: {path} needs 2 to 5 grades")
    return active, scale


SUBJECT = "the sentence from a World Bank project appraisal document in the 'sentence' field"


def graded_question(lab, scale, subject=SUBJECT):
    """The question a classification asks (assets, measures): how the item
    relates to the label, with the label's name and definition and its 0-3
    scale; its score is the probability of the grades the panel counts
    (read.grades). The checks after it ask fit_question. The question id is never shown to the model, so
    the name and definition go in the wording. subject names the item and
    its field (a package, a commitment and a package)."""
    # Word for word the measure question since 2026-10-20 for a sentence,
    # so its stored answers stay valid.
    return {"type": "score",
            "instructions": f"How does {subject}, relate to {lab['name']}? {lab['name']} "
                            f"means: {lab['definition']}",
            "criteria": [g.replace("{name}", lab["name"]) for g in scale]}


# The checks made after a classification (types, pair checks, packages to
# assets, commitments to packages) ask whether something applies, not how
# firmly a project commits, so they share this scale; the score is P(yes)
# (fit_score). Tested on 43 committed risk-assessment sentences and 7 draft
# types (2026-10-22): 1.8 types a sentence at 0.5, against 6.1 on the
# commitment scale.
FIT_SCALE = ["No: {name} does not apply.",
             "Unsure: the text does not say enough to tell whether {name} applies.",
             "Yes: {name} clearly applies."]


def fit_question(lab, subject=SUBJECT, given=""):
    """The question every check after a classification asks: does the label
    apply to the item, yes, unsure or no (FIT_SCALE). given says what is
    already decided (the sentence is a commitment to a measure), so the
    question asks only what is left."""
    return {"type": "score",
            "instructions": f"Does {lab['name']} apply to {subject}? {given}"
                            f"{lab['name']} means: {lab['definition']}",
            "criteria": [g.replace("{name}", lab["name"]) for g in FIT_SCALE]}


def fit_score(entry):
    """The score of a stored answer to a fit question: P(yes)."""
    a = entry.get("answer", entry)
    return float(a["probabilities"].get(str(len(FIT_SCALE) - 1), 0))


def questions(labels, scale):
    """The questions object of a request: one graded question per label."""
    return {lab["id"]: graded_question(lab, scale) for lab in labels}


# --------------------------------------------------------------- the sentences

def sentences(data):
    """Every sentence the search reads (the embedded units, copies once),
    with its text as embedded (a list item with its lead-in), section and
    component: the items of the sentence asset map, in reading order."""
    import units
    names = {}
    path = where(data, "appraisal", "components.csv")
    if os.path.exists(path):
        for r in sorted(_rows(path), key=lambda r: r["source"] == "heading"):
            names.setdefault((r["doc_id"], r["number"]), r["name"])
    paras = {r["paragraph_id"]: r for r in _rows(where(data, "appraisal", "paragraphs.csv"))}
    every = {u["unit_id"]: u for u in units.search_units(data)}
    index = _rows(where(data, "embeddings", "sentence_index.csv"))
    texts, out = {}, []
    for i, r in enumerate(index):
        u = every.get(r["unit_id"])
        if u is None:
            continue
        did = u["doc_id"]
        if did not in texts:
            with open(where(data, "text", f"{did}.txt"), encoding="utf-8") as fh:
                texts[did] = fh.read()
        body = texts[did][int(u["char_start"]):int(u["char_end"])]
        if u.get("lead_in_start"):
            body = texts[did][int(u["lead_in_start"]):int(u["lead_in_end"])] + "\n" + body
        para = paras.get(r["paragraph_id"], {})
        sub = r.get("subcomponent_number") or ""
        comp = sub or r.get("component_number") or ""
        name = names.get((did, comp)) or \
            (para.get("subcomponent_name") if sub else para.get("component_name")) or ""
        state = {"section": para.get("section_title", ""), "sentence": body}
        if comp:
            state["component"] = f"{comp} {name}".strip()
        row = {"unit_id": r["unit_id"], "paragraph_id": r["paragraph_id"], "doc_id": did,
               "project_ids": r.get("project_ids", ""),
               "component_number": r.get("component_number", "")}
        out.append({"row": row, "state": state, "i": i})
    return out


def request_body(state, qs, model):
    return {"model": model, "state": state, "questions": qs}


def request_sha(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- the store

def decide(body, *, api_key, endpoint=ENDPOINT, timeout=60, tries=5):
    """One request to the decisions endpoint. THE transport seam."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
               "HTTP-Referer": "https://github.com/devvartpoddar/wbg-digital-resilience",
               "X-Title": "wbg-digital-resilience"}
    last = None
    for attempt in range(tries):
        try:
            r = requests.post(endpoint, json=body, headers=headers, timeout=timeout)
            if r.status_code in (408, 409, 429, 500, 502, 503, 504, 520, 522, 524, 529):
                raise TransportError(f"HTTP {r.status_code}: {r.text[:200]}")
            if 400 <= r.status_code < 500:
                raise Refused(f"HTTP {r.status_code} from {endpoint}: {r.text[:500]}")
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                raise TransportError(f"response was not JSON: {r.text[:200]}")
        except TransportError as exc:
            last = exc
        except requests.RequestException as exc:
            last = TransportError(f"{type(exc).__name__}: {str(exc)[:300]}")
        if attempt < tries - 1:
            time.sleep(2 ** attempt)
    raise last


# ------------------------------------------------------------------- the run

def best_cosines(name, data, taxonomy=TAXONOMY):
    """{(label_id, unit_id): a sentence's best cosine over the label's
    queries} from the set's current search run, or {} if there is none."""
    import search
    try:
        folder, _ = search.run_folder(name, data, taxonomy=taxonomy)
    except SystemExit:
        return {}
    if not os.path.exists(os.path.join(folder, "manifest.json")):
        return {}
    import numpy
    queries, scores, index = search.load_run(folder, data)
    cols = {}
    for j, q in enumerate(queries):
        cols.setdefault(q["label_id"], []).append(j)
    out = {}
    for lab, js in cols.items():
        best = numpy.asarray(scores[:, js]).max(axis=1)
        for r, b in zip(index, best):
            k = (lab, r["unit_id"])
            if b > out.get(k, -2.0):
                out[k] = float(b)
    return out


def prior_inputs(name, data, taxonomy, items_, cosines, gate):
    """of(label_id, unit_id) -> the prior inputs (src/prior.py) of a
    sentence of the map for a label: its best cosine over the label's
    phrases and the phrase match weighted by word rarity. (The paragraph
    counts are made after every stage, from the classification.) Computed
    per row as it is written: the map has a row per sentence and label, and
    a second table of them would double the stage's memory."""
    import search
    cosines = cosines or best_cosines(name, data, taxonomy)
    weight = prior.Weights(*prior.word_df(data))
    phrases = {}
    for q in search.load_queries(name, taxonomy):
        if q["kind"] == "phrase":
            phrases.setdefault(q["label_id"], []).append(q["text"])
    phrases = {lid: prior.phrases(ts, weight) for lid, ts in phrases.items()}
    labs = set(lab["id"] for lab in load_labels(name, taxonomy)[0])
    item = {p["row"]["unit_id"]: p for p in items_}
    last = {}                   # the rows of one sentence are written together

    def of(lid, uid):
        p = item.get(uid)
        if p is None or lid not in labs:
            return {}
        if last.get("uid") != uid:
            last.update(uid=uid, words=set(prior.words(p["state"]["sentence"])))
        return {"prior_phrase_cosine": prior.fmt(cosines.get((lid, uid))),
                "prior_phrase_match": prior.fmt(prior.phrase_match(last["words"],
                                                                   phrases.get(lid, [])))}
    return of


def score_rows(p, response, labels, cosines=None, asked=None):
    out = []
    for lab in labels:
        r = p["row"]
        cos = (cosines or {}).get((lab["id"], r["unit_id"]))
        if asked is not None and lab["id"] not in asked:
            out.append({"unit_id": r["unit_id"], "paragraph_id": r["paragraph_id"], "doc_id": r["doc_id"],
                        "project_ids": r["project_ids"],
                        "component_number": r.get("component_number", ""),
                        "label_id": lab["id"], "asked": "false",
                        "best_cosine": "" if cos is None else f"{cos:.4f}"})
            continue
        a = response["answers"][lab["id"]]
        probs = [float(a["probabilities"][k]) for k in sorted(a["probabilities"], key=int)]
        total = sum(probs) or 1.0
        r = p["row"]
        out.append({"unit_id": r["unit_id"], "paragraph_id": r["paragraph_id"], "doc_id": r["doc_id"],
                    "project_ids": r["project_ids"],
                    "component_number": r.get("component_number", ""),
                    "label_id": lab["id"], "asked": "true",
                    "best_cosine": "" if cos is None else f"{cos:.4f}",
                    "grade": max(range(len(probs)), key=lambda i: probs[i]),
                    "expected": f"{sum(i * q for i, q in enumerate(probs)) / total:.3f}",
                    "confidence": f"{float(a.get('confidence', 0)):.3f}",
                    **{f"p{i}": (f"{probs[i]:.4f}" if i < len(probs) else "")
                       for i in range(4)},
                    "model_served": response.get("model", "")})
    return out


def in_scope(row, projects):
    """Whether a row's project_ids meet the scope (None or empty = every project)."""
    return not projects or bool(set(row.get("project_ids", "").split("|")) & set(projects))


def plan(name, data, *, model=MODEL, taxonomy=TAXONOMY, limit=0, min_cosine=MIN_COSINE,
         projects=None):
    """Everything the first pass would send, without sending it: (labels,
    scale, questions, [(sentence, request body)], request SHA-256s, run
    folder, cosines, routes). A label's question goes to a sentence when the
    sentence's cosine to the label's phrases reaches the label's gate or a
    word pattern of the label matches it (inputs/taxonomy/<set>_patterns.csv,
    the measures' rule); a sentence with no question is not sent. routes =
    {(unit_id, label_id): embedding | pattern | both}. The feedback rounds
    (classify) follow from the stored answers, so the folder is keyed on what
    they read too: asset_feedback.*, asset.floor and the asset layers.
    `projects` limits the run to the sentences of those projects (a cohort
    set, say); the run is keyed on the sentences it covers, so a scope is its
    own run and a wider one later reads the store."""
    import feedback
    import labels as labels_mod
    labels, scale = load_labels(name, taxonomy)
    qs = questions(labels, scale)
    paras = [p for p in sentences(data) if in_scope(p["row"], projects)]
    if limit:
        paras = paras[:limit]
    cosines = best_cosines(name, data, taxonomy) if min_cosine else {}
    if min_cosine and not cosines:
        raise SystemExit(f"classify: a cosine gate needs a search run for {name}; run "
                         f"./wbg search {name} first")
    gates = fitted_gates(name, data, taxonomy, qs) if min_cosine else {}
    pats = {}
    for pt in labels_mod.load_patterns(name, taxonomy=taxonomy):
        if pt["label_id"] in qs:
            pats.setdefault(pt["label_id"], []).append(pt)
    bodies, routes = [], {}
    for p in paras:
        uid, text = p["row"]["unit_id"], p["state"]["sentence"]
        ask = {}
        for lid, q in qs.items():
            near = not min_cosine or cosines.get((lid, uid), 0.0) >= gates.get(lid, min_cosine)
            hit = any(pt["regex"].search(text) for pt in pats.get(lid, ()))
            if near or hit:
                ask[lid] = q
                routes[(uid, lid)] = "both" if near and hit else ("pattern" if hit else "embedding")
        bodies.append((p, request_body(p["state"], ask, model)))
    # A sentence whose questions do not fit one request is asked in several
    # (answers.bodies_for); only a sentence too long for a single question
    # is refused, never truncated.
    for p, body in bodies:
        one = request_body(p["state"], dict(list(body["questions"].items())[:1]), model)
        if len(json.dumps(one, ensure_ascii=False)) > MAX_REQUEST_CHARS:
            raise SystemExit(f"classify: {p['row']['unit_id']} makes a request over "
                             f"{MAX_REQUEST_CHARS:,} characters with one question; not truncated")
    shas = [request_sha(b) for _, b in bodies]
    prefix = "smoke_" if limit else ""
    fb = feedback.settings(feedback.ASSET) if rounds_apply(min_cosine, limit) else {}
    h = hashlib.sha256(f"min_cosine={min_cosine}\nunit=sentence\nprior={prior.VERSION}\n"
                       f"gates={skip.digest(gates)}\n".encode())
    if pats or fb:
        h.update(json.dumps({"patterns": {l: [[x["pattern_id"], x["pattern"]] for x in v]
                                          for l, v in sorted(pats.items())},
                             "feedback": fb,
                             "floor": float(panel.get("asset.floor", 0.3)) if fb else None,
                             "grades": list(panel.grades()) if fb else None,
                             "layers_on": bool(panel.get("posterior.on", True)) if fb else None,
                             "layers": layers_digest_for(data, question_shas(qs)) if fb else ""},
                            sort_keys=True).encode())
    for p, sha in zip(paras, shas):
        h.update(f"{p['row']['unit_id']}:{sha}\n".encode())
    out = where(data, "classify", f"{prefix}{name}-{h.hexdigest()[:12]}")
    return labels, scale, qs, bodies, shas, out, cosines, routes


def rounds_apply(min_cosine, limit):
    """Feedback rounds run after a gated pass (with no gate every sentence
    is asked already), not on a smoke test, and while feedback.max_rounds is
    above 0."""
    import feedback
    return bool(min_cosine) and not limit and feedback.settings(feedback.ASSET)["max_rounds"] > 0


def feedback_rounds(name, data, qs, bodies, merged, asked, routes, round_of, *, model,
                    api_key, log, workers):
    """The asset map's second look below the gate, the measures' method
    (src/feedback.py): for each asset, the sentences whose own posterior
    reaches asset.floor (through the asset's layer, when fitted) against
    those asked that do not; the next feedback.batch sentences nearest that
    query and not yet asked the asset's question are asked it. One read of
    the embedding matrix serves every asset in a round. Updates merged,
    asked, routes and round_of in place. Returns (calls, cost, seconds,
    failures, {asset: [{asked, yes}] per round})."""
    import answers
    import feedback
    import numpy
    import posterior
    fb = feedback.settings(feedback.ASSET)
    floor = float(panel.get("asset.floor", 0.3))
    vector_of, projects_of = posterior.lookups(data)
    layers = layers_for(data, question_shas(qs), vector_of, projects_of)
    matrix = numpy.load(where(data, "embeddings", "sentence_emb.npy"), mmap_mode="r")
    item_of = {p["row"]["unit_id"]: p for p, _ in bodies}
    by_i = {p["i"]: p["row"]["unit_id"] for p, _ in bodies}
    yes_memo = {}

    def yes(uid, lid):
        k = (uid, lid)
        if k not in yes_memo:
            probs = merged[uid][lid]["answer"]["probabilities"]
            sc = sum(float(probs.get(str(g), 0)) for g in panel.grades())
            if lid in layers:
                sc = layers[lid].probability(uid, sc)
            yes_memo[k] = sc >= floor
        return yes_memo[k]

    calls, cost, seconds, history = 0, 0.0, 0.0, {lid: [] for lid in qs}
    going = set(qs)
    for rnd in range(1, fb["max_rounds"] + 1):
        lids, queries = [], []
        for lid in sorted(going):
            mine = [u for u, ls in asked.items() if lid in ls]
            q = feedback.query(matrix, [item_of[u]["i"] for u in mine if yes(u, lid)],
                               [item_of[u]["i"] for u in mine if not yes(u, lid)],
                               fb["negative_weight"])
            if q is None:
                going.discard(lid)
                continue
            lids.append(lid)
            queries.append(q)
        if not lids:
            break
        new = {}
        for lid, (order, _) in zip(lids, feedback.ranked(matrix, queries)):
            for i in feedback.next_rows(order, lambda i: i in by_i and
                                        lid not in asked.get(by_i[i], ()), fb["batch"]):
                new.setdefault(by_i[i], {})[lid] = qs[lid]
        if not new:
            break
        order_ = sorted(new, key=lambda u: item_of[u]["i"])
        requests_ = [(item_of[u]["state"], new[u]) for u in order_]
        _, pre = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                             dry_run=True, aliases=ALIASES)
        if pre["to_ask"] and not api_key:
            raise SystemExit(f"classify: no credential for the feedback round: set "
                             f"${embed.KEY_VAR} or put the key in {embed.KEY_FILE}")
        got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                              aliases=ALIASES)
        calls += st["calls"]
        cost += st["cost_usd"]
        seconds += st["seconds"]
        if st["failures"] or not all(got):
            return calls, cost, seconds, st["failures"] or [("", "missing answers")], history
        found = {lid: 0 for lid in lids}
        for u, g in zip(order_, got):
            merged.setdefault(u, {}).update(g)
            for lid in new[u]:
                asked.setdefault(u, set()).add(lid)
                routes[(u, lid)] = "feedback"
                round_of[(u, lid)] = rnd
                found[lid] += yes(u, lid)
        for lid in lids:
            n = sum(1 for u in new if lid in new[u])
            history[lid].append({"round": rnd, "asked": n, "yes": found[lid]})
            if found[lid] < fb["min_yield"]:
                going.discard(lid)
        log(f"  feedback round {rnd}: {sum(len(v) for v in new.values()):,} questions, "
            f"{sum(found.values())} at asset.floor or more")
    return calls, cost, seconds, [], history


def _marker(data, name, projects):
    """The file naming the run last written for a set and scope: the stages
    that read a map (the measure, the load) need not know which cosine gate
    it was made with."""
    scope = "all" if not projects else hashlib.sha256(
        "\n".join(sorted(projects)).encode()).hexdigest()[:12]
    return where(data, "classify", "current", f"{name}-{scope}.json")


def write_marker(data, name, projects, out, min_cosine):
    """Name the run just written (or found already written) as the current
    one for this set and scope, with the gate it was made with."""
    marker = _marker(data, name, projects)
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker + ".part", "w", encoding="utf-8") as fh:
        json.dump({"run": os.path.basename(out), "min_cosine": min_cosine,
                   "projects": sorted(projects) if projects else None}, fh, indent=1)
    os.replace(marker + ".part", marker)


def _plan_key(name, data, taxonomy, min_cosine, projects, model):
    """A cheap fingerprint of everything plan() reads: the corpus tables (by
    their remembered checksums), the label set's files, the pipeline's code,
    the search run a gate reads, the model, gate and scope. Equal keys mean
    plan() would name the same run folder, so readers need not rebuild
    every request to find it (that was 40,000 request bodies hashed on each
    lookup, about ten lookups per measure)."""
    from paths import file_sha256
    h = hashlib.sha256(f"{name}\t{model}\t{min_cosine}\t{sorted(projects or [])}\t"
                       f"{MAX_REQUEST_CHARS}\tsentence\n".encode())
    for f in ("paragraphs.csv", "components.csv", "sentences.csv"):
        p = where(data, "appraisal", f)
        h.update((file_sha256(p) if os.path.exists(p) else "-").encode())
    p = where(data, "embeddings", "sentence_index.csv")
    h.update((file_sha256(p) if os.path.exists(p) else "-").encode())
    folder = taxonomy or labels.folder(name)
    for f in (f"{name}.csv", f"{name}_scale.csv", f"{name}_patterns.csv"):
        p = os.path.join(folder, f)
        h.update((file_sha256(p, remember=False) if os.path.exists(p) else "-").encode())
    # what the feedback rounds read (plan() keys the folder on them too)
    h.update(json.dumps([panel.items("feedback."), panel.items("asset_feedback."),
                         panel.get("asset.floor"),
                         panel.get("posterior.on", True), panel.get("read.grades")],
                        sort_keys=True, default=str).encode())
    p = layers_path(data)
    h.update((file_sha256(p, remember=False) if os.path.exists(p) else "-").encode())
    src = os.path.dirname(os.path.abspath(__file__))
    for f in sorted(os.listdir(src)):
        if f.endswith(".py"):
            h.update(file_sha256(os.path.join(src, f), remember=False).encode())
    if min_cosine:
        import search
        h.update(os.path.basename(search.run_folder(name, data, taxonomy=taxonomy)[0]).encode())
        p = skip.path(data, name)
        h.update((file_sha256(p, remember=False) if os.path.exists(p) else "-").encode())
    return h.hexdigest()[:24]


def fitted_gates(name, data, taxonomy, qs):
    """{label_id: gate} the evaluation fitted by the skip rule (src/skip.py)
    for the labels' current questions and the set's search run; a label
    without one is gated at the panel's asset.gate_cosine."""
    import search
    try:
        folder, _ = search.run_folder(name, data, taxonomy=taxonomy)
    except SystemExit:
        return {}
    return skip.read(data, name, question_shas(qs), folder)


def _plan_cache(data, key):
    return where(data, "classify", "current", "plans", f"{key}.json")


def remember_plan(data, key, out):
    path = _plan_cache(data, key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".part", "w", encoding="utf-8") as fh:
            json.dump({"run": os.path.basename(out)}, fh)
        os.replace(path + ".part", path)
    except OSError:
        pass


def planned_run(name, data, *, taxonomy=TAXONOMY, min_cosine=MIN_COSINE, projects=None,
                model=MODEL):
    """The run folder plan() would name, from the fingerprint cache when it
    has seen these inputs before, else by planning (and remembering)."""
    try:
        key = _plan_key(name, data, taxonomy, min_cosine, projects, model)
    except (OSError, SystemExit):
        key = None
    if key:
        try:
            with open(_plan_cache(data, key), encoding="utf-8") as fh:
                return where(data, "classify", json.load(fh)["run"])
        except (OSError, ValueError, KeyError):
            pass
    out = plan(name, data, model=model, taxonomy=taxonomy, min_cosine=min_cosine,
               projects=projects)[5]
    if key:
        remember_plan(data, key, out)
    return out


def last_run(name, data, projects=None):
    """The run folder last written for the set and scope (its marker), even
    when what it was keyed on has since moved (a refitted asset layer, say),
    or None. For a report on the run the grades were drawn against; a stage
    reads current_run."""
    marker = _marker(data, name, projects)
    if not os.path.exists(marker):
        return None
    with open(marker, encoding="utf-8") as fh:
        out = where(data, "classify", json.load(fh)["run"])
    return out if os.path.exists(os.path.join(out, "manifest.json")) else None


def current_run(name, data, taxonomy=TAXONOMY, min_cosine=None, projects=None):
    """The run folder for the label set and sentences as they stand now, or
    None if that run has not been written. With min_cosine None (the
    default for readers), the gate is the one the last run for this set and
    scope was made with (the marker it wrote); the run is still checked
    against the sentences, questions and gate as they stand, so a stale
    marker finds nothing rather than something old."""
    import glob
    if not glob.glob(where(data, "classify", f"{glob.escape(name)}-*", "manifest.json")):
        return None                 # this set was never classified (a measure, say)
    gates = [min_cosine]
    if min_cosine is None:
        gates = [MIN_COSINE]
        marker = _marker(data, name, projects)
        if os.path.exists(marker):
            with open(marker, encoding="utf-8") as fh:
                gates.insert(0, float(json.load(fh).get("min_cosine", MIN_COSINE)))
    for gate in dict.fromkeys(gates):
        try:
            out = planned_run(name, data, taxonomy=taxonomy, min_cosine=gate,
                              projects=projects)
        except SystemExit:          # a gated plan with no search run to gate on
            continue
        if os.path.exists(os.path.join(out, "manifest.json")):
            return out
    return None


def classify(name, data, *, model=MODEL, api_key="", taxonomy=TAXONOMY, limit=0,
             dry_run=False, log=print, workers=WORKERS, min_cosine=MIN_COSINE, projects=None):
    """Classify the label set; returns the run folder (None on a dry run or
    when requests failed - run again to resume)."""
    try:
        labels, scale, qs, bodies, shas, out, cosines, routes = plan(
            name, data, model=model, taxonomy=taxonomy, limit=limit, min_cosine=min_cosine,
            projects=projects)
    except SystemExit as exc:
        if not dry_run or "search run" not in str(exc):
            raise           # only a search run not scored yet has no estimate
        # The gate reads the search run, and a dry run writes nothing, so a
        # dry run that starts before the search stage has scored the new index
        # cannot plan (./wbg run --dry-run --from embed, 2026-10-22: the run
        # died here and the stages after this one got no estimate). Say so and
        # carry on; a real run keeps the error.
        log(f"{name}: no estimate - {exc}")
        return None
    if not limit:
        try:
            remember_plan(data, _plan_key(name, data, taxonomy, min_cosine, projects, model), out)
        except (OSError, SystemExit):
            pass
    n_q = sum(len(b["questions"]) for _, b in bodies)
    log(f"{name}: {len(labels)} labels x {len(scale)} grades, {len(bodies):,} sentences, "
        f"{n_q:,} questions"
        + (f" (cosine gate {min_cosine}: {n_q / (len(bodies) * len(labels)):.0%} of all)"
           if min_cosine else ""))
    if os.path.exists(os.path.join(out, "manifest.json")):
        log(f"  already written: {out}")
        if not limit:
            write_marker(data, name, projects, out, min_cosine)   # a run made before markers
        return out
    import answers
    requests_ = [(p["state"], b["questions"]) for p, b in bodies if b["questions"]]
    got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                          dry_run=True, aliases=ALIASES)
    log(f"  answers stored already for {sum(1 for g in got if g):,} sentences; calls to make: "
        f"{st['to_ask']:,} (about {st['chars'] / 4 / 1e6:.1f}M tokens, US${st['estimate_usd']:.2f})")
    rounds = rounds_apply(min_cosine, limit)
    if rounds:
        import feedback
        fb = feedback.settings(feedback.ASSET)
        log(f"  then feedback rounds: at most {fb['max_rounds']} of {fb['batch']} sentences an "
            f"asset ({len(qs) * fb['max_rounds'] * fb['batch']:,} questions at most)")
    if dry_run:
        log("  --dry-run: first pass only; no request made, nothing written")
        return None
    calls, cost, secs = 0, 0.0, 0.0
    if st["to_ask"]:
        if not api_key:
            raise SystemExit(f"classify: no credential: set ${embed.KEY_VAR} or put the "
                             f"key in {embed.KEY_FILE} in the main checkout")
        log(f"  spent today before this run: US${spend_today(data)[1]:.2f}")
        got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                              aliases=ALIASES)
        calls, cost, secs = st["calls"], st["cost_usd"], st["seconds"]
    if st["failures"] or not all(got):
        if calls:
            record_spend(data, "classify", name, model, calls, cost, secs, "failed")
        log(f"  {len(st['failures'])} request(s) failed; nothing written. Run again to resume.")
        for sha, err in st["failures"][:5]:
            log(f"    {sha[:12]}: {err}")
        return None
    merged, asked = {}, {}
    it = iter(got)
    for p, b in bodies:
        if b["questions"]:
            uid = p["row"]["unit_id"]
            merged[uid] = dict(next(it))
            asked[uid] = set(b["questions"])
    round_of = {k: 0 for k in routes}
    history = {}
    if rounds:
        c2, k2, s2, failures, history = feedback_rounds(
            name, data, qs, bodies, merged, asked, routes, round_of, model=model,
            api_key=api_key, log=log, workers=workers)
        calls, cost, secs = calls + c2, cost + k2, secs + s2
        if failures:
            record_spend(data, "classify", name, model, calls, cost, secs, "failed")
            log(f"  {len(failures)} request(s) failed in a feedback round; nothing written. "
                f"Run again to resume.")
            return None
    if calls:
        record_spend(data, "classify", name, model, calls, cost, secs, os.path.basename(out))

    rows, served = [], {}
    prior_of = prior_inputs(name, data, taxonomy, [p for p, _ in bodies], cosines, min_cosine)
    for p, b in bodies:
        uid = p["row"]["unit_id"]
        if uid not in merged:
            rows += score_rows(p, {"answers": {}}, labels, cosines, asked=set())
            continue
        g = merged[uid]
        response = {"answers": {qid: e["answer"] for qid, e in g.items()},
                    "model": "|".join(sorted({e["model"] for e in g.values()}))}
        served[response["model"]] = served.get(response["model"], 0) + 1
        rows += score_rows(p, response, labels, cosines,
                           asked=asked[uid] if min_cosine else None)
    for r in rows:
        r.update(prior_of(r["label_id"], r["unit_id"]))
        k = (r["unit_id"], r["label_id"])
        if r["asked"] == "true":
            r["route"], r["round"] = routes.get(k, "embedding"), round_of.get(k, 0)
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "scores.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SCORE_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    manifest = {"label_set": name, "model_requested": model, "models_served": served,
                "min_cosine": min_cosine,
                "questions_asked": sum(len(v) for v in asked.values()) if min_cosine else n_q,
                "first_pass_questions": n_q, "feedback_rounds": history,
                "endpoint": ENDPOINT, "labels": [lab["id"] for lab in labels],
                "scale": scale, "unit": "sentence", "items": len(bodies), "paragraphs": len(bodies),
                "questions": qs,
                "question_sha256": question_shas(qs)}
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
    os.replace(part, out)
    if not limit:
        write_marker(data, name, projects, out, min_cosine)
    prefix = "smoke_" if limit else ""
    report = where(data, "reports", f"{prefix}classify_{name}.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"{name}: {len(bodies):,} sentences x {len(labels)} labels -> {out}\n"
                 f"models served: {served}\n")
        for lab in labels:
            scores = [sum(float(r.get(f"p{g}") or 0) for g in panel.grades())
                      for r in rows if r["label_id"] == lab["id"] and r["asked"] == "true"]
            counts = [sum(1 for p in scores if lo <= p < hi)
                      for lo, hi in ((0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.01))]
            skipped = sum(1 for r in rows if r["label_id"] == lab["id"] and r["asked"] != "true")
            fh.write(f"  {lab['id']:<26} sentences by model score (grades 2 and 3; "
                     f"<0.1, 0.1-0.3, 0.3-0.5, 0.5-0.7, 0.7+): {counts}"
                     + (f"; not asked (cosine gate): {skipped:,}" if skipped else "") + "\n")
    if len(served) > 1:
        log(f"  warning: more than one model snapshot served: {served}")
    log(f"  wrote {out}")
    return out


def layers_path(data):
    return where(data, "labels", "assets_posterior.json")


def layers_for(data, shas, vector_of, projects_of=None):
    """{asset: posterior.Layer} for the assets whose probability layer
    (fitted by ./wbg check all --evaluate) was fitted on the question now
    asked (shas = {asset: question SHA-256}); {} when none."""
    import posterior
    path = layers_path(data)
    if not panel.get("posterior.on", True) or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        specs = json.load(fh)
    return {a: posterior.Layer(spec, vector_of, projects_of) for a, spec in specs.items()
            if spec.get("question_sha256") == shas.get(a)}


def layers_digest_for(data, shas):
    """A short hash of the asset layers that apply to these questions ('' for none)."""
    import posterior
    path = layers_path(data)
    if not panel.get("posterior.on", True) or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        specs = json.load(fh)
    return posterior.digest({a: spec for a, spec in sorted(specs.items())
                             if spec.get("question_sha256") == shas.get(a)})


def _shas(folder):
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh).get("question_sha256", {})


def asset_layers(data, folder, vector_of, projects_of=None):
    """{asset: posterior.Layer} for the assets whose layer was fitted on the
    question the map at `folder` asked; {} when none."""
    return layers_for(data, _shas(folder), vector_of, projects_of)


def asset_layers_digest(data, folder):
    """A short hash of the asset layers that apply to the map at `folder`
    ('' for none): part of every reader's run key, so a refit gives a new run."""
    if not folder:
        return ""
    return layers_digest_for(data, _shas(folder))


def question_shas(qs):
    """{label_id: SHA-256 of the exact question asked}: a grade a person gave
    under one wording of a label is not a grade under another."""
    return {k: hashlib.sha256(json.dumps(v, sort_keys=True).encode("utf-8")).hexdigest()
            for k, v in qs.items()}


def scope(projects):
    """A --projects argument as a sorted list, or None for every project."""
    ids = sorted({p.strip().upper() for p in (projects or "").split(",") if p.strip()})
    return ids or None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set", help="a label set in inputs/taxonomy/ or analysis/labels/: "
                                      "assets, measures")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--limit", type=int, default=0, help="first N sentences (smoke test)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--projects", default="",
                    help="comma-separated project ids: only their sentences (./wbg classify "
                         "--set digital fills this from the cohort)")
    args = ap.parse_args()
    key = "" if args.dry_run else embed.read_key("")
    out = classify(args.label_set, args.data, model=args.model, api_key=key,
                   limit=args.limit, dry_run=args.dry_run, workers=args.workers,
                   projects=scope(args.projects))
    if out and not (args.dry_run or args.limit):
        import store
        store.try_publish(args.data, sets=[args.label_set], projects=scope(args.projects))
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
