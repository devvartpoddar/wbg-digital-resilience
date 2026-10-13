#!/usr/bin/env python3
"""Classify every model paragraph against a label set with a decision model.

Search finds candidate sentences; this stage reads the paragraph, where the
work is done. For each narrative and annex paragraph given to the model, one
request asks, for every active label in the set, a graded question: how does
this passage relate to the label? The grades are the label set's scale
(inputs/taxonomy/<set>_scale.csv), from "not about it" to the firmest level,
so for measures the grade is also how firm the commitment is.

The model is a decision model: it returns a probability for each grade, not
text. Default: TypeSafe's Jev, pinned to one version, through OpenRouter's
decisions endpoint with the project's own key (embed.read_key).

Reads  data/appraisal/{paragraphs,components}.csv, data/appraisal/text/
       inputs/taxonomy/<set>.csv, <set>_scale.csv
Writes data/classify/cache/<model>/<aa>/<sha>.json   one stored response per request
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
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import embed  # noqa: E402
from search import TAXONOMY, _active, _rows  # noqa: E402

MODEL = "typesafe/jev-1.13"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
# US$ per million input tokens (output is free), for the dry-run estimate only.
PRICE_PER_MTOK = 0.042
# The endpoint takes 32,000 tokens per request; a request is refused well
# before that rather than truncated.
MAX_REQUEST_CHARS = 60_000
BLOCKS = ("narrative", "annex")
WORKERS = 6
SCORE_COLS = ["paragraph_id", "doc_id", "project_ids", "component_number", "label_id",
              "grade", "expected", "confidence", "p0", "p1", "p2", "p3", "p4",
              "model_served"]


class TransportError(RuntimeError):
    """A request did not yield a usable answer; it is retried on a later run."""


class Refused(RuntimeError):
    """The endpoint refused the request outright (bad key, no credit, bad
    payload): every other request would be refused too, so the run stops."""


# ------------------------------------------------------------------ the labels

def load_labels(name, folder=TAXONOMY):
    """(labels, scale): the active labels [{id, name, definition}] and the
    grade descriptions, lowest first, with {name} standing for the label."""
    rows = _rows(os.path.join(folder, f"{name}.csv"))
    id_col = next(c for c in rows[0] if c.endswith("_id"))
    name_col = next(c for c in rows[0] if c.endswith("_name"))
    labels = [{"id": r[id_col].strip(), "name": r[name_col].strip(),
               "definition": " ".join(r["definition"].split())}
              for r in rows if _active(r)]
    if not labels:
        raise SystemExit(f"classify: {name}.csv has no active label")
    path = os.path.join(folder, f"{name}_scale.csv")
    if not os.path.exists(path):
        raise SystemExit(f"classify: {path} is missing: the grades are a person's "
                         f"judgement and are not assumed")
    scale = [" ".join(r["description"].split())
             for r in sorted(_rows(path), key=lambda r: int(r["grade"]))]
    if not 2 <= len(scale) <= 5:
        raise SystemExit(f"classify: {path} needs 2 to 5 grades")
    return labels, scale


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

def paragraphs(data, blocks=BLOCKS):
    """Model paragraphs with their text (checked against the recorded hash),
    section and component."""
    names = {}
    path = where(data, "appraisal", "components.csv")
    if os.path.exists(path):
        for r in sorted(_rows(path), key=lambda r: r["source"] == "heading"):
            names.setdefault((r["doc_id"], r["number"]), r["name"])
    rows = [r for r in _rows(where(data, "appraisal", "paragraphs.csv"))
            if r.get("for_model") == "true" and r["block"] in blocks]
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

def check(response, qs, n_grades):
    """A response is stored only if every question has a full set of grade
    probabilities."""
    if not isinstance(response, dict):
        raise TransportError(f"response is not an object: {str(response)[:200]}")
    answers = response.get("answers") or {}
    for qid in qs:
        a = answers.get(qid) or {}
        probs = a.get("probabilities") or {}
        if a.get("type") != "score" or sorted(probs) != [str(i) for i in range(n_grades)]:
            raise TransportError(f"answer for {qid!r} is incomplete: {str(a)[:200]}")


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


def fetch_missing(todo, root, qs, n_grades, *, api_key, log, workers=WORKERS):
    """Ask for and store every request in todo [(sha, body)]. Returns
    (stored, failures, cost)."""
    stored, failures, cost = 0, [], 0.0

    def one(sha, body):
        response = decide(body, api_key=api_key)
        check(response, qs, n_grades)
        write_cached(root, sha, response)
        return float((response.get("usage") or {}).get("cost") or 0)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, sha, body): sha for sha, body in todo}
        for n, f in enumerate(as_completed(futures), 1):
            try:
                cost += f.result()
                stored += 1
            except TransportError as exc:
                failures.append((futures[f], str(exc)[:200]))
            except Refused as exc:
                pool.shutdown(wait=True, cancel_futures=True)
                raise SystemExit(f"classify: refused, stopping ({stored:,} stored so far; "
                                 f"run again to resume): {exc}")
            if n % 500 == 0 or n == len(todo):
                log(f"  {n:,}/{len(todo):,} asked, {stored:,} stored, "
                    f"{len(failures)} failed, US${cost:.3f}")
    return stored, failures, cost


# ------------------------------------------------------------------- the run

def version(paras, shas):
    """Over every paragraph id and the SHA-256 of the exact request sent for
    it (model, state and questions), so anything that changes what is asked
    gives a new version."""
    h = hashlib.sha256()
    for p, sha in zip(paras, shas):
        h.update(f"{p['row']['paragraph_id']}:{sha}\n".encode())
    return h.hexdigest()[:12]


def score_rows(p, response, labels):
    out = []
    for lab in labels:
        a = response["answers"][lab["id"]]
        probs = [float(a["probabilities"][k]) for k in sorted(a["probabilities"], key=int)]
        total = sum(probs) or 1.0
        r = p["row"]
        out.append({"paragraph_id": r["paragraph_id"], "doc_id": r["doc_id"],
                    "project_ids": r["project_ids"],
                    "component_number": r.get("component_number", ""),
                    "label_id": lab["id"],
                    "grade": max(range(len(probs)), key=lambda i: probs[i]),
                    "expected": f"{sum(i * q for i, q in enumerate(probs)) / total:.3f}",
                    "confidence": f"{float(a.get('confidence', 0)):.3f}",
                    **{f"p{i}": (f"{probs[i]:.4f}" if i < len(probs) else "")
                       for i in range(5)},
                    "model_served": response.get("model", "")})
    return out


def plan(name, data, *, model=MODEL, taxonomy=TAXONOMY, limit=0):
    """Everything a run would send, without sending it: (labels, scale,
    questions, [(paragraph, request body)], request SHA-256s, run folder)."""
    labels, scale = load_labels(name, taxonomy)
    qs = questions(labels, scale)
    paras = paragraphs(data)
    if limit:
        paras = paras[:limit]
    bodies = [(p, request_body(p["state"], qs, model)) for p in paras]
    for p, body in bodies:
        if len(json.dumps(body, ensure_ascii=False)) > MAX_REQUEST_CHARS:
            raise SystemExit(f"classify: {p['row']['paragraph_id']} makes a request over "
                             f"{MAX_REQUEST_CHARS:,} characters; not truncated")
    shas = [request_sha(b) for _, b in bodies]
    prefix = "smoke_" if limit else ""
    out = where(data, "classify", f"{prefix}{name}-{version(paras, shas)}")
    return labels, scale, qs, bodies, shas, out


def current_run(name, data, taxonomy=TAXONOMY):
    """The run folder for the label set and paragraphs as they stand now, or
    None if that run has not been written."""
    out = plan(name, data, taxonomy=taxonomy)[-1]
    return out if os.path.exists(os.path.join(out, "manifest.json")) else None


def classify(name, data, *, model=MODEL, api_key="", taxonomy=TAXONOMY, limit=0,
             dry_run=False, log=print, workers=WORKERS):
    """Classify the label set; returns the run folder (None on a dry run or
    when requests failed - run again to resume)."""
    labels, scale, qs, bodies, shas, out = plan(name, data, model=model, taxonomy=taxonomy,
                                                limit=limit)
    log(f"{name}: {len(labels)} labels x {len(scale)} grades, {len(bodies):,} paragraphs")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log(f"  already written: {out}")
        return out
    root = cache_root(data, model)
    missing = [(s, b) for s, (_, b) in zip(shas, bodies) if read_cached(root, s) is None]
    todo = list({s: (s, b) for s, b in missing}.values())   # identical paragraphs ask once
    chars = sum(len(json.dumps(b, ensure_ascii=False)) for _, b in todo)
    log(f"  stored already: {len(bodies) - len(missing):,}; to ask: {len(todo):,} "
        f"(about {chars / 4 / 1e6:.1f}M tokens, US${chars / 4 / 1e6 * PRICE_PER_MTOK:.2f})")
    if dry_run:
        log("  --dry-run: no request made, nothing written")
        return None
    failures = []
    if todo:
        if not api_key:
            raise SystemExit(f"classify: no credential: set ${embed.KEY_VAR} or put the "
                             f"key in {embed.KEY_FILE} in the main checkout")
        _, failures, _ = fetch_missing(todo, root, qs, len(scale), api_key=api_key,
                                       log=log, workers=workers)
    if failures:
        log(f"  {len(failures)} request(s) failed; nothing written. Run again to resume.")
        for sha, err in failures[:5]:
            log(f"    {sha[:12]}: {err}")
        return None

    rows, served = [], {}
    for (p, _), sha in zip(bodies, shas):
        response = read_cached(root, sha)
        served[response.get("model", "")] = served.get(response.get("model", ""), 0) + 1
        rows += score_rows(p, response, labels)
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "scores.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SCORE_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    manifest = {"label_set": name, "model_requested": model, "models_served": served,
                "endpoint": ENDPOINT, "labels": [lab["id"] for lab in labels],
                "scale": scale, "paragraphs": len(bodies), "questions": qs,
                "question_sha256": question_shas(qs)}
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
    os.replace(part, out)
    prefix = "smoke_" if limit else ""
    report = where(data, "reports", f"{prefix}classify_{name}.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"{name}: {len(bodies):,} paragraphs x {len(labels)} labels -> {out}\n"
                 f"models served: {served}\n")
        for lab in labels:
            grades = [r["grade"] for r in rows if r["label_id"] == lab["id"]]
            counts = [grades.count(g) for g in range(len(scale))]
            fh.write(f"  {lab['id']:<26} paragraphs by most likely grade: {counts}\n")
    if len(served) > 1:
        log(f"  warning: more than one model snapshot served: {served}")
    log(f"  wrote {out}")
    return out


def question_shas(qs):
    """{label_id: SHA-256 of the exact question asked}: a grade a person gave
    under one wording of a label is not a grade under another."""
    return {k: hashlib.sha256(json.dumps(v, sort_keys=True).encode("utf-8")).hexdigest()
            for k, v in qs.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set", help="assets, measures")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--limit", type=int, default=0, help="first N paragraphs (smoke test)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    key = "" if args.dry_run else embed.read_key("")
    out = classify(args.label_set, args.data, model=args.model, api_key=key,
                   limit=args.limit, dry_run=args.dry_run, workers=args.workers)
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
