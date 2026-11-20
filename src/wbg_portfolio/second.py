"""The second looks and the flags of the measure stage (docs/method/decisions.md).

One pattern: the judge decides; anything in the uncertain band gets the same
second look; both must agree. Nothing here decides: each step asks and writes
scores, and src/wbg_portfolio/decide.py reads them.

  measure   every sentence x measure whose first-pass posterior is in
            [band.low, band.keep): the decision model again with the
            paragraph beside the sentence (kind measure_ctx, P(grades 2, 3)),
            and the chat model's grade 0 to 3 (kind measure_second)
  digital   every committed sentence, once whatever its measure: is it a
            digital asset or the digital sector (kind digital, P(yes))
  policy    every committed sentence: investment or policy instrument, by the
            chat model (kind policy, score 1 for policy, 0 for investment)

Every step honours projects (only those projects' sentences are asked, and
only their rows are replaced), dry_run (estimates, nothing asked) and the
spend cap, and asks nothing the store already holds.
"""
from wbg_portfolio import ask
from wbg_portfolio import chat
from wbg_portfolio import checks
from wbg_portfolio import decide
from wbg_portfolio import panel
from wbg_portfolio import questions as Q


def _partial(names, every):
    """True when a run names only some of the active measures (--measures a b)."""
    return every is not None and not set(every) <= set(names)


def _in_scope(corpus, units, projects, universe=None, partial=False):
    """(units to ask, scope of the rows to replace). The scope is the sentences this run
    is responsible for (`universe`, default the units asked): those of the named projects,
    or of the named measures when a subset of them is run; None (the whole kind) only for
    a run over every measure and every project, so a subset never deletes the rows of
    another measure's sentences."""
    units = [u for u in units if corpus.in_scope(u, projects)]
    if not projects and not partial:
        return units, None
    base = units if universe is None else universe
    return units, {u for u in base if corpus.in_scope(u, projects)}


def measure(conn, data, corpus, name, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
            log=print, projects=None):
    """The band's second look for one measure. Writes kinds measure_ctx and
    measure_second (label_set the measure). Returns the pairs looked at, or
    None when a request failed or on a dry run. Does nothing while band.on is off."""
    b = decide.band()
    if not b["on"]:
        return 0
    units, scope = _in_scope(corpus, [u for u, r in decide.measure_units(conn, corpus, name).items()
                                      if r["in_band"]], projects)
    labs, scale = Q.load(name)
    lab = labs[0]
    gq = Q.graded_questions(name)[0][lab["id"]]
    cq, sq = Q.in_context(gq), Q.second_grade_question(lab, scale)
    log(f"second look {name}: {len(units):,} sentence(s) with a first score in [{b['low']}, {b['keep']})")
    ctx = checks._ask(conn, model, [(u, corpus.context_state(u), {lab["id"]: cq}) for u in units],
                      f"second:{name}:context", name, api_key, dry_run, workers, log, data)
    second, ok = chat.asked(conn, [(u, corpus.chat_state(u), sq) for u in units], f"second:{name}:chat", name,
                       api_key, dry_run, workers, log)
    if dry_run or ctx is None:
        return None
    checks.write_scoped(conn, "measure_ctx", name,
           [checks._row("measure_ctx", name, lab["id"], u, corpus.context_state(u), cq, g[lab["id"]],
                        score=Q.score(g[lab["id"]])) for u, g in ctx.items()], scope)
    checks.write_scoped(conn, "measure_second", name,
           [checks._row("measure_second", name, lab["id"], u, corpus.chat_state(u), sq, a, score=a["grade"])
            for u, a in second.items()], scope)
    waiting = sum(1 for u in units if u not in second)
    log(f"  {len(ctx):,} context scores and {len(second):,} chat grades written; "
        f"{waiting:,} pair(s) waiting for a second look (not committed)")
    return len(units) if ok else None


def digital(conn, data, corpus, names, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
            log=print, projects=None, every=None):
    """Is each committed sentence a digital asset or the digital sector, asked
    once whatever its measure. Writes kind digital (label_set and label digital)."""
    if not decide.digital_settings()[0]:
        return 0
    if checks.cannot_estimate(conn, dry_run, log, "digital", ("measure", "measure")):
        return None
    units, scope = _in_scope(corpus, decide.committed_units(conn, corpus, names), projects,
                             partial=_partial(names, every))
    q = Q.flag_question("digital")
    log(f"digital: {len(units):,} committed sentence(s)")
    got = checks._ask(conn, model, [(u, corpus.context_state(u), {"digital": q}) for u in units],
                      "digital", "digital", api_key, dry_run, workers, log, data)
    if got is None:
        return None
    checks.write_scoped(conn, "digital", "digital", [checks._row("digital", "digital", "digital", u, corpus.context_state(u), q, g["digital"])
                                         for u, g in got.items()], scope)
    log(f"  {len(got):,} digital scores written")
    return len(got)


def policy(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
           projects=None, every=None, **_):
    """Investment or policy for each committed sentence (with digital.on, each committed digital one), by the chat model.
    Writes kind policy (label_set and label policy; score 1 policy, 0 investment).
    Nothing is asked while policy.on is false."""
    if not decide.policy_on():
        return 0
    if checks.cannot_estimate(conn, dry_run, log, "policy", ("measure", "measure")):
        return None
    units, scope = _in_scope(corpus, decide.committed_digital_units(conn, corpus, names), projects,
                             partial=_partial(names, every))
    q = Q.policy_question()
    log(f"policy: {len(units):,} committed sentence(s)" + (" that are digital" if decide.digital_settings()[0] else ""))
    got, ok = chat.asked(conn, [(u, corpus.context_state(u), q) for u in units], "policy", "policy", api_key,
                    dry_run, workers, log)
    if dry_run:
        return None
    checks.write_scoped(conn, "policy", "policy", [checks._row("policy", "policy", "policy", u, corpus.context_state(u), q, a,
                                                   score=a["labels"][0] == "policy") for u, a in got.items()], scope)
    log(f"  {len(got):,} policy answers written")
    return len(got) if ok else None


# ------------------------------------------------------------------ the assets

def valid_assets(pairs, assets, subtypes):
    """(kept [(asset_id, subtype_id)], dropped_assets, dropped_subtypes) from
    the chat model's "asset/subtype" strings. An asset the files do not have, or
    a second mention of one, is discarded. A kind the files do not have for that
    asset is discarded and the asset kept with no kind (''). Nothing the model
    invented is stored as valid."""
    known, kept, seen = {a["id"] for a in assets}, [], set()
    lost_assets = lost_kinds = 0
    for p in pairs:
        a, _, st = p.partition("/")
        if a not in known or a in seen:
            lost_assets += 1
            continue
        seen.add(a)
        if st not in {s["id"] for s in subtypes.get(a, [])}:
            lost_kinds += bool(st)
            st = ""
        kept.append((a, st))
    return kept, lost_assets, lost_kinds


def first_pass_assets(conn, corpus):
    """{unit_id: [asset_id]} the first pass gives each sentence (level unit)."""
    s = decide.asset_settings()
    return {u: [a for a, _ in decide.sentence_assets(own, [], dict(s, decay=0))[0]]
            for u, own in decide.asset_map(conn, corpus).items()
            if any(p >= s["floor"] for p in own.values())}


def asset_second(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
                 projects=None, every=None, **_):
    """The chat model's second look at the assets: every committed, digital
    sentence with no asset at asset.floor from the first pass is shown with its
    paragraph and the four assets, and names up to two (or none) with a kind
    each. Writes kind asset_second (label_set assets, label second; the answer's
    labels are the valid "asset/subtype" pairs). While band.on is off nothing is asked."""
    if not decide.band()["on"]:
        return 0
    if checks.cannot_estimate(conn, dry_run, log, "asset second look", ("asset", "classify"), digital=True):
        return None
    first = first_pass_assets(conn, corpus)
    digital_units = decide.committed_digital_units(conn, corpus, names)
    units, scope = _in_scope(corpus, [u for u in digital_units if u not in first], projects,
                             digital_units, _partial(names, every))
    assets, subtypes = Q.asset_catalogue()
    q = Q.asset_second_question(assets, subtypes)
    log(f"asset second look: {len(units):,} committed digital sentence(s) with no asset from the first pass")
    got, ok = chat.asked(conn, [(u, corpus.context_state(u), q) for u in units], "asset_second", Q.ASSETS, api_key,
                    dry_run, workers, log)
    if dry_run:
        return None
    rows, lost_a, lost_k = [], 0, 0
    for u, a in got.items():
        kept, la, lk = valid_assets(a["labels"], assets, subtypes)
        lost_a, lost_k = lost_a + la, lost_k + lk
        rows.append(checks._row("asset_second", Q.ASSETS, "second", u, corpus.context_state(u), q,
                                dict(a, labels=[f"{x}/{y}" for x, y in kept], discarded=len(a["labels"]) - len(kept)),
                                score=len(kept)))
    checks.write_scoped(conn, "asset_second", Q.ASSETS, rows, scope)
    log(f"  {len(rows):,} asset second looks written, {sum(1 for r in rows if r['score']):,} with an asset"
        + (f"; discarded {lost_a} unknown or repeated asset id(s) and {lost_k} unknown kind(s)"
           if lost_a or lost_k else ""))
    return len(rows) if ok else None


def asset_subtype(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
                  projects=None, every=None, **_):
    """The kind of each asset the first pass gave a committed, digital sentence,
    by the chat model, one question per sentence and asset. Writes kind
    asset_subtype (label_set assets, label the asset). A reply that is not one
    of the asset's kinds is unreadable (counted, asked again), never stored.
    While band.on is off nothing is asked."""
    if not decide.band()["on"]:
        return 0
    if checks.cannot_estimate(conn, dry_run, log, "asset kinds", ("asset", "classify"), digital=True):
        return None
    first = first_pass_assets(conn, corpus)
    digital_units = decide.committed_digital_units(conn, corpus, names)
    units, scope = _in_scope(corpus, [u for u in digital_units if u in first], projects,
                             digital_units, _partial(names, every))
    assets, subtypes = Q.asset_catalogue()
    qs = {a["id"]: Q.asset_subtype_question(a, subtypes[a["id"]]) for a in assets if subtypes.get(a["id"])}
    items = [((u, a), corpus.context_state(u), qs[a]) for u in units for a in first[u] if a in qs]
    log(f"asset kinds: {len(items):,} sentence x asset pair(s) from the first pass")
    got, ok = chat.asked(conn, items, "asset_subtype", Q.ASSETS, api_key, dry_run, workers, log)
    if dry_run:
        return None
    checks.write_scoped(conn, "asset_subtype", Q.ASSETS,
           [checks._row("asset_subtype", Q.ASSETS, a, u, corpus.context_state(u), qs[a], ans, score=1)
            for (u, a), ans in got.items()], scope)
    log(f"  {len(got):,} asset kinds written")
    return len(got) if ok else None


# ---------------------------------------------------------------- the packages

def package_asset_second(conn, data, corpus, names=None, *, api_key="", dry_run=False, workers=ask.WORKERS,
                         log=print, projects=None, **_):
    """The chat model's second look at a package's assets: every package x asset
    whose first-pass P(yes) is from link.asset_band_low up to link.asset_yes is
    asked yes or no (package description, category, component, the asset's
    definition). Writes kind package_asset_second (label_set assets, label the
    asset, item the package; score 1 yes, 0 no). Not asked while links.strengths is off."""
    if not decide.links_strengths():
        return 0
    if checks.cannot_estimate(conn, dry_run, log, "package asset second look", ("package_asset", "link")):
        return None
    low, yes = float(panel.require("link.asset_band_low")), float(panel.require("link.asset_yes"))
    packages = {p["package_id"]: p for p in checks.load_packages(data, projects)}
    assets = {a["id"]: a for a in Q.asset_catalogue()[0]}
    post = decide.package_asset_posteriors(conn)
    qs = {a: Q.package_asset_second_question(assets[a]) for a in assets}
    pairs = sorted((pid, aid) for (pid, aid), p in post.items() if pid in packages and aid in qs and low <= p < yes)

    def state(p):
        s = Q.package_state(p)
        return dict(s, category=p["category"]) if p.get("category") else s
    log(f"package asset second look: {len(pairs):,} package x asset pair(s) with a first score in [{low}, {yes})")
    got, ok = chat.asked(conn, [((pid, aid), state(packages[pid]), qs[aid]) for pid, aid in pairs],
                    "package_asset_second", Q.ASSETS, api_key, dry_run, workers, log)
    if dry_run:
        return None
    rows = [checks._row("package_asset_second", Q.ASSETS, aid, pid, state(packages[pid]), qs[aid], a,
                        score=a["grade"]) for (pid, aid), a in got.items()]
    checks.write_scoped(conn, "package_asset_second", Q.ASSETS, rows, packages if projects else None)
    waiting = len(pairs) - len(got)
    log(f"  {len(rows):,} package asset second looks written; {waiting:,} pair(s) waiting (not counted)")
    return len(rows) if ok else None
