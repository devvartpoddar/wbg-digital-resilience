"""The checks made after a classification, through the one asker, each writing
scores (docs/method/decisions.md):

  types            every committed, digital unit x the measure's listed
                   types (and the OTH types the gap step proposed): fit (yes, unsure, no)
  package_assets   every procurement package x its best link.asset_top assets
                   by cosine: how far the package is for the asset, graded 0 to 3
  package_measure  every package x its link.package_measure_top likeliest measures by
                   cosine among those its project has a kept sentence for: does
                   the measure apply to the package alone (the strong link)

Sentence-level questions send the same state as the measures did
(questions.Corpus.state). Nothing here decides; src/wbg_portfolio/decide.py reads the thresholds.
"""
import csv
import hashlib
import json
import os
import random
import re
from collections import defaultdict

import numpy

from wbg_portfolio import api
from wbg_portfolio import ask
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import embed
from wbg_portfolio import funnel
from wbg_portfolio import labels
from wbg_portfolio import panel
from wbg_portfolio import questions as Q
from wbg_portfolio import search
from wbg_portfolio.paths import where

OTH = Q.OTH
MAX_UNITS = 400
PROPOSE_PROMPT = """You are helping an analyst sort sentences from World Bank project appraisal documents.
Every sentence below was judged to commit the project to the measure "{name}", defined as: {definition}

Propose the distinct TYPES of this measure that these sentences show. Rules:
- Between 2 and {max_types} types. Fewer, well-separated types are better than many overlapping ones.
- A type is defined by WHAT is done and FOR WHAT, not by which country or project.
- Each type: a short id (lowercase, underscores), a name of at most six words, a one-sentence definition an analyst could apply to a new sentence, and the numbers of up to three example sentences.
- Do not invent types the sentences do not show. Do not add an "other" type.

Answer with JSON only, no prose: {{"types": [{{"type_id": "...", "type_name": "...", "definition": "...", "examples": [1, 2]}}]}}

Sentences:
{units}
"""
GAP_PROMPT = """You are helping an analyst sort sentences from World Bank project appraisal documents.
Every sentence below was judged to commit the project to the measure "{name}", defined as: {definition}
The measure's existing types are:
{existing}
None of these types fits the sentences below. Propose NEW types that these sentences show and the existing types do not cover. Rules:
- A new type must not restate or overlap an existing type.
- Define a type by WHAT the project does. Do not define it by the asset or facility it concerns (towers, data centres, schools, clinics), by whether it is a project-wide requirement or a single investment, or by another climate measure.
- Stay within the measure's definition; do not widen it.
- Propose a type only if at least {min_units} sentences show it; at most {max_types} types. If none qualifies, answer with an empty list.
- Each type: a short id (lowercase, underscores), a name of at most six words, a one-sentence definition an analyst could apply to a new sentence, and the numbers of up to three example sentences.

Answer with JSON only, no prose: {{"types": [{{"type_id": "...", "type_name": "...", "definition": "...", "examples": [1, 2]}}]}}

Sentences:
{units}
"""


# ---------------------------------------------------------------- shared

def committed_at_ask(conn, corpus, name):
    """[unit_id] a measure commits that the digital question says are digital: the units the
    types are asked about."""
    return sorted(decide.only_digital(conn, [u for u, r in decide.measure_units(conn, corpus, name).items()
                                             if r["committed"]]))


def write_scores(conn, kind, label_set, rows, replace_where=None, params=()):
    t = funnel.table(conn)
    with conn.transaction():
        where_ = "kind = %s AND label_set = %s" + (f" AND {replace_where}" if replace_where else "")
        db.replace(conn, t, funnel.SCORE_COLS, rows, where_, [kind, label_set, *params])
    conn.commit()


def cannot_estimate(conn, dry_run, log, step, *needs, digital=False):
    """On a dry run, say so when a step this one reads has not run: (kind, step name) pairs, and
    with digital the digital step. True when it said so, so the caller returns
    before printing a number (0) that is not an estimate."""
    if not dry_run:
        return False
    needs = needs + ((("digital", "digital"),) if digital else ())
    missing = [name for kind, name in needs if not decide.has_scores(conn, kind)]
    for name in missing:
        log(f"  {step}: cannot estimate until {name} has run")
    return bool(missing)


def write_scoped(conn, kind, label_set, rows, scope=None, replace_where="item_id = ANY(%s)"):
    """write_scores replacing the kind's rows of the set (scope None: all of them) or only the
    rows `replace_where` picks out of the items in scope (a run over named projects replaces
    only its own rows)."""
    if scope is None:
        write_scores(conn, kind, label_set, rows)
    else:
        write_scores(conn, kind, label_set, rows, replace_where, [sorted(scope)])


def _row(kind, label_set, label_id, item, state, q, answer, route="", rnd=0, cosine=None, rank=None, score=None):
    """A scores row. score: the answer's P(yes) of a fit question unless the step names it."""
    score = Q.score(answer, fit=True) if score is None else score
    return {"kind": kind, "label_set": label_set, "label_id": label_id, "item_id": item,
            "state_sha": Q.sha(state), "question_sha": Q.sha(q), "score": round(float(score), 6),
            "answer": {k: v for k, v in answer.items() if k != "served"}, "served": answer.get("served", ""),
            "route": route, "round": rnd, "cosine": None if cosine is None else round(cosine, 6), "rank": rank}


def _ask(conn, model, items, stage, name, api_key, dry_run, workers, log, data):
    got, st = ask.ask(conn, model, items, stage=stage, label_set=name, api_key=api_key, dry_run=dry_run,
                      workers=workers, log=log, data=data)
    log(ask.estimate_line(stage, st))
    if dry_run:
        return None
    skipped = ask.refused_items(st)
    if st["failures"] or len(got) + len(skipped) < len(items):
        log(f"  {len(st['failures'])} request(s) failed; nothing written. Run again to resume.")
        return None
    return got


# ---------------------------------------------------------------- types

def generate(conn, prompt, api_key, dry_run, stage="types:generate", label_set="", log=print):
    """A generative answer to one prompt (a type proposal, a gap proposal, a scratch expansion), asked
    through the chat asker like every chat question: the chat model (second.model), the same answer
    store (keyed on the prompt and the model), the spend cap and ledger, an empty reply asked again. Its
    own request size (chat.GENERATE_REQUEST), so a long proposal is never cut. Returns the text; None on
    a dry run with nothing stored; api.Transport when the request failed or was refused."""
    from wbg_portfolio import chat
    mdl = chat.model()
    state, q = {"prompt": prompt}, {"type": "generate", "model": mdl}
    got, st = chat.ask(conn, [("g", state, q)], stage=stage, label_set=label_set, api_key=api_key,
                       dry_run=dry_run, workers=1, log=log, mdl=mdl, request=chat.GENERATE_REQUEST)
    if "g" in got:
        return got["g"]["text"]
    if dry_run:
        return None
    raise api.Transport("; ".join(st["failures"][:1]) or "the provider refused it or gave no usable reply")


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
    return Q.oth_types(name, data)


def propose(conn, data, corpus, name, *, api_key="", dry_run=False, log=print, max_types=8, projects=None):
    """For a measure with no type list a person wrote: propose one from its
    committed sentences and write it as data/types/<set>_types.csv (the list
    until a person writes analysis/labels/<set>_types.csv). A run over named projects
    proposes nothing (a list is the cohort's, never one project's) and asks nothing."""
    if os.path.exists(os.path.join(labels.folder(name), f"{name}_types.csv")):
        return "person's list"
    if projects:
        log(f"{name}: a run over named projects proposes no types; the list already written is used")
        return None
    labs, _ = Q.load(name)
    units = committed_at_ask(conn, corpus, name)
    if not units:
        log(f"{name}: no committed unit; no types proposed")
        return None
    rng = random.Random(f"{name}:propose")
    chosen = sorted(rng.sample(units, MAX_UNITS)) if len(units) > MAX_UNITS else units
    listing = "\n".join(f"{i}. {' '.join(corpus.texts[u].split())}" for i, u in enumerate(chosen, 1))
    prompt = PROPOSE_PROMPT.format(name=labs[0]["name"], definition=labs[0]["definition"], max_types=max_types,
                                   units=listing)
    try:
        text = generate(conn, prompt, api_key, dry_run, label_set=name, log=log)
    except api.Transport as exc:
        log(f"{name}: proposal request failed ({str(exc)[:120]}); no types proposed")
        return None
    if text is None:
        log(f"{name}: one proposal call to the chat model to make; --dry-run: nothing asked")
        return None
    try:
        found = parse_types(text)
    except api.Transport as exc:
        log(f"{name}: the proposal is not usable ({str(exc)[:120]}); no types proposed")
        return None
    rows = [{"measure_id": name, "type_id": t["type_id"], "type_name": t["type_name"],
             "definition": t["definition"], "active": "true", "source": "proposed"} for t in found]
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
          log=print, projects=None):
    """Every committed, digital unit asked each listed type's fit
    question; the units no listed type reaches at floor.types asked the OTH
    types (re-proposed first when types.gaps is on). Writes kind 'type'. With projects, only
    those projects' sentences are asked and only their rows replaced, and no OTH type is
    proposed (the proposals already written are asked)."""
    tq = Q.type_questions(name)
    units = [u for u in committed_at_ask(conn, corpus, name) if corpus.in_scope(u, projects)]
    scope = {r["unit_id"] for r in corpus.index if corpus.in_scope(r["unit_id"], projects)} if projects else None
    labs, _ = Q.load(name)
    lab = labs[0]
    approved = tq.get(lab["id"], {})
    if not approved:
        log(f"{name}: no types listed (no person's list and no proposed list yet); skipped")
        return None
    items = [(u, corpus.state(u), approved) for u in units]
    log(f"types {name}: {len(units):,} committed units x {len(approved)} types")
    got = _ask(conn, model, items, f"types:{name}", name, api_key, dry_run, workers, log, data)
    if got is None:
        return None
    units = [u for u in units if u in got]               # an item the provider refused is left out
    fl = float(panel.require("floor.types"))
    none = [u for u in units if not any(Q.score(a, fit=True) >= fl for a in got[u].values())]
    oth = oth_types(data, name)
    proposed = None
    if not projects and str(panel.require("types.gaps")).lower() in ("on", "true") and \
            len(none) >= int(panel.require("types.gap_min_units")):
        existing = "\n".join(f"- {t['type_name']}: {t['definition']}"
                             for t in sorted(labels.load_types(name), key=lambda t: t["type_id"]))
        rng = random.Random(f"{name}:gaps")
        chosen = sorted(rng.sample(none, MAX_UNITS)) if len(none) > MAX_UNITS else none
        prompt = GAP_PROMPT.format(
            name=lab["name"], definition=lab["definition"], existing=existing,
            min_units=panel.require("types.gap_min_units"), max_types=5,
            units="\n".join(f"{i}. {' '.join(corpus.texts[u].split())}" for i, u in enumerate(chosen, 1)))
        try:
            text = generate(conn, prompt, api_key, False, label_set=name, log=log)
            found = parse_types(text, allow_empty=True)
        except api.Transport as exc:
            # a request that failed or a reply that is not the JSON asked for proposes nothing: the
            # proposals already written stay, and the stage goes on
            log(f"  {name}: gap proposal not usable ({str(exc)[:120]}); no OTH types proposed this run")
            found = None
        if found is not None:
            taken = {t["type_id"] for t in labels.load_types(name)}
            oth = [{"measure_id": name, "label_id": lab["id"], "type_id": OTH + t["type_id"],
                    "type_name": f"{OTH} {t['type_name']}", "definition": t["definition"], "active": "true",
                    "source": "gap"} for t in found if t["type_id"] not in taken]
            proposed = len(oth)
            path = oth_path(data, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=["measure_id", "label_id", "type_id", "type_name",
                                                   "definition", "active", "source"], lineterminator="\n")
                w.writeheader()
                w.writerows(oth)
            os.replace(path + ".part", path)
    oth_q = {t["type_id"]: Q.type_question(lab, t) for t in oth if t.get("label_id", lab["id"]) == lab["id"]}
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
    write_scoped(conn, "type", name, rows, scope)
    log(f"  {len(rows):,} type scores written; {len(none):,} units no listed type reaches"
        + (f"; {proposed} OTH type(s) proposed" if proposed is not None else
           f"; {len(oth)} OTH type(s) kept from earlier"))
    return len(rows)


# ---------------------------------------------------------------- packages

def load_packages(data, projects=None):
    """The cleaned packages with a description worth asking about."""
    path = where(data, "procurement", "packages.csv")
    if not os.path.exists(path):
        return []
    out, included = [], Q.included_projects()
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["project_id"] not in included or projects and r["project_id"] not in projects or (r.get("is_placeholder") or "").lower() == "true":
                continue
            text = " ".join((r.get("description_clean") or "").split())
            if text:
                out.append({"package_id": r["package_id"], "project_id": r["project_id"], "text": text,
                            "sha": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "category": (r.get("category") or "").strip(),
                            "status": (r.get("status") or "").strip(),
                            "component_number": (r.get("component_number") or "").strip(),
                            "component": " ".join(f"{r.get('component_number') or ''} {r.get('component') or ''}".split())})
    return out


def package_vectors(conn, data, packages, api_key, log):
    """(P, dim) unit vectors of the package texts, embedded once and cached. A description not embedded
    yet is paid for like the sentences' vectors: checked against --max-spend and counted in the ledger
    (stage link:embed)."""
    model, dim = embed.DEFAULT_MODEL, embed.DEFAULT_DIM
    root = embed.cache_root(data, model, dim)
    unique = {p["sha"]: p["text"] for p in packages}
    todo = [s for s in unique if embed.read_cached(root, s, dim) is None]
    if todo:
        if not api_key:
            raise SystemExit(f"link: {len(todo)} package description(s) are not embedded yet and no key is set")
        with api.Spend(conn, "link:embed", "", model) as sp:
            sp.check(sum(len(unique[s]) for s in todo) / 4 / 1e6 * embed.PRICE_PER_MTOK,
                     "estimate from the descriptions' length")
            _, failures = embed.fetch_missing(todo, unique, root, dim, model=model, api_key=api_key,
                                              base_url=embed.DEFAULT_BASE, log=log, spend=sp)
        if failures:
            raise SystemExit(f"link: {len(failures)} package description(s) failed to embed")
    out = numpy.zeros((len(packages), dim), dtype="float32")
    for i, p in enumerate(packages):
        out[i] = numpy.frombuffer(embed.read_cached(root, p["sha"], dim), dtype="<f4")
    return out


def package_assets(conn, data, corpus, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
                   log=print, projects=None):
    """Package to asset, first pass: every package x its best link.asset_top
    assets by cosine, asked of the decision model. Writes kind 'package_asset'
    (label_set assets, label the asset, item the package); with projects, only
    those projects' packages are replaced. Returns (packages, {package: assets
    at link.asset_yes}), or None on a dry run, a failed request or no package."""
    packages = load_packages(data, projects)
    if not packages:
        log("link: no package in scope")
        return None
    vecs = package_vectors(conn, data, packages, api_key, log)
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
                             route="cosine", cosine=cos, rank=rank, score=Q.score(g[a])))
            if Q.score(g[a]) >= yes:
                has[pid].add(a)
    write_scoped(conn, "package_asset", Q.ASSETS, rows, by_pkg if projects else None)
    return packages, has


# ---------------------------------------------------------------- strong links

def measure_cosines(conn, data, packages, names, api_key, log):
    """{measure: (P,) array}: each package's best cosine over the measure's phrases and definition
    (the measure's search queries), from the cached vectors."""
    vecs = package_vectors(conn, data, packages, api_key, log)
    out = {}
    for name in names:
        queries = search.load_queries(name, labels.folder(name))
        qv = search.query_vectors(queries, data, embed.DEFAULT_MODEL, embed.DEFAULT_DIM, embed.DEFAULT_BASE,
                                  api_key, log=log)
        out[name] = (vecs @ qv.T).max(axis=1)
    return out


def package_measure(conn, data, corpus, names, *, model=Q.MODEL, api_key="", dry_run=False,
                    workers=ask.WORKERS, log=print, projects=None):
    """The strong link: every package in scope (placeholders left out), whether or not it has an
    asset, asked on its own about the measures its project has a kept sentence for, but only its
    link.package_measure_top likeliest of them by cosine (the package's description against the measure's
    phrases and definition), and every one the store already answers (nothing paid for is dropped).
    Writes kind 'package_measure' (label_set the measure, item the package)."""
    if cannot_estimate(conn, dry_run, log, "package_measure", ("package_asset", "link"), digital=True):
        return None
    packages = load_packages(data, projects)
    if not packages:
        log("package_measure: no package in scope")
        return 0
    by_pkg = {p["package_id"]: p for p in packages}
    wanted = defaultdict(set)                              # project -> measures with a kept sentence
    for name in names:
        for u in decide.committed_digital_units(conn, corpus, [name]):
            for pid in corpus.projects(u):
                wanted[pid].add(name)
    labs = {name: Q.load(name)[0][0] for name in names}
    qs = {name: {labs[name]["id"]: Q.package_measure_question(labs[name])} for name in names}
    cands = [(i, p, name) for i, p in enumerate(packages) for name in sorted(wanted.get(p["project_id"], ()))]
    have = ask.stored(conn, model, [(Q.sha(Q.package_state(p)), Q.sha(qs[n][labs[n]["id"]])) for _, p, n in cands])
    cos = measure_cosines(conn, data, packages, sorted({n for _, _, n in cands}), api_key, log) if cands else {}
    top = int(panel.require("link.package_measure_top"))
    chosen = defaultdict(list)                             # measure -> packages to ask
    for i, p in enumerate(packages):
        mine = sorted(wanted.get(p["project_id"], ()), key=lambda n: (-float(cos[n][i]), n))
        for rank, name in enumerate(mine):
            if rank < top or (Q.sha(Q.package_state(p)), Q.sha(qs[name][labs[name]["id"]])) in have:
                chosen[name].append(p)
    log(f"package_measure: {sum(len(v) for v in chosen.values()):,} package x measure pair(s) of "
        f"{len(cands):,} (each package's {top} likeliest measures of its project by cosine, and what is stored)")
    asked = {}
    for name in names:
        lab, q = labs[name], qs[name]
        items = [(p["package_id"], Q.package_state(p), q) for p in chosen.get(name, [])]
        got = _ask(conn, model, items, f"package_measure:{name}", name, api_key, dry_run, workers, log, data)
        if got is None:
            if dry_run:
                continue
            return None
        asked[name] = (lab, q, got)
    if dry_run:
        return None
    n, at = 0, {p["package_id"]: i for i, p in enumerate(packages)}
    for name, (lab, q, got) in asked.items():
        rows = [_row("package_measure", name, lab["id"], pid, Q.package_state(by_pkg[pid]), q[lab["id"]],
                     g[lab["id"]], cosine=float(cos[name][at[pid]]))
                for pid, g in got.items()]
        write_scoped(conn, "package_measure", name, rows, by_pkg)
        n += len(rows)
    log(f"  {n:,} package-measure scores written")
    return n
