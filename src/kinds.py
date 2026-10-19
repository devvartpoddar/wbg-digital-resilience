#!/usr/bin/env python3
"""The kinds of a measure: proposed by a generative model from the committed
units, approved by a person, assigned by the decision model.

Two steps, each repeatable and stored:

  propose   The committed units of the current measure run (src/measure.py)
            go to a generative model in one call, which proposes named types
            with one-line definitions. The call is made --runs times (default
            3) with the units in a different, seeded order each time, because
            one call over-splits or merges by chance; every response is stored
            under the SHA-256 of its request and re-read on a re-run. The
            proposals are written side by side for a person to merge and
            approve into analysis/labels/<set>_types.csv (columns measure_id,
            type_id, type_name, definition, active; a `pattern` column is
            optional and only used by src/candidates.py). Nothing here writes
            that file.

  assign    Each committed unit is put to the decision model with one yes/no
            question per approved type ("Is this sentence an instance of
            <type>?"), never a multiple choice: the decision model shows a
            first-option bias, and a unit may be of several types. A unit no
            type claims at the reading threshold is 'other'; the share of
            'other' is reported, as the signal that the list needs a new
            version. Every response is stored as in src/classify.py.

Reads  data/measures/<set>-<version>/units.csv
       analysis/labels/<set>.csv, <set>_types.csv (assign)
Writes data/types/cache/<model>/<aa>/<sha>.json          stored generative responses
       data/types/<set>-<version>/proposals.csv          run, type_id, type_name, definition
       data/types/<set>-<version>/proposals.md           the same, to read
       data/types/<set>-assign-<version>/assignments.csv unit x type probabilities
       data/reports/types_<set>.txt

  ./wbg types cra              propose (3 runs)
  ./wbg types cra --assign     assign to the approved types
"""
import argparse, csv, hashlib, json, os, random, re, sys, time
from collections import defaultdict

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
import labels  # noqa: E402
import measure  # noqa: E402

# The cheapest capable generative model on the route the project already
# uses. OpenRouter publishes no dated snapshot for it; the response's model
# string is recorded with every stored response.
GEN_MODEL = "anthropic/claude-haiku-5.5"
GEN_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
RUNS = 3
MAX_UNITS = 400          # more than this is sampled by seed, and reported
PROPOSAL_COLS = ["run", "type_id", "type_name", "definition", "example_units"]
ASSIGN_COLS = ["label_id", "unit_id", "paragraph_id", "doc_id", "project_ids",
               "component_number", "model_served", "cost", "types_yes", "best_type", "best_p"]

PROMPT = """You are helping an analyst sort sentences from World Bank project appraisal documents.
Every sentence below was judged to commit the project to the measure "{name}", defined as: {definition}

Propose the distinct KINDS of this measure that these sentences show. Rules:
- Between 2 and {max_types} kinds. Fewer, well-separated kinds are better than many overlapping ones.
- A kind is defined by WHAT is done and FOR WHAT, not by which country or project.
- Each kind: a short id (lowercase, underscores), a name of at most six words, a one-sentence definition an analyst could apply to a new sentence, and the numbers of up to three example sentences.
- Do not invent kinds the sentences do not show. Do not add an "other" kind.

Answer with JSON only, no prose: {{"types": [{{"type_id": "...", "type_name": "...", "definition": "...", "examples": [1, 2]}}]}}

Sentences:
{units}
"""


# ---------------------------------------------------------------- the store

def gen_cache_root(data, model):
    return where(data, "types", "cache", model.replace("/", "__").replace(":", "_"))


def generate(body, *, api_key, endpoint=GEN_ENDPOINT, timeout=180, tries=4):
    """One chat completion. Returns the parsed JSON response."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
               "HTTP-Referer": "https://github.com/devvartpoddar/wbg-digital-resilience",
               "X-Title": "wbg-digital-resilience"}
    last = None
    for attempt in range(tries):
        try:
            r = requests.post(endpoint, json=body, headers=headers, timeout=timeout)
            if r.status_code in (408, 429, 500, 502, 503, 504, 529):
                raise classify.TransportError(f"HTTP {r.status_code}: {r.text[:200]}")
            if 400 <= r.status_code < 500:
                raise classify.Refused(f"HTTP {r.status_code}: {r.text[:500]}")
            r.raise_for_status()
            return r.json()
        except classify.TransportError as exc:
            last = exc
        except requests.RequestException as exc:
            last = classify.TransportError(f"{type(exc).__name__}: {str(exc)[:300]}")
        if attempt < tries - 1:
            time.sleep(2 ** attempt)
    raise last


def parse_types(response):
    """The proposed types from a completion, or a TransportError."""
    try:
        text = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise classify.TransportError(f"no completion in response: {str(response)[:200]}")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise classify.TransportError(f"no JSON in completion: {text[:200]}")
    try:
        types = json.loads(m.group(0))["types"]
    except (ValueError, KeyError, TypeError):
        raise classify.TransportError(f"completion is not the expected JSON: {text[:200]}")
    out = []
    for t in types:
        if not all(k in t for k in ("type_id", "type_name", "definition")):
            raise classify.TransportError(f"a type lacks a field: {t}")
        out.append({"type_id": re.sub(r"[^a-z0-9_]+", "_", str(t["type_id"]).lower()).strip("_"),
                    "type_name": " ".join(str(t["type_name"]).split()),
                    "definition": " ".join(str(t["definition"]).split()),
                    "examples": [int(x) for x in t.get("examples", []) if str(x).isdigit()]})
    if not out:
        raise classify.TransportError("no types proposed")
    return out


# ------------------------------------------------------------------ propose

def committed_units(name, data, **kw):
    """The current measure run's committed units (the top grade), per label."""
    folder = measure.current_run(name, data, **kw)
    if not folder:
        raise SystemExit(f"types: no measure run for {name}; run ./wbg measure {name} first")
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
        top = len(json.load(fh)["scale"]) - 1
    out = defaultdict(list)
    with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
        for u in csv.DictReader(fh):
            if u["sent"] == "true" and int(u["grade"]) == top:
                out[u["label_id"]].append(u)
    return folder, dict(out)


def propose(name, data, *, api_key="", model=GEN_MODEL, runs=RUNS, max_types=8,
            dry_run=False, log=print, **kw):
    run_folder, units = committed_units(name, data, **kw)
    labs = {lab["id"]: lab for lab in labels.load_set(name)}
    root = gen_cache_root(data, model)
    requests_ = []
    for lid, us in sorted(units.items()):
        if len(us) > MAX_UNITS:
            log(f"  {lid}: {len(us):,} committed units; {MAX_UNITS} drawn by seed")
        for k in range(runs):
            rng = random.Random(f"{name}:{lid}:{k}")
            chosen = us if len(us) <= MAX_UNITS else rng.sample(us, MAX_UNITS)
            chosen = list(chosen)
            rng.shuffle(chosen)
            listing = "\n".join(f"{i}. {' '.join(u['text'].split())}"
                                for i, u in enumerate(chosen, 1))
            prompt = PROMPT.format(name=labs[lid]["name"], definition=labs[lid]["definition"],
                                   max_types=max_types, units=listing)
            body = {"model": model, "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}]}
            requests_.append((lid, k, chosen, body, classify.request_sha(body)))
    h = hashlib.sha256()
    h.update(f"{os.path.basename(run_folder)}\t{model}\t{runs}\t{max_types}\n".encode())
    for _, _, _, _, sha in requests_:
        h.update(sha.encode())
    out = where(data, "types", f"{name}-{h.hexdigest()[:12]}")
    log(f"{name}: {sum(len(u) for u in units.values()):,} committed units, "
        f"{len(requests_)} proposal call(s) to {model} -> {out}")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log("  already written")
        return out
    missing = [r for r in requests_ if classify.read_cached(root, r[4]) is None]
    log(f"  stored already: {len(requests_) - len(missing)}; to ask: {len(missing)}")
    if dry_run:
        log("  --dry-run: nothing asked, nothing written")
        return None
    if missing and not api_key:
        raise SystemExit("types: no credential: set $WBG_OPENROUTER_KEY")
    t0 = time.time()
    for lid, k, chosen, body, sha in missing:
        try:
            response = generate(body, api_key=api_key)
            parse_types(response)
        except classify.TransportError as exc:
            log(f"  {lid} run {k}: failed ({exc}); nothing written, run again to resume")
            return None
        classify.write_cached(root, sha, response)
    rows, md = [], [f"# Proposed types for {name}", ""]
    for lid, k, chosen, body, sha in requests_:
        response = classify.read_cached(root, sha)
        md.append(f"## {lid}, run {k} ({response.get('model', '')})\n")
        for t in parse_types(response):
            ex = [chosen[i - 1]["unit_id"] for i in t["examples"] if 0 < i <= len(chosen)]
            rows.append({"run": k, "type_id": t["type_id"], "type_name": t["type_name"],
                         "definition": t["definition"], "example_units": "|".join(ex)})
            md.append(f"- **{t['type_id']}** {t['type_name']}: {t['definition']}")
            for i in t["examples"][:3]:
                if 0 < i <= len(chosen):
                    md.append(f"    - {' '.join(chosen[i - 1]['text'].split())[:240]}")
        md.append("")
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "proposals.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PROPOSAL_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(part, "proposals.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    usage = [classify.read_cached(root, r[4]).get("usage", {}) for r in requests_]
    manifest = {"label_set": name, "measure_run": os.path.basename(run_folder), "model": model,
                "models_served": sorted({classify.read_cached(root, r[4]).get("model", "")
                                         for r in requests_}),
                "runs": runs, "max_types": max_types,
                "units": {lid: len(us) for lid, us in units.items()},
                "tokens_in": sum(u.get("prompt_tokens", 0) for u in usage),
                "tokens_out": sum(u.get("completion_tokens", 0) for u in usage),
                "cost_usd": round(sum(float(u.get("cost") or 0) for u in usage), 6),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    os.replace(part, out)
    classify.record_spend(data, "types-propose", name, model, len(missing),
                          sum(float((classify.read_cached(root, r[4]).get("usage") or {}).get("cost") or 0)
                              for r in missing), time.time() - t0, os.path.basename(out))
    log(f"  {len(rows)} types proposed over {runs} run(s); US${manifest['cost_usd']:.4f}; "
        f"read {os.path.join(out, 'proposals.md')} and write the approved list to "
        f"analysis/labels/{name}_types.csv")
    return out


# ------------------------------------------------------------------- assign

def load_approved(name):
    """The approved types with definitions: {label_id: [{type_id, type_name,
    definition}]}."""
    path = os.path.join(labels.folder(name), f"{name}_types.csv")
    if not os.path.exists(path):
        raise SystemExit(f"types: no approved list {path}; run ./wbg types {name} and "
                         f"write it from the proposals")
    out = defaultdict(list)
    for r in labels.rows(path):
        if not labels.is_active(r) or not (r.get("definition") or "").strip():
            continue
        lid = r[labels._id_col(r, path)].strip()
        out[lid].append({"type_id": r["type_id"].strip(), "type_name": r["type_name"].strip(),
                         "definition": " ".join(r["definition"].split())})
    if not out:
        raise SystemExit(f"types: {path} has no active type with a definition")
    return dict(out)


def assign_questions(types):
    return {t["type_id"]: {
        "type": "noul",
        "instructions": (f"Is the sentence in the 'sentence' field an instance of the kind "
                         f"'{t['type_name']}'? {t['type_name']} means: {t['definition']}"),
        "criteria": {"true": f"The sentence describes {t['type_name']}: {t['definition']}",
                     "false": f"The sentence does not describe {t['type_name']}."}}
        for t in types}


def assign(name, data, *, api_key="", model=measure.MODEL, dry_run=False, log=print,
           workers=classify.WORKERS, read_at=0.5, **kw):
    run_folder, units = committed_units(name, data, **kw)
    approved = load_approved(name)
    bodies, shas, rows = [], [], []
    for lid, us in sorted(units.items()):
        if lid not in approved:
            log(f"  {lid}: no approved types; skipped")
            continue
        qs = assign_questions(approved[lid])
        for u in us:
            body = classify.request_body({"sentence": u["text"]}, qs, model)
            bodies.append(body)
            shas.append(classify.request_sha(body))
            rows.append((lid, u, qs))
    h = hashlib.sha256()
    h.update(f"{os.path.basename(run_folder)}\t{model}\n".encode())
    for (lid, u, _), sha in zip(rows, shas):
        h.update(f"{u['unit_id']}:{sha}\n".encode())
    out = where(data, "types", f"{name}-assign-{h.hexdigest()[:12]}")
    log(f"{name}: {len(rows):,} committed units x their approved types -> {out}")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log("  already written")
        return out
    import answers
    requests_ = [({"sentence": u["text"]}, qs) for (lid, u, qs) in rows]
    got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                          dry_run=True)
    log(f"  stored already: {sum(1 for g in got if g):,}; to ask: {st['to_ask']:,} "
        f"(about US${st['estimate_usd']:.3f})")
    if dry_run:
        log("  --dry-run: nothing asked, nothing written")
        return None
    if st["to_ask"]:
        if not api_key:
            raise SystemExit("types: no credential: set $WBG_OPENROUTER_KEY")
        got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers)
        classify.record_spend(data, "types-assign", name, model, st["calls"], st["cost_usd"],
                              st["seconds"], os.path.basename(out))
        if st["failures"] or not all(got):
            log(f"  {len(st['failures'])} request(s) failed; nothing written. Run again to resume.")
            return None
    out_rows, counts, served, cost = [], defaultdict(lambda: defaultdict(int)), set(), 0.0
    all_types = {lid: [t["type_id"] for t in ts] for lid, ts in approved.items()}
    for (lid, u, qs), g in zip(rows, got):
        response = {"answers": {t: e["answer"] for t, e in g.items()},
                    "model": "|".join(sorted({e["model"] for e in g.values()})),
                    "usage": {"cost": sum(float(e.get("cost_usd") or 0) for e in g.values())}}
        probs = {t: float(response["answers"][t]["noul"]) for t in qs}
        yes = [t for t in all_types[lid] if probs[t] >= read_at]
        best = max(probs, key=probs.get)
        for t in yes:
            counts[lid][t] += 1
        if not yes:
            counts[lid]["other"] += 1
        served.add(response.get("model", ""))
        c = float((response.get("usage") or {}).get("cost") or 0)
        cost += c
        out_rows.append({"label_id": lid, "unit_id": u["unit_id"], "paragraph_id": u["paragraph_id"],
                         "doc_id": u["doc_id"], "project_ids": u["project_ids"],
                         "component_number": u["component_number"],
                         "model_served": response.get("model", ""), "cost": f"{c:.6f}",
                         "types_yes": "|".join(yes), "best_type": best,
                         "best_p": f"{probs[best]:.3f}",
                         **{f"p_{t}": f"{probs[t]:.3f}" for t in all_types[lid]}})
    cols = ASSIGN_COLS + sorted({k for r in out_rows for k in r if k.startswith("p_")})
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "assignments.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(out_rows)
    lines = [f"{name}: {len(out_rows):,} committed units assigned to approved types by "
             f"{model} (read at p >= {read_at}; stored responses US${cost:.4f}; served {sorted(served)})"]
    for lid, ts in sorted(counts.items()):
        n = sum(1 for r in out_rows if r["label_id"] == lid)
        lines.append(f"  {lid}: " + ", ".join(f"{t} {c} ({c / n:.0%})"
                                             for t, c in sorted(ts.items(), key=lambda x: -x[1])))
        for t in all_types[lid]:
            proj = defaultdict(int)
            for r in out_rows:
                if r["label_id"] == lid and t in r["types_yes"].split("|"):
                    for p in r["project_ids"].split("|"):
                        proj[p] += 1
            lines.append(f"    {t}: {len(proj)} projects: " + ", ".join(
                f"{p} {c}" for p, c in sorted(proj.items(), key=lambda x: (-x[1], x[0]))))
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"label_set": name, "measure_run": os.path.basename(run_folder),
                   "model": model, "models_served": sorted(served), "read_at": read_at,
                   "types": all_types, "units": len(out_rows), "cost_usd": round(cost, 6),
                   "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  fh, indent=1, sort_keys=True)
    os.replace(part, out)
    report = where(data, "reports", f"types_{name}.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + f"\n  -> {out}\n")
    for line in lines:
        log(line)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("--assign", action="store_true", help="assign to the approved types")
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--max-types", type=int, default=8)
    ap.add_argument("--read-at", type=float, default=0.5)
    ap.add_argument("--context", default="sentence", choices=measure.CONTEXTS)
    ap.add_argument("--gate", default="none", choices=measure.GATES)
    ap.add_argument("--asset-min", type=float, default=0.0)
    ap.add_argument("--projects", default="", help="comma-separated project ids: the run's scope")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    import embed
    key = "" if args.dry_run else embed.read_key("")
    kw = dict(context=args.context, gate=args.gate, asset_min=args.asset_min,
              projects=classify.scope(args.projects))
    if args.assign:
        out = assign(args.label_set, args.data, api_key=key, dry_run=args.dry_run,
                     read_at=args.read_at, **kw)
    else:
        out = propose(args.label_set, args.data, api_key=key, runs=args.runs,
                      max_types=args.max_types, dry_run=args.dry_run, **kw)
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
