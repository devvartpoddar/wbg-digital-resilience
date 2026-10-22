#!/usr/bin/env python3
"""The kinds of a measure: proposed by a generative model from the committed
units, approved by a person, assigned by the decision model.

Two steps, each repeatable and stored:

  propose   Only for a measure with no list written by a person: the
            committed units of the current measure run (src/measure.py) go to
            a generative model, which proposes named types with one-line
            definitions. The call is made --runs times (default 3) with the
            units in a different, seeded order each time; every response is
            stored under the SHA-256 of its request and re-read on a re-run.
            Once analysis/labels/<set>_types.csv exists the list is the
            person's and this step does nothing (the gap step below finds what
            the list misses).

  assign    Each committed unit is put to the decision model with ONE choice
            question: which of the measure's types it is, or none of them. The
            types are written to exclude each other, so one choice per unit is
            the reading; asked as separate yes/no questions the model said yes
            to every plausible type (2.7 a sentence on climate-informed
            siting, 2026-10-21). On a test of 225 committed units the choice
            came out the same in three option orders for every unit, so it is
            asked in one order.

  gaps      The units the choice puts in "none" (at least OTH_MIN of them)
            go to the generative model with the existing types, which proposes
            only new kinds the list does not cover (not by asset, by scope or
            by another measure, and within the measure's definition). Each is
            named with the prefix "OTH." and assigned straight away by a second
            choice question over the OTH types and none: nothing waits on an
            approval, and a person promotes an OTH type by writing it into
            the list. The OTH types are written to data/types/<set>_oth_types.csv
            and loaded with the approved ones.

Reads  data/measures/<set>-<version>/units.csv
       analysis/labels/<set>.csv, <set>_types.csv (assign)
Writes data/types/cache/<model>/<aa>/<sha>.json          stored generative responses
       data/types/<set>-<version>/proposals.csv          run, type_id, type_name, definition
       data/types/<set>-<version>/proposals.md           the same, to read
       data/types/<set>-assign-<version>/assignments.csv unit x type probabilities
       data/types/<set>-assign-<version>/oth_types.csv   the gap step's OTH types
       data/types/<set>_oth_types.csv                    the current OTH types
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
import common  # noqa: E402
import labels  # noqa: E402
import measure  # noqa: E402
import panel  # noqa: E402
import prior  # noqa: E402

# The cheapest capable generative model on the route the project already
# uses. OpenRouter publishes no dated snapshot for it; the response's model
# string is recorded with every stored response.
GEN_MODEL = "anthropic/claude-haiku-5.5"
GEN_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
RUNS = 3
MAX_UNITS = 400          # more than this is sampled by seed, and reported
PROPOSAL_COLS = ["run", "type_id", "type_name", "definition", "example_units"]
TYPE_NONE = "none"
OTH_PREFIX = "OTH."
OTH_MIN = 5              # fewer "none" units than this are reported, not proposed from
# A sentence takes its most likely type when that type's probability reaches
# this (control panel floor.types; 0 = always), else no type.
TYPE_FLOOR = float(panel.get("floor.types", 0) or 0)
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


GAP_PROMPT = """You are helping an analyst sort sentences from World Bank project appraisal documents.
Every sentence below was judged to commit the project to the measure "{name}", defined as: {definition}
The measure's existing kinds are:
{existing}
None of these kinds fits the sentences below. Propose NEW kinds that these sentences show and the existing kinds do not cover. Rules:
- A new kind must not restate or overlap an existing kind.
- Define a kind by WHAT the project does. Do not define it by the asset or facility it concerns (towers, data centres, schools, clinics), by whether it is a project-wide requirement or a single investment, or by another climate measure.
- Stay within the measure's definition; do not widen it.
- Propose a kind only if at least {min_units} sentences show it; at most {max_types} kinds. If none qualifies, answer with an empty list.
- Each kind: a short id (lowercase, underscores), a name of at most six words, a one-sentence definition an analyst could apply to a new sentence, and the numbers of up to three example sentences.

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


def parse_types(response, allow_empty=False):
    """The proposed types from a completion, or a TransportError. The gap
    step may be answered with an empty list (allow_empty)."""
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
    if not out and not allow_empty:
        raise classify.TransportError("no types proposed")
    return out


# ------------------------------------------------------------------ propose

def committed_units(name, data, **kw):
    """The current measure run's committed units (measure.is_committed: the
    posterior at the measure's floor), per label."""
    folder = measure.current_run(name, data, **kw)
    if not folder:
        raise SystemExit(f"types: no measure run for {name}; run ./wbg measure {name} first")
    floor = panel.floor(name)
    out = defaultdict(list)
    with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
        for u in csv.DictReader(fh):
            if measure.is_committed(u, floor):
                out[u["label_id"]].append(u)
    return folder, dict(out)


def no_run_yet(name, data, dry_run, what, log, **kw):
    """True when this is a dry run and the measure run these steps key on is
    not there. A dry run writes nothing, so the funnel has not written it and
    the estimate cannot be made: it needs the committed units only a real
    measure run holds. Say so and carry on, so the stage's other sets and the
    cross-measure checks still get their estimates; a real run keeps the
    error of committed_units."""
    if not dry_run or measure.current_run(name, data, **kw) is not None:
        return False
    log(f"{name}: no measure run at this version yet, so the {what}'s estimate needs the "
        f"funnel's committed units; nothing asked, nothing written")
    return True


def person_list(name):
    """The type list a person wrote for the set (analysis/labels/<set>_types.csv),
    or None."""
    path = os.path.join(labels.folder(name), f"{name}_types.csv")
    return path if os.path.exists(path) else None


def propose(name, data, *, api_key="", model=GEN_MODEL, runs=RUNS, max_types=8,
            dry_run=False, log=print, **kw):
    if person_list(name):
        log(f"{name}: the type list is a person's ({person_list(name)}); no proposals "
            f"(the gap step of --assign proposes OTH types for what it misses)")
        return "person's list"
    if no_run_yet(name, data, dry_run, "proposal", log, **kw):
        return "no measure run"
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
    missing = [r for r in requests_ if common.read_json(root, r[4]) is None]
    log(f"  stored already: {len(requests_) - len(missing)}; to ask: {len(missing)}")
    if dry_run:
        log("  --dry-run: nothing asked, nothing written")
        return None
    if missing and not api_key:
        raise SystemExit("types: no credential: set $WBG_OPENROUTER_KEY")
    t0 = time.time()

    def one(req):
        lid, k, chosen, body, sha = req
        response = generate(body, api_key=api_key)
        parse_types(response)
        common.write_json(root, sha, response)

    # The proposal calls are independent (a seeded order each): asked side
    # by side, a measure's three take the time of one.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, min(len(missing), 4))) as pool:
        futures = [(req, pool.submit(one, req)) for req in missing]
    failed = []
    for (lid, k, *_), f in futures:
        try:
            f.result()
        except classify.TransportError as exc:
            failed.append(f"{lid} run {k}: {exc}")
    if failed:
        log(f"  {'; '.join(failed)}: failed; nothing written, run again to resume")
        return None
    rows, md = [], [f"# Proposed types for {name}", ""]
    for lid, k, chosen, body, sha in requests_:
        response = common.read_json(root, sha)
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
    # The first run's list is the measure's types until a person writes
    # analysis/labels/<set>_types.csv (labels.load_types reads that first).
    # Overwritten by every proposal run, so the default follows the measure.
    default = where(data, "types", f"{name}_types.csv")
    seen, listed = set(), []
    for r in rows:
        if r["run"] == 0 and r["type_id"] not in seen and r["definition"]:
            seen.add(r["type_id"])
            listed.append({"measure_id": name, "type_id": r["type_id"],
                           "type_name": r["type_name"], "definition": r["definition"],
                           "active": "true", "source": "proposed"})
    with open(default + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["measure_id", "type_id", "type_name", "definition",
                                           "active", "source"], lineterminator="\n")
        w.writeheader()
        w.writerows(listed)
    os.replace(default + ".part", default)
    usage = [common.read_json(root, r[4]).get("usage", {}) for r in requests_]
    manifest = {"label_set": name, "measure_run": os.path.basename(run_folder), "model": model,
                "models_served": sorted({common.read_json(root, r[4]).get("model", "")
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
                          sum(float((common.read_json(root, r[4]).get("usage") or {}).get("cost") or 0)
                              for r in missing), time.time() - t0, os.path.basename(out))
    log(f"  {len(rows)} types proposed over {runs} run(s); US${manifest['cost_usd']:.4f}; "
        f"the first run's {len(listed)} are the list ({default}) until "
        f"analysis/labels/{name}_types.csv says otherwise")
    return out


# ------------------------------------------------------------------- assign

def load_approved(name):
    """The approved types with definitions: {label_id: [{type_id, type_name,
    definition}]}."""
    out = defaultdict(list)
    for t in labels.load_types(name):
        if t["definition"]:
            out[t["label_id"]].append({"type_id": t["type_id"], "type_name": t["type_name"],
                                       "definition": t["definition"]})
    if not out:
        raise SystemExit(f"types: no type list for {name}: neither analysis/labels/"
                         f"{name}_types.csv nor a proposal (run ./wbg types {name} first)")
    return dict(out)


def assign_questions(types):
    """One choice question: which type the unit is, or none of them."""
    crit = {t["type_id"]: f"{t['type_name']}: {t['definition']}" for t in types}
    crit[TYPE_NONE] = "None of these: the sentence is none of these kinds."
    return {"type": {
        "type": "choice",
        "instructions": ("Which one kind best describes what the sentence in the 'sentence' "
                         "field commits the project to? Choose exactly one; choose 'none' if "
                         "no kind fits."),
        "criteria": crit}}


def choice_probs(entry):
    """{option: probability} of a stored choice answer."""
    return {k: float(v) for k, v in entry["answer"]["probabilities"].items()}


def assign_plan(name, data, model=measure.MODEL, log=lambda *_: None, **kw):
    """(run folder, approved, rows [(label, unit, questions)], out folder): what
    an assignment run asks and where it goes, without asking. The folder is
    keyed on the measure run, the model and every request, so a changed
    type list or a new measure run is a new folder."""
    run_folder, units = committed_units(name, data, **kw)
    approved = load_approved(name)
    shas, rows = [], []
    for lid, us in sorted(units.items()):
        if lid not in approved:
            log(f"  {lid}: no approved types; skipped")
            continue
        qs = assign_questions(approved[lid])
        for u in us:
            body = classify.request_body({"sentence": u["text"]}, qs, model)
            shas.append(classify.request_sha(body))
            rows.append((lid, u, qs))
    h = hashlib.sha256()
    h.update(f"{os.path.basename(run_folder)}\t{model}\n".encode())
    h.update(f"prior={prior.VERSION}\nfloor={TYPE_FLOOR}\n".encode())
    for (lid, u, _), sha in zip(rows, shas):
        h.update(f"{u['unit_id']}:{sha}\n".encode())
    return run_folder, approved, rows, where(data, "types", f"{name}-assign-{h.hexdigest()[:12]}")


def current_assign(name, data, **kw):
    """The assignments folder for the set as it stands now, or None: no
    measure run, no approved list, or not yet assigned."""
    try:
        out = assign_plan(name, data, **kw)[3]
    except SystemExit:
        return None
    return out if os.path.exists(os.path.join(out, "manifest.json")) else None


def gaps(name, lid, label, approved, none_units, data, *, api_key, dry_run, log,
         model=GEN_MODEL, max_types=5, min_units=None):
    """(OTH types, spend) proposed from the units no approved type claims.
    One call, stored like a proposal; an empty list is a valid answer."""
    min_units = OTH_MIN if min_units is None else min_units
    if len(none_units) < min_units:
        return [], 0.0
    root = gen_cache_root(data, model)
    rng = random.Random(f"{name}:{lid}:gaps")
    chosen = none_units if len(none_units) <= MAX_UNITS else rng.sample(none_units, MAX_UNITS)
    chosen = sorted(chosen, key=lambda u: u["unit_id"])
    existing = "\n".join(f"- {t['type_name']}: {t['definition']}" for t in approved)
    listing = "\n".join(f"{i}. {' '.join(u['text'].split())}" for i, u in enumerate(chosen, 1))
    prompt = GAP_PROMPT.format(name=label["name"], definition=label["definition"],
                               existing=existing, min_units=min_units, max_types=max_types,
                               units=listing)
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    sha = classify.request_sha(body)
    response = common.read_json(root, sha)
    cost = 0.0
    if response is None:
        if dry_run:
            log(f"  {lid}: {len(none_units)} unit(s) in none; one gap call to {model} to ask")
            return [], 0.0
        if not api_key:
            raise SystemExit("types: no credential: set $WBG_OPENROUTER_KEY")
        response = generate(body, api_key=api_key)
        parse_types(response, allow_empty=True)
        common.write_json(root, sha, response)
        cost = float((response.get("usage") or {}).get("cost") or 0)
    taken = {t["type_id"] for t in approved}
    out = []
    for t in parse_types(response, allow_empty=True):
        tid = OTH_PREFIX + t["type_id"]
        if tid in taken or t["type_id"] in taken:
            continue
        taken.add(tid)
        out.append({"type_id": tid, "type_name": f"{OTH_PREFIX} {t['type_name']}",
                    "definition": t["definition"]})
    return out, cost


def assign(name, data, *, api_key="", model=measure.MODEL, dry_run=False, log=print,
           workers=classify.WORKERS, **kw):
    """Each committed unit's type: one choice over the approved types and
    none; then the gap step (OTH types from the none units, assigned by a
    second choice)."""
    if no_run_yet(name, data, dry_run, "assignment", log, **kw):
        return "no measure run"
    run_folder, approved, rows, out = assign_plan(name, data, model=model, log=log,
                                                  **kw)
    log(f"{name}: {len(rows):,} committed units x one type choice -> {out}")
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
        if not st["to_ask"]:
            _gap_pass(name, approved, rows, got, data, api_key, True, log, model, workers)
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
    oth, oth_got = _gap_pass(name, approved, rows, got, data, api_key, False, log, model, workers)
    if oth is None:
        return None
    out_rows, counts, served, cost = [], defaultdict(lambda: defaultdict(int)), set(), 0.0
    all_types = {lid: [t["type_id"] for t in ts] + [t["type_id"] for t in oth.get(lid, [])]
                 for lid, ts in approved.items()}
    # The prior inputs (src/prior.py): each type's name and definition matched
    # against the sentence, words weighted by rarity. Types have no embedded
    # phrases, so there is no phrase cosine.
    weight = prior.Weights(*prior.word_df(data))
    type_phrases = {lid: {t["type_id"]: prior.phrases([t["type_name"], t["definition"]], weight)
                          for t in ts + oth.get(lid, [])} for lid, ts in approved.items()}
    for i, ((lid, u, qs), g) in enumerate(zip(rows, got)):
        probs = choice_probs(g["type"])
        best = max(probs, key=probs.get)
        entries = [g["type"]]
        if best == TYPE_NONE and i in oth_got:
            second = choice_probs(oth_got[i]["type"])
            entries.append(oth_got[i]["type"])
            b2 = max(second, key=second.get)
            if b2 != TYPE_NONE:
                best, probs = b2, {**{k: v for k, v in probs.items()}, **second}
        yes = [] if best == TYPE_NONE or probs[best] < TYPE_FLOOR else [best]
        counts[lid][best if yes else "other"] += 1
        model_served = "|".join(sorted({e["model"] for e in entries}))
        served.add(model_served)
        c = sum(float(e.get("cost_usd") or 0) for e in entries)
        cost += c
        out_rows.append({"label_id": lid, "unit_id": u["unit_id"], "paragraph_id": u["paragraph_id"],
                         "doc_id": u["doc_id"], "project_ids": u["project_ids"],
                         "component_number": u["component_number"],
                         "model_served": model_served, "cost": f"{c:.6f}",
                         "types_yes": "|".join(yes), "best_type": best if yes else "",
                         "best_p": f"{probs[best]:.3f}" if yes else "",
                         **{f"p_{t}": f"{probs.get(t, 0.0):.3f}" for t in all_types[lid]},
                         **{f"prior_phrase_match_{t}": prior.fmt(prior.phrase_match(
                             set(prior.words(u["text"])), type_phrases[lid].get(t, [])))
                            for t in all_types[lid]}})
    cols = ASSIGN_COLS + sorted({k for r in out_rows for k in r if k.startswith("p_")}) + \
        sorted({k for r in out_rows for k in r if k.startswith("prior_phrase_match_")})
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "assignments.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)
    oth_rows = [{"measure_id": name, "label_id": lid, "type_id": t["type_id"],
                 "type_name": t["type_name"], "definition": t["definition"], "active": "true",
                 "source": "gap"} for lid, ts in sorted(oth.items()) for t in ts]
    oth_cols = ["measure_id", "label_id", "type_id", "type_name", "definition", "active", "source"]
    for path in (os.path.join(part, "oth_types.csv"), where(data, "types", f"{name}_oth_types.csv")):
        with open(path + ".tmp", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=oth_cols, lineterminator="\n")
            w.writeheader()
            w.writerows(oth_rows)
        os.replace(path + ".tmp", path)
    lines = [f"{name}: {len(out_rows):,} committed units, one type each by {model} (choice over "
             f"the approved types and none; OTH types from the none units; stored responses "
             f"US${cost:.4f}; served {sorted(served)})"]
    for lid, ts in sorted(counts.items()):
        n = sum(1 for r in out_rows if r["label_id"] == lid)
        lines.append(f"  {lid}: " + ", ".join(f"{t} {c} ({c / n:.0%})"
                                             for t, c in sorted(ts.items(), key=lambda x: -x[1])))
        for t in oth.get(lid, []):
            lines.append(f"    {t['type_id']}: {t['definition']}")
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
                   "model": model, "models_served": sorted(served), "method": "choice+oth",
                   "types": all_types, "oth_types": oth_rows, "units": len(out_rows),
                   "cost_usd": round(cost, 6),
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


def _gap_pass(name, approved, rows, got, data, api_key, dry_run, log, model, workers):
    """({label: OTH types}, {row index: second choice entries}) for the units
    the first choice put in none; (None, None) when a request failed."""
    import answers
    labs = {lab["id"]: lab for lab in labels.load_set(name)}
    none_by = defaultdict(list)
    for i, ((lid, u, qs), g) in enumerate(zip(rows, got)):
        if g:
            probs = choice_probs(g["type"])
            if max(probs, key=probs.get) == TYPE_NONE:
                none_by[lid].append((i, u))
    oth, spent = {}, 0.0
    for lid, items in sorted(none_by.items()):
        ts, c = gaps(name, lid, labs[lid], approved[lid], [u for _, u in items], data,
                     api_key=api_key, dry_run=dry_run, log=log)
        spent += c
        if ts:
            oth[lid] = ts
    if spent:
        classify.record_spend(data, "types-gaps", name, GEN_MODEL, len(oth), spent, 0.0, "")
    reqs, idx = [], []
    for lid, ts in oth.items():
        qs = assign_questions(ts)
        for i, u in none_by[lid]:
            reqs.append(({"sentence": u["text"]}, qs))
            idx.append(i)
    if not reqs:
        return oth, {}
    got2, st = answers.ask(model, reqs, data, api_key=api_key, log=log, workers=workers,
                           dry_run=dry_run)
    if dry_run:
        log(f"  OTH choice: {st['to_ask']:,} to ask (about US${st['estimate_usd']:.3f})")
        return oth, {}
    if st["calls"]:
        classify.record_spend(data, "types-assign", name, model, st["calls"], st["cost_usd"],
                              st["seconds"], "oth")
    if st["failures"] or not all(got2):
        log(f"  {len(st['failures'])} OTH request(s) failed; nothing written. Run again to resume.")
        return None, None
    return oth, {i: g for i, g in zip(idx, got2)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("--assign", action="store_true", help="assign to the approved types")
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--max-types", type=int, default=8)
    ap.add_argument("--projects", default="", help="comma-separated project ids: the run's scope")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-publish", action="store_true",
                    help="leave Postgres alone (./wbg run publishes once per stage)")
    args = ap.parse_args()
    import embed
    key = "" if args.dry_run else embed.read_key("")
    kw = dict(projects=classify.scope(args.projects))
    if args.assign:
        out = assign(args.label_set, args.data, api_key=key, dry_run=args.dry_run,
                     **kw)
        if out and not (args.dry_run or args.no_publish):
            import store
            store.try_publish(args.data, sets=[args.label_set], projects=kw["projects"])
    else:
        out = propose(args.label_set, args.data, api_key=key, runs=args.runs,
                      max_types=args.max_types, dry_run=args.dry_run, **kw)
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
