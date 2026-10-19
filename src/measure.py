#!/usr/bin/env python3
"""Grade a measure's units with the decision model, through the funnel, and
deepen the search while it still yields.

The funnel, each level cheaper than the next and each one's loss measurable:

  1. Candidates (src/candidates.py): the units a measure's word patterns
     match, plus its embedding top N (default 2,000). No model reads anything.
  2. The asset map (src/classify.py on the assets set), as columns on every
     unit: the best asset in its paragraph and in its component.
  3. The decision model reads each unit once. One request carries the
     measure's graded question AND one yes/no question per digital asset
     ("does this sentence concern fiber networks?"), so the asset link is
     answered for the sentence itself, in the same call, at no extra call.
     The paragraph map stays as coarse context; the unit answer is what the
     gate and the reports use.
  4. Relevance feedback: the vectors of the units the model graded committed
     are averaged into a centroid; the corpus is re-ranked by cosine to it;
     the next --batch unseen units are graded in one parallel batch; and this
     repeats until a round yields fewer committed units than --min-yield, or
     --max-rounds is reached. Each round is one batch of parallel requests,
     tens of seconds, not a reading loop. The stopping rule is a yield rule
     for retrieval depth, not a recall guarantee: what the search never
     reaches stays unmeasured until a person grades a sample.

The measure's question names the measure and its definition and never the
asset: the decision model reads literally, and a question about "climate
risk assessment of digital infrastructure" scored a tower-siting assessment
at 0.17 where the same question without the asset phrase scored it 0.97.

Nothing is gated by default. --gate unit|paragraph|component with --asset-min
sends only the units the chosen asset link scores at least that high: the
setting at scale, once the gate's cost has been measured
(src/evaluate_activities.py). The unit gate can only apply from round 1 on
(round 0 is what produces the unit answers), so it is reported, not applied,
for round 0.

Reads  data/candidates/<set>-<version>/candidates.csv
       data/classify/assets-<version>/scores.csv        the asset map (optional)
       data/embeddings/{sentence_emb.npy,sentence_index.csv}
       data/appraisal/{paragraphs,components}.csv, data/appraisal/text/
       analysis/labels/<set>.csv, <set>_scale.csv; inputs/taxonomy/assets.csv
Writes data/classify/cache/<model>/...                   every response, once
       data/measures/<set>-<version>/units.csv           one row per graded unit
       data/measures/<set>-<version>/manifest.json       rounds, calls, cost, time
       data/reports/measure_<set>.txt

<version> is a SHA-256 over the candidates version, the asset map version,
the exact questions, the model, the context design, the gate and the
feedback settings. The units a feedback round reads depend on the stored
answers, which are the record, so an unchanged run reads the store and asks
nothing.

  ./wbg measure cra                      sentence context, top 2,000, feedback rounds
  ./wbg measure cra --context paragraph
  ./wbg measure cra --gate unit --asset-min 0.5
  ./wbg measure cra --dry-run            round 0 only, no request
"""
import argparse, csv, hashlib, json, os, sys, time
from collections import defaultdict

import numpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import candidates  # noqa: E402
import classify  # noqa: E402
import commitment  # noqa: E402
import labels as labels_mod  # noqa: E402
import search  # noqa: E402

# The dated snapshot, not the alias: the alias moves when the vendor ships a
# release, and a run must be repeatable. The response names the snapshot that
# served it; a mismatch is reported.
MODEL = "typesafe/jev-1.13-20260917"
ASSET_SET = "assets"
CONTEXTS = ("sentence", "paragraph")
GATES = ("none", "unit", "paragraph", "component")
TOP = 2000            # embedding depth of round 0
BATCH = 500           # units per feedback round
MIN_YIELD = 10        # a round yielding fewer committed units than this is the last
MAX_ROUNDS = 6
UNIT_COLS = ["label_id", "unit_id", "kind", "paragraph_id", "doc_id", "project_ids",
             "component_number", "subcomponent_number", "component_key", "copies", "route",
             "round", "rank", "score", "patterns", "p_commitment", "firmness", "read_order",
             "asset_para", "asset_p_para", "asset_comp", "asset_p_comp",
             "asset_unit", "asset_p_unit", "assets_yes", "sent",
             "grade", "expected", "confidence", "p0", "p1", "p2", "p3", "p4",
             "model_served", "cost", "text"]
# Asset thresholds the report tabulates (nothing is chosen here).
REPORT_THRESHOLDS = (0.3, 0.5, 0.7, 0.9)
ASSET_YES = 0.5       # an asset the unit answer puts at or above this is listed in assets_yes


def questions(labels, scale, context, assets):
    """The measure's graded question(s) and one yes/no question per asset,
    all about the sentence; the paragraph is context when the design says so."""
    where_ = ("the 'sentence' field, read with the 'paragraph' it sits in,"
              if context == "paragraph" else "the 'sentence' field,")
    out = {}
    for lab in labels:
        out[lab["id"]] = {
            "type": "score",
            "instructions": (f"How does the sentence from a World Bank project appraisal "
                             f"document in {where_} relate to {lab['name']}? "
                             f"{lab['name']} means: {lab['definition']}"),
            "criteria": [g.replace("{name}", lab["name"]) for g in scale],
        }
    for a in assets:
        out[f"asset:{a['id']}"] = {
            "type": "noul",
            "instructions": (f"Does the sentence in {where_} concern {a['name']}? "
                             f"{a['name']} means: {a['definition']}"),
            "criteria": {"true": f"The sentence is about {a['name']}, or about infrastructure "
                                 f"or systems of that kind, named or clearly implied.",
                         "false": f"The sentence does not concern {a['name']}."}}
    return out


def check(response, qs, n_grades):
    """Stored only if the graded question has every grade and every yes/no
    question has its probability."""
    if not isinstance(response, dict):
        raise classify.TransportError(f"response is not an object: {str(response)[:200]}")
    answers = response.get("answers") or {}
    for qid, q in qs.items():
        a = answers.get(qid) or {}
        if q["type"] == "score":
            probs = a.get("probabilities") or {}
            if a.get("type") != "score" or sorted(probs) != [str(i) for i in range(n_grades)]:
                raise classify.TransportError(f"answer for {qid!r} is incomplete: {str(a)[:200]}")
        elif a.get("type") != "noul" or not isinstance(a.get("noul"), (int, float)):
            raise classify.TransportError(f"answer for {qid!r} is incomplete: {str(a)[:200]}")


# ------------------------------------------------------------------ the inputs

def asset_map(data):
    """({paragraph_id: (label, p_at_least_2)}, {(doc_id, component_number):
    (label, p)}, run name) from the current assets classification. Empty
    without a run."""
    folder = classify.current_run(ASSET_SET, data)
    if not folder:
        return {}, {}, ""
    para, comp = {}, {}
    with open(os.path.join(folder, "scores.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            p = float(r["p2"] or 0) + float(r["p3"] or 0)
            pid = r["paragraph_id"]
            if p > para.get(pid, ("", -1.0))[1]:
                para[pid] = (r["label_id"], p)
            key = (r["doc_id"], r.get("component_number") or "")
            if key[1] and p > comp.get(key, ("", -1.0))[1]:
                comp[key] = (r["label_id"], p)
    return para, comp, os.path.basename(folder)


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
                 asset_min=0.0, top=TOP, batch=BATCH, min_yield=MIN_YIELD,
                 max_rounds=MAX_ROUNDS, log=print):
        if context not in CONTEXTS or gate not in GATES:
            raise SystemExit(f"measure: --context is one of {CONTEXTS}, --gate one of {GATES}")
        self.name, self.data, self.log = name, data, log
        self.settings = {"model": model, "context": context, "gate": gate,
                         "asset_min": asset_min, "top": top, "batch": batch,
                         "min_yield": min_yield, "max_rounds": max_rounds}
        self.cand = candidates.build(name, data, top=top, log=log)
        self.labels, self.scale = classify.load_labels(name, None)
        self.assets = [a for a in labels_mod.load_set(ASSET_SET) if a["active"] == "true"]
        self.qs = questions(self.labels, self.scale, context, self.assets)
        self.para_assets, self.comp_assets, self.asset_run = asset_map(data)
        if gate in ("paragraph", "component") and not self.asset_run:
            raise SystemExit(f"measure: --gate {gate} needs the asset map: run ./wbg classify "
                             f"{ASSET_SET} first")
        self.paras = paragraph_rows(data)
        self.names = component_names(data)
        run, _ = search.run_folder(name, data)
        _, _, self.index = search.load_run(run, data)
        self.row_of = {r["unit_id"]: i for i, r in enumerate(self.index)}
        self.matrix = numpy.load(where(data, "embeddings", "sentence_emb.npy"), mmap_mode="r")
        self.texts = commitment.unit_texts(data, self.index)
        self.commit = commitment.current_scores(data)
        self.doc_texts = {}
        h = hashlib.sha256()
        h.update(f"{os.path.basename(self.cand)}\t{self.asset_run}\t"
                 f"{json.dumps(self.settings, sort_keys=True)}\n".encode())
        h.update(json.dumps(self.qs, sort_keys=True).encode())
        self.out = where(data, "measures", f"{name}-{h.hexdigest()[:12]}")
        self.root = classify.cache_root(data, model)

    # ---- units

    def base_row(self, r, route, rnd, rank="", score="", patterns=""):
        """A units.csv row for index row r, without the model's answers."""
        para = self.paras[r["paragraph_id"]]
        a_para = self.para_assets.get(r["paragraph_id"], ("", ""))
        a_comp = self.comp_assets.get((r["doc_id"], r.get("component_number") or ""), ("", ""))
        p_commit, firm = self.commit.get(r["unit_id"], (None, ""))
        return {**{c: r.get(c, "") for c in search.CONTEXT}, "label_id": self.labels[0]["id"],
                "route": route, "round": rnd, "rank": rank, "score": score,
                "patterns": patterns,
                "p_commitment": "" if p_commit is None else f"{p_commit:.4f}",
                "firmness": firm, "read_order": "",
                "asset_para": a_para[0],
                "asset_p_para": "" if a_para[1] == "" else f"{a_para[1]:.4f}",
                "asset_comp": a_comp[0],
                "asset_p_comp": "" if a_comp[1] == "" else f"{a_comp[1]:.4f}",
                "asset_unit": "", "asset_p_unit": "", "assets_yes": "", "sent": "false",
                "text": self.texts[r["unit_id"]], "_para": para}

    def candidate_rows(self):
        rows = []
        with open(os.path.join(self.cand, "candidates.csv"), newline="", encoding="utf-8") as fh:
            for c in csv.DictReader(fh):
                r = self.index[self.row_of[c["unit_id"]]]
                row = self.base_row(r, c["route"], 0, c["rank"], c["score"], c["patterns"])
                row["read_order"] = c.get("read_order", "")
                rows.append(row)
        return rows

    def gated(self, row, rnd):
        s = self.settings
        if s["gate"] == "none" or (s["gate"] == "unit" and rnd == 0):
            return True
        p = {"unit": row["asset_p_unit"], "paragraph": row["asset_p_para"],
             "component": row["asset_p_comp"]}[s["gate"]]
        return p != "" and float(p) >= s["asset_min"]

    def body_for(self, row):
        r = row
        para = row["_para"]
        doc = _text(self.doc_texts, self.data, r["doc_id"])
        para_text = doc[int(para["char_start"]):int(para["char_end"])]
        state = state_for(r, para, row["text"], para_text, self.names, self.settings["context"])
        body = classify.request_body(state, self.qs, self.settings["model"])
        if len(json.dumps(body, ensure_ascii=False)) > classify.MAX_REQUEST_CHARS:
            raise SystemExit(f"measure: {r['unit_id']} makes a request over "
                             f"{classify.MAX_REQUEST_CHARS:,} characters")
        return body

    def answers_into(self, row, response):
        lab = self.labels[0]["id"]
        a = response["answers"][lab]
        probs = [float(a["probabilities"][k]) for k in sorted(a["probabilities"], key=int)]
        total = sum(probs) or 1.0
        row.update({"grade": max(range(len(probs)), key=lambda i: probs[i]),
                    "expected": f"{sum(i * q for i, q in enumerate(probs)) / total:.3f}",
                    "confidence": f"{float(a.get('confidence', 0)):.3f}",
                    **{f"p{i}": (f"{probs[i]:.4f}" if i < len(probs) else "") for i in range(5)},
                    "model_served": response.get("model", ""),
                    "cost": f"{float((response.get('usage') or {}).get('cost') or 0):.6f}"})
        ps = {x["id"]: float(response["answers"][f"asset:{x['id']}"]["noul"]) for x in self.assets}
        best = max(ps, key=ps.get) if ps else ""
        row.update({"asset_unit": best, "asset_p_unit": f"{ps[best]:.3f}" if best else "",
                    "assets_yes": "|".join(x for x in ps if ps[x] >= ASSET_YES)})

    # ---- a batch to the model

    def grade(self, rows, rnd, api_key, dry_run, workers):
        """Send the rows that pass the gate; fill their answers from the
        store. Returns (calls, cost, failures)."""
        todo = {}
        for row in rows:
            if not self.gated(row, rnd):
                continue
            row["sent"] = "true"
            body = self.body_for(row)
            sha = classify.request_sha(body)
            row["_sha"] = sha
            if classify.read_cached(self.root, sha) is None:
                todo[sha] = body
        sent = [r for r in rows if r["sent"] == "true"]
        chars = sum(len(json.dumps(b, ensure_ascii=False)) for b in todo.values())
        self.log(f"  round {rnd}: {len(rows):,} units, {len(sent):,} pass the gate, "
                 f"{len(sent) - len(todo):,} stored, {len(todo):,} to ask "
                 f"(about US${chars / 4 / 1e6 * classify.PRICE_PER_MTOK:.3f})")
        if dry_run:
            return 0, 0.0, []
        calls, cost, failures = 0, 0.0, []
        if todo:
            if not api_key:
                raise SystemExit("measure: no credential: set $WBG_OPENROUTER_KEY")
            calls, failures, cost = classify.fetch_missing(
                list(todo.items()), self.root, self.qs, len(self.scale), api_key=api_key,
                log=self.log, workers=workers, check_fn=check)
            if failures:
                return calls, cost, failures
        for row in sent:
            self.answers_into(row, classify.read_cached(self.root, row["_sha"]))
        return calls, cost, []

    # ---- relevance feedback

    def feedback_rows(self, graded, rnd):
        """The next batch: the unseen units nearest the centroid of the
        committed units' vectors, in index order on ties."""
        top = len(self.scale) - 1
        pos = [self.row_of[r["unit_id"]] for r in graded
               if r["sent"] == "true" and int(r["grade"]) == top]
        if not pos:
            return []
        centroid = numpy.asarray(self.matrix[sorted(pos)], dtype="float32").mean(axis=0)
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
            if uid in seen:
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
             + f", top {s['top']}, batch {s['batch']}, min yield {s['min_yield']})",
             f"  candidates {os.path.basename(run.cand)}; asset map {run.asset_run or 'none'}",
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


def measure(name, data, *, api_key="", dry_run=False, log=print, workers=classify.WORKERS, **kw):
    run = Run(name, data, log=log, **kw)
    if os.path.exists(os.path.join(run.out, "manifest.json")):
        log(f"{name}: already written: {run.out}")
        return run.out
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
            log(f"  {len(failures)} request(s) failed; nothing written. Run again to resume.")
            for sha, err in failures[:5]:
                log(f"    {sha[:12]}: {err}")
            return None
        if dry_run:
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
    seconds = time.time() - t0
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
    manifest = dict(run.settings, label_set=name, candidates=os.path.basename(run.cand),
                    asset_map=run.asset_run, version=os.path.basename(run.out).rsplit("-", 1)[1],
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
    return run.out


def current_run(name, data, **kw):
    """The run folder for the set as it stands now, or None."""
    out = Run(name, data, log=lambda *_: None, **kw).out
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
    ap.add_argument("--top", type=int, default=TOP, help="embedding depth of round 0")
    ap.add_argument("--batch", type=int, default=BATCH, help="units per feedback round")
    ap.add_argument("--min-yield", type=int, default=MIN_YIELD,
                    help="a round yielding fewer committed units than this is the last")
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--workers", type=int, default=classify.WORKERS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    import embed
    key = "" if args.dry_run else embed.read_key("")
    out = measure(args.label_set, args.data, api_key=key, dry_run=args.dry_run,
                  workers=args.workers, model=args.model, context=args.context,
                  gate=args.gate, asset_min=args.asset_min, top=args.top, batch=args.batch,
                  min_yield=args.min_yield, max_rounds=args.max_rounds)
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
