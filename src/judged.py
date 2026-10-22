"""The full loop for the decisions made after a measure (docs/method/decisions.md).

Assets and measures go through: a gate, the model's answer, a probability
layer fitted on grades (src/posterior.py), a floor, and the tuning report.
The four decisions after them go through the same loop:

  kind            item graded            label (one layer each)     score the layer reads
  types           a committed sentence   <set>:<type>               P(yes) of the type's fit question
  pairs           a sentence, one side   keep:<set>                 P(yes) of the side's fit question
  package_assets  a package              <asset>                    P(yes)
  package_links   a commitment-package   link                       P(yes)

Each kind's labels are pooled into one fit when posterior.pool is on (as
for measures and assets: posterior.fit_pooled, one intercept per label, the
weights shared), since a type, a pair side or an asset has few graded rows
of its own. Grades come from the combined
workbook (./wbg check all): the type column of each measure tab, and the
tabs 'pair checks', 'package assets' and 'package links'; ./wbg check all
--evaluate stores them in data/labels/ and fits the layers into
data/labels/<kind>_posterior.json, keyed on the exact question each label
asks; the stage reads them (its posterior column) and its floor reads the
posterior. With no grades the posterior is the model's answer. The tuning
report (src/tune.py) reads the out-of-fold probabilities written here.
"""
import csv
import datetime
import hashlib
import json
import os
import random

import numpy

import panel
import posterior
from paths import where

KINDS = ("types", "pairs", "package_assets", "package_links")
BANDS = [("p<0.3", 0.0, 0.3, 10), ("p0.3-0.5", 0.3, 0.5, 15), ("p0.5-0.7", 0.5, 0.7, 15),
         ("p0.7-0.9", 0.7, 0.9, 15), ("p0.9+", 0.9, 1.01, 15)]
PAIR_ANSWERS = ["only A", "only B", "both", "neither", "unsure"]


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def layer_path(data, kind):
    return where(data, "labels", f"{kind}_posterior.json")


def label_path(data, kind):
    return where(data, "labels", f"{kind}.csv")


def layers(data, kind, shas, vector_of=None):
    """{label: posterior.Layer} for the labels whose layer was fitted on the
    question the label asks now (shas = {label: question SHA-256}).
    vector_of defaults to the sentence embeddings, read only when a layer
    applies."""
    path = layer_path(data, kind)
    if not panel.get("posterior.on", True) or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        specs = {lab: s for lab, s in json.load(fh).items()
                 if s.get("question_sha256") == shas.get(lab)}
    if specs and vector_of is None:
        vector_of = posterior.lookups(data)[0]
    return {lab: posterior.Layer(s, vector_of) for lab, s in specs.items()}


def digest(data, kind, shas):
    """A short hash of the layers that apply (part of the stage's run key)."""
    path = layer_path(data, kind)
    if not panel.get("posterior.on", True) or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        specs = json.load(fh)
    return posterior.digest({lab: s for lab, s in sorted(specs.items())
                             if s.get("question_sha256") == shas.get(lab)})


def calibrated(lays, label, item, score):
    """The posterior for one item: through the label's layer when fitted."""
    lay = lays.get(label)
    return lay.probability(item, score) if lay else float(score)


def pair_vector(u, p):
    """A commitment-package pair's vector: the sum of the two unit vectors,
    normalised, so the cosine between two pairs is the mean of their
    sentences' and their packages' cosines (up to scale)."""
    if u is None or p is None:
        return None
    u = numpy.asarray(u, dtype="float64")
    p = numpy.asarray(p, dtype="float64")
    v = u / (numpy.linalg.norm(u) or 1) + p / (numpy.linalg.norm(p) or 1)
    return v / (numpy.linalg.norm(v) or 1)


def package_vector_of(data, packages, dim=None):
    """vector_of(package_id) from the embedding cache (None when not embedded)."""
    import embed
    dim = dim or embed.DEFAULT_DIM
    root = embed.cache_root(data, embed.DEFAULT_MODEL, dim)
    sha_of = {p["package_id"]: p["sha"] for p in packages}

    def of(pid):
        s = sha_of.get(pid)
        raw = embed.read_cached(root, s, dim) if s else None
        return None if raw is None else numpy.frombuffer(raw, dtype="<f4").astype("float64")
    return of


# ------------------------------------------------------------ grades

def store(data, kind, cols, rows, keys):
    """Merge rows into data/labels/<kind>.csv, the latest grade of each key
    winning."""
    path = label_path(data, kind)
    current = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                current[tuple(r.get(k, "") for k in keys)] = r
    for r in rows:
        current[tuple(r.get(k, "") for k in keys)] = r
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(current.values(), key=lambda r: tuple(r.get(k, "") for k in keys)))
    os.replace(path + ".part", path)


def read_labels(data, kind):
    path = label_path(data, kind)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def draw(rows, score, seed, bands=BANDS):
    """[(row, stratum, stratum size, drawn)] by band of score(row)."""
    rng = random.Random(seed)
    strata = {}
    for r in rows:
        x = score(r)
        s = next((b[0] for b in bands if b[1] <= x < b[2]), None)
        if s is not None:
            strata.setdefault(s, []).append(r)
    out = []
    for s, _, _, per in bands:
        pool = strata.get(s, [])
        n = min(per, len(pool))
        for r in rng.sample(pool, n):
            out.append((r, s, len(pool), n))
    rng.shuffle(out)
    return out


# ------------------------------------------------------------ the pair checks tab

PAIR_KEY_COLS = ["row_id", "unit_id", "set_a", "set_b", "text_sha256", "question_sha256",
                 "p_keep_a", "p_keep_b", "stratum", "stratum_size", "stratum_drawn", "run"]
PAIR_SHEET = [("row_id", 8), ("project", 12), ("measure A", 30), ("measure B", 30),
              ("sentence", 90), ("answer", 12), ("note", 24)]
PAIR_LABEL_COLS = ["unit_id", "set_a", "set_b", "text_sha256", "question_sha256", "answer",
                   "weight", "source", "graded_on"]


def pair_rows(data, seed, projects=None):
    """(key rows, sheet rows) of the 'pair checks' tab, drawn from the
    current pair-check run by band of the larger drop probability."""
    import check_units
    import commit_checks
    folder = commit_checks.current_run(data, projects)
    if not folder:
        return [], []
    with open(os.path.join(folder, "pairs.csv"), newline="", encoding="utf-8") as fh:
        pairs = list(csv.DictReader(fh))
    _, _, known = commit_checks.plan(data, projects)
    texts = {uid: u["text"] for (_, uid), u in known.items()}
    proj = {uid: u.get("project_ids", "") for (_, uid), u in known.items()}
    names = {}
    for p in pairs:
        for s in (p["set_a"], p["set_b"]):
            names.setdefault(s, commit_checks.boundary_of(s)[0])
    keep = lambda p: min(float(p.get("p_keep_a") or 0), float(p.get("p_keep_b") or 0))
    key, sheet = [], []
    for i, (p, st, size, n) in enumerate(draw(pairs, keep, seed), 1):
        q = commit_checks.pair_question(p["set_a"], p["set_b"])
        key.append({"row_id": i, "unit_id": p["unit_id"], "set_a": p["set_a"],
                    "set_b": p["set_b"], "text_sha256": check_units.text_key(texts[p["unit_id"]]),
                    "question_sha256": sha(q), "p_keep_a": p.get("p_keep_a", ""),
                    "p_keep_b": p.get("p_keep_b", ""), "stratum": st, "stratum_size": size,
                    "stratum_drawn": n, "run": os.path.basename(folder)})
        sheet.append({"row_id": i, "project": proj.get(p["unit_id"], ""),
                      "measure A": names[p["set_a"]], "measure B": names[p["set_b"]],
                      "sentence": texts[p["unit_id"]]})
    return key, sheet


# ------------------------------------------------------------ the package assets tab

ASSET_KEY_COLS = ["row_id", "package_id", "asset_id", "package_sha256", "question_sha256",
                  "probability", "stratum", "stratum_size", "stratum_drawn", "run"]
ASSET_SHEET = [("row_id", 8), ("project", 12), ("package", 70), ("package component", 30),
               ("asset", 30), ("answer", 12), ("note", 24)]
ASSET_LABEL_COLS = ["package_id", "asset_id", "package_sha256", "question_sha256", "answer",
                    "weight", "source", "graded_on"]


def package_asset_rows(data, seed, projects=None):
    """(key rows, sheet rows) of the 'package assets' tab: packages and the
    assets they were asked about, by band of the posterior."""
    import labels
    import link_packages
    folder = link_packages.current_run(data, projects=projects)
    if not folder:
        return [], []
    with open(os.path.join(folder, "package_assets.csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    packages = {p["package_id"]: p for p in link_packages.load_packages(data, projects)}
    assets = {a["id"]: a for a in labels.load_set("assets") if a["active"] == "true"}
    qs = link_packages.asset_questions(list(assets.values()))
    rows = [r for r in rows if r["package_id"] in packages and r["label_id"] in qs]
    score = lambda r: float(r.get("p_calibrated") or r["probability"])
    key, sheet = [], []
    for i, (r, st, size, n) in enumerate(draw(rows, score, seed), 1):
        p = packages[r["package_id"]]
        key.append({"row_id": i, "package_id": r["package_id"], "asset_id": r["label_id"],
                    "package_sha256": p["sha"], "question_sha256": sha(qs[r["label_id"]]),
                    "probability": f"{score(r):.3f}", "stratum": st, "stratum_size": size,
                    "stratum_drawn": n, "run": os.path.basename(folder)})
        sheet.append({"row_id": i, "project": p["project_id"], "package": p["text"],
                      "package component": p.get("component", ""),
                      "asset": assets[r["label_id"]]["name"]})
    return key, sheet


def read_answers(xlsx, tab):
    """{row_id: answer} of a tab's answer column."""
    import check_units
    head, rows = check_units._sheet_values(xlsx, tab)
    ai, ri = head.index("answer"), head.index("row_id")
    return {str(r[ri]): check_units._clean(r[ai]) for r in rows if check_units._clean(r[ai])}


def evaluate_tab(data, seed, kind):
    """Store a tab's answers in data/labels/<kind>.csv. Returns rows stored."""
    import check_units
    tab, kcols, lcols, keys, fname = {
        "pairs": ("pair checks", PAIR_KEY_COLS, PAIR_LABEL_COLS,
                  ("text_sha256", "set_a", "set_b", "question_sha256"), "check_pairs"),
        "package_assets": ("package assets", ASSET_KEY_COLS, ASSET_LABEL_COLS,
                           ("package_sha256", "asset_id", "question_sha256"),
                           "check_package_assets")}[kind]
    kpath = where(data, "review", f"{fname}_s{seed}.key.csv")
    if not os.path.exists(kpath):
        return 0
    with open(kpath, newline="", encoding="utf-8") as fh:
        key = list(csv.DictReader(fh))
    try:
        answered = read_answers(check_units.all_path(data, seed), tab)
    except KeyError:
        return 0
    today = datetime.date.today().isoformat()
    rows = []
    for k in key:
        a = (answered.get(k["row_id"]) or "").strip()
        if not a or a.lower() == "unsure":
            continue
        rows.append({**{c: k.get(c, "") for c in lcols}, "answer": a,
                     "weight": f"{int(k['stratum_size']) / int(k['stratum_drawn']):.6g}",
                     "source": os.path.basename(kpath)[:-8], "graded_on": today})
    if rows:
        store(data, kind, lcols, rows, keys)
    return len(rows)


# ------------------------------------------------------------ fitting

def fit_kind(data, kind, blocks):
    """Fit one kind's pooled layer on blocks ({label: (graded [(item, y,
    weight, projects)], scores, vectors, question SHA-256)}), write it, and
    return (report lines, {label: {item: out-of-fold probability}})."""
    s = posterior.settings()
    if s["pool"]:
        specs = posterior.fit_pooled(blocks, s)
    else:
        specs = {lab: sp for lab, (g, sc, v, q) in blocks.items()
                 if (sp := posterior.fit(g, sc, v, q, lab, s))}
    path = layer_path(data, kind)
    n = sum(len(b[0]) for b in blocks.values())
    yes = sum(r[1] for b in blocks.values() for r in b[0])
    if not specs:
        if os.path.exists(path):
            os.remove(path)
        return [f"- {kind}: {n} graded ({yes} yes) over {len(blocks)} labels: too few for a "
                f"layer; the model's answer stands"], {}
    posterior.write(path, specs)
    oof = posterior.out_of_fold_all(blocks, s)
    return [f"- {kind}: {'pooled ' if s['pool'] else ''}layer over {len(specs)} labels ({n} graded, {yes} yes), "
            f"written to {os.path.basename(path)}"], oof


def type_blocks(data):
    """The types' graded blocks: for each measure and type, the committed
    sentences a person gave a type (yes = that type), scored by the type's
    probability in the current assignment."""
    import check_units
    import kinds
    import labels
    import measure
    lo = min(panel.grades())
    vector_of, projects_of = posterior.lookups(data)
    blocks = {}
    for name in labels.label_sets():
        if name == measure.ASSET_SET:
            continue
        folder = kinds.current_assign(name, data)
        units, _ = check_units.latest_run(name, data)
        if not folder or not units:
            continue
        with open(os.path.join(folder, "assignments.csv"), newline="", encoding="utf-8") as fh:
            assigned = {r["unit_id"]: r for r in csv.DictReader(fh)}
        by_text = {check_units.text_key(u.get("text")): uid for uid, u in units.items()}
        shas = type_question_shas(name, data)
        given = {}
        for r in read_type_grades(data, name):
            uid = by_text.get(r.get("text_sha256", ""))
            if uid in assigned and int(r["grade"]) >= lo:
                ts = set(r["type"].split("|")) - {"", "other"}
                if ts:
                    given[uid] = (ts, float(r.get("weight") or 1.0))
        for label, qsha in shas.items():
            t = label.split(":", 1)[1]
            rows = [(u, int(t in given[u][0]), given[u][1], projects_of(u)) for u in given
                    if f"p_{t}" in assigned[u]]
            score = {u: float(assigned[u][f"p_{t}"] or 0) for u, *_ in rows}
            vecs = {u: vector_of(u) for u in score}
            vecs = {u: v for u, v in vecs.items() if v is not None}
            if rows:
                blocks[label] = (rows, score, vecs, qsha)
    return blocks


def read_type_grades(data, name):
    path = where(data, "labels", f"{name}_units.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh)
                if r.get("type") and r.get("grade") not in ("", None)
                and not (r.get("stratum") or "").startswith("eval:")]


def type_question_shas(name, data):
    """{<set>:<type>: SHA-256 of the type's fit question as asked now}."""
    import kinds
    try:
        approved = kinds.load_approved(name)
        mname = kinds.measure_name(name)
    except SystemExit:
        return {}
    out = {}
    for lid, ts in approved.items():
        for t, q in kinds.assign_questions(ts, mname).items():
            out[f"{name}:{t}"] = sha(q)
    return out


def pair_blocks(data):
    """The pair checks' graded blocks: per measure, the sentences a person
    answered for a pair it is in (yes = the person says the sentence is that
    measure: only it, or both), scored by the side's graded score."""
    import commit_checks
    folder = commit_checks.current_run(data)
    if not folder:
        return {}
    with open(os.path.join(folder, "pairs.csv"), newline="", encoding="utf-8") as fh:
        pairs = {(r["unit_id"], r["set_a"], r["set_b"]): r for r in csv.DictReader(fh)}
    _, _, known = commit_checks.plan(data)
    import check_units
    by_text = {check_units.text_key(u["text"]): uid for (_, uid), u in known.items()}
    vector_of, projects_of = posterior.lookups(data)
    qsha = pair_question_shas(data)
    blocks = {}
    for r in read_labels(data, "pairs"):
        uid = by_text.get(r["text_sha256"])
        p = pairs.get((uid, r["set_a"], r["set_b"]))
        if not p or r["question_sha256"] != qsha.get((r["set_a"], r["set_b"])):
            continue
        ans = r["answer"].lower()
        for side, s, mine in (("a", r["set_a"], "only a"), ("b", r["set_b"], "only b")):
            label = f"keep:{s}"
            rows, score, vecs, _ = blocks.setdefault(label, ([], {}, {}, label_sha(s, data)))
            item = f"{uid}#{r['set_a']}|{r['set_b']}"
            v = vector_of(uid)
            if v is None:
                continue
            rows.append((item, int(ans in (mine, "both")), float(r.get("weight") or 1.0),
                         projects_of(uid)))
            # the model's score, not the stored posterior: the layer reads it
            score[item] = float(p.get(f"score_{side}") or 0)
            vecs[item] = v
    return blocks


def label_sha(name, data):
    """The SHA-256 of a measure's fit question: a pair layer for a
    measure applies while its definition and scale are unchanged."""
    import commit_checks
    return commit_checks.keep_shas([name])[f"keep:{name}"]


def pair_question_shas(data):
    import commit_checks
    import labels
    import measure
    sets = [n for n in labels.label_sets() if n != measure.ASSET_SET]
    out = {}
    for i, a in enumerate(sorted(sets)):
        for b in sorted(sets)[i + 1:]:
            try:
                out[(a, b)] = sha(commit_checks.pair_question(a, b))
            except SystemExit:
                continue
    return out


def package_asset_blocks(data):
    """Per asset, the packages a person answered (yes = it buys or builds
    the asset), scored by the model's P(yes)."""
    import labels
    import link_packages
    folder = link_packages.current_run(data)
    if not folder:
        return {}
    with open(os.path.join(folder, "package_assets.csv"), newline="", encoding="utf-8") as fh:
        asked = {(r["package_id"], r["label_id"]): r for r in csv.DictReader(fh)}
    packages = link_packages.load_packages(data)
    by_sha = {p["sha"]: p for p in packages}
    vector_of = package_vector_of(data, packages)
    assets = [a for a in labels.load_set("assets") if a["active"] == "true"]
    qsha = {a: sha(q) for a, q in link_packages.asset_questions(assets).items()}
    blocks = {}
    for r in read_labels(data, "package_assets"):
        p = by_sha.get(r["package_sha256"])
        a = r["asset_id"]
        if not p or r["question_sha256"] != qsha.get(a) or (p["package_id"], a) not in asked:
            continue
        v = vector_of(p["package_id"])
        if v is None:
            continue
        rows, score, vecs, _ = blocks.setdefault(a, ([], {}, {}, qsha[a]))
        pid = p["package_id"]
        rows.append((pid, int(r["answer"].lower() == "yes"), float(r.get("weight") or 1.0),
                     frozenset({p["project_id"]})))
        score[pid] = float(asked[(pid, a)]["probability"])
        vecs[pid] = v
    return blocks


def package_link_blocks(data):
    """One label ('link'): the commitment-package pairs a person answered
    (data/labels/package_links.csv), scored by the model's P(yes)."""
    import check_units
    import link_packages
    folder = link_packages.current_run(data)
    if not folder:
        return {}
    with open(os.path.join(folder, "commitment_packages.csv"), newline="", encoding="utf-8") as fh:
        asked = {(r["label_set"], r["unit_id"], r["package_id"]): r for r in csv.DictReader(fh)}
    packages = link_packages.load_packages(data)
    by_sha = {p["sha"]: p for p in packages}
    pvec = package_vector_of(data, packages)
    vector_of, projects_of = posterior.lookups(data)
    units = {}
    for name, (_, us) in link_packages.committed_units(data).items():
        for u in us:
            units[check_units.text_key(u["text"])] = u["unit_id"]
    qsha = check_units.link_question_sha()
    rows, score, vecs = [], {}, {}
    for r in read_labels(data, "package_links"):
        uid, p = units.get(r["text_sha256"]), by_sha.get(r["package_sha256"])
        if not uid or not p or r["question_sha256"] != qsha or r["applies"] not in ("yes", "no"):
            continue
        a = asked.get((r["label_set"], uid, p["package_id"]))
        v = pair_vector(vector_of(uid), pvec(p["package_id"]))
        if not a or v is None:
            continue
        item = f"{r['label_set']}|{uid}|{p['package_id']}"
        rows.append((item, int(r["applies"] == "yes"), float(r.get("weight") or 1.0),
                     frozenset({p["project_id"]})))
        score[item] = float(a["probability"])
        vecs[item] = v
    return {"link": (rows, score, vecs, qsha)} if rows else {}


BLOCKS = {"types": type_blocks, "pairs": pair_blocks, "package_assets": package_asset_blocks,
          "package_links": package_link_blocks}
FLOOR = {"types": "floor.types", "pairs": "floor.pair_check", "package_assets": "link.asset_yes",
         "package_links": "dashboard.package_link_min_probability"}


def evaluate(data, seed):
    """Store the 'pair checks' and 'package assets' answers, fit every kind's
    layer, and write data/reports/evaluate_after_measures.md with each
    kind's precision and recall at its floor, out of sample. Returns the
    report path."""
    stored = {k: evaluate_tab(data, seed, k) for k in ("pairs", "package_assets")}
    lines = ["# The decisions after the measures: types, pair checks, package links", "",
             f"Answers stored this time: pair checks {stored['pairs']}, package assets "
             f"{stored['package_assets']} (package links and types are stored with their "
             f"tabs). Each kind's layer pools its labels; figures are out of sample, folds "
             f"by project, weighted.", ""]
    oofs = {}
    for kind in KINDS:
        try:
            blocks = BLOCKS[kind](data)
        except SystemExit as exc:
            lines.append(f"- {kind}: {exc}")
            continue
        got, oof = fit_kind(data, kind, blocks)
        lines += got
        oofs[kind] = (blocks, oof)
        if blocks:
            lines += floor_lines(kind, blocks, oof)
    out = where(data, "reports", "evaluate_after_measures.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out


def floor_lines(kind, blocks, oof):
    """Weighted precision and recall of the model's answer and the layer's,
    at a range of floors, pooled over the kind's labels."""
    raw, post, ys, ws = [], [], [], []
    for label, (rows, score, _, _) in blocks.items():
        for item, y, w, _ in rows:
            raw.append(score[item])
            post.append(oof.get(label, {}).get(item, score[item]))
            ys.append(y)
            ws.append(w)
    floor = float(panel.get(FLOOR[kind], 0.5) or 0)
    f = lambda x: "-" if x is None else f"{x:.2f}"
    lines = ["", f"  {FLOOR[kind]} now {floor}; {len(ys)} rows, {sum(ys)} yes", "",
             "  | floor | precision, model | recall | precision, layer | recall |",
             "  |---|---|---|---|---|"]
    for t in sorted({0.3, 0.4, 0.5, 0.6, 0.7, 0.8, floor}):
        p1, r1, _ = posterior.weighted_pr(raw, ys, ws, t)
        p2, r2, _ = posterior.weighted_pr(post, ys, ws, t)
        lines.append(f"  | {t:.2f} | {f(p1)} | {f(r1)} | {f(p2)} | {f(r2)} |")
    target = float(panel.get("floor.target_precision", 0.8))
    s = posterior.suggest_floor(post, ys, target, ws)
    if s:
        lines += ["", f"  suggested {FLOOR[kind]}: {s[0]:.2f} (precision {s[1]:.2f}, recall "
                  f"{s[2]:.2f}, target {target})"]
    return lines + [""]
