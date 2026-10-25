"""The funnel, the same for the asset map and for every measure: which
sentences a label's question goes to, the ask, and the feedback rounds below
the gate (docs/method/decisions.md).

1. Round 0: every in-scope sentence a word pattern of the label matches, plus
   every one whose best cosine over the label's phrases reaches the label's
   gate (the fitted skip gate, else the panel's starting gate; 0 asks all),
   plus every sentence the store already holds an answer for under the
   current question (nothing paid for is dropped).
2. The ask (src/wbg_portfolio/ask.py).
3. Rounds (Rocchio relevance feedback, src/wbg_portfolio/feedback.py): the sentences that
   count at the ask floor against those asked that do not; the next batch of
   unasked sentences nearest that query; until a round yields too few or the
   rounds run out.
4. Every answer becomes one row of <schema>.scores: kind, label set, label,
   unit, the score (src/wbg_portfolio/questions.py score) and the route it was asked by.
   src/wbg_portfolio/decide.py reads the floors onto it.
"""
import time

import numpy

from wbg_portfolio import ask
from wbg_portfolio import db
from wbg_portfolio import feedback
from wbg_portfolio import fits
from wbg_portfolio import labels
from wbg_portfolio import panel
from wbg_portfolio import questions as Q
from wbg_portfolio import search

SCORE_COLS = ["kind", "label_set", "label_id", "item_id", "state_sha", "question_sha", "score",
              "answer", "served", "route", "round", "cosine", "rank"]


def table(conn):
    return db.scores_table(conn)


def kind_of(name):
    """asset for the asset set, scratch for a set under data/scratch/
    (./wbg_portfolio try), measure otherwise."""
    if name == Q.ASSETS:
        return "asset"
    return "scratch" if labels.is_scratch(name) else "measure"


def settings(name):
    """The funnel's numbers for a set: the measures' lines, or the asset
    map's where the panel writes one."""
    asset = name == Q.ASSETS
    fb = feedback.settings(feedback.ASSET if asset else "feedback.")
    return {"gate": float(panel.get("asset.gate_cosine" if asset else "measure.gate_cosine", 0.3)),
            "ask_floor": float(panel.get("asset.ask_floor" if asset else "floor.ask", 0.5)),
            "top": 0 if asset else int(panel.get("measure.top", 0)), **fb}


def best_cosines(name, data, corpus):
    """(search run name, {label_id: best cosine per index row})."""
    folder, _ = search.run_folder(name, data)
    queries, scores, index = search.load_run(folder, data)
    if len(index) != len(corpus.index):
        raise SystemExit(f"{name}: the search run and the corpus disagree on rows")
    cols = {}
    for j, q in enumerate(queries):
        cols.setdefault(q["label_id"], []).append(j)
    import os
    return os.path.basename(folder), {lid: numpy.asarray(scores[:, js]).max(axis=1)
                                      for lid, js in cols.items()}


def stored_units(conn, model, corpus, qshas, states):
    """{label_id: set of unit ids} the store already answers under the
    current questions, so a sentence once asked stays scored."""
    db.temp_keys(conn, "funnel_states", ["state_sha", "unit_id"],
                 [(s, u) for u, s in states.items()])
    got = db.rows(conn, f"SELECT a.question_sha, k.unit_id FROM {db.STORE}.answers a "
                  f"JOIN funnel_states k USING (state_sha) WHERE a.model = %s AND a.question_sha = ANY(%s)",
                  (model, list(qshas.values())))
    by_q = {q: lid for lid, q in qshas.items()}
    out = {lid: set() for lid in qshas}
    for q, u in got:
        out[by_q[q]].add(u)
    return out


def up_to_date(conn, kind, name, fingerprint):
    """True when the last finished run of this set had the same fingerprint and
    its scores are still there: nothing a run could ask or change."""
    row = db.one(conn, f"SELECT fingerprint FROM {db.SCHEMA}.funnel_state WHERE kind = %s AND label_set = %s",
                 (kind, name))
    if not row or row[0] != fingerprint:
        return False
    n = db.one(conn, f"SELECT count(*) FROM {db.SCHEMA}.scores WHERE kind = %s AND label_set = %s", (kind, name))
    return bool(n and n[0])


def summary_from_scores(conn, kind, name, qs, ask_floor):
    rows = db.rows(conn, f"SELECT label_id, count(*), count(*) FILTER (WHERE score >= %s) FROM {db.SCHEMA}.scores "
                         f"WHERE kind = %s AND label_set = %s GROUP BY label_id", (ask_floor, kind, name))
    got = {lid: (a, y) for lid, a, y in rows}
    return {lid: {"asked": got.get(lid, (0, 0))[0], "yes": got.get(lid, (0, 0))[1], "rounds": []} for lid in qs}


def run(conn, data, name, *, corpus=None, model=Q.MODEL, api_key="", dry_run=False, workers=ask.WORKERS,
        log=print, projects=None):
    """The set through the funnel; writes its scores. Returns a summary dict
    ({label_id: {asked, yes}}, calls, cost) or None when a request failed."""
    corpus = corpus or Q.Corpus(data)
    kind = kind_of(name)
    s = settings(name)
    qs, _ = Q.graded_questions(name)
    qshas = {lid: Q.sha(q) for lid, q in qs.items()}
    run_name, cos = best_cosines(name, data, corpus)
    gate = fits.gates(conn, name, qshas, run_name, s["gate"]) if s["gate"] else {lid: 0.0 for lid in qs}
    pats = {}
    for pt in labels.load_patterns(name):
        pats.setdefault(pt["label_id"], []).append(pt)
    t = table(conn)
    print_ = Q.sha({"model": model, "questions": sorted(qshas.values()), "gate": sorted(gate.items()),
                    "patterns": sorted((p["label_id"], p["pattern"], p["regex"].flags) for ps in pats.values() for p in ps),
                    "settings": sorted(s.items()), "search": run_name, "context": corpus.context})
    if projects is None and not dry_run and up_to_date(conn, kind, name, print_):
        log(f"{name}: up to date (same questions, gates, patterns, search run and settings as the "
            f"last finished run); nothing asked, scores kept")
        return {"labels": summary_from_scores(conn, kind, name, qs, s["ask_floor"]), "calls": 0,
                "cost_usd": 0.0, "skipped": True}
    scope = [r["unit_id"] for r in corpus.index if corpus.in_scope(r["unit_id"], projects)]
    states = {u: Q.sha(corpus.state(u)) for u in scope}
    stored = stored_units(conn, model, corpus, qshas, states)
    # round 0
    asked, route, rnd_of, cosine = {}, {}, {}, {}
    for lid in qs:
        best = cos.get(lid)
        order = numpy.argsort(-best, kind="stable") if best is not None and s["top"] else None
        top = set(order[:s["top"]].tolist()) if order is not None else set()
        for u in scope:
            i = corpus.row_of[u]
            c = float(best[i]) if best is not None else 0.0
            near = c >= gate[lid] or i in top
            hit = any(p["regex"].search(corpus.texts[u]) for p in pats.get(lid, ()))
            if near or hit or u in stored[lid]:
                asked.setdefault(u, set()).add(lid)
                route[(u, lid)] = "both" if near and hit else ("pattern" if hit else
                                                              "embedding" if near else "stored")
                rnd_of[(u, lid)] = 0
            cosine[(u, lid)] = c
    n0 = sum(len(v) for v in asked.values())
    log(f"{name}: {len(qs)} label(s), {len(scope):,} sentences, {n0:,} questions in round 0 "
        f"(gates {', '.join(f'{l} {g:.2f}' for l, g in sorted(gate.items()))})")
    answers, total = {}, {"calls": 0, "cost_usd": 0.0, "failures": []}
    layers = fits.layers(conn, "layer", name, qshas, corpus.vector)

    def yes(u, lid):
        sc = Q.score(answers[u][lid])
        if lid in layers:
            sc = layers[lid].probability(u, sc)
        return sc >= s["ask_floor"]

    def one_ask(batch):
        items = [(u, corpus.state(u), {lid: qs[lid] for lid in lids}) for u, lids in sorted(batch.items())]
        got, st = ask.ask(conn, model, items, stage=f"{kind}:{name}", label_set=name, api_key=api_key,
                          dry_run=dry_run, workers=workers, log=log, data=data)
        total["calls"] += st["calls"]
        total["cost_usd"] += st["cost_usd"]
        total["failures"] += st["failures"]
        log(ask.estimate_line(name, st))
        for u, g in got.items():
            answers.setdefault(u, {}).update(g)
        return st

    st = one_ask(asked)
    if dry_run:
        fb = s["max_rounds"]
        log(f"  then up to {fb} feedback round(s) of {s['batch']} sentences a label; --dry-run: "
            f"nothing asked, nothing written")
        return None
    if total["failures"] or any(lid not in answers.get(u, {}) for u, lids in asked.items() for lid in lids):
        log(f"  {len(total['failures'])} request(s) failed; nothing written. Run again to resume.")
        return None
    history = {lid: [] for lid in qs}
    going = set(qs) if s["max_rounds"] and s["gate"] else set()
    if corpus.matrix is None:
        going = set()
    # a sentence asked in an earlier run keeps the route and round it was first
    # asked by; and when round 0 asked nothing new of a set already scored, the
    # rounds have nothing new to learn from: re-seeding them from a larger pool
    # would only move the admitted set between otherwise identical runs
    earlier = {(u, lid): (r, k) for u, lid, r, k in db.rows(
        conn, f"SELECT item_id, label_id, route, round FROM {t} WHERE kind = %s AND label_set = %s",
        (kind, name))}
    if earlier and st["to_ask"] == 0:
        going = set()
    for rnd in range(1, s["max_rounds"] + 1):
        lids, queries = [], []
        for lid in sorted(going):
            mine = [u for u, ls in asked.items() if lid in ls]
            q = feedback.query(corpus.matrix, [corpus.row_of[u] for u in mine if yes(u, lid)],
                               [corpus.row_of[u] for u in mine if not yes(u, lid)], s["negative_weight"])
            if q is None:
                going.discard(lid)
                continue
            lids.append(lid)
            queries.append(q)
        if not lids:
            break
        new = {}
        in_scope = set(scope)
        for lid, (order, sims) in zip(lids, feedback.ranked(corpus.matrix, queries)):
            picked = feedback.next_rows(order, lambda i: corpus.index[i]["unit_id"] in in_scope and
                                        lid not in asked.get(corpus.index[i]["unit_id"], ()), s["batch"])
            for i in picked:
                u = corpus.index[i]["unit_id"]
                new.setdefault(u, set()).add(lid)
                route[(u, lid)], rnd_of[(u, lid)] = "feedback", rnd
                cosine[(u, lid)] = float(sims[i])
        if not new:
            break
        one_ask(new)
        if total["failures"]:
            log("  a feedback round failed; nothing written. Run again to resume.")
            return None
        found = {lid: 0 for lid in lids}
        for u, ls in new.items():
            asked.setdefault(u, set()).update(ls)
            for lid in ls:
                found[lid] += yes(u, lid)
        for lid in lids:
            history[lid].append({"round": rnd, "asked": sum(1 for u in new if lid in new[u]),
                                 "yes": found[lid]})
            if found[lid] < s["min_yield"]:
                going.discard(lid)
        log(f"  round {rnd}: {sum(len(v) for v in new.values()):,} questions, "
            f"{sum(found.values())} at the ask floor or more")
    # write the scores; a sentence asked in an earlier run keeps the route and
    # round it first entered the pool by, whatever gate or pattern admits it now
    for k, v in earlier.items():
        if k in route:
            route[k], rnd_of[k] = v
    rows = []
    for u, ls in asked.items():
        for lid in ls:
            a = answers[u][lid]
            rows.append({"kind": kind, "label_set": name, "label_id": lid, "item_id": u,
                         "state_sha": states[u], "question_sha": qshas[lid],
                         "score": round(Q.score(a), 6), "answer": {k: v for k, v in a.items() if k != "served"},
                         "served": a.get("served", ""), "route": route[(u, lid)],
                         "round": rnd_of[(u, lid)], "cosine": round(cosine.get((u, lid), 0.0), 6),
                         "rank": None})
    with conn.transaction():
        where = "kind = %s AND label_set = %s"
        params = [kind, name]
        if projects:
            where += " AND item_id = ANY(%s)"
            params.append(scope)
        db.replace(conn, t, SCORE_COLS, rows, where, params)
        if projects is None:
            conn.execute(f"INSERT INTO {db.SCHEMA}.funnel_state (kind, label_set, fingerprint, finished_at) "
                         f"VALUES (%s, %s, %s, now()) ON CONFLICT (kind, label_set) DO UPDATE SET "
                         f"fingerprint = EXCLUDED.fingerprint, finished_at = now()", (kind, name, print_))
    conn.commit()
    summary = {lid: {"asked": sum(1 for u, ls in asked.items() if lid in ls),
                     "yes": sum(1 for u, ls in asked.items() if lid in ls and yes(u, lid)),
                     "rounds": history[lid]} for lid in qs}
    for lid, v in sorted(summary.items()):
        log(f"  {lid}: {v['asked']:,} asked, {v['yes']:,} at the ask floor {s['ask_floor']}"
            + (f"; rounds {[(r['asked'], r['yes']) for r in v['rounds']]}" if v["rounds"] else ""))
    log(f"  {total['calls']:,} calls, US${total['cost_usd']:.4f}; {len(rows):,} scores written")
    return {"labels": summary, "calls": total["calls"], "cost_usd": total["cost_usd"]}


def run_many(conn, data, names, **kw):
    """Several sets through the funnel in one process, the corpus read once."""
    corpus = Q.Corpus(data)
    return {name: run(conn, data, name, corpus=corpus, **kw) for name in names}
