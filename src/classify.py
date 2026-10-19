#!/usr/bin/env python3
"""The asset map: grade every model paragraph, footnote and prose table row
against each asset with a decision model.

For each paragraph, one request asks, for every active label in the set, a
graded question: how does this passage relate to the label? The grades are
the label set's scale (inputs/taxonomy/<set>_scale.csv). The model is a
decision model: it returns a probability for each grade, not text. Default:
TypeSafe's Jev, pinned to a dated snapshot, through OpenRouter's decisions
endpoint with the project's own key (embed.read_key). Every answer is stored
per question (src/answers.py), so a changed definition re-asks one question.
With --min-cosine, a label's question goes only to the paragraphs where the
search sees the label.

Reads  data/appraisal/{paragraphs,components}.csv, data/appraisal/text/
       inputs/taxonomy/<set>.csv, <set>_scale.csv
Writes data/classify/answers/<model>/<aa>/<sha>.json one stored answer per question
       data/classify/<set>-<version>/scores.csv      paragraph x label: the
                                                     probability of each grade,
                                                     the expected grade, the
                                                     model's confidence
       data/classify/<set>-<version>/manifest.json
       data/reports/classify_<set>.txt

Repeatable: the model's probabilities move a little from call to call, so a
response is requested once and stored under the SHA-256 of the exact request;
every later run reads the store. The stored responses are the record.
Resumable: an interrupted run asks only for what is not stored.
Versioned: <version> is a SHA-256 over the model, the questions and the
paragraphs; changing a definition, a grade or the text gives a new version.

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
from search import TAXONOMY, _active, _rows  # noqa: E402

MODEL = "typesafe/jev-1.13"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
# US$ per million input tokens (output is free), for the dry-run estimate only.
PRICE_PER_MTOK = 0.042
# Above about 32,000 bytes the endpoint switches to a long-context mode the
# vendor describes as slower and less accurate; a request that would pass
# that is split over several (answers.bodies_for), never truncated.
MAX_REQUEST_CHARS = 30_000
# Narrative and annex prose, every footnote given to the model, and every
# table row the search reads as prose (units.table_units): the climate annex
# tables are where most commitments sit, and an asset map that skipped them
# left those commitments with no asset score at all.
BLOCKS = ("narrative", "annex", "footnote")
TABLE_ROWS = True
# A label's question is put to a paragraph only when a unit of the paragraph
# scores at least this against the label's phrases (0 = ask every label of
# every paragraph). Measured against the full asset map on the 70-project
# corpus: 0.25 asks 58 per cent of the questions and loses 5.8 per cent of
# the links at 0.5 (4.0 per cent at 0.9); 0.30 asks 39 per cent and loses
# 9.0 (5.4). The questions are most of the cost, so this is the dial.
MIN_COSINE = 0.0
WORKERS = 6
SCORE_COLS = ["paragraph_id", "doc_id", "project_ids", "component_number", "label_id",
              "asked", "best_cosine", "grade", "expected", "confidence", "p0", "p1", "p2",
              "p3", "p4", "model_served"]


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
                    calls += int(r["calls"])
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


def questions(labels, scale):
    """The questions object of a request: one graded question per label. The
    question id is never shown to the model, so the label's name and
    definition go in the wording."""
    out = {}
    for lab in labels:
        out[lab["id"]] = {
            "type": "score",
            "instructions": (f"How does this passage from a World Bank project appraisal "
                             f"document relate to {lab['name']}? {lab['name']} means: "
                             f"{lab['definition']}"),
            "criteria": [g.replace("{name}", lab["name"]) for g in scale],
        }
    return out


# -------------------------------------------------------------- the paragraphs

def paragraphs(data, blocks=BLOCKS, table_rows=TABLE_ROWS):
    """Model paragraphs with their text (checked against the recorded hash),
    section and component: the blocks named, plus the table rows the search
    reads as prose when table_rows is set."""
    names = {}
    path = where(data, "appraisal", "components.csv")
    if os.path.exists(path):
        for r in sorted(_rows(path), key=lambda r: r["source"] == "heading"):
            names.setdefault((r["doc_id"], r["number"]), r["name"])
    prose_rows = set()
    if table_rows:
        import units
        prose_rows = {u["paragraph_id"] for u in units.search_units(data)
                      if u["kind"] == "table" and not u["left_out"]}
    rows = [r for r in _rows(where(data, "appraisal", "paragraphs.csv"))
            if (r.get("for_model") == "true" and r["block"] in blocks)
            or r["paragraph_id"] in prose_rows]
    texts, out = {}, []
    for r in rows:
        did = r["doc_id"]
        if did not in texts:
            with open(where(data, "text", f"{did}.txt"), encoding="utf-8") as fh:
                texts[did] = fh.read()
        body = texts[did][int(r["char_start"]):int(r["char_end"])]
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != r["text_sha256"]:
            raise SystemExit(f"classify: {r['paragraph_id']} does not match its recorded "
                             f"hash - re-run src/clean.py first")
        # The sub-component if there is one, else the component: its number
        # and the data sheet's name, or failing that the heading's own title.
        sub = r.get("subcomponent_number") or ""
        comp = sub or r.get("component_number") or ""
        name = names.get((did, comp)) or \
            (r.get("subcomponent_name") if sub else r.get("component_name")) or ""
        state = {"section": r.get("section_title", ""), "passage": body}
        if comp:
            state["component"] = f"{comp} {name}".strip()
        out.append({"row": r, "state": state})
    return out


def request_body(state, qs, model):
    return {"model": model, "state": state, "questions": qs}


def request_sha(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- the store

def cache_root(data, model):
    return where(data, "classify", "cache", model.replace("/", "__").replace(":", "_"))


def cache_path(root, sha):
    return os.path.join(root, sha[:2], sha + ".json")


def read_cached(root, sha):
    try:
        with open(cache_path(root, sha), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_cached(root, sha, response):
    path = cache_path(root, sha)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump(response, fh, ensure_ascii=False, sort_keys=True)
    os.replace(path + ".part", path)


# --------------------------------------------------------------- the transport

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
    """{(label_id, paragraph_id): best unit cosine over the label's queries}
    from the set's current search run, or {} if there is none."""
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
            k = (lab, r["paragraph_id"])
            if b > out.get(k, -2.0):
                out[k] = float(b)
    return out


def score_rows(p, response, labels, cosines=None, asked=None):
    out = []
    for lab in labels:
        r = p["row"]
        cos = (cosines or {}).get((lab["id"], r["paragraph_id"]))
        if asked is not None and lab["id"] not in asked:
            out.append({"paragraph_id": r["paragraph_id"], "doc_id": r["doc_id"],
                        "project_ids": r["project_ids"],
                        "component_number": r.get("component_number", ""),
                        "label_id": lab["id"], "asked": "false",
                        "best_cosine": "" if cos is None else f"{cos:.4f}"})
            continue
        a = response["answers"][lab["id"]]
        probs = [float(a["probabilities"][k]) for k in sorted(a["probabilities"], key=int)]
        total = sum(probs) or 1.0
        r = p["row"]
        out.append({"paragraph_id": r["paragraph_id"], "doc_id": r["doc_id"],
                    "project_ids": r["project_ids"],
                    "component_number": r.get("component_number", ""),
                    "label_id": lab["id"], "asked": "true",
                    "best_cosine": "" if cos is None else f"{cos:.4f}",
                    "grade": max(range(len(probs)), key=lambda i: probs[i]),
                    "expected": f"{sum(i * q for i, q in enumerate(probs)) / total:.3f}",
                    "confidence": f"{float(a.get('confidence', 0)):.3f}",
                    **{f"p{i}": (f"{probs[i]:.4f}" if i < len(probs) else "")
                       for i in range(5)},
                    "model_served": response.get("model", "")})
    return out


def in_scope(row, projects):
    """Whether a row's project_ids meet the scope (None or empty = every project)."""
    return not projects or bool(set(row.get("project_ids", "").split("|")) & set(projects))


def plan(name, data, *, model=MODEL, taxonomy=TAXONOMY, limit=0, min_cosine=MIN_COSINE,
         projects=None):
    """Everything a run would send, without sending it: (labels, scale,
    questions, [(paragraph, request body)], request SHA-256s, run folder,
    cosines). With min_cosine, a paragraph's request holds only the labels
    whose best unit cosine in it reaches the threshold; a paragraph with
    none is not sent. `projects` limits the run to the paragraphs of those
    projects (a cohort set, say); the run is keyed on the paragraphs it
    covers, so a scope is its own run and a wider one later reads the store."""
    labels, scale = load_labels(name, taxonomy)
    qs = questions(labels, scale)
    paras = [p for p in paragraphs(data) if in_scope(p["row"], projects)]
    if limit:
        paras = paras[:limit]
    cosines = best_cosines(name, data, taxonomy) if min_cosine else {}
    if min_cosine and not cosines:
        raise SystemExit(f"classify: --min-cosine needs a search run for {name}; run "
                         f"./wbg search {name} first")
    bodies = []
    for p in paras:
        ask = qs if not min_cosine else {
            lid: q for lid, q in qs.items()
            if cosines.get((lid, p["row"]["paragraph_id"]), 0.0) >= min_cosine}
        bodies.append((p, request_body(p["state"], ask, model)))
    # A paragraph whose questions do not fit one request is asked in several
    # (answers.bodies_for); only a paragraph too long for a single question
    # is refused, never truncated.
    for p, body in bodies:
        one = request_body(p["state"], dict(list(body["questions"].items())[:1]), model)
        if len(json.dumps(one, ensure_ascii=False)) > MAX_REQUEST_CHARS:
            raise SystemExit(f"classify: {p['row']['paragraph_id']} makes a request over "
                             f"{MAX_REQUEST_CHARS:,} characters with one question; not truncated")
    shas = [request_sha(b) for _, b in bodies]
    prefix = "smoke_" if limit else ""
    h = hashlib.sha256(f"min_cosine={min_cosine}\n".encode())
    for p, sha in zip(paras, shas):
        h.update(f"{p['row']['paragraph_id']}:{sha}\n".encode())
    out = where(data, "classify", f"{prefix}{name}-{h.hexdigest()[:12]}")
    return labels, scale, qs, bodies, shas, out, cosines


def _marker(data, name, projects):
    """The file naming the run last written for a set and scope: the stages
    that read a map (the measure, the load) need not know which cosine gate
    it was made with."""
    scope = "all" if not projects else hashlib.sha256(
        "\n".join(sorted(projects)).encode()).hexdigest()[:12]
    return where(data, "classify", "current", f"{name}-{scope}.json")


def current_run(name, data, taxonomy=TAXONOMY, min_cosine=None, projects=None):
    """The run folder for the label set and paragraphs as they stand now, or
    None if that run has not been written. With min_cosine None (the
    default for readers), the gate is the one the last run for this set and
    scope was made with (the marker it wrote); the run is still checked
    against the paragraphs, questions and gate as they stand, so a stale
    marker finds nothing rather than something old."""
    gates = [min_cosine]
    if min_cosine is None:
        gates = [MIN_COSINE]
        marker = _marker(data, name, projects)
        if os.path.exists(marker):
            with open(marker, encoding="utf-8") as fh:
                gates.insert(0, float(json.load(fh).get("min_cosine", MIN_COSINE)))
    for gate in gates:
        try:
            out = plan(name, data, taxonomy=taxonomy, min_cosine=gate, projects=projects)[5]
        except SystemExit:          # a gated plan with no search run to gate on
            continue
        if os.path.exists(os.path.join(out, "manifest.json")):
            return out
    return None


def classify(name, data, *, model=MODEL, api_key="", taxonomy=TAXONOMY, limit=0,
             dry_run=False, log=print, workers=WORKERS, min_cosine=MIN_COSINE, projects=None):
    """Classify the label set; returns the run folder (None on a dry run or
    when requests failed - run again to resume)."""
    labels, scale, qs, bodies, shas, out, cosines = plan(
        name, data, model=model, taxonomy=taxonomy, limit=limit, min_cosine=min_cosine,
        projects=projects)
    n_q = sum(len(b["questions"]) for _, b in bodies)
    log(f"{name}: {len(labels)} labels x {len(scale)} grades, {len(bodies):,} paragraphs, "
        f"{n_q:,} questions"
        + (f" (cosine gate {min_cosine}: {n_q / (len(bodies) * len(labels)):.0%} of all)"
           if min_cosine else ""))
    if os.path.exists(os.path.join(out, "manifest.json")):
        log(f"  already written: {out}")
        return out
    import answers
    requests_ = [(p["state"], b["questions"]) for p, b in bodies if b["questions"]]
    got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                          dry_run=True, legacy_questions=(qs,))
    log(f"  answers stored already for {sum(1 for g in got if g):,} paragraphs; calls to make: "
        f"{st['to_ask']:,} (about {st['chars'] / 4 / 1e6:.1f}M tokens, US${st['estimate_usd']:.2f})")
    if dry_run:
        log("  --dry-run: no request made, nothing written")
        return None
    if st["to_ask"]:
        if not api_key:
            raise SystemExit(f"classify: no credential: set ${embed.KEY_VAR} or put the "
                             f"key in {embed.KEY_FILE} in the main checkout")
        log(f"  spent today before this run: US${spend_today(data)[1]:.2f}")
        got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                              legacy_questions=(qs,))
        record_spend(data, "classify", name, model, st["calls"], st["cost_usd"], st["seconds"],
                     os.path.basename(out))
    if st["failures"] or not all(got):
        log(f"  {len(st['failures'])} request(s) failed; nothing written. Run again to resume.")
        for sha, err in st["failures"][:5]:
            log(f"    {sha[:12]}: {err}")
        return None

    rows, served = [], {}
    it = iter(got)
    for p, b in bodies:
        if not b["questions"]:
            rows += score_rows(p, {"answers": {}}, labels, cosines, asked=set())
            continue
        g = next(it)
        response = {"answers": {qid: e["answer"] for qid, e in g.items()},
                    "model": "|".join(sorted({e["model"] for e in g.values()}))}
        served[response["model"]] = served.get(response["model"], 0) + 1
        rows += score_rows(p, response, labels, cosines,
                           asked=set(b["questions"]) if min_cosine else None)
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "scores.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SCORE_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    manifest = {"label_set": name, "model_requested": model, "models_served": served,
                "min_cosine": min_cosine, "questions_asked": n_q,
                "endpoint": ENDPOINT, "labels": [lab["id"] for lab in labels],
                "scale": scale, "paragraphs": len(bodies), "questions": qs,
                "question_sha256": question_shas(qs)}
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
    os.replace(part, out)
    if not limit:
        marker = _marker(data, name, projects)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker + ".part", "w", encoding="utf-8") as fh:
            json.dump({"run": os.path.basename(out), "min_cosine": min_cosine,
                       "projects": sorted(projects) if projects else None}, fh, indent=1)
        os.replace(marker + ".part", marker)
    prefix = "smoke_" if limit else ""
    report = where(data, "reports", f"{prefix}classify_{name}.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"{name}: {len(bodies):,} paragraphs x {len(labels)} labels -> {out}\n"
                 f"models served: {served}\n")
        for lab in labels:
            grades = [r["grade"] for r in rows if r["label_id"] == lab["id"] and r["asked"] == "true"]
            counts = [grades.count(g) for g in range(len(scale))]
            skipped = sum(1 for r in rows if r["label_id"] == lab["id"] and r["asked"] != "true")
            fh.write(f"  {lab['id']:<26} paragraphs by most likely grade: {counts}"
                     + (f"; not asked (cosine gate): {skipped:,}" if skipped else "") + "\n")
    if len(served) > 1:
        log(f"  warning: more than one model snapshot served: {served}")
    log(f"  wrote {out}")
    return out


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
    ap.add_argument("--limit", type=int, default=0, help="first N paragraphs (smoke test)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-cosine", type=float, default=MIN_COSINE,
                    help="ask a label's question of a paragraph only when a unit of it "
                         "scores at least this against the label's phrases (0 = all)")
    ap.add_argument("--projects", default="",
                    help="comma-separated project ids: only their paragraphs (./wbg classify "
                         "--set digital fills this from the cohort)")
    args = ap.parse_args()
    key = "" if args.dry_run else embed.read_key("")
    out = classify(args.label_set, args.data, model=args.model, api_key=key,
                   limit=args.limit, dry_run=args.dry_run, workers=args.workers,
                   min_cosine=args.min_cosine, projects=scope(args.projects))
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
