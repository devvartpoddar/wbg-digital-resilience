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
from wbg_portfolio import labels
from wbg_portfolio import panel
from wbg_portfolio import questions as Q


def _row(kind, label_set, label_id, item, state, q, answer, score):
    return {"kind": kind, "label_set": label_set, "label_id": label_id, "item_id": item,
            "state_sha": Q.sha(state), "question_sha": Q.sha(q), "score": round(float(score), 6),
            "answer": {k: v for k, v in answer.items() if k != "served"}, "served": answer.get("served", ""),
            "route": "", "round": 0, "cosine": None, "rank": None}


def _write(conn, kind, label_set, rows, scope):
    """Replace the kind's rows of the set: all, or those of the sentences in scope."""
    if scope is None:
        checks.write_scores(conn, kind, label_set, rows)
    else:
        checks.write_scores(conn, kind, label_set, rows, "item_id = ANY(%s)", [sorted(scope)])


def _in_scope(corpus, units, projects):
    units = [u for u in units if corpus.in_scope(u, projects)]
    return units, (set(units) if projects else None)


def _chat(conn, items, stage, label_set, api_key, dry_run, workers, log):
    """(answers, ok): ok is False when a request failed (run again to resume);
    an unreadable reply is counted and the item left without an answer."""
    got, st = chat.ask(conn, items, stage=stage, label_set=label_set, api_key=api_key, dry_run=dry_run,
                       workers=workers, log=log)
    log(chat.estimate_line(stage, st))
    if dry_run:
        return got, False
    if st["failures"]:
        log(f"  {len(st['failures'])} request(s) failed; run again to resume.")
    return got, not st["failures"]


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
    cq, sq = Q.in_context(gq), chat.grade_question(lab, scale)
    log(f"second look {name}: {len(units):,} sentence(s) with a first score in [{b['low']}, {b['keep']})")
    ctx = checks._ask(conn, model, [(u, corpus.context_state(u), {lab["id"]: cq}) for u in units],
                      f"second:{name}:context", name, api_key, dry_run, workers, log, data)
    second, ok = _chat(conn, [(u, corpus.chat_state(u), sq) for u in units], f"second:{name}:chat", name,
                       api_key, dry_run, workers, log)
    if dry_run or ctx is None:
        return None
    _write(conn, "measure_ctx", name,
           [_row("measure_ctx", name, lab["id"], u, corpus.context_state(u), cq, g[lab["id"]],
                 Q.score(g[lab["id"]])) for u, g in ctx.items()], scope)
    _write(conn, "measure_second", name,
           [_row("measure_second", name, lab["id"], u, corpus.chat_state(u), sq, a, a["grade"])
            for u, a in second.items()], scope)
    waiting = sum(1 for u in units if u not in second)
    log(f"  {len(ctx):,} context scores and {len(second):,} chat grades written; "
        f"{waiting:,} pair(s) waiting for a second look (not committed)")
    return len(units) if ok else None


def digital(conn, data, corpus, names, *, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
            log=print, projects=None):
    """Is each committed sentence a digital asset or the digital sector, asked
    once whatever its measure. Writes kind digital (label_set and label digital)."""
    if not decide.digital_settings()[0]:
        return 0
    units, scope = _in_scope(corpus, decide.committed_units(conn, corpus, names), projects)
    q = Q.flag_question("digital")
    log(f"digital: {len(units):,} committed sentence(s)")
    got = checks._ask(conn, model, [(u, corpus.context_state(u), {"digital": q}) for u in units],
                      "digital", "digital", api_key, dry_run, workers, log, data)
    if got is None:
        return None
    _write(conn, "digital", "digital", [_row("digital", "digital", "digital", u, corpus.context_state(u), q,
                                              g["digital"], Q.score(g["digital"], fit=True))
                                         for u, g in got.items()], scope)
    log(f"  {len(got):,} digital scores written")
    return len(got)


def policy_question():
    f = labels.flag("policy")
    return chat.question(
        f"Only the sentence in the 'sentence' field is judged; the 'context' field is the paragraph around it, "
        f"for reference. Does the sentence commit the project to an investment or to a policy instrument? "
        f"{f['name'][:1].upper() + f['name'][1:]} means: {f['definition']}", "choice", ["investment", "policy"])


def policy(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
           projects=None, **_):
    """Investment or policy for each committed sentence, by the chat model.
    Writes kind policy (label_set and label policy; score 1 policy, 0 investment)."""
    units, scope = _in_scope(corpus, decide.committed_units(conn, corpus, names), projects)
    q = policy_question()
    log(f"policy: {len(units):,} committed sentence(s)")
    got, ok = _chat(conn, [(u, corpus.context_state(u), q) for u in units], "policy", "policy", api_key,
                    dry_run, workers, log)
    if dry_run:
        return None
    _write(conn, "policy", "policy", [_row("policy", "policy", "policy", u, corpus.context_state(u), q, a,
                                            a["labels"][0] == "policy") for u, a in got.items()], scope)
    log(f"  {len(got):,} policy answers written")
    return len(got) if ok else None


# ------------------------------------------------------------------ the assets

def asset_catalogue():
    """The four assets with their finer kinds, as the chat questions show them:
    ([asset {id, name, definition}], {asset_id: [subtype {id, name, definition}]})."""
    assets, _ = Q.load(Q.ASSETS)
    return assets, labels.load_subtypes(Q.ASSETS)


def _catalogue_text(assets, subtypes):
    out = []
    for a in assets:
        out.append(f"- {a['id']}: {a['name']}. {a['definition']}")
        for s in subtypes.get(a["id"], []):
            out.append(f"    kind {s['id']}: {s['name']}. {s['definition']}")
    return "\n".join(out)


def asset_second_question():
    """Up to two of the four assets, or none, each with one of its kinds."""
    assets, subtypes = asset_catalogue()
    return chat.question(
        Q.CONTEXT_NOTE + "Which digital asset or assets, if any, does the sentence commit the project to "
        "financing, building, buying or running? Choose at most two assets from the list, and for each the "
        "one kind of it that fits best; choose none when the sentence concerns no digital asset. "
        "Assets, with their kinds:\n" + _catalogue_text(assets, subtypes),
        "assets", [a["id"] for a in assets], 2)


def asset_subtype_question(asset_id):
    """Which kind of an asset a sentence already counted for it concerns."""
    assets, subtypes = asset_catalogue()
    a = next(x for x in assets if x["id"] == asset_id)
    return chat.question(
        Q.CONTEXT_NOTE + f"The sentence is already counted as concerning {a['name']}; this asks only which kind "
        f"of it. {a['name']} means: {a['definition']}\nKinds:\n" +
        "\n".join(f"- {s['id']}: {s['name']}. {s['definition']}" for s in subtypes[asset_id]),
        "choice", [s["id"] for s in subtypes[asset_id]])


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
                 projects=None, **_):
    """The chat model's second look at the assets: every committed, digital
    sentence with no asset at asset.floor from the first pass is shown with its
    paragraph and the four assets, and names up to two (or none) with a kind
    each. Writes kind asset_second (label_set assets, label second; the answer's
    labels are the valid "asset/subtype" pairs). While band.on is off nothing is asked."""
    if not decide.band()["on"]:
        return 0
    first = first_pass_assets(conn, corpus)
    units, scope = _in_scope(corpus, [u for u in decide.committed_digital_units(conn, corpus, names)
                                      if u not in first], projects)
    q = asset_second_question()
    log(f"asset second look: {len(units):,} committed digital sentence(s) with no asset from the first pass")
    got, ok = _chat(conn, [(u, corpus.context_state(u), q) for u in units], "asset_second", Q.ASSETS, api_key,
                    dry_run, workers, log)
    if dry_run:
        return None
    assets, subtypes = asset_catalogue()
    rows, lost_a, lost_k = [], 0, 0
    for u, a in got.items():
        kept, la, lk = valid_assets(a["labels"], assets, subtypes)
        lost_a, lost_k = lost_a + la, lost_k + lk
        rows.append(_row("asset_second", Q.ASSETS, "second", u, corpus.context_state(u), q,
                         dict(a, labels=[f"{x}/{y}" for x, y in kept], discarded=len(a["labels"]) - len(kept)),
                         len(kept)))
    _write(conn, "asset_second", Q.ASSETS, rows, scope)
    log(f"  {len(rows):,} asset second looks written, {sum(1 for r in rows if r['score']):,} with an asset"
        + (f"; discarded {lost_a} unknown or repeated asset id(s) and {lost_k} unknown kind(s)"
           if lost_a or lost_k else ""))
    return len(rows) if ok else None


def asset_subtype(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
                  projects=None, **_):
    """The kind of each asset the first pass gave a committed, digital sentence,
    by the chat model, one question per sentence and asset. Writes kind
    asset_subtype (label_set assets, label the asset). A reply that is not one
    of the asset's kinds is unreadable (counted, asked again), never stored.
    While band.on is off nothing is asked."""
    if not decide.band()["on"]:
        return 0
    first = first_pass_assets(conn, corpus)
    units, scope = _in_scope(corpus, [u for u in decide.committed_digital_units(conn, corpus, names)
                                      if u in first], projects)
    assets, subtypes = asset_catalogue()
    qs = {a: asset_subtype_question(a) for a in subtypes if subtypes[a]}
    items = [((u, a), corpus.context_state(u), qs[a]) for u in units for a in first[u] if a in qs]
    log(f"asset kinds: {len(items):,} sentence x asset pair(s) from the first pass")
    got, ok = _chat(conn, items, "asset_subtype", Q.ASSETS, api_key, dry_run, workers, log)
    if dry_run:
        return None
    _write(conn, "asset_subtype", Q.ASSETS,
           [_row("asset_subtype", Q.ASSETS, a, u, corpus.context_state(u), qs[a], ans, 1)
            for (u, a), ans in got.items()], scope)
    log(f"  {len(got):,} asset kinds written")
    return len(got) if ok else None


# ---------------------------------------------------------------- the packages

def package_asset_question(asset):
    """Does the package procure the asset, as the chat model's yes or no."""
    return chat.question(
        f"Does the procurement package in the 'package' field procure {asset['name']}, or equipment, works, "
        f"systems or services for it, named or clearly implied in the package's own description? "
        f"{asset['name']} means: {asset['definition']}", "yesno")


def package_asset_second(conn, data, corpus, names=None, *, api_key="", dry_run=False, workers=ask.WORKERS,
                         log=print, projects=None, **_):
    """The chat model's second look at a package's assets: every package x asset
    whose first-pass P(yes) is from link.asset_band_low up to link.asset_yes is
    asked yes or no (package description, category, component, the asset's
    definition). Writes kind package_asset_second (label_set assets, label the
    asset, item the package; score 1 yes, 0 no). Not asked while links.strengths is off."""
    if not decide.links_strengths():
        return 0
    low, yes = float(panel.require("link.asset_band_low")), float(panel.require("link.asset_yes"))
    packages = {p["package_id"]: p for p in checks.load_packages(data, projects)}
    assets = {a["id"]: a for a in Q.load(Q.ASSETS)[0]}
    post = decide.package_asset_posteriors(conn)
    qs = {a: package_asset_question(assets[a]) for a in assets}
    pairs = sorted((pid, aid) for (pid, aid), p in post.items() if pid in packages and aid in qs and low <= p < yes)

    def state(p):
        s = Q.package_state(p)
        return dict(s, category=p["category"]) if p.get("category") else s
    log(f"package asset second look: {len(pairs):,} package x asset pair(s) with a first score in [{low}, {yes})")
    got, ok = _chat(conn, [((pid, aid), state(packages[pid]), qs[aid]) for pid, aid in pairs],
                    "package_asset_second", Q.ASSETS, api_key, dry_run, workers, log)
    if dry_run:
        return None
    rows = [_row("package_asset_second", Q.ASSETS, aid, pid, state(packages[pid]), qs[aid], a, a["grade"])
            for (pid, aid), a in got.items()]
    if projects:
        checks.write_scores(conn, "package_asset_second", Q.ASSETS, rows, "item_id = ANY(%s)", [sorted(packages)])
    else:
        checks.write_scores(conn, "package_asset_second", Q.ASSETS, rows)
    waiting = len(pairs) - len(got)
    log(f"  {len(rows):,} package asset second looks written; {waiting:,} pair(s) waiting (not counted)")
    return len(rows) if ok else None
