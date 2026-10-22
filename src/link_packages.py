#!/usr/bin/env python3
"""Which digital asset a procurement package buys, and which commitments it
would carry out: the semantic layer between the appraisal document and the
procurement plan (./wbg link).

The plan's component column reaches about a fifth of the paragraphs the
model read, and a commitment ("resilience measures for the government
network") and the package that delivers it ("GovNet phase 2: supply and
installation of core equipment") never share a component number reliably.
So the link is made on meaning, in two steps, both repeatable, scored and
stored like every other decision here (docs/methodology.md, section 5e):

  1. Package -> asset. Every package description is embedded once (the same
     model and cache as the sentences) and ranked against the asset
     taxonomy's phrases; its best ASSET_TOP assets by cosine go to the
     decision model, one yes/no each ("does this package buy, build,
     install, connect or operate fiber networks? Fiber networks means: ...").
     Every answer is kept, with its cosine rank, so the report can say how
     many yes answers sat at the last rank: the measure of whether ASSET_TOP
     is deep enough.

  2. Commitment <-> package, many to many. For every measure run, each
     committed unit (measure.is_committed: the posterior at the floor) is
     paired with the packages of its project that share an asset with the
     unit's own asset (assets_yes, else its linked asset). Each pair goes to
     the decision model with one yes/no question: would carrying out this
     package be where this commitment is delivered or applied? A commitment
     may link to several packages and a package to several commitments.

Nothing is cut here: the probabilities are stored and a dashboard chooses
its threshold; YES (control panel link.asset_yes) is only the reading threshold the report counts at
and the pair step uses to say which assets a package "has".

  data/links/packages-<version>/package_assets.csv
      package_id, project_id, label_id, cosine, cosine_rank, probability,
      model_served, cost
  data/links/packages-<version>/commitment_packages.csv
      label_set, run, label_id, unit_id, package_id, project_id,
      shared_asset_ids, probability, model_served, cost
  data/links/packages-<version>/manifest.json
  data/reports/link_packages.txt

<version> is a SHA-256 over LINK_VERSION, the packages table, the asset
taxonomy's queries and definitions, the questions, the model, ASSET_TOP and
the measure runs linked; the answers themselves are in the store
(src/answers.py), so an unchanged run asks nothing.
"""
import argparse, csv, hashlib, json, os, sys, time
from collections import defaultdict

import numpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
import embed  # noqa: E402
import labels  # noqa: E402
import measure  # noqa: E402
import panel  # noqa: E402
import prior  # noqa: E402
import search  # noqa: E402

LINK_VERSION = "4"   # 3: pairs by a shared asset only, the asset groups removed (2026-10-21);
                     # 4: the prior inputs stored (src/prior.py, 2026-10-22)
MODEL = measure.MODEL
ASSET_SET = "assets"
ASSET_TOP = int(panel.get("link.asset_top", 4))  # assets asked per package: its best by cosine
# Packages also paired by the measure itself (Dev, 2026-10-20): a climate
# risk assessment consultancy or works to raise tower sites buys no digital
# asset, so the asset route never pairs them. For each committed unit, its
# project's packages nearest the measure's phrases and definition (cosine
# over the stored embeddings), not already paired by asset, up to this many.
MEASURE_TOP = int(panel.get("link.measure_top", 3))  # control panel
YES = float(panel.get("link.asset_yes", 0.5))  # an asset at or above it is one the package "has"
# The prior inputs (src/prior.py) a package has: the cosine to the label's
# phrases and the phrase match weighted by word rarity. A package is no
# sentence, so the paragraph counts do not apply.
PRIOR_COLS = ["prior_phrase_cosine", "prior_phrase_match"]
ASSET_COLS = ["package_id", "project_id", "label_id", "cosine", "cosine_rank", "probability",
              "model_served", "cost", *PRIOR_COLS]
PAIR_COLS = ["label_set", "run", "label_id", "unit_id", "package_id", "project_id",
             "shared_asset_ids", "route", "measure_rank", "measure_cosine",
             "probability", "model_served", "cost", *PRIOR_COLS]


# ------------------------------------------------------------------ questions

def asset_questions(assets):
    """One yes/no per active asset, of a package description."""
    return {a["id"]: {
        "type": "noul",
        "instructions": (f"Does the procurement package in the 'package' field buy, build, "
                         f"install, connect, operate or design {a['name']}? {a['name']} "
                         f"means: {a['definition']}"),
        "criteria": {"true": f"Yes: what the package procures is {a['name']}, or equipment, "
                             f"works, systems or services for infrastructure of that kind, "
                             f"named or clearly implied.",
                     "false": f"No: the package procures something else."}}
        for a in assets}


PAIR_QUESTION = {
    "type": "noul",
    "instructions": ("Would carrying out the procurement package in the 'package' field be "
                     "where the commitment in the 'commitment' field is delivered or "
                     "applied? Both come from the same World Bank project: the commitment "
                     "from its appraisal document, the package from its procurement plan."),
    "criteria": {"true": "Yes: the package procures, builds, installs or contracts the asset, "
                         "works, equipment, system or service the commitment concerns, or the "
                         "study or service the commitment itself requires, so the commitment "
                         "applies to what the package delivers.",
                 "false": "No: the package concerns something else, or the connection is "
                          "only that both belong to the same project."}}


# ------------------------------------------------------------------- inputs

def load_packages(data, projects=None):
    """The cleaned packages in scope that have a description worth asking
    about: [{package_id, project_id, text, sha, component}]."""
    path = where(data, "procurement", "packages.csv")
    if not os.path.exists(path):
        raise SystemExit(f"link: {path} not found; run the procurement stages first")
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if projects and r["project_id"] not in projects:
                continue
            if (r.get("is_placeholder") or "").lower() == "true":
                continue
            text = " ".join((r.get("description_clean") or "").split())
            if not text:
                continue
            comp = " ".join(f"{r.get('component_number') or ''} {r.get('component') or ''}".split())
            out.append({"package_id": r["package_id"], "project_id": r["project_id"],
                        "text": text, "sha": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "component": comp})
    return out


def package_state(p):
    state = {"package": p["text"]}
    if p["component"]:
        state["component"] = p["component"]
    return state


def pair_state(unit, p):
    state = {"commitment": unit["text"], "package": p["text"]}
    comp = " ".join(f"{unit.get('subcomponent_number') or unit.get('component_number') or ''}".split())
    if comp:
        state["commitment_component"] = comp
    if p["component"]:
        state["package_component"] = p["component"]
    return state


def committed_units(data, projects=None, sets=None):
    """{label_set: (run folder name, [committed unit rows])} for every
    measure set with a current run over the scope (measure.is_committed)."""
    out = {}
    for name in sorted(sets or labels.label_sets()):
        if name == ASSET_SET:
            continue
        try:
            folder = measure.current_run(name, data, projects=projects)
        except SystemExit:
            continue
        if not folder:
            continue
        floor = panel.floor(name)
        rows = []
        with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
            for u in csv.DictReader(fh):
                if measure.is_committed(u, floor):
                    rows.append(u)
        out[name] = (os.path.basename(folder), rows)
    return out


def unit_assets(u):
    """The assets a committed unit names: its yes answers, else its link."""
    ids = {x for x in (u.get("assets_yes") or "").split("|") if x}
    if not ids and u.get("asset_link"):
        ids.add(u["asset_link"])
    return ids


# ------------------------------------------------------------------- the plan

class Plan:
    """Everything one link run needs, computed without asking anything."""

    def __init__(self, data, *, model=MODEL, asset_top=ASSET_TOP, projects=None, sets=None,
                 dim=embed.DEFAULT_DIM, log=print):
        self.data, self.log, self.dim = data, log, dim
        self.settings = {"model": model, "asset_top": asset_top, "yes": YES,
                         "measure_top": MEASURE_TOP, "link_version": LINK_VERSION}
        self.packages = load_packages(data, projects)
        self.assets = [a for a in labels.load_set(ASSET_SET) if a["active"] == "true"]
        self.queries = search.load_queries(ASSET_SET, labels.folder(ASSET_SET))
        self.asset_qs = asset_questions(self.assets)
        self.runs = committed_units(data, projects, sets)
        h = hashlib.sha256()
        h.update(f"{LINK_VERSION}\t{model}\t{asset_top}\t{MEASURE_TOP}\t{YES}\n".encode())
        for name in sorted(self.runs):
            for q in search.load_queries(name, labels.folder(name)):
                h.update(f"{name}\t{q['query_id']}\t{q['text']}\n".encode())
        pk = where(data, "procurement", "packages.csv")
        h.update(hashlib.sha256(open(pk, "rb").read()).hexdigest().encode())
        for q in self.queries:
            h.update(f"{q['query_id']}\t{q['text']}\n".encode())
        h.update(json.dumps(self.asset_qs, sort_keys=True).encode())
        h.update(json.dumps(PAIR_QUESTION, sort_keys=True).encode())
        for name, (run, _) in sorted(self.runs.items()):
            h.update(f"{name}\t{run}\n".encode())
        if projects:
            h.update(("\n".join(sorted(projects))).encode())
        self.version = h.hexdigest()[:12]
        self.out = where(data, "links", f"packages-{self.version}")

    # ---- step 1: the assets each package is asked about

    def package_vectors(self, api_key):
        """(P, dim) unit vectors for the package texts, from the cache or
        embedded now (and cached)."""
        model, dim = embed.DEFAULT_MODEL, self.dim
        root = embed.cache_root(self.data, model, dim)
        unique = {p["sha"]: p["text"] for p in self.packages}
        todo = [s for s in unique if embed.read_cached(root, s, dim) is None]
        if todo:
            self.log(f"  {len(todo):,} package description(s) to embed "
                     f"(about US${len(todo) * 40 / 1e6 * embed.PRICE_PER_MTOK:.3f})")
            if not api_key:
                raise SystemExit("link: package descriptions are not embedded yet and "
                                 f"${embed.KEY_VAR} is not set")
            _stats, failures = embed.fetch_missing(todo, unique, root, dim, model=model,
                                                   api_key=api_key, base_url=embed.DEFAULT_BASE,
                                                   log=self.log)
            if failures:
                raise SystemExit(f"link: {len(failures)} package description(s) failed to embed")
        out = numpy.zeros((len(self.packages), dim), dtype="float32")
        for i, p in enumerate(self.packages):
            out[i] = numpy.frombuffer(embed.read_cached(root, p["sha"], dim), dtype="<f4")
        return out

    def asset_candidates(self, api_key):
        """[(package, [(asset_id, cosine, rank)])]: each package's best
        asset_top assets by cosine over the taxonomy's phrases."""
        vecs = self.vecs = self.package_vectors(api_key)
        qv = search.query_vectors(self.queries, self.data, embed.DEFAULT_MODEL, self.dim,
                                  embed.DEFAULT_BASE, api_key, log=self.log)
        scores = vecs @ qv.T                                   # (P, Q)
        ids = [a["id"] for a in self.assets]
        best = numpy.full((len(self.packages), len(ids)), -1.0, dtype="float32")
        col = {a: i for i, a in enumerate(ids)}
        for j, q in enumerate(self.queries):
            if q["label_id"] in col:
                k = col[q["label_id"]]
                best[:, k] = numpy.maximum(best[:, k], scores[:, j])
        out = []
        for i, p in enumerate(self.packages):
            order = numpy.argsort(-best[i])[:self.settings["asset_top"]]
            out.append((p, [(ids[k], float(best[i, k]), r + 1) for r, k in enumerate(order)]))
        return out


# --------------------------------------------------------------------- run

def link(data, *, api_key="", dry_run=False, log=print, workers=classify.WORKERS, **kw):
    import answers
    plan = Plan(data, log=log, **kw)
    model = plan.settings["model"]
    log(f"link: {len(plan.packages):,} packages, {len(plan.assets)} assets, "
        f"{len(plan.runs)} measure run(s) -> {plan.out}")
    missing = sorted(set(kw.get("sets") or labels.label_sets()) - set(plan.runs) - {ASSET_SET})
    if missing:
        # A dry run writes no measure run, so after a measure changes its pairs
        # cannot be estimated until the funnel has run: say so, not "0 pairs".
        log(f"  no measure run at this version for {', '.join(missing)}: their pairs are "
            f"{'not estimated (a dry run writes no measure run)' if dry_run else 'not linked'}")
    if os.path.exists(os.path.join(plan.out, "manifest.json")):
        log("  already written")
        return plan.out
    if not plan.packages:
        raise SystemExit("link: no package in scope")
    t0 = time.time()
    # Step 1: package -> asset.
    cands = plan.asset_candidates(api_key)
    requests_ = [(package_state(p), {a: plan.asset_qs[a] for a, _, _ in top}) for p, top in cands]
    got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                          dry_run=True)
    log(f"  assets: {len(requests_):,} packages x {plan.settings['asset_top']} questions; "
        f"to ask: {st['to_ask']:,} (about US${st['estimate_usd']:.3f})")
    if dry_run:
        log("  --dry-run: nothing asked, nothing written")
        return None
    calls, cost = 0, 0.0
    if st["to_ask"]:
        if not api_key:
            raise SystemExit("link: no credential: set $WBG_OPENROUTER_KEY")
        got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers)
        calls, cost = st["calls"], st["cost_usd"]
        if st["failures"] or not all(got):
            classify.record_spend(data, "link", "packages", model, calls, cost,
                                  time.time() - t0, "failed")
            log(f"  {len(st['failures'])} request(s) failed; nothing written. Run again to resume.")
            return None
    weight = prior.Weights(*prior.word_df(data))
    asset_phrases = defaultdict(list)
    for q in plan.queries:
        if q["kind"] == "phrase":
            asset_phrases[q["label_id"]].append(q["text"])
    asset_phrases = {a: prior.phrases(ts, weight) for a, ts in asset_phrases.items()}
    asset_rows, has = [], defaultdict(set)
    for (p, top), g in zip(cands, got):
        pw = set(prior.words(p["text"]))
        for a, cos, rank in top:
            e = g[a]
            prob = float(e["answer"]["noul"])
            asset_rows.append({"package_id": p["package_id"], "project_id": p["project_id"],
                               "label_id": a, "cosine": f"{cos:.4f}", "cosine_rank": rank,
                               "probability": f"{prob:.3f}", "model_served": e["model"],
                               "cost": f"{float(e.get('cost_usd') or 0):.6f}",
                               "prior_phrase_cosine": f"{cos:.4f}",
                               "prior_phrase_match": prior.fmt(prior.phrase_match(
                                   pw, asset_phrases.get(a, [])))})
            if prob >= YES:
                has[p["package_id"]].add(a)
    # Step 2: commitment <-> package.
    by_project = defaultdict(list)
    for p in plan.packages:
        by_project[p["project_id"]].append(p)
    pairs = []
    for name, (run, units) in sorted(plan.runs.items()):
        for u in units:
            ua = unit_assets(u)
            if not ua:
                continue
            for pid in u["project_ids"].split("|"):
                for p in by_project.get(pid, []):
                    pa = has.get(p["package_id"], set())
                    shared = ua & pa
                    if shared:
                        pairs.append((name, run, u, pid, p, shared))
    # The measure route: for each committed unit, its project's packages
    # nearest the measure (not already paired by asset), MEASURE_TOP of them.
    asset_paired = {(name, u["unit_id"], pid, p["package_id"])
                    for name, _, u, pid, p, _ in pairs}
    pairs = [x + ("asset", "", "") for x in pairs]
    row_of = {p["package_id"]: i for i, p in enumerate(plan.packages)}
    mbest = {}                       # each package's best cosine to each measure's phrases
    for name, (run, units) in sorted(plan.runs.items()):
        if not units:
            continue
        mq = search.query_vectors(search.load_queries(name, labels.folder(name)), plan.data,
                                  embed.DEFAULT_MODEL, plan.dim, embed.DEFAULT_BASE, api_key,
                                  log=log)
        best = mbest[name] = (plan.vecs @ mq.T).max(axis=1)     # package x measure
        if not plan.settings["measure_top"]:
            continue
        for u in units:
            for pid in u["project_ids"].split("|"):
                cands = sorted(by_project.get(pid, []),
                               key=lambda p: (-best[row_of[p["package_id"]]], p["package_id"]))
                n = 0
                for p in cands:
                    if n >= plan.settings["measure_top"]:
                        break
                    if (name, u["unit_id"], pid, p["package_id"]) in asset_paired:
                        continue
                    n += 1
                    pairs.append((name, run, u, pid, p, set(), "measure", n,
                                  f"{float(best[row_of[p['package_id']]]):.4f}"))
    requests_ = [(pair_state(u, p), {"pair": PAIR_QUESTION}) for _, _, u, _, p, *_ in pairs]
    got2, st2 = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                            dry_run=True)
    log(f"  pairs: {len(pairs):,} commitment-package pairs (by a shared asset, or nearest "
        f"the measure); "
        f"to ask: {st2['to_ask']:,} (about US${st2['estimate_usd']:.3f})")
    if st2["to_ask"]:
        got2, st2 = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers)
        calls += st2["calls"]
        cost += st2["cost_usd"]
        if st2["failures"] or not all(got2):
            classify.record_spend(data, "link", "packages", model, calls, cost,
                                  time.time() - t0, "failed")
            log(f"  {len(st2['failures'])} request(s) failed; nothing written. Run again to resume.")
            return None
    pair_rows = []
    for (name, run, u, pid, p, shared, route, mrank, mcos), g in zip(pairs, got2):
        e = g["pair"]
        # The prior inputs: the package's cosine to the measure's phrases, and
        # how much of the package description the commitment sentence holds.
        own = prior.phrases([p["text"]], weight)
        pair_rows.append({"label_set": name, "run": run, "label_id": u["label_id"],
                          "unit_id": u["unit_id"], "package_id": p["package_id"],
                          "project_id": pid, "shared_asset_ids": "|".join(sorted(shared)),
                          "route": route, "measure_rank": mrank, "measure_cosine": mcos,
                          "probability": f"{float(e['answer']['noul']):.3f}",
                          "model_served": e["model"],
                          "cost": f"{float(e.get('cost_usd') or 0):.6f}",
                          "prior_phrase_cosine": prior.fmt(
                              float(mbest[name][row_of[p["package_id"]]]) if name in mbest
                              else None),
                          "prior_phrase_match": prior.fmt(prior.phrase_match(
                              set(prior.words(u["text"])), own))})
    seconds = time.time() - t0
    classify.record_spend(data, "link", "packages", model, calls, cost, seconds,
                          os.path.basename(plan.out))
    write_run(plan, asset_rows, pair_rows, calls, cost, seconds)
    return plan.out


def report_lines(plan, asset_rows, pair_rows):
    yes = [r for r in asset_rows if float(r["probability"]) >= YES]
    pk_yes = {r["package_id"] for r in yes}
    by_rank = defaultdict(int)
    for r in yes:
        by_rank[int(r["cosine_rank"])] += 1
    names = {a["id"]: a["name"] for a in plan.assets}
    per_asset = defaultdict(int)
    for r in yes:
        per_asset[r["label_id"]] += 1
    lines = [f"link: packages to assets and commitments ({plan.version})",
             f"  packages in scope:            {len(plan.packages):,}",
             f"  assets asked per package:     {plan.settings['asset_top']}",
             f"  packages with an asset at {YES}: {len(pk_yes):,}",
             "  yes answers by cosine rank:   "
             + ", ".join(f"rank {k}: {by_rank[k]:,}" for k in sorted(by_rank))
             + (f"  (rank {plan.settings['asset_top']} is the last asked: yes answers there "
                f"mean deeper would find more)" if by_rank else ""),
             "  packages per asset at the reading threshold:"]
    for a, n in sorted(per_asset.items(), key=lambda x: -x[1]):
        lines.append(f"    {names.get(a, a):<50} {n:>6,}")
    lines.append(f"  commitment-package pairs asked: {len(pair_rows):,}")
    for route in ("asset", "measure"):
        rs = [r for r in pair_rows if r.get("route", "asset") == route]
        ys = [r for r in rs if float(r["probability"]) >= YES]
        line = f"    by {route}: {len(rs):,} pairs, {len(ys):,} at {YES}"
        if route == "measure" and ys:
            by = defaultdict(int)
            for r in ys:
                by[int(r["measure_rank"])] += 1
            line += ("; yes by rank " + ", ".join(f"{k}: {by[k]:,}" for k in sorted(by))
                     + f" (rank {plan.settings['measure_top']} is the last asked)")
        lines.append(line)
    for name, (run, units) in sorted(plan.runs.items()):
        ps = [r for r in pair_rows if r["label_set"] == name]
        linked = {r["unit_id"] for r in ps if float(r["probability"]) >= YES}
        pks = {r["package_id"] for r in ps if float(r["probability"]) >= YES}
        lines.append(f"    {name:<28} committed units {len(units):>5,}; pairs {len(ps):>6,}; "
                     f"at {YES}: {len(linked):>5,} units linked to {len(pks):,} packages")
    return lines


def write_run(plan, asset_rows, pair_rows, calls, cost, seconds):
    part = plan.out + ".part"
    os.makedirs(part, exist_ok=True)
    for file, cols, rows in (("package_assets.csv", ASSET_COLS, asset_rows),
                             ("commitment_packages.csv", PAIR_COLS, pair_rows)):
        with open(os.path.join(part, file), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    served = defaultdict(int)
    for r in asset_rows + pair_rows:
        served[r["model_served"]] += 1
    lines = report_lines(plan, asset_rows, pair_rows)
    manifest = dict(plan.settings, version=plan.version,
                    packages=len(plan.packages), asset_rows=len(asset_rows),
                    pair_rows=len(pair_rows),
                    measure_runs={n: r for n, (r, _) in plan.runs.items()},
                    asset_questions=plan.asset_qs, pair_question=PAIR_QUESTION,
                    question_sha256=classify.question_shas(
                        dict(plan.asset_qs, pair=PAIR_QUESTION)),
                    models_served=dict(served), calls_this_run=calls,
                    cost_this_run_usd=round(cost, 6), seconds_this_run=round(seconds, 1),
                    cost_stored_usd=round(sum(float(r["cost"] or 0)
                                              for r in asset_rows + pair_rows), 6),
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False, sort_keys=True)
    os.replace(part, plan.out)
    report = where(plan.data, "reports", "link_packages.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + f"\n  -> {plan.out}\n")
    for line in lines:
        plan.log(line)
    plan.log(f"  wrote {plan.out}")


def current_run(data, **kw):
    """The link run folder as it stands now, or None."""
    try:
        out = Plan(data, log=lambda *_: None, **kw).out
    except SystemExit:
        return None
    return out if os.path.exists(os.path.join(out, "manifest.json")) else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--projects", default="", help="comma-separated project ids (the scope)")
    ap.add_argument("--label-set", action="append", dest="sets",
                    help="measure set(s) to pair; default: every set with a current run")
    ap.add_argument("--workers", type=int, default=classify.WORKERS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    api_key = "" if args.dry_run else embed.read_key("")
    out = link(args.data, api_key=api_key, dry_run=args.dry_run, workers=args.workers,
               model=args.model,
               projects=classify.scope(args.projects), sets=args.sets)
    if out and not args.dry_run:
        import store
        store.try_publish(args.data, projects=classify.scope(args.projects), links=True)
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
