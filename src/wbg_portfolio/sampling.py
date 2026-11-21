"""The evaluation sample (docs/method/evaluation.md): a reproducible Poisson draw with unequal chances from
what is published, so three grader models can mark it and the thresholds can be tuned on one part and
reported on the other.

  draw(conn, data, seed, dry_run=False) -> {kind: rows}   writes data/samples/s<seed>/<kind>.jsonl

Four kinds of item, each a population read from the published tables (db.SCHEMA) and the stored scores:

  measure          every scored sentence x measure (measure_findings)
  package_measure  every package x measure with a package_measure score, and the packages never asked it
  link             every kept sentence x package linked in commitment_package_links, at its published level
  asset            every kept sentence (is_committed and is_digital, in measure_findings)

Each unit falls in one region of its stored scores, and each region has a target: the number of its
units the sample should hold in expectation. A unit's chance pi is its region's target over the region's
size (at most 1); a measure whose expected count is under MEASURE_MIN has the chances of its pairs from
0.3 up raised until it reaches it, and a project whose expected count is over PROJECT_CAP of the kind's
total has its chances scaled down to the cap. A unit is in the sample when a hash of the seed, the kind and
its ids, read as a number in [0, 1), is below pi; a second, independent hash puts it in the tuning set
(TUNING of them) or the holdout set. Every row carries pi, so every estimate weighs it by 1 / pi.

Nothing here assumes a threshold: the regions are fixed bins of the scores, what is kept is read from the
published tables, and what a package has is read through decide (the settings loader). The rows carry the
text the graders are shown (sentence, the paragraph with the sentence marked, section, component,
lead-in; package description, category, component, method, amount), so they are data, never committed.
"""
import json
import os
import re
from collections import Counter, defaultdict

from wbg_portfolio import common
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import labels
from wbg_portfolio import questions as Q
from wbg_portfolio.grading import uniform
from wbg_portfolio.paths import where

KINDS = ("measure", "package_measure", "link", "asset")
TUNING = 0.6                 # share of the sample in the tuning set; the rest is the holdout set
PROJECT_CAP = 0.04           # most of a kind's expected count one project may hold
MEASURE_MIN = 25             # fewest expected measure pairs a measure should have
TYPES_ASKED = 300            # kept measure pairs of the sample also asked the measure's types, in expectation
PARAGRAPH_CHARS = 4000       # most of a paragraph a grader is shown, centred on the sentence
UNKNOWN_TARGET = 5           # a region with no target of its own (never empty: no unit has pi 0)

TARGETS = {
    "measure": {
        "first>=0.95": 130, "first0.85-0.95": 150,
        "band|context<0.3|grade<2": 25, "band|context<0.3|grade>=2": 30,
        "band|context0.3-0.5|grade<2": 30, "band|context0.3-0.5|grade>=2": 45,
        "band|context0.5-0.7|grade<2": 35, "band|context0.5-0.7|grade>=2": 70,
        "band|context0.7-0.85|grade<2": 30, "band|context0.7-0.85|grade>=2": 70,
        "band|context>=0.85|grade<2": 25, "band|context>=0.85|grade>=2": 60,
        "band|no second look": 20,
        "digital<0.1|first>=0.85": 25, "digital<0.1|band": 30,
        "digital0.1-0.3|first>=0.85": 30, "digital0.1-0.3|band": 40,
        "digital0.3-0.5|first>=0.85": 40, "digital0.3-0.5|band": 50,
        "no digital answer|first>=0.85": 10, "no digital answer|band": 10,
        "first0.3-0.5": 100, "first<0.3": 60},
    "package_measure": {
        "score>=0.9": 40, "score0.7-0.9": 60, "score0.5-0.7": 60, "score0.3-0.5": 50, "score<0.3": 40,
        "never asked|pattern match": 70, "never asked|no match": 30},
    "link": {
        "1 strong": 50, "2 same type, same component": 45, "3 same type": 45,
        "4 same asset, same component": 40, "5 same asset, component unknown": 35,
        "6 same asset, component differs": 35},
    "asset": {
        "unit|<0.5": 10, "unit|0.5-0.8": 20, "unit|0.8-0.9": 40, "unit|>=0.9": 40,
        "second_look|<0.5": 40, "second_look|0.5-0.8": 50, "second_look|0.8-0.9": 15, "second_look|>=0.9": 10,
        "none|<0.5": 30, "none|0.5-0.8": 40, "none|0.8-0.9": 10, "none|>=0.9": 5,
        "paragraph|<0.5": 3, "paragraph|0.5-0.8": 3, "paragraph|0.8-0.9": 2, "paragraph|>=0.9": 2},
}
# Regions a measure's minimum does not raise: below 0.3 a pair is only there to bound recall.
NOT_RAISED = {"first<0.3"}

# The link regions, from the level the publish gives each link (commitment_package_links.level) and,
# for a same-asset link, whether the components are unknown or differ.
LEVEL_REGION = {"strong": "1 strong", "type_and_component": "2 same type, same component", "type": "3 same type",
                "component": "4 same asset, same component"}


def folder(data, seed):
    return where(data, "samples", f"s{seed}")


def path_of(data, seed, kind):
    return os.path.join(folder(data, seed), f"{kind}.jsonl")


def read(data, seed, kind):
    """The rows of one kind of a drawn sample ([] when the file is missing)."""
    p = path_of(data, seed, kind)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ------------------------------------------------------------------ regions

def _bin(x, edges, names):
    for e, n in zip(edges, names):
        if x < e:
            return n
    return names[-1]


def measure_region(first, ctx, grade, dig):
    """The region of a sentence x measure pair by its first score, digital P(yes), context score and chat
    grade (None where not asked)."""
    if first < 0.3:
        return "first<0.3"
    if first < 0.5:
        return "first0.3-0.5"
    hi = "first>=0.85" if first >= 0.85 else "band"
    if dig is None:
        return f"no digital answer|{hi}"
    if dig < 0.5:
        return _bin(dig, (0.1, 0.3), ("digital<0.1", "digital0.1-0.3", "digital0.3-0.5")) + "|" + hi
    if first >= 0.95:
        return "first>=0.95"
    if first >= 0.85:
        return "first0.85-0.95"
    if ctx is None or grade is None:
        return "band|no second look"
    c = _bin(ctx, (0.3, 0.5, 0.7, 0.85), ("<0.3", "0.3-0.5", "0.5-0.7", "0.7-0.85", ">=0.85"))
    return f"band|context{c}|grade{'>=2' if grade >= 2 else '<2'}"


def score_region(p):
    return "score" + _bin(p, (0.3, 0.5, 0.7, 0.9), ("<0.3", "0.3-0.5", "0.5-0.7", "0.7-0.9", ">=0.9"))


def asset_band(p):
    return _bin(p if p is not None else 0.0, (0.5, 0.8, 0.9), ("<0.5", "0.5-0.8", "0.8-0.9", ">=0.9"))


def link_region(level, relation):
    """The region of a link from its published level and component relation."""
    if level in LEVEL_REGION:
        return LEVEL_REGION[level]
    return "5 same asset, component unknown" if relation == "unknown" else "6 same asset, component differs"


# ------------------------------------------------------------------ chances

def _fill_to(pis, want):
    """The factor f with sum(min(1, f * pi)) = want (all 1 when there are no more units than want)."""
    if len(pis) <= want:
        return float("inf")
    lo, hi = 1.0, 1.0
    while sum(min(1.0, hi * p) for p in pis) < want:
        hi *= 2
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(min(1.0, mid * p) for p in pis) < want:
            lo = mid
        else:
            hi = mid
    return hi


def chances(units, targets, minimum=0, group="", cap=PROJECT_CAP, rounds=4):
    """Set u["pi"] for every unit (a dict with region, project and, when `group` names a key, a group):
    the region's target over its size, raised within a group under `minimum`, scaled down within a project
    over `cap` of the total (never below twice an average project's share, so a population of few projects
    keeps its sample). Returns the groups left under their minimum."""
    size = Counter(u["region"] for u in units)
    for u in units:
        u["pi"] = min(1.0, targets.get(u["region"], UNKNOWN_TARGET) / size[u["region"]])
    short = []
    for _ in range(rounds):
        if minimum and group:
            by = defaultdict(list)
            for u in units:
                if u["region"] not in NOT_RAISED:
                    by[u[group]].append(u)
            for g, us in by.items():
                if sum(u["pi"] for u in us) < minimum:
                    f = _fill_to([u["pi"] for u in us], minimum)
                    for u in us:
                        u["pi"] = min(1.0, f * u["pi"])
        by_p = defaultdict(list)
        for u in units:
            by_p[u["project"]].append(u)
        share = max(cap, 2.0 / len(by_p)) if by_p else cap
        for _ in range(100):                 # scaling a project down lowers the total: repeat until none is over
            total = sum(u["pi"] for u in units)
            over = [(us, sum(u["pi"] for u in us)) for us in by_p.values()]
            over = [(us, e) for us, e in over if e > share * total * (1 + 1e-6)]
            if not over:
                break
            for us, e in over:
                for u in us:
                    u["pi"] *= share * total / e
    if minimum and group:
        e = Counter()
        for u in units:
            if u["region"] not in NOT_RAISED:
                e[u[group]] += u["pi"]
        short = sorted(g for g, v in e.items() if v < minimum - 0.5)
    return short


def drawn(units, seed, kind):
    """The units the seed draws (hash below pi), each with its set (an independent hash)."""
    out = []
    for u in units:
        if uniform(seed, f"{kind}\t{u['key']}") < u["pi"]:
            out.append(dict(u, set="tuning" if uniform(seed, f"set\t{kind}\t{u['key']}") < TUNING else "holdout",
                            weight=1.0 / u["pi"]))
    return out


def expected_lines(kind, units, short=()):
    """The expected count per region (size, chance, expected), and the total."""
    by = defaultdict(list)
    for u in units:
        by[u["region"]].append(u["pi"])
    lines = [f"{kind}: {len(units):,} unit(s) in the population, {sum(u['pi'] for u in units):,.0f} expected "
             f"in the sample"]
    for r in sorted(by):
        ps = by[r]
        lines.append(f"  {r:<36} {len(ps):>9,} units  pi {min(ps):.5f}-{max(ps):.5f}  expected {sum(ps):7.1f}")
    if short:
        lines.append(f"  under the minimum of {MEASURE_MIN} after the project cap: {', '.join(short)}")
    return lines


# ------------------------------------------------------------------ populations

def _f(x):
    return None if x is None else float(x)


def _cluster(project_ids):
    ids = sorted(p for p in (project_ids or []) if p)
    return (ids[0] if ids else ""), ids


def published(conn):
    """The published rows the populations are read from."""
    s = db.SCHEMA
    need = ("measure_findings", "unit_assets", "commitment_package_links")
    missing = [t for t in need if not db.exists(conn, f"{s}.{t}")]
    if missing:
        raise SystemExit(f"check sample: {s} has no {', '.join(missing)}; publish first")
    mf = db.rows(conn, f"SELECT label_set, label_id, unit_id, project_ids, score_first, score_context, second_grade, "
                       f"digital_p, is_committed, is_digital, decided_by, component_number "
                       f"FROM {s}.measure_findings")
    ua = db.rows(conn, f"SELECT unit_id, asset_rank, asset_id, subtype_id, asset_level, own_best_probability, "
                       f"second_look_asset_id, project_ids FROM {s}.unit_assets")
    links = db.rows(conn, f"SELECT project_id, label_set, unit_id, package_id, strength, shared_asset_ids, strong_p, "
                          f"level, level_rank, same_type, component_relation, sentence_component, package_component, "
                          f"package_cancelled FROM {s}.commitment_package_links")
    return mf, ua, links


def commitments_of(conn):
    """{(label_set, unit_id): [(commitment_id, commitment_key)]} of the published commitments, {} before any."""
    s = db.SCHEMA
    if not (db.exists(conn, f"{s}.commitment_members") and db.exists(conn, f"{s}.commitments")):
        return {}
    out = defaultdict(list)
    for lset, uid, cid, key in db.rows(conn, f"SELECT m.label_set, m.unit_id, m.commitment_id, c.commitment_key "
                                             f"FROM {s}.commitment_members m JOIN {s}.commitments c USING "
                                             f"(commitment_id) ORDER BY 1, 2, 3"):
        out[(lset, uid)].append((cid, key))
    return out


def measure_units(mf, type_scores):
    out = []
    for lset, lid, uid, pids, first, ctx, grade, dig, committed, digital, by, comp in mf:
        project, ids = _cluster(pids)
        first = _f(first) or 0.0
        out.append({"key": f"{lset}|{uid}", "label_set": lset, "label_id": lid, "unit_id": uid,
                    "project": project, "project_ids": ids, "group": lset,
                    "score_first": first, "score_context": _f(ctx), "second_grade": _f(grade),
                    "digital_p": _f(dig), "is_committed": bool(committed), "is_digital": bool(digital),
                    "kept": bool(committed) and bool(digital), "decided_by": by or "",
                    "type_scores": type_scores.get((lset, uid), {}),
                    "region": measure_region(first, _f(ctx), _f(grade), _f(dig))})
    return out


def asset_units(mf, ua, own_scores):
    """Every kept sentence (is_committed and is_digital for some measure) with its published asset level,
    assets and types, its first-pass asset probabilities (`own_scores`, decide.asset_map) and the second
    look's assets."""
    kept = {}
    for lset, lid, uid, pids, *_rest in mf:
        committed, digital = _rest[4], _rest[5]
        if committed and digital:
            kept.setdefault(uid, pids)
    rows = defaultdict(list)
    for r in ua:
        rows[r[0]].append(r)
    out = []
    for uid, pids in sorted(kept.items()):
        rs = sorted(rows.get(uid, []), key=lambda r: r[1] or 0)
        level = rs[0][4] if rs else "none"
        assets = [(r[2], r[3] or "") for r in rs if r[2]]
        best = _f(rs[0][5]) if rs else None
        second = [a for a in ((rs[0][6] or "") if rs else "").split("|") if a]
        unspecified = any(st == "unspecified" for _, st in assets)
        project, ids = _cluster(pids)
        out.append({"key": uid, "unit_id": uid, "project": project, "project_ids": ids, "level": level,
                    "assets": assets, "own_best_probability": best, "own_scores": own_scores.get(uid, {}),
                    "second_look_assets": second, "unspecified": unspecified,
                    "region": f"{level}|{asset_band(best)}" + ("|unspecified" if unspecified else "")})
    return out


def asset_targets(units):
    """The asset targets, each (level, band) cell's split equally over its typed and unspecified parts."""
    parts = defaultdict(set)
    for u in units:
        parts[u["region"].split("|unspecified")[0]].add(u["region"])
    out = {}
    for cell, regions in parts.items():
        t = TARGETS["asset"].get(cell, UNKNOWN_TARGET)
        for r in regions:
            out[r] = t / len(regions)
    return out


def link_units(links, packages, commitments=None):
    """Every kept sentence x package linked, at the best level of its links (one per measure), read as
    published: level, same asset type, component relation, the package's cancelled flag, and the
    commitments the sentence belongs to under the link's measure."""
    commitments = commitments or {}
    pairs = defaultdict(list)
    for pid_, lset, uid, pkg, strength, shared, sp, level, rank, same, rel, sc, pc, cancelled in links:
        pairs[(uid, pkg)].append({"label_set": lset, "strength": strength, "shared": list(shared or []),
                                  "strong_p": _f(sp), "project": pid_, "level": level, "rank": rank or 9,
                                  "same_type": bool(same), "relation": rel, "sc": sc or "", "pc": pc or "",
                                  "cancelled": bool(cancelled)})
    out = []
    for (uid, pkg), rs in sorted(pairs.items()):
        p = packages.get(pkg, {})
        best = min(rs, key=lambda r: (r["rank"], -(r["strong_p"] or 0), r["label_set"]))
        out.append({"key": f"{uid}|{pkg}", "unit_id": uid, "package_id": pkg, "label_set": best["label_set"],
                    "label_sets": sorted({r["label_set"] for r in rs}), "project": best["project"],
                    "project_ids": [best["project"]], "strong": best["level"] == "strong",
                    "strong_p": max((r["strong_p"] for r in rs if r["strong_p"] is not None), default=None),
                    "shared_asset_ids": sorted({a for r in rs for a in r["shared"]}), "level": best["level"],
                    "same_type": best["same_type"], "sentence_component": best["sc"],
                    "package_component": best["pc"], "component_relation": best["relation"],
                    "commitment_ids": [c for c, _ in commitments.get((best["label_set"], uid), [])],
                    "commitment_keys": sorted({k for _, k in commitments.get((best["label_set"], uid), [])}),
                    "status": p.get("status", ""), "cancelled": best["cancelled"],
                    "region": link_region(best["level"], best["relation"])})
    return out


def package_measure_units(conn, mf, packages, seed):
    """Every scored package x measure by its score, and the packages never asked the question (no
    package_measure score for any measure), each paired with the kept measures of its project that its
    description matches a word pattern of, else with one of them chosen by the seed."""
    has = decide.package_asset_sets(conn)
    scored = defaultdict(dict)
    for lset, _, pkg, score, *_ in decide.scores(conn, "package_measure"):
        scored[pkg][lset] = float(score)
    out = []
    for pkg, by in scored.items():
        project = packages.get(pkg, {}).get("project_id", "")
        for lset, sc in by.items():
            out.append({"key": f"{lset}|{pkg}", "label_set": lset, "package_id": pkg, "project": project,
                        "project_ids": [project], "score": sc, "has_asset": bool(has.get(pkg)),
                        "region": score_region(sc)})
    kept = defaultdict(set)
    for lset, lid, uid, pids, *_rest in mf:
        if _rest[4] and _rest[5]:
            for p in pids or []:
                kept[p].add(lset)
    pats = {n: labels.load_patterns(n) for n in sorted({m for v in kept.values() for m in v})}
    for pkg, p in sorted(packages.items()):
        if pkg in scored or not kept.get(p["project_id"]):
            continue
        measures = sorted(kept[p["project_id"]])
        hit = [m for m in measures if any(x["regex"].search(p["description"]) for x in pats.get(m, []))]
        chosen = hit or [measures[int(uniform(seed, f"measure\t{pkg}") * len(measures))]]
        for lset in chosen:
            out.append({"key": f"{lset}|{pkg}", "label_set": lset, "package_id": pkg, "project": p["project_id"],
                        "project_ids": [p["project_id"]], "score": None, "has_asset": bool(has.get(pkg)),
                        "region": "never asked|" + ("pattern match" if hit else "no match")})
    return out


def load_packages(data):
    """{package_id: the plan row's fields the graders read} of the non-placeholder packages
    of included projects with a description."""
    included = Q.included_projects()
    out = {}
    for r in common.read_csv(where(data, "procurement", "packages.csv")):
        text = " ".join((r.get("description_clean") or r.get("description") or "").split())
        if r["project_id"] not in included or (r.get("is_placeholder") or "").lower() == "true" or not text:
            continue
        out[r["package_id"]] = {
            "project_id": r["project_id"], "description": text, "category": (r.get("category") or "").strip(),
            "method": (r.get("method") or "").strip(), "amount": (r.get("estimated_amount") or "").strip(),
            "status": (r.get("status") or "").strip(), "component_number": (r.get("component_number") or "").strip(),
            "component": " ".join(f"{r.get('component_number') or ''} {r.get('component') or ''}".split())}
    return out


# ------------------------------------------------------------------ text

def mark(paragraph, sentence):
    """The paragraph with the sentence marked >>> <<< (the trial ruler's mark), cut to PARAGRAPH_CHARS
    around the sentence."""
    p, s = paragraph or "", sentence or ""
    i = p.find(s)
    if i < 0:
        s2, p2 = re.sub(r"\s+", " ", s), re.sub(r"\s+", " ", p)
        i = p2.find(s2)
        if i >= 0:
            p, s = p2, s2
    if i < 0:
        return p[:PARAGRAPH_CHARS] + "\n>>> " + s + " <<<"
    lo, hi = Q.window(len(p), i, i + len(s), PARAGRAPH_CHARS)
    return p[lo:i] + ">>> " + s + " <<<" + p[i + len(s):hi]


def sentence_text(corpus, uid):
    """The fields a grader is shown of a sentence."""
    text = corpus.texts[uid]
    parts = text.split("\n")
    body, lead = parts[-1], "\n".join(parts[:-1])
    r = corpus.row(uid)
    return {"sentence": body, "lead_in": lead,
            "section": corpus.paras.get(r["paragraph_id"], {}).get("section_title", ""),
            "component": corpus.component(uid),
            "paragraph": mark(corpus.paragraph_text(r["paragraph_id"]), body)}


# ------------------------------------------------------------------ the draw

def draw(conn, data, seed, corpus=None, log=print, dry_run=False):
    """Draw the sample for a seed (refuses a seed already drawn) or, with dry_run, print the expected counts
    per region only. Returns {kind: drawn rows}."""
    out_dir = folder(data, seed)
    if os.path.exists(os.path.join(out_dir, "sample.json")) and not dry_run:
        raise SystemExit(f"check sample: {out_dir} is drawn and may be graded; use another --seed")
    mf, ua, links = published(conn)
    from wbg_portfolio import questions
    corpus = corpus or questions.Corpus(data)
    # the type and asset probabilities as publish reads them (through a fitted layer when one is on)
    type_scores = {}
    for name in sorted({r[0] for r in mf}):
        for uid, probs in decide.types_of(conn, corpus, name).items():
            type_scores[(name, uid)] = dict(probs)
    own = decide.asset_map(conn, corpus)
    packages = load_packages(data)
    units = {"measure": measure_units(mf, type_scores), "asset": asset_units(mf, ua, own),
             "package_measure": package_measure_units(conn, mf, packages, seed),
             "link": link_units(links, packages, commitments_of(conn))}
    lines, short = [], {}
    for kind in KINDS:
        targets = asset_targets(units[kind]) if kind == "asset" else TARGETS[kind]
        short[kind] = chances(units[kind], targets, minimum=MEASURE_MIN if kind == "measure" else 0, group="group")
        lines += expected_lines(kind, units[kind], short[kind])
    kept_e = sum(u["pi"] for u in units["measure"] if u["kept"] and u["type_scores"])
    q_types = min(1.0, TYPES_ASKED / kept_e) if kept_e else 0.0
    lines.append(f"types: about {min(TYPES_ASKED, kept_e):,.0f} kept measure pair(s) of the sample also asked "
                 f"their measure's types (chance {q_types:.3f} each)")
    for line in lines:
        log(line)
    if dry_run:
        log("check sample: --dry-run: nothing drawn or written")
        return {}
    rows, missing = {}, 0
    for kind in KINDS:
        got = []
        for u in drawn(units[kind], seed, kind):
            if kind in ("measure", "asset", "link") and u["unit_id"] not in corpus.texts:
                missing += 1
                continue
            if kind in ("package_measure", "link") and u["package_id"] not in packages:
                missing += 1
                continue
            u = dict(u, kind=kind)
            u.pop("group", None)
            if kind in ("measure", "asset", "link"):
                u.update(sentence_text(corpus, u["unit_id"]))
            if kind in ("package_measure", "link"):
                p = packages[u["package_id"]]
                u.update(package=p["description"], category=p["category"], method=p["method"], amount=p["amount"],
                         package_component=p["component"], status=p["status"])
            if kind == "measure":
                u["ask_types"] = bool(u["kept"] and u["type_scores"]
                                      and uniform(seed, f"types\t{u['key']}") < q_types)
                u["types_pi"] = u["pi"] * q_types if u["ask_types"] else None
            got.append(u)
        rows[kind] = got
    if missing:
        log(f"  {missing:,} drawn unit(s) left out: not in the corpus or the package list of this data folder")
    for kind in KINDS:
        common.write_atomic(path_of(data, seed, kind),
                            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows[kind]))
        sets = Counter(r["set"] for r in rows[kind])
        log(f"check sample: {kind}: {len(rows[kind]):,} drawn ({sets['tuning']:,} tuning, {sets['holdout']:,} "
            f"holdout) -> {path_of(data, seed, kind)}")
    manifest = {"seed": seed, "schema": db.SCHEMA, "store": db.STORE,
                "drawn": {k: len(v) for k, v in rows.items()},
                "expected": {k: round(sum(u["pi"] for u in units[k]), 1) for k in KINDS},
                "population": {k: len(units[k]) for k in KINDS}, "types_chance": q_types,
                "short_of_minimum": short["measure"]}
    common.write_atomic(os.path.join(out_dir, "sample.json"), json.dumps(manifest, indent=1, sort_keys=True))
    return rows
