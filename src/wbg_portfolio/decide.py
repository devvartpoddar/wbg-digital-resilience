"""Every decision, read once from the scores and the control panel, into the
tables the dashboards' views read (docs/method/decisions.md).

A score is the model's answer (src/wbg_portfolio/funnel.py, src/wbg_portfolio/checks.py); a decision is
that score, through the label's probability layer when one is fitted and on,
against the measure's band rule (a first-pass score, then two second looks in
the band) or the other labels' floors. Nothing here asks the model; a moved
threshold is a publish. The one place that says what "committed" means is committed();
the views only join and aggregate.

  publish(conn, data)   rewrite measure_findings, paragraph_grades, unit_assets,
                        package_assets, commitment_package_links, commitments and
                        commitment_members (the commitment rule, no model call), the
                        label tables, then the views
"""
import hashlib
import re
import time
from collections import defaultdict

from wbg_portfolio import db
from wbg_portfolio import fits
from wbg_portfolio import labels
from wbg_portfolio import load_pg
from wbg_portfolio import panel
from wbg_portfolio import questions as Q

OTH = Q.OTH
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


def setting(key):
    return panel.require(key)


# ---------------------------------------------------------------- reading scores

def measure_sets():
    """Every measure set (every label set but the assets)."""
    return [n for n in labels.label_sets() if n != Q.ASSETS]


def question_shas(kind, label_set=None):
    """{(label_set, label_id): sha} of the question each label of a kind is asked now (every measure
    set for a measure-level kind when no set is named). A score stored under another sha was asked
    under an earlier wording and is not an answer to the current question."""
    if kind in ("measure", "measure_ctx", "measure_second", "type", "package_measure"):
        out = {}
        for name in ([label_set] if label_set else measure_sets()):
            labs, scale = Q.load(name)
            for lab in labs:
                if kind == "measure":
                    q = {lab["id"]: Q.graded_question(lab, scale)}
                elif kind == "measure_ctx":
                    q = {lab["id"]: Q.in_context(Q.graded_question(lab, scale))}
                elif kind == "measure_second":
                    q = {lab["id"]: Q.second_grade_question(lab, scale)}
                elif kind == "package_measure":
                    q = {lab["id"]: Q.package_measure_question(lab)}
                else:
                    q = {**Q.type_questions(name).get(lab["id"], {}),
                         **Q.oth_type_questions(name).get(lab["id"], {})}
                out.update({(name, lid): Q.sha(x) for lid, x in q.items()})
        return out
    if kind == "digital":
        qs = {"digital": Q.digital_question()}
    elif kind == "policy":
        qs = {"policy": Q.policy_question()}
    elif kind == "asset":
        qs = Q.graded_questions(Q.ASSETS)[0]
    elif kind == "asset_second":
        qs = {"second": Q.asset_second_question(Q.asset_catalogue()[0])}
    elif kind == "asset_subtype":
        assets, subtypes = Q.asset_catalogue()
        qs = {a["id"]: Q.asset_subtype_question(a, subtypes[a["id"]]) for a in assets if subtypes.get(a["id"])}
    elif kind == "package_asset":
        qs = Q.package_asset_questions()
    elif kind == "package_asset_second":
        qs = {a["id"]: Q.package_asset_second_question(a) for a in Q.asset_catalogue()[0]}
    elif kind == "package_asset_subtype":
        qs = Q.package_subtype_questions()
    else:
        raise ValueError(f"scores: no current question known for kind {kind!r}")
    lset = kind if kind in ("digital", "policy") else Q.ASSETS
    return {(lset, lid): Q.sha(q) for lid, q in qs.items()}


IGNORED = {}            # (kind, label_set) -> rows the last read left out as asked under an earlier question


def scores(conn, kind, label_set=None, log=print):
    """Rows of the scores table for a kind (and set) whose question is the current one
    (question_shas). A row asked under an earlier wording is left out, read as not asked yet, and
    counted in IGNORED; the count is logged once per kind, set and number."""
    t = f"{db.SCHEMA}.scores"
    if not db.exists(conn, t):
        return []
    sql = (f"SELECT label_set, label_id, item_id, score, answer, served, route, round, cosine, rank, "
           f"question_sha FROM {t} WHERE kind = %s")
    params = [kind]
    if label_set:
        sql += " AND label_set = %s"
        params.append(label_set)
    current = question_shas(kind, label_set)
    out, dropped = [], 0
    for r in db.rows(conn, sql + " ORDER BY label_set, label_id, item_id", params):
        if current.get((r[0], r[1])) == r[10]:
            out.append(r[:10])
        else:
            dropped += 1
    key = (kind, label_set or "")
    if dropped and IGNORED.get(key) != dropped and log:
        log(f"  scores: {dropped:,} {kind} row(s){' of ' + label_set if label_set else ''} were asked under an "
            f"earlier wording of the question; ignored (read as not asked yet)")
    IGNORED[key] = dropped
    return out


def posterior(conn, kind, label_set, qshas, vector_of):
    """probability(label_id, item, score): through the label's layer when fitted and on."""
    lays = fits.layers(conn, "layer", label_set, qshas, vector_of)

    def p(lid, item, score):
        lay = lays.get(lid)
        return lay.probability(item, score) if lay else float(score)
    return p


def band():
    """The band rule's settings (band.*)."""
    b = {"low": float(setting("band.low")), "keep": float(setting("band.keep")),
         "context": float(setting("band.context")), "second_grade": float(setting("band.second_grade")),
         "ask_low": float(setting("band.ask_low")), "ask_high": float(setting("band.ask_high"))}
    check_asked("band.", b["ask_low"], b["low"], b["ask_high"], b["keep"])
    return b


def check_asked(prefix, ask_low, low, ask_high, keep):
    """Fail loudly when the decision band is wider than the asked band: a pair the
    decision needs a second look for, but that was never asked, would wait forever."""
    if ask_low > low:
        raise SystemExit(f"panel: {prefix}ask_low ({ask_low}) is above the lowest score the decision reads "
                         f"a second look for ({low}); lower {prefix}ask_low to {low} or less, else pairs "
                         f"between them would wait for a second look that is never asked")
    if ask_high < keep:
        raise SystemExit(f"panel: {prefix}ask_high ({ask_high}) is below the score that is kept on its own "
                         f"({keep}); raise {prefix}ask_high to {keep} or more")


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


def asked_band(post, b):
    """True for a first-pass score the second look is asked about: band.ask_low up to
    band.ask_high. What it decides is band_rule's, from band.low and band.keep."""
    return b["ask_low"] <= post < b["ask_high"]


def look_scores(conn, kind, name):
    """{unit_id: score} of a second-look kind (measure_ctx, measure_second) of a measure."""
    return {uid: float(score) for _, _, uid, score, *_ in scores(conn, kind, name)}


def measure_units(conn, corpus, name):
    """{unit_id: row} of a measure's scored units with posterior, committed and
    asked. committed is the band rule (band_rule): the sentences the later
    stages ask about are the committed ones. A row also says its first
    score (score_first), the two looks (score_context, second_grade, None when
    not asked), decided_by ('score', 'second_look' or '') and waiting (in the
    band, a look missing)."""
    qs, _ = Q.graded_questions(name)
    qshas = {lid: Q.sha(q) for lid, q in qs.items()}
    p = posterior(conn, "measure", name, qshas, corpus.vector)
    b = band()
    ctx, second = look_scores(conn, "measure_ctx", name), look_scores(conn, "measure_second", name)
    dig, dig_low = digital_scores(conn), digital_ask_low()
    out = {}
    for lset, lid, uid, score, answer, served, route, rnd, cos, rank in scores(conn, "measure", name):
        if uid not in corpus.row_of:
            continue
        post = p(lid, uid, score)
        row = {"label_id": lid, "score": float(score), "answer": answer, "served": served,
               "route": route, "round": rnd, "cosine": float(cos or 0), "posterior": post,
               "score_first": post, "score_context": None, "second_grade": None, "waiting": False}
        row["score_context"], row["second_grade"] = ctx.get(uid), second.get(uid)
        row["committed"], row["decided_by"], row["waiting"] = band_rule(
            post, row["score_context"], row["second_grade"], b)
        row["asked"] = asked_band(post, b)
        if row["waiting"] and dig.get(uid, 1.0) < dig_low:
            row["waiting"] = False                  # not digital, so never asked its second looks: not waiting
        out[uid] = row
    return out


def committed_units(conn, corpus, names):
    """[unit_id] committed to any of the measures, in order."""
    return sorted({u for n in names for u, r in measure_units(conn, corpus, n).items() if r["committed"]})


def digital_asked_units(conn, corpus, names):
    """[unit_id] the digital question is asked about: every sentence with a first-pass score
    in the asked band [band.ask_low, band.ask_high) for at least one of the measures (the range the
    measure second look is asked for), so a threshold loosened at publish finds the newly committed
    sentences already scored. Committed sentences outside that range are added."""
    asked = {u for n in names for u, r in measure_units(conn, corpus, n).items() if r.get("asked")}
    return sorted(asked | set(committed_units(conn, corpus, names)))


def digital_floor():
    """The P(yes) the digital question needs for a sentence to count as digital (digital.floor)."""
    fl = float(setting("digital.floor"))
    digital_ask_low(fl)
    return fl


def digital_ask_low(floor=None):
    """The P(yes) from which a sentence is asked its measure second looks (digital.ask_low); fails loudly when
    it is above digital.floor, because a sentence counted as digital would then have no second look."""
    low = float(setting("digital.ask_low"))
    fl = float(setting("digital.floor")) if floor is None else floor
    if low > fl:
        raise SystemExit(f"panel: digital.ask_low ({low}) is above digital.floor ({fl}); lower digital.ask_low "
                         f"to {fl} or less, else sentences counted as digital would never be asked their "
                         f"second looks")
    return low


def digital_scores(conn):
    """{unit_id: P(yes)} of the digital question (kind digital)."""
    return {uid: float(score) for _, _, uid, score, *_ in scores(conn, "digital")}


def is_digital(p, fl=None):
    """Whether a sentence with the digital question's P(yes) `p` (None when not
    asked) counts as digital: a sentence not asked is not digital."""
    fl = digital_floor() if fl is None else fl
    return p is not None and p >= fl


def only_digital(conn, units):
    """The units the digital rule keeps."""
    dig, fl = digital_scores(conn), digital_floor()
    return [u for u in units if is_digital(dig.get(u), fl)]


def committed_digital_units(conn, corpus, names):
    """[unit_id] committed to any of the measures and digital: what every
    later question (assets, links) is asked about."""
    return only_digital(conn, committed_units(conn, corpus, names))


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


def sentence_assets(own, earlier, s=None, second=None):
    """([(asset, probability)] best first, level) for a sentence: its own
    assets at asset.floor (level unit); else the assets the chat model's
    second look named (`second`, a list of asset ids; level second_look,
    probability None: the second look gives no probability); else, with
    asset.decay on, the assets whose decayed sum over the earlier sentences
    of the paragraph reaches asset.paragraph_floor (level paragraph); else
    ([], none). Every asset at the floor with asset.multiple, the best only
    without."""
    s = s or asset_settings()
    rank = lambda d: sorted(d.items(), key=lambda x: (-x[1], x[0]))
    win = [(a, p) for a, p in rank(own) if p >= s["floor"]]
    if win:
        return (win if s["multiple"] else win[:1]), "unit"
    if second:
        return [(a, None) for a in (second if s["multiple"] else second[:1])], "second_look"
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


def asset_ask_high():
    """The best first-pass asset score under which a committed, digital sentence also gets the
    chat model's asset opinion (asset.ask_high); never below asset.floor, which decides."""
    high, fl = float(setting("asset.ask_high")), float(setting("asset.floor"))
    if high < fl:
        raise SystemExit(f"panel: asset.ask_high ({high}) is below asset.floor ({fl}); raise asset.ask_high "
                         f"to {fl} or more, else sentences the first pass gave no asset would not be asked")
    return high


def asset_second_looks(conn):
    """{unit_id: [asset_id]} the chat model's second look named (kind
    asset_second), only assets the asset file still has; a sentence it named
    none for is absent. (An answer stored before 2026-11-21 named "asset/type";
    only the asset is read.)"""
    known = {a["id"] for a in labels.load_set(Q.ASSETS) if a["active"] == "true"}
    out = {}
    for _, _, uid, _, answer, *_ in scores(conn, "asset_second"):
        ids = [x.partition("/")[0] for x in (answer or {}).get("labels") or []]
        ids = [a for a in dict.fromkeys(ids) if a in known]
        if ids:
            out[uid] = ids
    return out


def asset_types(conn):
    """{(unit_id, asset_id): subtype_id} of the assets that count (kind
    asset_subtype), whichever look found them; absent where the question was not asked or answered."""
    out = {}
    for _, aid, uid, _, answer, *_ in scores(conn, "asset_subtype"):
        labs = (answer or {}).get("labels") or []
        if labs:
            out[(uid, aid)] = labs[0]
    return out


def unit_assets(corpus, amap, second=None):
    """{unit_id: (own {asset: p}, winners [(asset, p)], level)} for every
    indexed sentence, in reading order. `second` is asset_second_looks()."""
    second = second or {}
    s = asset_settings()
    before, out = {}, {}
    for r in corpus.index:
        uid, pid = r["unit_id"], r["paragraph_id"]
        earlier = before.setdefault(pid, [])
        own = amap.get(uid, {})
        win, level = sentence_assets(own, [amap.get(u, {}) for u in reversed(earlier)], s, second.get(uid, []))
        earlier.append(uid)
        out[uid] = (own, win, level)
    return out


def has_scores(conn, kind):
    """Whether any score of this kind is stored (what a dry run needs of the step before)."""
    t = f"{db.SCHEMA}.scores"
    return db.exists(conn, t) and bool(db.one(conn, f"SELECT count(*) FROM {t} WHERE kind = %s", (kind,))[0])


def package_band():
    """(low, yes): the first-pass P(yes) of a package x asset pair from which a chat yes counts
    (link.asset_band_low) and at which it counts without one (link.asset_yes). What the chat model
    is asked about is wider: package_ask()."""
    low, yes = float(setting("link.asset_band_low")), float(setting("link.asset_yes"))
    package_ask(low, yes)
    return low, yes


def package_ask(low=None, yes=None):
    """(ask_low, ask_high): the first-pass P(yes) of a package x asset pair that gets the chat
    model's second look (link.asset_ask_low up to link.asset_ask_high); checked against the
    decision band."""
    low = float(setting("link.asset_band_low")) if low is None else low
    yes = float(setting("link.asset_yes")) if yes is None else yes
    a, b = float(setting("link.asset_ask_low")), float(setting("link.asset_ask_high"))
    check_asked("link.asset_", a, low, b, yes)
    return a, b


def package_asset_posteriors(conn):
    """{(package_id, asset_id): P(yes)} of the first-pass package-to-asset
    question, through the asset's layer when one is fitted."""
    pq = Q.package_asset_questions()
    pa = posterior(conn, "package_asset", Q.ASSETS, {a: Q.sha(q) for a, q in pq.items()}, lambda i: None)
    return {(pid, aid): pa(aid, pid, score)
            for _, aid, pid, score, *_ in scores(conn, "package_asset")}


def package_asset_sets(conn, only=None):
    """{package_id: {asset_id}} the packages have. An asset counts at
    link.asset_yes or more, and from link.asset_band_low
    up to link.asset_yes when the chat model's second look (kind
    package_asset_second) says yes. A missing second look is never a yes.
    `only` limits the packages (indexed once as a set, so a list of 14,000 costs nothing per pair)."""
    only = None if only is None else set(only)
    low, yes = package_band()
    second = {(pid, aid): float(score) >= 1 for _, aid, pid, score, *_ in scores(conn, "package_asset_second")}
    out = defaultdict(set)
    for (pid, aid), p in package_asset_posteriors(conn).items():
        if only is not None and pid not in only:
            continue
        if p >= yes or (low <= p < yes and second.get((pid, aid))):
            out[pid].add(aid)
    return out


def package_asset_types(conn):
    """{(package_id, asset_id): subtype_id} of the assets a package has (kind package_asset_subtype)."""
    out = {}
    for _, aid, pid, _, answer, *_ in scores(conn, "package_asset_subtype"):
        labs = (answer or {}).get("labels") or []
        if labs:
            out[(pid, aid)] = labs[0]
    return out


def package_measure_scores(conn):
    """{(label_set, package_id): P(yes)} of the package-alone question about a measure."""
    return {(lset, pid): float(score) for lset, _, pid, score, *_ in scores(conn, "package_measure")}


# The levels of a link, most to least telling (docs/method/links.md says what each can be trusted to mean).
LINK_LEVELS = ("strong", "type_and_component", "type", "component", "asset")
# A package whose description is about running the project rather than a digital thing (published as a
# flag, never a cut). Whole words only, and no bare "communication": "Unified Communication Tools" is not
# generic. An audit of security or of an information system is not generic either.
GENERIC = re.compile(r"\b(?:project management|project implementation unit|PIU|PMU|audit(?:s|ing|or|ors)?|"
                     r"vehicles?|(?:office )?furniture|workshops?|per diem|accounting|accountants?|"
                     r"salar(?:y|ies))\b", re.I)
NOT_GENERIC = re.compile(r"\b(?:security|cyber\w*|IT|information systems?|data)\s+audit", re.I)


def is_generic(description):
    """True when a package description matches the generic pattern (GENERIC) and is not an audit of
    security or of a system (NOT_GENERIC)."""
    d = description or ""
    return bool(GENERIC.search(d)) and not NOT_GENERIC.search(d)


# The plan statuses (as clean_procurement writes them) that mean a package was cancelled: the one list,
# read by the published package_cancelled flag here and by the views' wbg.is_cancelled (package_status).
CANCELLED = ("Canceled", "Terminated")


def is_cancelled(status):
    return (status or "").strip() in CANCELLED


def top_component(x):
    """The top-level component of a plan or appraisal component ('Component 2.1', '2b', 'II' -> '2'; 'D',
    'Component C.1' -> 'D', 'C'), '' when there is none."""
    t = (x or "").strip().lower()
    t = re.sub(r"^(component|composante|componente|sub-?component)\s*", "", t)
    m = re.match(r"(\d+)", t)
    if m:
        return str(int(m.group(1)))
    m = re.match(r"([ivx]+)\b", t)
    if m:
        vals, total, prev = {"i": 1, "v": 5, "x": 10}, 0, 0
        for ch in reversed(m.group(1)):
            v = vals[ch]
            total, prev = (total - v, prev) if v < prev else (total + v, v)
        return str(total)
    m = re.match(r"([a-h])(?:\b|[.:)])", t)
    return m.group(1).upper() if m else ""


def component_relation(a, b):
    """same, different, or unknown when either side has no top-level component."""
    if not a or not b:
        return "unknown"
    return "same" if a == b else "different"


def link_level(strong, same_type, relation):
    if strong:
        return "strong"
    if same_type:
        return "type_and_component" if relation == "same" else "type"
    return "component" if relation == "same" else "asset"


def link_strength_rows(conn, corpus, names, ua, packages, asset_types_=None):
    """The commitment_package_links rows. A kept sentence and a package of the same project are
    linked when the package, asked alone, is about the sentence's measure at link.strong_yes or more
    (strength strong), or when they share a top-level asset (the sentence's, whichever look found it,
    and the package's; strength asset). A pair that is both is one row, strong. Each link
    also has a level (LINK_LEVELS): strong; else by whether the sentence and the package share an
    asset type (`asset_types_` is asset_types(); the package's from package_asset_types(); unspecified never
    counts) and a top-level component (both known and equal). The levels only order the links: none
    is added or removed by them. The package's cancelled and generic flags ride along."""
    strong_yes = float(setting("link.strong_yes"))
    has = package_asset_sets(conn)
    asked = package_measure_scores(conn)
    stype_of = asset_types(conn) if asset_types_ is None else asset_types_
    ptype_of = package_asset_types(conn)
    by_project = defaultdict(list)
    for p in packages:
        by_project[p["project_id"]].append(p)
    typed = lambda pairs: {(a, t) for a, t in pairs if t and t != Q.UNSPECIFIED}
    out = []
    for name in names:
        for u in committed_digital_units(conn, corpus, [name]):
            mine = linked_assets(ua.get(u, ({}, [], "none")))
            stypes = typed((a, stype_of.get((u, a), "")) for a in mine)
            scomp = top_component(corpus.row(u).get("component_number", ""))
            for pid in corpus.projects(u):
                for p in by_project.get(pid, []):
                    pkg = p["package_id"]
                    shared = mine & has.get(pkg, set())
                    sp = asked.get((name, pkg))
                    strong = sp is not None and sp >= strong_yes
                    if not strong and not shared:
                        continue
                    same_type = bool({x for x in stypes if x[0] in shared}
                                     & typed((a, ptype_of.get((pkg, a), "")) for a in shared))
                    pcomp = top_component(p.get("component_number", ""))
                    rel = component_relation(scomp, pcomp)
                    level = link_level(strong, same_type, rel)
                    out.append({"project_id": pid, "label_set": name, "unit_id": u,
                                "package_id": pkg, "strength": "strong" if strong else "asset",
                                "shared_asset_ids": "|".join(sorted(shared)),
                                "strong_p": "" if sp is None else f"{sp:.4f}",
                                "package_category": p.get("category", ""),
                                "level": level, "level_rank": LINK_LEVELS.index(level) + 1,
                                "same_type": str(same_type).lower(), "component_relation": rel,
                                "sentence_component": scomp, "package_component": pcomp,
                                "package_cancelled": str(is_cancelled(p.get("status"))).lower(),
                                "package_generic": str(is_generic(p.get("text"))).lower()})
    return sorted(out, key=lambda r: (r["label_set"], r["unit_id"], r["level_rank"], r["package_id"]))


def linked_assets(ua):
    """The assets a unit's commitment carries: the assets the sentence has (unit_assets), whichever
    level found them."""
    own, win, level = ua
    return {a for a, _ in win} if level != "none" else set()


# ---------------------------------------------------------------- commitments
#
# The commitment rule (docs/method/decisions.md, "Commitments"), no model call: the kept sentence x
# measure pairs of a project grouped by what they commit to.

COMMITMENT_KEYS = ("asset_type", "type", "paragraph")
PARAGRAPH_LABEL = "Sentences of one paragraph"


def commitment_keys(instrument, assets, types, paragraph_id):
    """[(key, asset_id, subtype_id, type_id, paragraph_id)] of one kept sentence of a project and measure.
    An investment sentence (or one whose instrument is not known yet, keyed as investment) belongs to one commitment per
    asset it has with a stated type (assets: [(asset_id, subtype_id)]; unspecified or no type yet is
    not stated); with no asset at all, to one per measure type it has (types); a policy sentence to one
    per measure type. A sentence left with none of these (no asset and no type, only unspecified asset
    types, a policy sentence with no type) is grouped by its paragraph."""
    if instrument != "policy":
        typed = [(a, t) for a, t in assets if t and t != Q.UNSPECIFIED]
        if typed:
            return [("asset_type", a, t, "", "") for a, t in typed]
        if assets:
            return [("paragraph", "", "", "", paragraph_id)]
    if types:
        return [("type", "", "", t, "") for t in types]
    return [("paragraph", "", "", "", paragraph_id)]


def commitment_assets(ua, type_of):
    """{unit_id: [(asset_id, subtype_id)]}: each sentence's assets (unit_assets()) with their types
    (asset_types(); '' where none was asked yet)."""
    return {u: [(a, type_of.get((u, a), "")) for a in sorted(linked_assets(x))] for u, x in ua.items()}


def commitment_names():
    """(asset names, {(asset, subtype): name}, {(measure, type): name}) from the label files, for a
    commitment's label. A proposed (OTH) type is named by its readable name, marked "(proposed)"."""
    assets, subtypes = Q.asset_catalogue()
    types = {}
    for name in measure_sets():
        shown = labels.type_resolution(name)[1]
        types.update({(name, t): v["type_name"] for (_, t), v in shown.items()})
        for t in Q.oth_types(name):
            n = t["type_name"][len(OTH):].strip() if t["type_name"].startswith(OTH) else t["type_name"]
            types.setdefault((name, t["type_id"]), f"{n} (proposed)")
    return ({a["id"]: a["name"] for a in assets},
            {(a, s["id"]): s["name"] for a, v in subtypes.items() for s in v}, types)


def commitment_rows(findings, assets_of, links, names_of):
    """(commitments rows, commitment_members rows) from the measure_findings rows (kept ones only:
    committed 'true'), each sentence's assets with their types (commitment_assets()) and the
    commitment_package_links rows. `names_of` is commitment_names(). One commitment per project,
    measure, instrument and key (commitment_keys); it carries its type tags (every type of its
    sentences), its counts of sentences, paragraphs and documents, the component numbers seen, and its
    links: the packages linked to any of its sentences, each at its best level."""
    assets_n, subtypes_n, types_n = names_of
    best = defaultdict(dict)                                   # (project, measure, unit) -> {package: rank}
    for r in links:
        d = best[(r["project_id"], r["label_set"], r["unit_id"])]
        d[r["package_id"]] = min(d.get(r["package_id"], 9), r["level_rank"])
    groups, members = {}, []
    for f in findings:
        if f["committed"] != "true":
            continue
        uid, name = f["unit_id"], f["label_set"]
        assets = assets_of.get(uid, [])
        types = [t for t in f["types_yes"].split("|") if t]
        # a sentence with no policy answer yet is grouped as investment, so its commitment (and id) stays
        # the same when an investment answer arrives
        instrument = f["instrument"] or "investment"
        for pid in [p for p in f["project_ids"].split("|") if p]:
            for key, a, st, t, para in commitment_keys(instrument, assets, types, f["paragraph_id"]):
                cid = hashlib.md5("|".join((pid, name, instrument, key, a, st, t, para)).encode()).hexdigest()
                g = groups.setdefault(cid, {
                    "commitment_id": cid, "project_id": pid, "label_set": name, "instrument": instrument,
                    "commitment_key": key, "asset_id": a, "subtype_id": st, "type_id": t, "paragraph_id": para,
                    "commitment_label": (f"{assets_n.get(a, a)}: {subtypes_n.get((a, st), st)}" if key == "asset_type"
                              else types_n.get((name, t), t) if key == "type" else PARAGRAPH_LABEL),
                    "_units": set(), "_paragraphs": set(), "_documents": set(), "_components": set(),
                    "_types": set(), "_packages": {}})
                g["_units"].add(uid)
                g["_paragraphs"].add(f["paragraph_id"])
                g["_documents"].add(f["doc_id"])
                if f["component_number"]:
                    g["_components"].add(f["component_number"])
                g["_types"].update(types)
                for pkg, rank in best.get((pid, name, uid), {}).items():
                    g["_packages"][pkg] = min(g["_packages"].get(pkg, 9), rank)
                members.append({"commitment_id": cid, "project_id": pid, "label_set": name, "unit_id": uid,
                                "paragraph_id": f["paragraph_id"], "doc_id": f["doc_id"]})
    rows = []
    for g in groups.values():
        ranks = list(g["_packages"].values())
        rows.append({**{k: v for k, v in g.items() if not k.startswith("_")},
                     "type_ids": "|".join(sorted(g["_types"])), "sentences": len(g["_units"]),
                     "paragraphs": len(g["_paragraphs"]), "documents": len(g["_documents"]),
                     "components": "|".join(sorted(g["_components"])),
                     "linked_packages": len(ranks), "strong_packages": sum(1 for x in ranks if x == 1),
                     "best_link_level": LINK_LEVELS[min(ranks) - 1] if ranks else "",
                     "best_link_rank": min(ranks) if ranks else ""})
    rows.sort(key=lambda r: (r["project_id"], r["label_set"], r["commitment_key"], r["commitment_id"]))
    members.sort(key=lambda r: (r["commitment_id"], r["unit_id"]))
    return rows, members


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


# ---------------------------------------------------------------- the tables

def _probs(answer):
    pr = {int(k): float(v) for k, v in answer["probabilities"].items()}
    return {f"p{i}": f"{pr.get(i, 0.0):.4f}" for i in range(4)}


def measure_rows(conn, corpus, names, ua, stats=None):
    """One row per scored sentence x measure. stats, a dict, collects the count
    of pairs waiting for a second look and of committed pairs with no digital answer."""
    fl_rows, stats = [], {} if stats is None else stats
    dig, dig_floor, instrument = digital_scores(conn), digital_floor(), instruments(conn)
    for name in names:
        units = measure_units(conn, corpus, name)
        tps = types_of(conn, corpus, name)
        pats = labels.load_patterns(name)
        mapping = labels.type_resolution(name)[0]
        for uid, u in units.items():
            r = corpus.row(uid)
            stats["waiting"] = stats.get("waiting", 0) + u["waiting"]
            stats["no_digital_answer"] = stats.get("no_digital_answer", 0) + (
                u["committed"] and uid not in dig)
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
            digital = is_digital(dig.get(uid), dig_floor)
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
                "asset_link_p": f"{win[0][1]:.4f}" if win and win[0][1] is not None else "",
                "asset_links": "|".join(a for a, _ in win),
                "template_projects": r.get("template_projects", ""),
                "template_share": r.get("template_share", ""),
                "sent": "true", "committed": str(u["committed"] and digital).lower(),
                "measure_rule": str(u["committed"]).lower(),
                "confidence": f"{float(u['answer'].get('confidence', 0) or 0):.3f}",
                **_probs(u["answer"]), "p_committed_calibrated": f"{u['posterior']:.4f}",
                "types_yes": "|".join(shown), "best_type": shown[0] if shown else "",
                "best_p": f"{probs[yes[0]]:.3f}" if yes else "",
                "score_first": f"{u['score_first']:.4f}",
                "score_context": "" if u["score_context"] is None else f"{u['score_context']:.4f}",
                "second_grade": "" if u["second_grade"] is None else f"{u['second_grade']:g}",
                "decided_by": u["decided_by"],
                "is_digital": str(digital).lower(),
                "digital_p": f"{dig[uid]:.4f}" if uid in dig else "",
                "instrument": instrument.get(uid, ""),
                "model_served": u["served"] or "", "text": text})
    return fl_rows


def asset_rows(conn, corpus, amap, ua, type_of=None, second=None):
    """(paragraph_grades rows, unit_assets rows). `type_of` is asset_types()
    and `second` asset_second_looks(): a row's subtype_id is the chat model's
    type of that asset for that sentence, '' where none was asked."""
    type_of, second = type_of or {}, second or {}
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
        chat_assets = "|".join(second.get(uid, []))
        for k, (a, p) in enumerate(win, 1):
            assets.append(dict(base, asset_rank=k, asset_id=a, subtype_id=type_of.get((uid, a), ""),
                               second_look_asset_id=chat_assets,
                               probability="" if p is None else f"{p:.4f}"))
        if not win:
            assets.append(dict(base, asset_rank=0, asset_id="", subtype_id="", probability="",
                               second_look_asset_id=chat_assets))
    return grades, assets


def package_asset_rows(conn, packages):
    """The package_assets rows: every package x asset the first pass asked about, with the asset's type
    where the package has the asset and its type was asked (kind package_asset_subtype)."""
    pq = Q.package_asset_questions()
    type_of, has = package_asset_types(conn), package_asset_sets(conn)
    pa = posterior(conn, "package_asset", Q.ASSETS, {a: Q.sha(q) for a, q in pq.items()}, lambda i: None)
    by_pkg = {p["package_id"]: p for p in packages}
    assets = []
    for lset, aid, pid, score, answer, served, route, rnd, cos, rank in scores(conn, "package_asset"):
        if pid not in by_pkg:
            continue
        assets.append({"package_id": pid, "project_id": by_pkg[pid]["project_id"], "label_id": aid,
                       "cosine": f"{float(cos or 0):.4f}", "cosine_rank": rank or "",
                       "probability": f"{float(score):.3f}", "p_calibrated": f"{pa(aid, pid, score):.3f}",
                       "has_asset": str(aid in has.get(pid, ())).lower(),
                       "subtype_id": type_of.get((pid, aid), "") if aid in has.get(pid, ()) else "",
                       "model_served": served or ""})
    return assets


def publish(conn, data, *, corpus=None, log=print, views=True):
    """Rewrite every decision table from the scores as the panel reads them
    now, then the views. Returns {table: rows}."""
    from wbg_portfolio import checks
    t0 = time.time()
    corpus = corpus or Q.Corpus(data)
    names = measure_sets()
    amap = asset_map(conn, corpus)
    second = asset_second_looks(conn)
    ua = unit_assets(corpus, amap, second)
    type_of = asset_types(conn)
    grades, assets = asset_rows(conn, corpus, amap, ua, type_of, second)
    counts = {}
    findings = measure_rows(conn, corpus, names, ua, counts)
    if counts.get("waiting"):
        log(f"  {counts['waiting']:,} pair(s) in the band are waiting for a second look; not committed")
    if counts.get("no_digital_answer"):
        log(f"  {counts['no_digital_answer']:,} committed pair(s) have no digital answer yet; not digital")
    packages = checks.load_packages(data)
    pk_assets = package_asset_rows(conn, packages)
    strengths = link_strength_rows(conn, corpus, names, ua, packages, type_of)
    commitments, members = commitment_rows(findings, commitment_assets(ua, type_of), strengths, commitment_names())
    written = {}
    with load_pg.transaction(conn):
        load_pg.prepare(conn)
        for table, cols, rows in (("labels", load_pg.LABEL_COLS, load_pg.label_rows()),
                                  ("measure_types", load_pg.TYPE_COLS, load_pg.type_rows(data)),
                                  ("asset_subtypes", load_pg.SUBTYPE_COLS, load_pg.subtype_rows()),
                                  ("flags", load_pg.FLAG_COLS, load_pg.flag_rows()),
                                  *zip(("label_queries", "label_patterns", "label_scale"),
                                       (load_pg.QUERY_COLS, load_pg.PATTERN_COLS, load_pg.SCALE_COLS),
                                       load_pg.definition_rows()),
                                  ("measure_findings", load_pg.FINDING_COLS, findings),
                                  ("paragraph_grades", load_pg.GRADE_COLS, grades),
                                  ("unit_assets", load_pg.UNIT_ASSET_COLS, assets),
                                  ("package_assets", load_pg.PACKAGE_ASSET_COLS, pk_assets),
                                  ("commitment_package_links", load_pg.STRENGTH_COLS, strengths),
                                  ("commitments", load_pg.COMMITMENT_COLS, commitments),
                                  ("commitment_members", load_pg.MEMBER_COLS, members)):
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
