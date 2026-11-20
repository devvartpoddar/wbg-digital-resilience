"""Every decision, read once from the scores and the control panel, into the
tables the dashboards' views read (docs/method/decisions.md).

A score is the model's answer (src/wbg_portfolio/funnel.py, src/wbg_portfolio/checks.py); a decision is
that score, through the label's probability layer when one is fitted and on,
against the label's floor. Nothing here asks the model; a moved floor is a
publish. The one place that says what "committed" means is committed();
the views only join and aggregate.

  publish(conn, data)   rewrite measure_findings, paragraph_grades, unit_assets,
                        unit_pair_checks, package_assets, commitment_packages,
                        the label tables, then the views
"""
import re
import time
from collections import defaultdict

from wbg_portfolio import db
from wbg_portfolio import fits
from wbg_portfolio import labels
from wbg_portfolio import load_pg
from wbg_portfolio import panel
from wbg_portfolio import questions as Q

OTH = "OTH."
# The wording's firmness, a column to filter on, never a cut: 'activity' (the
# project finances, builds, requires...), 'outcome' (what should follow), 'none'.
ACTIVITY = re.compile(
    r"\b(?:will|shall|to) (?:also |further |specifically |be )?(?:be )?"
    r"(?:financ|fund|support|install|deploy|build|construct|establish|develop|procur|"
    r"integrat|link|connect|upgrad|rehabilitat|conduct|carr(?:y|ied) out|undertak|prepar|"
    r"design|implement|introduc|provid|acquir|equip|strengthen(?:ed|ing)? (?:the|its)|"
    r"set up|put in place|creat|adopt|requir|includ|incorporat|expand|digiti[sz]|"
    r"mainstream|assess|map|bur(?:y|ied)|elevat|reinforc|harden|relocat|backup|back up)\w*"
    r"|\b(?:is|are) (?:being |to be )?(?:financed|supported|required|included|integrated|"
    r"deployed|installed|built|procured|conducted|undertaken|planned|designed)\b"
    r"|\b(?:activities|investments) (?:will )?includ\w*|\bthe following\b|\bincludes?\b"
    r"|\bsupported by the (?:project|component)|\bunder (?:this |the )?(?:sub-?)?component\b"
    r"|\bthe (?:project|component|program(?:me)?) (?:will )?(?:finances?|supports?)\b"
    r"|^\(?[a-z0-9ivx]{1,4}[).]\s", re.I)
OUTCOME = re.compile(
    r"\bwill (?:also |further |thus |therefore |thereby |in turn |greatly )?"
    r"(?:enable|allow|help|contribut|improv|enhanc|increas|reduc|mitigat|facilitat|"
    r"make it|lead|result|benefit|boost|promot|foster|minimi[sz]|lower|empower)\w*"
    r"|\bby enabling\b|\b(?:is|are) expected to\b", re.I)


def firmness(text):
    return "activity" if ACTIVITY.search(text) else "outcome" if OUTCOME.search(text) else "none"


def floor(name):
    v = panel.get(f"floor.{name}")
    return float(v if v is not None else panel.require("floor.default"))


def setting(key):
    return panel.require(key)


# ---------------------------------------------------------------- reading scores

def scores(conn, kind, label_set=None):
    """Rows of the scores table for a kind (and set)."""
    t = f"{db.SCHEMA}.scores"
    if not db.exists(conn, t):
        return []
    sql = f"SELECT label_set, label_id, item_id, score, answer, served, route, round, cosine, rank FROM {t} WHERE kind = %s"
    params = [kind]
    if label_set:
        sql += " AND label_set = %s"
        params.append(label_set)
    return db.rows(conn, sql + " ORDER BY label_set, label_id, item_id", params)


def posterior(conn, kind, label_set, qshas, vector_of):
    """probability(label_id, item, score): through the label's layer when fitted and on."""
    lays = fits.layers(conn, "layer", label_set, qshas, vector_of)

    def p(lid, item, score):
        lay = lays.get(lid)
        return lay.probability(item, score) if lay else float(score)
    return p


def band():
    """The band rule's settings (band.*); only `on` is read while it is off."""
    on = bool(panel.get("band.on", False))
    if not on:
        return {"on": False}
    return {"on": True, "low": float(setting("band.low")), "keep": float(setting("band.keep")),
            "context": float(setting("band.context")), "second_grade": float(setting("band.second_grade"))}


def band_rule(post, ctx, second, b):
    """(committed, decided_by, waiting) for a sentence x measure pair under the
    band rule. A first-pass score of band.keep or more is committed on its own
    ('score'). A score in [band.low, band.keep) is committed only when the
    decision model, shown the paragraph, scores it band.context or more AND the
    chat model grades it band.second_grade or more ('second_look'). A pair in
    the band with either look missing is not committed and is waiting."""
    if post >= b["keep"]:
        return True, "score", False
    if post < b["low"]:
        return False, "", False
    if ctx is None or second is None:
        return False, "", True
    if ctx >= b["context"] and second >= b["second_grade"]:
        return True, "second_look", False
    return False, "", False


def in_band(post, b):
    """True for a first-pass score the second look is asked about."""
    return b["low"] <= post < b["keep"]


def look_scores(conn, kind, name):
    """{unit_id: score} of a second-look kind (measure_ctx, measure_second) of a measure."""
    return {uid: float(score) for _, _, uid, score, *_ in scores(conn, kind, name)}


def measure_units(conn, corpus, name):
    """{unit_id: row} of a measure's scored units with posterior, committed and
    asked_yes. With band.on, committed is the band rule (band_rule) and
    asked_yes is the same: the sentences the later stages ask about are the
    committed ones. Without it, committed is the posterior at the measure's
    floor and asked_yes the posterior at floor.ask. A row also says its first
    score (score_first), the two looks (score_context, second_grade, None when
    not asked), decided_by ('score', 'second_look' or '') and waiting (in the
    band, a look missing)."""
    qs, _ = Q.graded_questions(name)
    qshas = {lid: Q.sha(q) for lid, q in qs.items()}
    p = posterior(conn, "measure", name, qshas, corpus.vector)
    b = band()
    if b["on"]:
        ctx, second = look_scores(conn, "measure_ctx", name), look_scores(conn, "measure_second", name)
    else:
        fl, ask_fl = floor(name), float(setting("floor.ask"))
    out = {}
    for lset, lid, uid, score, answer, served, route, rnd, cos, rank in scores(conn, "measure", name):
        if uid not in corpus.row_of:
            continue
        post = p(lid, uid, score)
        row = {"label_id": lid, "score": float(score), "answer": answer, "served": served,
               "route": route, "round": rnd, "cosine": float(cos or 0), "posterior": post,
               "score_first": post, "score_context": None, "second_grade": None, "waiting": False}
        if b["on"]:
            row["score_context"], row["second_grade"] = ctx.get(uid), second.get(uid)
            row["committed"], row["decided_by"], row["waiting"] = band_rule(
                post, row["score_context"], row["second_grade"], b)
            row["asked_yes"] = row["committed"]
            row["in_band"] = in_band(post, b)
        else:
            row["committed"], row["asked_yes"] = post >= fl, post >= ask_fl
            row["decided_by"] = "score" if row["committed"] else ""
            row["in_band"] = False
        out[uid] = row
    return out


def committed_units(conn, corpus, names):
    """[unit_id] committed to any of the measures, in order: the sentences the
    digital and policy questions are asked about."""
    return sorted({u for n in names for u, r in measure_units(conn, corpus, n).items() if r["committed"]})


def digital_settings():
    """(on, floor): whether the digital rule applies (digital.on) and the P(yes) a sentence needs."""
    return bool(panel.get("digital.on", False)), float(panel.get("digital.floor", 0.5))


def digital_scores(conn):
    """{unit_id: P(yes)} of the digital question (kind digital)."""
    return {uid: float(score) for _, _, uid, score, *_ in scores(conn, "digital")}


def is_digital(p, on=None, fl=None):
    """Whether a sentence with the digital question's P(yes) `p` (None when not
    asked) counts as digital. With the rule off every sentence does; a sentence
    not asked is not digital."""
    on0, fl0 = digital_settings()
    on, fl = on0 if on is None else on, fl0 if fl is None else fl
    return True if not on else (p is not None and p >= fl)


def committed_digital_units(conn, corpus, names):
    """[unit_id] committed to any of the measures and digital: what every
    later question (assets, activities, links) is asked about."""
    dig, (on, fl) = digital_scores(conn), digital_settings()
    return [u for u in committed_units(conn, corpus, names) if is_digital(dig.get(u), on, fl)]


def instruments(conn):
    """{unit_id: 'investment' | 'policy'} from the policy question (kind policy)."""
    out = {}
    for _, _, uid, _, answer, *_ in scores(conn, "policy"):
        labs = (answer or {}).get("labels") or []
        if labs:
            out[uid] = labs[0]
    return out


def asset_map(conn, corpus):
    """{unit_id: {asset_id: posterior}} from the asset map's scores."""
    qs, _ = Q.graded_questions(Q.ASSETS)
    qshas = {lid: Q.sha(q) for lid, q in qs.items()}
    p = posterior(conn, "asset", Q.ASSETS, qshas, corpus.vector)
    out = defaultdict(dict)
    for lset, lid, uid, score, *_ in scores(conn, "asset"):
        if uid in corpus.row_of:
            out[uid][lid] = p(lid, uid, score)
    return out


def sentence_assets(own, earlier, s=None):
    """([(asset, probability)] best first, level) for a sentence: its own
    assets at asset.floor (level unit); else, with asset.decay on, the assets
    whose decayed sum over the earlier sentences of the paragraph reaches
    asset.paragraph_floor (level paragraph); else ([], none). Every asset at
    the floor with asset.multiple, the best only without."""
    s = s or asset_settings()
    rank = lambda d: sorted(d.items(), key=lambda x: (-x[1], x[0]))
    win = [(a, p) for a, p in rank(own) if p >= s["floor"]]
    if win:
        return (win if s["multiple"] else win[:1]), "unit"
    if not s["decay"]:
        return [], "none"
    total = dict(own)
    for k, prev in enumerate(earlier, 1):
        for a, p in prev.items():
            total[a] = total.get(a, 0.0) + (s["decay"] ** k) * p
    win = [(a, min(p, 1.0)) for a, p in rank(total) if p >= s["paragraph_floor"]]
    if win:
        return (win if s["multiple"] else win[:1]), "paragraph"
    return [], "none"


def asset_settings():
    return {"floor": float(setting("asset.floor")), "decay": float(setting("asset.decay")),
            "paragraph_floor": float(setting("asset.paragraph_floor")),
            "multiple": bool(setting("asset.multiple"))}


def unit_assets(corpus, amap):
    """{unit_id: (own {asset: p}, winners [(asset, p)], level)} for every
    indexed sentence, in reading order."""
    s = asset_settings()
    before, out = {}, {}
    for r in corpus.index:
        uid, pid = r["unit_id"], r["paragraph_id"]
        earlier = before.setdefault(pid, [])
        own = amap.get(uid, {})
        win, level = sentence_assets(own, [amap.get(u, {}) for u in reversed(earlier)], s)
        earlier.append(uid)
        out[uid] = (own, win, level)
    return out


def link_levels():
    return {x.strip() for x in str(setting("dashboard.commitment_link_levels")).split("|") if x.strip()}


def linked_assets(ua, levels=None):
    """The assets a unit's commitment carries, at the levels the panel counts."""
    levels = link_levels() if levels is None else levels
    own, win, level = ua
    return {a for a, _ in win} if level in levels and level != "none" else set()


def types_of(conn, corpus, name):
    """{unit_id: {type_id: posterior}} of a measure's type scores."""
    tq = Q.type_questions(name)
    qshas = {t: Q.sha(q) for ts in tq.values() for t, q in ts.items()}
    p = posterior(conn, "type", name, qshas, corpus.vector)
    out = defaultdict(dict)
    for lset, tid, uid, score, *_ in scores(conn, "type", name):
        out[uid][tid] = p(tid, uid, score)
    return out


def type_winners(probs):
    """The types that count, likeliest first: the approved types at
    floor.types (every one with types.multiple, else the best); the OTH
    types only where no approved type counts."""
    fl, multiple = float(setting("floor.types")), bool(setting("types.multiple"))
    pick = lambda d: sorted((t for t, q in d.items() if q >= fl), key=lambda t: (-d[t], t))
    win = pick({t: q for t, q in probs.items() if not t.startswith(OTH)})
    if not win:
        win = pick({t: q for t, q in probs.items() if t.startswith(OTH)})
    return win if multiple else win[:1]


def pair_checks(conn, corpus):
    """[(unit_id, set_a, set_b, p_keep_a, p_keep_b, raw_a, raw_b, served)]."""
    rows = defaultdict(dict)
    served = {}
    for lset, side, uid, score, answer, sv, *_ in scores(conn, "pair"):
        a, b = lset.split("|")
        rows[(uid, a, b)][side] = float(score)
        served[(uid, a, b)] = sv
    qa = {}
    out = []
    for (uid, a, b), sides in sorted(rows.items()):
        if "keep_a" not in sides or "keep_b" not in sides:
            continue
        for s, other, key in ((a, b, "keep_a"), (b, a, "keep_b")):
            if (s, other) not in qa:
                qa[(s, other)] = Q.sha(Q.pair_questions(s, other)[key]) if s < other or True else None
        pa = posterior(conn, "pair", f"{a}|{b}", {"keep_a": qa[(a, b)], "keep_b": qa[(b, a)]}, corpus.vector)
        out.append((uid, a, b, pa("keep_a", uid, sides["keep_a"]), pa("keep_b", uid, sides["keep_b"]),
                    sides["keep_a"], sides["keep_b"], served[(uid, a, b)]))
    return out


# ---------------------------------------------------------------- the tables

def _probs(answer):
    pr = {int(k): float(v) for k, v in answer["probabilities"].items()}
    return {f"p{i}": f"{pr.get(i, 0.0):.4f}" for i in range(4)}


def measure_rows(conn, corpus, names, ua, stats=None):
    """One row per scored sentence x measure. stats, a dict, collects the count
    of pairs waiting for a second look and of committed pairs with no digital answer."""
    fl_rows, stats = [], {} if stats is None else stats
    dig, (dig_on, dig_floor), instrument = digital_scores(conn), digital_settings(), instruments(conn)
    for name in names:
        units = measure_units(conn, corpus, name)
        tps = types_of(conn, corpus, name)
        pats = labels.load_patterns(name)
        mapping = labels.type_resolution(name)[0]
        for uid, u in units.items():
            r = corpus.row(uid)
            stats["waiting"] = stats.get("waiting", 0) + u["waiting"]
            stats["no_digital_answer"] = stats.get("no_digital_answer", 0) + (
                dig_on and u["committed"] and uid not in dig)
            own, win, level = ua.get(uid, ({}, [], "none"))
            best = max(own, key=own.get) if own else ""
            probs = tps.get(uid, {})
            yes = type_winners(probs) if u["committed"] else []
            shown = []
            for t in yes:
                t2 = mapping.get((u["label_id"], t), t)
                if t2 not in shown:
                    shown.append(t2)
            text = corpus.texts[uid]
            fl_rows.append({
                "label_set": name, "label_id": u["label_id"], "unit_id": uid, "kind": r.get("kind", ""),
                "paragraph_id": r["paragraph_id"], "doc_id": r["doc_id"], "project_ids": r.get("project_ids", ""),
                "component_number": r.get("component_number", ""),
                "subcomponent_number": r.get("subcomponent_number", ""),
                "component_key": r.get("component_key", ""), "copies": r.get("copies", ""),
                "route": u["route"], "round": u["round"], "score": f"{u['cosine']:.6f}",
                "patterns": "|".join(p["pattern_id"] for p in pats if p["regex"].search(text)),
                "firmness": firmness(text),
                "asset_unit": best, "asset_p_unit": f"{own[best]:.4f}" if best else "",
                "assets_yes": "|".join(a for a, _ in win) if level == "unit" else "",
                "asset_link": win[0][0] if win else "", "asset_link_level": level,
                "asset_link_p": f"{win[0][1]:.4f}" if win else "",
                "asset_links": "|".join(a for a, _ in win),
                "template_projects": r.get("template_projects", ""),
                "template_share": r.get("template_share", ""),
                "sent": "true", "committed": str(u["committed"]).lower(),
                "confidence": f"{float(u['answer'].get('confidence', 0) or 0):.3f}",
                **_probs(u["answer"]), "p_committed_calibrated": f"{u['posterior']:.4f}",
                "types_yes": "|".join(shown), "best_type": shown[0] if shown else "",
                "best_p": f"{probs[yes[0]]:.3f}" if yes else "",
                "score_first": f"{u['score_first']:.4f}",
                "score_context": "" if u["score_context"] is None else f"{u['score_context']:.4f}",
                "second_grade": "" if u["second_grade"] is None else f"{u['second_grade']:g}",
                "decided_by": u["decided_by"],
                "is_digital": str(is_digital(dig.get(uid), dig_on, dig_floor)).lower(),
                "digital_p": f"{dig[uid]:.4f}" if uid in dig else "",
                "instrument": instrument.get(uid, ""),
                "model_served": u["served"] or "", "text": text})
    return fl_rows


def asset_rows(conn, corpus, amap, ua):
    grades, assets = [], []
    qs, _ = Q.graded_questions(Q.ASSETS)
    for lset, lid, uid, score, answer, served, route, rnd, cos, rank in scores(conn, "asset"):
        if uid not in corpus.row_of:
            continue
        r = corpus.row(uid)
        grades.append({"label_set": lset, "unit_id": uid, "paragraph_id": r["paragraph_id"],
                       "doc_id": r["doc_id"], "project_ids": r.get("project_ids", ""),
                       "component_number": r.get("component_number", ""), "label_id": lid,
                       "asked": "true", "route": route, "round": rnd, "best_cosine": f"{float(cos or 0):.6f}",
                       "confidence": f"{float(answer.get('confidence', 0) or 0):.3f}", **_probs(answer),
                       "p_committed_calibrated": f"{amap[uid][lid]:.4f}", "model_served": served or ""})
    for r in corpus.index:
        uid = r["unit_id"]
        own, win, level = ua[uid]
        best = max(own, key=own.get) if own else ""
        base = {"unit_id": uid, "paragraph_id": r["paragraph_id"], "project_ids": r.get("project_ids", ""),
                "asset_level": level, "own_best_asset_id": best,
                "own_best_probability": f"{own[best]:.4f}" if best else ""}
        for k, (a, p) in enumerate(win, 1):
            assets.append(dict(base, asset_rank=k, asset_id=a, probability=f"{p:.4f}"))
        if not win:
            assets.append(dict(base, asset_rank=0, asset_id="", probability=""))
    return grades, assets


def pair_rows(conn, corpus):
    fl = float(setting("floor.pair_check"))
    out = []
    for uid, a, b, ka, kb, ra, rb, served in pair_checks(conn, corpus):
        d = ("both" if ka >= fl and kb >= fl else "only_a" if ka >= fl else "only_b" if kb >= fl
             else "neither")
        out.append({"unit_id": uid, "set_a": a, "set_b": b, "decision": d, "score_a": f"{ra:.3f}",
                    "score_b": f"{rb:.3f}", "p_keep_a": f"{ka:.3f}", "p_keep_b": f"{kb:.3f}",
                    "model_served": served or ""})
    return out


def package_rows(conn, corpus, ua, packages):
    """(package_assets rows, commitment_packages rows)."""
    pq = Q.package_asset_questions()
    pa = posterior(conn, "package_asset", Q.ASSETS, {a: Q.sha(q) for a, q in pq.items()}, lambda i: None)
    by_pkg = {p["package_id"]: p for p in packages}
    assets, has = [], defaultdict(set)
    yes = float(setting("link.asset_yes"))
    for lset, aid, pid, score, answer, served, route, rnd, cos, rank in scores(conn, "package_asset"):
        if pid not in by_pkg:
            continue
        prob = pa(aid, pid, score)
        assets.append({"package_id": pid, "project_id": by_pkg[pid]["project_id"], "label_id": aid,
                       "cosine": f"{float(cos or 0):.4f}", "cosine_rank": rank or "",
                       "probability": f"{float(score):.3f}", "p_calibrated": f"{prob:.3f}",
                       "model_served": served or ""})
        if prob >= yes:
            has[pid].add(aid)
    pl = posterior(conn, "link", "links", {"link": Q.sha(Q.LINK_QUESTION)}, lambda i: None)
    links = []
    levels = link_levels()
    for lset, lid, item, score, answer, served, route, rnd, cos, rank in scores(conn, "link"):
        uid, pid = item.rsplit("|", 1)
        if pid not in by_pkg or uid not in corpus.row_of:
            continue
        shared = linked_assets(ua.get(uid, ({}, [], "none")), levels) & has.get(pid, set())
        links.append({"label_set": lset, "label_id": lid, "unit_id": uid, "package_id": pid,
                      "project_id": by_pkg[pid]["project_id"], "shared_asset_ids": "|".join(sorted(shared)),
                      "route": route, "measure_rank": rank or "",
                      "measure_cosine": f"{float(cos):.4f}" if cos is not None else "",
                      "probability": f"{float(score):.3f}", "p_calibrated": f"{pl('link', item, score):.3f}",
                      "model_served": served or ""})
    return assets, links


def publish(conn, data, *, corpus=None, log=print, views=True):
    """Rewrite every decision table from the scores as the panel reads them
    now, then the views. Returns {table: rows}."""
    from wbg_portfolio import checks
    t0 = time.time()
    corpus = corpus or Q.Corpus(data)
    names = [n for n in labels.label_sets() if n != Q.ASSETS]
    amap = asset_map(conn, corpus)
    ua = unit_assets(corpus, amap)
    grades, assets = asset_rows(conn, corpus, amap, ua)
    counts = {}
    findings = measure_rows(conn, corpus, names, ua, counts)
    if counts.get("waiting"):
        log(f"  {counts['waiting']:,} pair(s) in the band are waiting for a second look; not committed")
    if counts.get("no_digital_answer"):
        log(f"  {counts['no_digital_answer']:,} committed pair(s) have no digital answer yet; not digital")
    pairs = pair_rows(conn, corpus)
    packages = checks.load_packages(data)
    pk_assets, links = package_rows(conn, corpus, ua, packages)
    written = {}
    with load_pg.transaction(conn):
        load_pg.prepare(conn)
        for table, cols, rows in (("labels", load_pg.LABEL_COLS, load_pg.label_rows()),
                                  ("measure_types", load_pg.TYPE_COLS, load_pg.type_rows(data)),
                                  *zip(("label_queries", "label_patterns", "label_scale"),
                                       (load_pg.QUERY_COLS, load_pg.PATTERN_COLS, load_pg.SCALE_COLS),
                                       load_pg.definition_rows()),
                                  ("measure_findings", load_pg.FINDING_COLS, findings),
                                  ("paragraph_grades", load_pg.GRADE_COLS, grades),
                                  ("unit_assets", load_pg.UNIT_ASSET_COLS, assets),
                                  ("unit_pair_checks", load_pg.PAIR_COLS, pairs),
                                  ("package_assets", load_pg.PACKAGE_ASSET_COLS, pk_assets),
                                  ("commitment_packages", load_pg.LINK_COLS, links)):
            written[table] = load_pg.load_rows(conn, table, cols, rows, "decide")[0]
        if views:
            load_pg.finish(conn, log=log)
    # The reads above opened a transaction on this connection, so the block's
    # load_rows ran as savepoints inside it. Only this commit makes the publish
    # real: without it, ./wbg_portfolio run's publish stage prints its counts and the
    # process exit throws them away, leaving the dashboards at the last publish.
    conn.commit()
    log("  postgres: " + ", ".join(f"{t} {n:,}" for t, n in written.items()) + f" ({time.time() - t0:.0f}s)")
    return written
