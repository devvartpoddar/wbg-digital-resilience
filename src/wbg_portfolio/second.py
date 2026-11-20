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
