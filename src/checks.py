"""The checks made after a classification, each a fit question (yes, unsure,
no) through the one asker, each writing scores (docs/method/decisions.md):

  types            every unit committed at floor.ask x the measure's approved
                   types (and the OTH types the gap step proposed)
  pairs            every unit two measures both commit at floor.ask, each side
                   asked with the other measure's boundary beside it
  package_assets   every procurement package x its best link.asset_top assets
                   by cosine: does the package buy, build or run it
  links            every committed unit x the packages of its project that
                   share an asset, plus the link.measure_top nearest the
                   measure: would carrying out the package deliver it

Sentence-level questions send the same state as the measures did
(questions.Corpus.state). Nothing here decides; src/decide.py reads the floors.
"""
import csv
import hashlib
import json
import os
import random
import re
from collections import defaultdict

import numpy

import api
import ask
import db
import decide
import embed
import funnel
import labels
import panel
import questions as Q
import search
from paths import where

GEN_MODEL = "anthropic/claude-haiku-5.5"
OTH = decide.OTH
MAX_UNITS = 400
PROPOSE_PROMPT = """You are helping an analyst sort sentences from World Bank project appraisal documents.
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


# ---------------------------------------------------------------- shared

def committed_at_ask(conn, corpus, name):
    """[unit_id] a measure counts at floor.ask: the units the checks ask about."""
    return sorted(u for u, r in decide.measure_units(conn, corpus, name).items() if r["asked_yes"])


def write_scores(conn, kind, label_set, rows, replace_where=None, params=()):
    t = funnel.table(conn)
    with conn.transaction():
        where_ = "kind = %s AND label_set = %s" + (f" AND {replace_where}" if replace_where else "")
        db.replace(conn, t, funnel.SCORE_COLS, rows, where_, [kind, label_set, *params])
    conn.commit()


def _row(kind, label_set, label_id, item, state, q, answer, route="", rnd=0, cosine=None, rank=None):
    return {"kind": kind, "label_set": label_set, "label_id": label_id, "item_id": item,
            "state_sha": Q.sha(state), "question_sha": Q.sha(q), "score": round(Q.score(answer, fit=True), 6),
            "answer": {k: v for k, v in answer.items() if k != "served"}, "served": answer.get("served", ""),
            "route": route, "round": rnd, "cosine": None if cosine is None else round(cosine, 6), "rank": rank}


def _ask(conn, model, items, stage, name, api_key, dry_run, workers, log, data):
    got, st = ask.ask(conn, model, items, stage=stage, label_set=name, api_key=api_key, dry_run=dry_run,
                      workers=workers, log=log, data=data)
    log(ask.estimate_line(stage, st))
    if dry_run:
        return None
    if st["failures"] or len(got) < len(items):
        log(f"  {len(st['failures'])} request(s) failed; nothing written. Run again to resume.")
        return None
    return got


# ---------------------------------------------------------------- types

def generate(conn, body, api_key, dry_run):
    """A generative completion, stored in the answer store under its prompt
    (model GEN_MODEL); None on a dry run with nothing stored."""
    state = {"prompt": body["messages"][0]["content"]}
    q = {"type": "generate", "model": body["model"]}
    have = ask.stored(conn, GEN_MODEL, [(Q.sha(state), Q.sha(q))])
    if have:
        return next(iter(have.values()))["text"]
    if dry_run:
        return None
    with api.Spend(conn, "types:generate", "", GEN_MODEL) as spend:
        r = api.post("/v1/chat/completions", body, api.key(True) if not api_key else api_key, timeout=180)
        text = r["choices"][0]["message"]["content"]
        spend.add(float((r.get("usage") or {}).get("cost") or 0))
    ask._store(conn, GEN_MODEL, [(Q.sha(state), Q.sha(q), {"type": "generate", "text": text},
                                   r.get("model", ""), spend.cost, Q.sha(body), None)], {Q.sha(state): state},
               {Q.sha(q): q})
    conn.commit()
    return text


def parse_types(text, allow_empty=False):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        types = json.loads(m.group(0))["types"] if m else None
    except (ValueError, KeyError, TypeError):
        types = None
    if types is None:
        raise api.Transport(f"completion is not the expected JSON: {(text or '')[:200]}")
    out = []
    for t in types:
        if not all(k in t for k in ("type_id", "type_name", "definition")):
            raise api.Transport(f"a type lacks a field: {t}")
        out.append({"type_id": re.sub(r"[^a-z0-9_]+", "_", str(t["type_id"]).lower()).strip("_"),
                    "type_name": " ".join(str(t["type_name"]).split()),
                    "definition": " ".join(str(t["definition"]).split())})
    if not out and not allow_empty:
        raise api.Transport("no types proposed")
    return out


def oth_path(data, name):
    return where(data, "types", f"{name}_oth_types.csv")


def oth_types(data, name):
    path = oth_path(data, name)
    return [t for t in labels.rows(path)] if os.path.exists(path) else []


def propose(conn, data, corpus, name, *, api_key="", dry_run=False, log=print, max_types=8):
    """For a measure with no type list a person wrote: propose one from its
    committed sentences and write it as data/types/<set>_types.csv (the list
    until a person writes analysis/labels/<set>_types.csv)."""
    if os.path.exists(os.path.join(labels.folder(name), f"{name}_types.csv")):
        return "person's list"
    labs, _ = Q.load(name)
    units = committed_at_ask(conn, corpus, name)
    if not units:
        log(f"{name}: no committed unit; no types proposed")
        return None
    rng = random.Random(f"{name}:propose")
    chosen = sorted(rng.sample(units, MAX_UNITS)) if len(units) > MAX_UNITS else units
    listing = "\n".join(f"{i}. {' '.join(corpus.texts[u].split())}" for i, u in enumerate(chosen, 1))
    body = {"model": GEN_MODEL, "temperature": 0, "messages": [{"role": "user", "content": PROPOSE_PROMPT.format(
        name=labs[0]["name"], definition=labs[0]["definition"], max_types=max_types, units=listing)}]}
    text = generate(conn, body, api_key, dry_run)
    if text is None:
        log(f"{name}: one proposal call to {GEN_MODEL} to make; --dry-run: nothing asked")
        return None
    rows = [{"measure_id": name, "type_id": t["type_id"], "type_name": t["type_name"],
             "definition": t["definition"], "active": "true", "source": "proposed"} for t in parse_types(text)]
    path = where(data, "types", f"{name}_types.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(path + ".part", path)
    log(f"{name}: {len(rows)} types proposed -> {path}")
    return path


def types(conn, data, corpus, name, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
          log=print):
    """Every unit committed at floor.ask asked each approved type's fit
    question; the units no approved type reaches at floor.types asked the OTH
    types (re-proposed first when types.gaps is on). Writes kind 'type'."""
    tq = Q.type_questions(name)
    units = committed_at_ask(conn, corpus, name)
    labs, _ = Q.load(name)
    lab = labs[0]
    approved = tq.get(lab["id"], {})
    if not approved:
        log(f"{name}: no approved types; skipped")
        return None
    items = [(u, corpus.state(u), approved) for u in units]
    log(f"types {name}: {len(units):,} committed units x {len(approved)} types")
    got = _ask(conn, model, items, f"types:{name}", name, api_key, dry_run, workers, log, data)
    if got is None:
        return None
    fl = float(panel.require("floor.types"))
    none = [u for u in units if not any(Q.score(a, fit=True) >= fl for a in got[u].values())]
    oth = oth_types(data, name)
    if str(panel.get("types.gaps", "off")).lower() in ("on", "true") and \
            len(none) >= int(panel.require("types.gap_min_units")):
        existing = "\n".join(f"- {t['type_name']}: {t['definition']}" for t in labels.load_types(name))
        rng = random.Random(f"{name}:gaps")
        chosen = sorted(rng.sample(none, MAX_UNITS)) if len(none) > MAX_UNITS else none
        body = {"model": GEN_MODEL, "temperature": 0, "messages": [{"role": "user", "content": GAP_PROMPT.format(
            name=lab["name"], definition=lab["definition"], existing=existing,
            min_units=panel.require("types.gap_min_units"), max_types=5,
            units="\n".join(f"{i}. {' '.join(corpus.texts[u].split())}" for i, u in enumerate(chosen, 1)))}]}
        text = generate(conn, body, api_key, False)
        taken = {t["type_id"] for t in labels.load_types(name)}
        oth = [{"measure_id": name, "label_id": lab["id"], "type_id": OTH + t["type_id"],
                "type_name": f"{OTH} {t['type_name']}", "definition": t["definition"], "active": "true",
                "source": "gap"} for t in parse_types(text, allow_empty=True) if t["type_id"] not in taken]
        path = oth_path(data, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["measure_id", "label_id", "type_id", "type_name",
                                               "definition", "active", "source"], lineterminator="\n")
            w.writeheader()
            w.writerows(oth)
        os.replace(path + ".part", path)
    oth_q = {t["type_id"]: Q.fit_question({"id": t["type_id"], "name": t["type_name"],
                                           "definition": t["definition"]},
                                          given=(f"The sentence is already counted as a commitment to "
                                                 f"{lab['name']}; this asks only which kind it is. "))
             for t in oth}
    got2 = {}
    if oth_q and none:
        got2 = _ask(conn, model, [(u, corpus.state(u), oth_q) for u in none], f"types:{name}:oth", name,
                    api_key, False, workers, log, data)
        if got2 is None:
            return None
    rows = []
    for u in units:
        for tid, a in got[u].items():
            rows.append(_row("type", name, tid, u, corpus.state(u), approved[tid], a))
        for tid, a in got2.get(u, {}).items():
            rows.append(_row("type", name, tid, u, corpus.state(u), oth_q[tid], a))
    write_scores(conn, "type", name, rows)
    log(f"  {len(rows):,} type scores written; {len(none):,} units no approved type reaches")
    return len(rows)


# ---------------------------------------------------------------- pairs

def pairs(conn, data, corpus, names, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
          log=print):
    """A unit two measures both commit at floor.ask is asked, for each side,
    whether the measure applies with the other's boundary beside it. Writes
    kind 'pair', label_set 'a|b', label_id keep_a / keep_b."""
    by_unit = defaultdict(set)
    for name in names:
        for u in committed_at_ask(conn, corpus, name):
            by_unit[u].add(name)
    items, qs = [], {}
    from itertools import combinations
    for u in sorted(by_unit):
        for a, b in combinations(sorted(by_unit[u]), 2):
            qs.setdefault((a, b), Q.pair_questions(a, b))
            items.append(((u, a, b), corpus.state(u), qs[(a, b)]))
    log(f"pairs: {len(items):,} unit pair(s) two measures both commit")
    got = _ask(conn, model, items, "pairs", "", api_key, dry_run, workers, log, data)
    if got is None:
        return None
    rows = [_row("pair", f"{a}|{b}", side, u, corpus.state(u), qs[(a, b)][side], ans)
            for (u, a, b), g in got.items() for side, ans in g.items()]
    t = funnel.table(conn)
    with conn.transaction():
        db.replace(conn, t, funnel.SCORE_COLS, rows, "kind = 'pair'")
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------- packages

def load_packages(data, projects=None):
    """The cleaned packages with a description worth asking about."""
    path = where(data, "procurement", "packages.csv")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if projects and r["project_id"] not in projects or (r.get("is_placeholder") or "").lower() == "true":
                continue
            text = " ".join((r.get("description_clean") or "").split())
            if text:
                out.append({"package_id": r["package_id"], "project_id": r["project_id"], "text": text,
                            "sha": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "component": " ".join(f"{r.get('component_number') or ''} {r.get('component') or ''}".split())})
    return out


def package_vectors(data, packages, api_key, log):
    """(P, dim) unit vectors of the package texts, embedded once and cached."""
    model, dim = embed.DEFAULT_MODEL, embed.DEFAULT_DIM
    root = embed.cache_root(data, model, dim)
    unique = {p["sha"]: p["text"] for p in packages}
    todo = [s for s in unique if embed.read_cached(root, s, dim) is None]
    if todo:
        if not api_key:
            raise SystemExit(f"link: {len(todo)} package description(s) are not embedded yet and no key is set")
        _, failures = embed.fetch_missing(todo, unique, root, dim, model=model, api_key=api_key,
                                          base_url=embed.DEFAULT_BASE, log=log)
        if failures:
            raise SystemExit(f"link: {len(failures)} package description(s) failed to embed")
    out = numpy.zeros((len(packages), dim), dtype="float32")
    for i, p in enumerate(packages):
        out[i] = numpy.frombuffer(embed.read_cached(root, p["sha"], dim), dtype="<f4")
    return out


def links(conn, data, corpus, names, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
          log=print, projects=None):
    """Package to asset, then commitment to package. Writes kinds
    'package_asset' (label_set assets, label the asset, item the package) and
    'link' (label_set the measure, item 'unit|package')."""
    packages = load_packages(data, projects)
    if not packages:
        log("link: no package in scope")
        return None
    vecs = package_vectors(data, packages, api_key, log)
    aq = Q.package_asset_questions()
    queries = search.load_queries(Q.ASSETS, labels.folder(Q.ASSETS))
    qv = search.query_vectors(queries, data, embed.DEFAULT_MODEL, embed.DEFAULT_DIM, embed.DEFAULT_BASE,
                              api_key, log=log)
    sims = vecs @ qv.T
    ids = sorted(aq)
    best = numpy.full((len(packages), len(ids)), -1.0, dtype="float32")
    for j, q in enumerate(queries):
        if q["label_id"] in ids:
            k = ids.index(q["label_id"])
            best[:, k] = numpy.maximum(best[:, k], sims[:, j])
    top = int(panel.require("link.asset_top"))
    cands = {}
    for i, p in enumerate(packages):
        order = numpy.argsort(-best[i], kind="stable")[:top]
        cands[p["package_id"]] = [(ids[k], float(best[i, k]), r + 1) for r, k in enumerate(order)]
    items = [(p["package_id"], Q.package_state(p), {a: aq[a] for a, _, _ in cands[p["package_id"]]})
             for p in packages]
    log(f"link: {len(packages):,} packages x {top} assets")
    got = _ask(conn, model, items, "link:assets", Q.ASSETS, api_key, dry_run, workers, log, data)
    if got is None:
        return None
    by_pkg = {p["package_id"]: p for p in packages}
    rows, has = [], defaultdict(set)
    yes = float(panel.require("link.asset_yes"))
    for pid, g in got.items():
        for a, cos, rank in cands[pid]:
            rows.append(_row("package_asset", Q.ASSETS, a, pid, Q.package_state(by_pkg[pid]), aq[a], g[a],
                             route="cosine", cosine=cos, rank=rank))
            if Q.score(g[a], fit=True) >= yes:
                has[pid].add(a)
    write_scores(conn, "package_asset", Q.ASSETS, rows)
    # commitment to package
    amap = decide.asset_map(conn, corpus)
    ua = decide.unit_assets(corpus, amap)
    levels = decide.link_levels()
    by_project = defaultdict(list)
    for i, p in enumerate(packages):
        by_project[p["project_id"]].append(i)
    mtop = int(panel.require("link.measure_top"))
    pairs_, meta = [], {}
    for name in names:
        units = committed_at_ask(conn, corpus, name)
        if not units:
            continue
        mq = search.query_vectors(search.load_queries(name, labels.folder(name)), data, embed.DEFAULT_MODEL,
                                  embed.DEFAULT_DIM, embed.DEFAULT_BASE, api_key, log=log)
        mbest = (vecs @ mq.T).max(axis=1)
        lid = Q.load(name)[0][0]["id"]
        for u in units:
            mine = decide.linked_assets(ua.get(u, ({}, [], "none")), levels)
            paired = set()
            for pid in corpus.projects(u):
                for i in by_project.get(pid, []):
                    p = packages[i]
                    if mine & has.get(p["package_id"], set()):
                        paired.add(i)
                        meta[(name, u, p["package_id"])] = ("asset", None, None)
                if mtop:
                    n = 0
                    for i in sorted(by_project.get(pid, []), key=lambda i: (-mbest[i], packages[i]["package_id"])):
                        if i in paired:
                            continue
                        n += 1
                        if n > mtop:
                            break
                        paired.add(i)
                        meta[(name, u, packages[i]["package_id"])] = ("measure", n, float(mbest[i]))
            for i in sorted(paired):
                p = packages[i]
                pairs_.append(((name, lid, u, p["package_id"]),
                               Q.pair_state(corpus.texts[u], corpus.component(u), p), {"link": Q.LINK_QUESTION}))
    log(f"  {len(pairs_):,} commitment-package pairs")
    got2 = _ask(conn, model, pairs_, "link:pairs", "", api_key, dry_run, workers, log, data)
    if got2 is None:
        return None
    rows = []
    for (name, lid, u, pid), g in got2.items():
        route, rank, cos = meta[(name, u, pid)]
        rows.append(_row("link", name, lid, f"{u}|{pid}", {}, Q.LINK_QUESTION, g["link"], route=route,
                         cosine=cos, rank=rank))
    t = funnel.table(conn)
    with conn.transaction():
        db.replace(conn, t, funnel.SCORE_COLS, rows, "kind = 'link'")
    conn.commit()
    log(f"  {len(rows):,} link scores written")
    return len(rows)
