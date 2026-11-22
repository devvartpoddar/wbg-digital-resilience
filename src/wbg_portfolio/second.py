"""The second looks and the flags of the measure stage (docs/method/decisions.md).

One pattern: the judge decides; anything in the uncertain band gets the same
second look; both must agree. Nothing here decides: each step asks and writes
scores, and src/wbg_portfolio/decide.py reads them.

  measure_second  every sentence x measure whose first-pass posterior is in
            band.ask_low or more and whose digital score (P(2)+P(3)) is digital.ask_low or more
            (so it runs after digital; a sentence with no digital answer yet waits, one under
            digital.ask_low is left out): the decision model again with the
            paragraph beside the sentence (kind measure_ctx, P(grades 2, 3)),
            and the chat model's grade 0 to 3 (kind measure_second)
  digital   (before measure_second) every sentence with a first-pass score of band.ask_low or more
            for some measure, once whatever its measure: how far is it about a digital
            technology, graded 0 to 3 (kind digital, P(grades 2, 3)); only committed ones are published
  policy    every committed, digital sentence: investment or policy instrument, by the
            chat model (kind policy, score 1 for policy, 0 for investment)
  asset_second, asset_subtype   for the committed, digital sentences: the chat model's
            assets (asset_second, for those whose best first-pass asset is under asset.ask_high),
            then the type of every asset that counts, whichever look found it (asset_subtype)
  package_asset_second   every package x asset pair whose first-pass score is
            in [link.asset_ask_low, link.asset_ask_high): the chat model's yes or no
  package_asset_subtype  every package x asset that counts: the type of the asset, the
            sentences' type question with the package as subject

What is asked and what decides are separate, the same way for each look: the
`*ask_low`/`*ask_high` settings say which pairs are asked (wide), and
decide.py applies band.low/band.context/band.second_grade, link.asset_band_low/link.asset_yes and
asset.floor to the stored scores at publish. A look stored where it decides
nothing is published for reading only.

The three second looks (measure_second, asset_second, package_asset_second) read
alike: the same call shape, the same log tail, a missing look is waiting and never
a yes (asset_second has no score to band: it asks the sentences whose best first-pass asset is under asset.ask_high). Question wording is all in questions.py; rows and writes are checks._row and
checks.write_scoped; asking is chat.asked and checks._ask.

Every step honours projects (only those projects' sentences are asked, and
only their rows are replaced), dry_run (estimates, nothing asked) and the
spend cap, and asks nothing the store already holds.
"""
from wbg_portfolio import ask
from wbg_portfolio import chat
from wbg_portfolio import checks
from wbg_portfolio import decide
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


def measure_second(conn, data, corpus, name, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
            log=print, projects=None):
    """The band's second look for one measure (one measure a call, unlike the steps below, which take
    every measure's sentences: the band is a measure's own first-pass score). Writes kinds measure_ctx and
    measure_second (label_set the measure). Returns the pairs looked at, or
    None when a request failed or on a dry run."""
    b = decide.band()
    if checks.cannot_estimate(conn, dry_run, log, f"second look {name}", ("digital", "digital")):
        return None
    dig, low = decide.digital_scores(conn), decide.digital_ask_low()
    in_band = [u for u, r in decide.measure_units(conn, corpus, name).items() if r["asked"]]
    waiting_dig = [u for u in in_band if u not in dig]
    skipped = [u for u in in_band if u in dig and dig[u] < low]
    units, scope = _in_scope(corpus, [u for u in in_band if u in dig and dig[u] >= low], projects, universe=in_band)
    labs, scale = Q.load(name)
    lab = labs[0]
    gq = Q.graded_questions(name)[0][lab["id"]]
    cq, sq = Q.in_context(gq), Q.second_grade_question(lab, scale)
    log(f"second look {name}: {len(units):,} sentence(s) with a first score of {b['ask_low']} or more "
        f"and a digital score of {low} or more; {len(skipped):,} left out as not digital, "
        f"{len(waiting_dig):,} waiting for the digital question")
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
                        [checks._row("measure_second", name, lab["id"], u, corpus.chat_state(u), sq, a,
                                     score=a["grade"]) for u, a in second.items()], scope)
    log(f"  {len(ctx):,} context scores and {len(second):,} chat grades written; "
        f"{len(units) - len(second):,} pair(s) waiting for a second look (not committed)")
    return len(units) if ok else None


def digital(conn, data, corpus, names, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
            log=print, projects=None, every=None):
    """How far each sentence in the asked band of some measure is about a digital technology, asked
    once whatever its measure (the decision stays with the committed ones). Writes kind digital (label_set and
    label digital), scored as the probability of the counted grades (read.grades), as a measure is."""
    if checks.cannot_estimate(conn, dry_run, log, "digital", ("measure", "measure")):
        return None
    units, scope = _in_scope(corpus, decide.digital_asked_units(conn, corpus, names), projects,
                             partial=_partial(names, every))
    q = Q.digital_question()
    b = decide.band()
    log(f"digital: {len(units):,} sentence(s) with a first score of {b['ask_low']} or more")
    got = checks._ask(conn, model, [(u, corpus.context_state(u), {"digital": q}) for u in units],
                      "digital", "digital", api_key, dry_run, workers, log, data)
    if got is None:
        return None
    checks.write_scoped(conn, "digital", "digital",
                        [checks._row("digital", "digital", "digital", u, corpus.context_state(u), q, g["digital"],
                                     score=Q.score(g["digital"])) for u, g in got.items()], scope)
    log(f"  {len(got):,} digital scores written")
    return len(got)


def policy(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
           projects=None, every=None, **_):
    """Investment or policy for each committed, digital sentence, by the chat model.
    Writes kind policy (label_set and label policy; score 1 policy, 0 investment)."""
    if checks.cannot_estimate(conn, dry_run, log, "policy", ("measure", "measure")):
        return None
    units, scope = _in_scope(corpus, decide.committed_digital_units(conn, corpus, names), projects,
                             partial=_partial(names, every))
    q = Q.policy_question()
    log(f"policy: {len(units):,} committed sentence(s) that are digital")
    got, ok = chat.asked(conn, [(u, corpus.context_state(u), q) for u in units], "policy", "policy", api_key,
                    dry_run, workers, log)
    if dry_run:
        return None
    checks.write_scoped(conn, "policy", "policy", [checks._row("policy", "policy", "policy", u, corpus.context_state(u), q, a,
                                                   score=a["labels"][0] == "policy") for u, a in got.items()], scope)
    log(f"  {len(got):,} policy answers written")
    return len(got) if ok else None


# ------------------------------------------------------------------ the assets

def asset_second(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
                 projects=None, every=None, **_):
    """The chat model's second look at the assets: every committed, digital
    sentence whose best first-pass asset is under asset.ask_high (no asset at all
    counts as under) is shown with its paragraph and the four assets, and names
    one (a second only when the sentence names two) or none. The type is asked after,
    by asset_subtype. The decision does not change: the first pass wins at asset.floor
    and the look fills only where there is none. A reply naming an asset the file does
    not have is unreadable (asked again), never stored. Writes kind asset_second
    (label_set assets, label second; the answer's labels are the asset ids)."""
    if checks.cannot_estimate(conn, dry_run, log, "asset second look", ("asset", "classify"), digital=True):
        return None
    high = decide.asset_ask_high()
    own = decide.asset_map(conn, corpus)
    digital_units = decide.committed_digital_units(conn, corpus, names)
    units, scope = _in_scope(corpus, [u for u in digital_units if max(own.get(u, {}).values(), default=0.0) < high],
                             projects, digital_units, _partial(names, every))
    q = Q.asset_second_question(Q.asset_catalogue()[0])
    log(f"asset second look: {len(units):,} committed digital sentence(s) whose best first-pass asset is under {high}")
    got, ok = chat.asked(conn, [(u, corpus.context_state(u), q) for u in units], "asset_second", Q.ASSETS, api_key,
                    dry_run, workers, log)
    if dry_run:
        return None
    rows = [checks._row("asset_second", Q.ASSETS, "second", u, corpus.context_state(u), q, a, score=len(a["labels"]))
            for u, a in got.items()]
    checks.write_scoped(conn, "asset_second", Q.ASSETS, rows, scope)
    log(f"  {len(rows):,} asset second looks written, {sum(1 for r in rows if r['score']):,} with an asset; "
        f"{len(units) - len(rows):,} sentence(s) waiting (not counted)")
    return len(rows) if ok else None


def counted_assets(conn, corpus):
    """{unit_id: [asset_id]} of the assets that count for each sentence, whichever look found them
    (decide.unit_assets: the first pass at asset.floor, else the second look, else the paragraph)."""
    ua = decide.unit_assets(corpus, decide.asset_map(conn, corpus), decide.asset_second_looks(conn))
    return {u: [a for a, _ in win] for u, (_, win, _) in ua.items() if win}


def asset_subtype(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
                  projects=None, every=None, **_):
    """The type of each asset that counts for a committed, digital sentence, by
    the chat model, one question per sentence and asset (asked after asset_second,
    so an asset the second look found gets its type too). Writes kind
    asset_subtype (label_set assets, label the asset). A reply that is not one
    of the asset's types is unreadable (counted, asked again), never stored."""
    if checks.cannot_estimate(conn, dry_run, log, "asset types", ("asset", "classify"), digital=True):
        return None
    counted = counted_assets(conn, corpus)
    digital_units = decide.committed_digital_units(conn, corpus, names)
    units, scope = _in_scope(corpus, [u for u in digital_units if u in counted], projects,
                             digital_units, _partial(names, every))
    assets, subtypes = Q.asset_catalogue()
    qs = {a["id"]: Q.asset_subtype_question(a, subtypes[a["id"]]) for a in assets if subtypes.get(a["id"])}
    items = [((u, a), corpus.context_state(u), qs[a]) for u in units for a in counted[u] if a in qs]
    log(f"asset types: {len(items):,} sentence x asset pair(s) that count")
    got, ok = chat.asked(conn, items, "asset_subtype", Q.ASSETS, api_key, dry_run, workers, log)
    if dry_run:
        return None
    checks.write_scoped(conn, "asset_subtype", Q.ASSETS,
           [checks._row("asset_subtype", Q.ASSETS, a, u, corpus.context_state(u), qs[a], ans, score=1)
            for (u, a), ans in got.items()], scope)
    log(f"  {len(got):,} asset types written")
    return len(got) if ok else None


# ---------------------------------------------------------------- the packages

def package_asset_second(conn, data, corpus, names=None, *, api_key="", dry_run=False, workers=ask.WORKERS,
                         log=print, projects=None, **_):
    """The chat model's second look at a package's assets: every package x asset
    whose first-pass score (P(2)+P(3)) is from link.asset_ask_low up to link.asset_ask_high is
    asked yes or no (package description, category, component, the asset's
    definition). Writes kind package_asset_second (label_set assets, label the
    asset, item the package; score 1 yes, 0 no)."""
    if checks.cannot_estimate(conn, dry_run, log, "package asset second look", ("package_asset", "link")):
        return None
    decide.package_band()                                  # checks the asked band covers the decision band
    low, high = decide.package_ask()
    packages = {p["package_id"]: p for p in checks.load_packages(data, projects)}
    assets = {a["id"]: a for a in Q.asset_catalogue()[0]}
    post = decide.package_asset_posteriors(conn)
    qs = {a: Q.package_asset_second_question(assets[a]) for a in assets}
    pairs = sorted((pid, aid) for (pid, aid), p in post.items() if pid in packages and aid in qs and low <= p < high)

    def state(p):
        s = Q.package_state(p)
        return dict(s, category=p["category"]) if p.get("category") else s
    log(f"package asset second look: {len(pairs):,} package x asset pair(s) with a first score in [{low}, {high})")
    got, ok = chat.asked(conn, [((pid, aid), state(packages[pid]), qs[aid]) for pid, aid in pairs],
                    "package_asset_second", Q.ASSETS, api_key, dry_run, workers, log)
    if dry_run:
        return None
    rows = [checks._row("package_asset_second", Q.ASSETS, aid, pid, state(packages[pid]), qs[aid], a,
                        score=a["grade"]) for (pid, aid), a in got.items()]
    checks.write_scoped(conn, "package_asset_second", Q.ASSETS, rows, packages if projects else None)
    log(f"  {len(rows):,} package asset second looks written; {len(pairs) - len(rows):,} pair(s) waiting "
        f"(not counted)")
    return len(rows) if ok else None


def package_asset_subtype(conn, data, corpus, names=None, *, api_key="", dry_run=False, workers=ask.WORKERS,
                          log=print, projects=None, **_):
    """The type of each asset a package has (decide.package_asset_sets, after the package second look):
    the sentences' type question with the package as its subject, by the chat model, one question per
    package and asset. Writes kind package_asset_subtype (label_set assets, label the asset, item the
    package; the answer's labels are the type). A reply that is not one of the asset's types is
    unreadable (asked again), never stored."""
    if checks.cannot_estimate(conn, dry_run, log, "package asset types", ("package_asset", "link")):
        return None
    packages = {p["package_id"]: p for p in checks.load_packages(data, projects)}
    qs = Q.package_subtype_questions()
    has = decide.package_asset_sets(conn, packages)
    items = [((pid, aid), Q.package_state(packages[pid]), qs[aid])
             for pid in sorted(has) for aid in sorted(has[pid]) if aid in qs]
    log(f"package asset types: {len(items):,} package x asset pair(s) that count")
    got, ok = chat.asked(conn, items, "package_asset_subtype", Q.ASSETS, api_key, dry_run, workers, log)
    if dry_run:
        return None
    rows = [checks._row("package_asset_subtype", Q.ASSETS, aid, pid, Q.package_state(packages[pid]), qs[aid], a,
                        score=1) for (pid, aid), a in got.items()]
    checks.write_scoped(conn, "package_asset_subtype", Q.ASSETS, rows, packages if projects else None)
    log(f"  {len(rows):,} package asset types written; {len(items) - len(rows):,} pair(s) waiting")
    return len(rows) if ok else None
