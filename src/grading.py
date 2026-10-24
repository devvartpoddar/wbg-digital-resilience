"""Grading: the tasks a person or an agent grades, the grades, and the
evaluation that fits the layers and gates and reports precision and recall
(docs/method/measures.md, "Graded sheets").

  draw(conn, data, corpus, seed, design)   -> data/review/tasks_s<seed>.jsonl
  fill(conn, data, seed, path, grader)     -> grades into the store (strict)
  evaluate(conn, data, corpus)             -> fits, measure_quality, data/reports/evaluate.md

A task is one decision on one item, self-contained: the sentence (or
package), its passage, project, section and component, the label's name
and definition, the exact question the model was asked, the scale, and the
values allowed. The grader writes {task_id, grade[, rationale]} per task.
Grades are keyed on the item's text and the label (never a position or a
run); the question the grade was given under is recorded as provenance.
Design weights (one over the item's chance of being drawn) weight every
precision and recall reported; the fits use them raised to
posterior.fit_weight_power (0 = unweighted).
"""
import csv
import hashlib
import json
import math
import os
import time
from collections import defaultdict

import db
import decide
import fits
import labels
import panel
import posterior
import questions as Q
import skip
from paths import where

NEAR = 0.2                       # "near" a floor: within this of it
ELUSION_BAND = 0.10              # "near" a gate: within this much below it
TRAIN = {"measure": {"train:near": 30, "train:far": 10}, "asset": {"train:near": 15, "train:far": 5},
         "asset_paragraph": 60, "random": 50, "pairs": 15, "package_assets": 15, "links": 15}
EVAL = {"committed_total": 900, "not_committed": 30, "asset": {"eval:asset_yes": 20, "eval:asset_no": 5},
        "asset_paragraph": 40, "random": 50, "package_assets": 15, "links": 15}
ELUSION = {"near": 25, "far": 25}
GRADES = ["0", "1", "2", "3", "unsure"]
YES_NO = ["yes", "no", "unsure"]
FIT_KINDS = ("type", "pair", "package_asset", "link")
FLOOR_OF = {"measure": None, "asset": "asset.floor", "type": "floor.types", "pair": "floor.pair_check",
            "package_asset": "link.asset_yes", "link": "dashboard.package_link_min_probability"}


def sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def uniform(seed, item):
    h = hashlib.sha256(f"{seed}\t{item}".encode("utf-8")).hexdigest()
    return int(h[:15], 16) / 16 ** 15


def inclusion(members, targets):
    """{item: chance}: each stratum's target over its size, the largest over
    an item's strata, at most 1."""
    size = defaultdict(int)
    for strata in members.values():
        for s in set(strata):
            size[s] += 1
    out = {}
    for item, strata in members.items():
        p = max((min(1.0, targets.get(s, 0) / size[s]) for s in set(strata)), default=0.0)
        if p > 0:
            out[item] = p
    return out


def band(x, bands):
    return next((b[0] for b in bands if b[1] <= x < b[2]), None)


BANDS = [("p<0.3", 0.0, 0.3), ("p0.3-0.5", 0.3, 0.5), ("p0.5-0.7", 0.5, 0.7), ("p0.7+", 0.7, 1.01)]


# ---------------------------------------------------------------- what the decisions say

def frames(conn, data, corpus, names):
    """Everything the draw reads, from the scores as the panel reads them."""
    f = {"measures": {}, "names": {}}
    for name in names:
        units = decide.measure_units(conn, corpus, name)
        if not units:
            continue
        lab = Q.load(name)[0][0]
        f["measures"][name] = {"units": units, "label_id": lab["id"], "name": lab["name"],
                               "definition": lab["definition"], "floor": decide.floor(name),
                               "types": decide.types_of(conn, corpus, name),
                               "type_defs": {t["type_id"]: t for t in labels.load_types(name)}}
    amap = decide.asset_map(conn, corpus)
    assets = Q.load(Q.ASSETS)[0]
    f["assets"] = {"amap": amap, "ua": decide.unit_assets(corpus, amap), "defs": {a["id"]: a for a in assets},
                   "floor": float(panel.require("asset.floor"))}
    f["pairs"] = decide.pair_checks(conn, corpus)
    import checks
    f["packages"] = {p["package_id"]: p for p in checks.load_packages(data)}
    f["package_assets"] = [(lset, aid, pid, float(score)) for lset, aid, pid, score, *_ in
                           decide.scores(conn, "package_asset") if pid in f["packages"]]
    f["links"] = [(lset, lid, item, float(score)) for lset, lid, item, score, *_ in decide.scores(conn, "link")
                  if item.rsplit("|", 1)[1] in f["packages"] and item.rsplit("|", 1)[0] in corpus.row_of]
    return f


def items_and_targets(f, corpus, design, data):
    """({item: [stratum]}, {stratum: target}) over sentences and packages. An
    item is (kind, id, lines): the lines a stratum wants answered together."""
    items, targets = defaultdict(list), {}
    ev = design == "eval"
    add = lambda key, s: items[key].append(s)
    if design == "elusion":
        import funnel
        for name, m in f["measures"].items():
            run, cos = funnel.best_cosines(name, data, corpus)
            gate = funnel.settings(name)["gate"]
            lid = m["label_id"]
            for r in corpus.index:
                uid = r["unit_id"]
                if uid in m["units"] or cos.get(lid) is None:
                    continue
                c = float(cos[lid][corpus.row_of[uid]])
                s = "near" if c >= gate - ELUSION_BAND else "far"
                add(("sentence", uid, (("measure", name),)), f"elusion:{name}:{s}")
                targets[f"elusion:{name}:{s}"] = ELUSION[s]
        return items, targets
    per_yes = math.ceil(EVAL["committed_total"] / max(1, len(f["measures"])))
    for name, m in f["measures"].items():
        for uid, u in m["units"].items():
            if ev:
                s = "eval:committed" if u["committed"] else "eval:not_committed"
            else:
                s = "train:near" if abs(u["posterior"] - m["floor"]) <= NEAR else "train:far"
            add(("sentence", uid, (("measure", name),)), f"measure:{name}:{s}")
        if ev:
            targets[f"measure:{name}:eval:committed"] = per_yes
            targets[f"measure:{name}:eval:not_committed"] = EVAL["not_committed"]
        else:
            for s, n in TRAIN["measure"].items():
                targets[f"measure:{name}:{s}"] = n
    a = f["assets"]
    for uid, own in a["amap"].items():
        if uid not in corpus.row_of:
            continue
        _, win, level = a["ua"][uid]
        winners = {x for x, _ in win}
        for asset, p in own.items():
            if ev:
                s = "eval:asset_yes" if level == "unit" and asset in winners else "eval:asset_no"
            else:
                s = "train:near" if abs(p - a["floor"]) <= NEAR else "train:far"
            add(("sentence", uid, (("asset", asset),)), f"asset:{asset}:{s}")
    for asset in a["defs"]:
        for s, n in (EVAL if ev else TRAIN)["asset"].items():
            targets[f"asset:{asset}:{s}"] = n
    for uid, (own, win, level) in a["ua"].items():
        if level == "paragraph":
            for asset, _ in win:
                add(("sentence", uid, (("asset", asset),)), "asset_paragraph")
    targets["asset_paragraph"] = (EVAL if ev else TRAIN)["asset_paragraph"]
    if not ev:
        for uid, sa, sb, ka, kb, *_ in f["pairs"]:
            s = band(min(ka, kb), BANDS)
            if s:
                add(("sentence", uid, (("measure", sa), ("measure", sb))), f"pairs:{s}")
        for s, *_ in BANDS:
            targets[f"pairs:{s}"] = TRAIN["pairs"]
    for r in corpus.index:
        add(("sentence", r["unit_id"], (("block",),)), "random")
    targets["random"] = (EVAL if ev else TRAIN)["random"]
    pre = "eval:" if ev else ""
    for lset, aid, pid, score in f["package_assets"]:
        s = band(score, BANDS)
        if s:
            add(("package", pid, (("package_asset", aid),)), f"package_assets:{pre}{s}")
    for lset, lid, item, score in f["links"]:
        s = band(score, BANDS)
        if s:
            add(("package", item.rsplit("|", 1)[1], (("link", lset, item.rsplit("|", 1)[0]),)), f"links:{pre}{s}")
    for s, *_ in BANDS:
        targets[f"package_assets:{pre}{s}"] = (EVAL if ev else TRAIN)["package_assets"]
        targets[f"links:{pre}{s}"] = (EVAL if ev else TRAIN)["links"]
    return items, targets


def line_chances(chance, f):
    """{(kind, id): {line: chance}}: a line's chance is the largest of the
    items it is in; the asset lines of a shown commitment take at least its
    measure line's chance, so measure and asset are judged together."""
    out = defaultdict(dict)
    strata = defaultdict(set)
    for (kind, key, lines), p in chance.items():
        d = out[(kind, key)]
        for ln in lines:
            d[ln] = max(d.get(ln, 0.0), p)
    ua = f["assets"]["ua"]
    levels = decide.link_levels()
    for name, m in f["measures"].items():
        for (kind, uid), d in out.items():
            p, u = d.get(("measure", name)), m["units"].get(uid)
            if kind == "sentence" and p and u and u["committed"]:
                for asset in decide.linked_assets(ua.get(uid, ({}, [], "none")), levels):
                    d[("asset", asset)] = max(d.get(("asset", asset), 0.0), p)
    return out


def draw_lines(lines, seed):
    """[(key, chance, {line: chance})] in draw order: one uniform per item
    (permanent random numbers) decides all its lines, so each line is shown
    with exactly its own chance."""
    got = []
    for key, d in lines.items():
        x = uniform(seed, key)
        shown = {ln: p for ln, p in d.items() if x < p}
        if shown:
            got.append((x, key, max(d.values()), shown))
    return [(key, c, shown) for _, key, c, shown in sorted(got)]


def strata_of(items, key):
    out = defaultdict(set)
    for (kind, k, lines), ss in items.items():
        if (kind, k) == key:
            for ln in lines:
                out[ln].update(ss)
    return out


# ---------------------------------------------------------------- tasks

def tasks_path(data, seed):
    return where(data, "review", f"tasks_s{seed}.jsonl")


def _mark(corpus, uid):
    """The paragraph with the sentence marked »like this«."""
    r = corpus.row(uid)
    para = corpus.paragraph_text(r["paragraph_id"])
    body = corpus.texts[uid].split("\n")[-1]
    i = para.find(body)
    return para[:i] + "»" + body + "«" + para[i + len(body):] if i >= 0 else para


def build_tasks(f, corpus, drawn, items, design, seed, scale):
    """One task per decision line, self-contained."""
    out = []
    graded_q = {name: Q.graded_questions(name)[0] for name in f["measures"]}
    asset_q, _ = Q.graded_questions(Q.ASSETS)
    type_q = {name: Q.type_questions(name)[m["label_id"]] for name, m in f["measures"].items()}
    pk_q = Q.package_asset_questions()
    scale_text = [f"{i} = {g}" for i, g in enumerate(scale)]
    fit_text = [f"{v} = {g.split(':', 1)[1].strip()}" for v, g in zip(("no", "unsure", "yes"), Q.FIT_SCALE)]
    for n, ((kind, key), chance, shown) in enumerate(drawn, 1):
        block = f"{'S' if kind == 'sentence' else 'K'}{n:04d}"
        strata = strata_of(items, (kind, key))
        base = {"block": block, "design": design, "seed": seed, "block_chance": round(chance, 6)}
        if kind == "sentence":
            uid = key
            r = corpus.row(uid)
            base.update(unit_id=uid, item_sha=sha(corpus.texts[uid]), sentence=corpus.texts[uid],
                        paragraph=_mark(corpus, uid), project_ids=corpus.projects(uid),
                        section=corpus.paras.get(r["paragraph_id"], {}).get("section_title", ""),
                        component=corpus.component(uid))
        else:
            p = f["packages"][key]
            base.update(package_id=key, item_sha=p["sha"], package=p["text"], project_ids=[p["project_id"]],
                        component=p["component"])
        k = 0
        for ln, p in sorted(shown.items(), key=lambda x: (x[0][0] != "measure", x[0])):
            k += 1
            t = dict(base, task_id=f"{block}.{k:02d}", chance=round(p, 6), weight=round(1 / p, 6),
                     strata=sorted(strata.get(ln, ())))
            if ln[0] == "measure":
                name = ln[1]
                m = f["measures"][name]
                t.update(kind="measure", label_set=name, label_id=m["label_id"], label_name=m["name"],
                         definition=m["definition"],
                         question=graded_q[name][m["label_id"]]["instructions"], scale=scale_text,
                         answer="grade", allowed=GRADES)
                out.append(t)
                if m["units"].get(uid, {}).get("committed"):
                    for tid, td in sorted(m["type_defs"].items()):
                        k += 1
                        out.append(dict(base, task_id=f"{block}.{k:02d}", chance=t["chance"], weight=t["weight"],
                                        strata=t["strata"], kind="type", label_set=name, label_id=tid,
                                        label_name=td["type_name"], definition=td["definition"],
                                        parent={"label_set": name, "task_id": t["task_id"],
                                                "note": "answer only when the measure task is graded 2 or 3"},
                                        question=type_q[name][tid]["instructions"], scale=fit_text,
                                        answer="grade", allowed=YES_NO))
            elif ln[0] == "asset":
                a = f["assets"]["defs"][ln[1]]
                t.update(kind="asset", label_set=Q.ASSETS, label_id=ln[1], label_name=a["name"],
                         definition=a["definition"], question=asset_q[ln[1]]["instructions"], scale=scale_text,
                         answer="grade", allowed=GRADES)
                out.append(t)
            elif ln[0] == "block":
                k -= 1
            elif ln[0] == "package_asset":
                a = f["assets"]["defs"][ln[1]]
                t.update(kind="package_asset", label_set=Q.ASSETS, label_id=ln[1], label_name=a["name"],
                         definition=a["definition"], question=pk_q[ln[1]]["instructions"], scale=fit_text,
                         answer="grade", allowed=YES_NO)
                out.append(t)
            elif ln[0] == "link":
                name, uid2 = ln[1], ln[2]
                m = f["measures"][name]
                t.update(kind="link", label_set=name, label_id=m["label_id"], label_name=m["name"],
                         definition=m["definition"], commitment=corpus.texts[uid2], unit_id=uid2,
                         commitment_component=corpus.component(uid2), item_sha=p_sha(corpus.texts[uid2], base["item_sha"]),
                         question=Q.LINK_QUESTION["instructions"], scale=fit_text, answer="grade", allowed=YES_NO)
                out.append(t)
        # the closing task: anything else that applies (measures and assets not listed)
        k += 1
        listed = {x["label_set"] if x["kind"] == "measure" else x["label_id"]
                  for x in out if x["block"] == block and x["kind"] in ("measure", "asset", "package_asset")}
        ids = sorted(set(f["measures"]) | set(f["assets"]["defs"])) if kind == "sentence" else sorted(f["assets"]["defs"])
        out.append(dict(base, task_id=f"{block}.{k:02d}", chance=round(chance, 6), weight=round(1 / chance, 6),
                        strata=sorted(strata.get(("block",), ())), kind="other",
                        question=("Which of these ids, not asked above, does the item commit the project to or "
                                  "concern? Answer none, or ids joined by |."),
                        allowed=["none"] + [i for i in ids if i not in listed], answer="ids"))
    return out


def p_sha(text, package_sha):
    return sha(sha(text) + "|" + package_sha)


def draw(conn, data, corpus, seed, design="train", names=None, log=print, dry_run=False):
    """Write the tasks for a seed. Refuses a seed already drawn."""
    path = tasks_path(data, seed)
    if os.path.exists(path) and not dry_run:
        raise SystemExit(f"check: {path} exists and may have grades; use another --seed")
    names = names or [n for n in labels.label_sets() if n != Q.ASSETS]
    f = frames(conn, data, corpus, names)
    if not f["measures"]:
        raise SystemExit("check: no measure has scores; run the measure stage first")
    items, targets = items_and_targets(f, corpus, design, data)
    chances = line_chances(inclusion(items, targets), f)
    drawn = draw_lines(chances, seed)
    scale = Q.load(next(iter(f["measures"])))[1]
    tasks = build_tasks(f, corpus, drawn, items, design, seed, scale)
    n_s = sum(1 for k, *_ in drawn if k[0] == "sentence")
    log(f"check: {n_s} sentences and {len(drawn) - n_s} packages drawn, {len(tasks)} tasks"
        + ("; --dry-run: nothing written" if dry_run else ""))
    if dry_run:
        return tasks
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    os.replace(path + ".part", path)
    log(f"  -> {path}")
    return tasks


def read_tasks(data, seed):
    with open(tasks_path(data, seed), encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


# ---------------------------------------------------------------- fills and grades

def read_fills(path):
    """{task_id: {grade, rationale, grader}} from a JSONL of fills; a
    duplicate task_id is an error."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            r = json.loads(line)
            tid = r.get("task_id")
            if tid in out:
                raise SystemExit(f"{path}:{i}: task {tid} answered twice")
            out[tid] = {"grade": str(r.get("grade", "")).strip().lower(), "rationale": r.get("rationale", ""),
                        "grader": r.get("grader", "")}
    return out


def check_fills(tasks, fills):
    """Every task answered with an allowed value; problems listed, not skipped."""
    by_id = {t["task_id"]: t for t in tasks}
    problems = [f"{tid}: not a task of this seed" for tid in fills if tid not in by_id]
    for t in tasks:
        a = fills.get(t["task_id"])
        if a is None:
            problems.append(f"{t['task_id']}: not answered")
            continue
        g = a["grade"]
        if t["kind"] == "other":
            ids = set() if g == "none" else {x.strip() for x in g.split("|") if x.strip()}
            bad = ids - set(t["allowed"])
            if bad:
                problems.append(f"{t['task_id']}: {sorted(bad)} not among the allowed ids")
        elif g not in t["allowed"]:
            problems.append(f"{t['task_id']}: {g!r} is not one of {t['allowed']}")
    return problems


def grade_rows(tasks, fills, source, grader_default=""):
    """The grade rows a filled task file gives: kinds measure, asset, type,
    pair (from two measure grades), package_asset, link, other."""
    by_block = defaultdict(list)
    for t in tasks:
        by_block[t["block"]].append(t)
    rows = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lo = min(panel.grades())
    qsha = {}

    def q_of(kind, label_set, label_id):
        key = (kind, label_set, label_id)
        if key not in qsha:
            if kind in ("measure", "asset"):
                qsha[key] = Q.sha(Q.graded_questions(label_set)[0][label_id])
            elif kind == "type":
                qsha[key] = Q.sha(next(iter(Q.type_questions(label_set).values()))[label_id])
            elif kind == "package_asset":
                qsha[key] = Q.sha(Q.package_asset_questions()[label_id])
            elif kind == "link":
                qsha[key] = Q.sha(Q.LINK_QUESTION)
            else:
                qsha[key] = ""
        return qsha[key]

    def row(t, kind, label_set, label_id, grade, item_sha=None, weight=None, question_sha=None):
        a = fills[t["task_id"]]
        return {"kind": kind, "label_set": label_set, "label_id": label_id, "item_sha": item_sha or t["item_sha"],
                "design": t["design"], "grade": grade, "grader": a.get("grader") or grader_default,
                "source": source, "question_sha": q_of(kind, label_set, label_id) if question_sha is None else question_sha,
                "stratum": "|".join(t.get("strata", [])), "weight": weight or t["weight"],
                "note": a.get("rationale", ""), "graded_at": now}
    for block, ts in by_block.items():
        measures = {}
        for t in ts:
            g = fills[t["task_id"]]["grade"]
            if g == "unsure":
                continue
            if t["kind"] == "measure":
                measures[t["label_set"]] = (int(g), t)
                rows.append(row(t, "measure", t["label_set"], t["label_id"], g))
            elif t["kind"] == "type":
                parent = measures.get(t["label_set"])
                if parent and parent[0] >= lo:
                    rows.append(row(t, "type", t["label_set"], t["label_id"], g))
            elif t["kind"] in ("asset", "package_asset", "link"):
                rows.append(row(t, t["kind"], t["label_set"], t["label_id"], g))
            elif t["kind"] == "other":
                ids = set() if g == "none" else {x.strip() for x in g.split("|") if x.strip()}
                for i in sorted(ids):
                    is_asset = any(a["id"] == i for a in Q.load(Q.ASSETS)[0])
                    rows.append(row(t, "other", Q.ASSETS if is_asset else i, i if is_asset else "", "yes",
                                    question_sha=""))
        # pair checks: two measure grades on one sentence answer them
        names = sorted(measures)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                (ga, ta), (gb, tb) = measures[a], measures[b]
                w = max(ta["weight"], tb["weight"])
                rows.append(row(ta, "pair", f"{a}|{b}", "keep_a", "yes" if ga >= lo else "no", weight=w,
                                question_sha=Q.sha(Q.pair_questions(a, b)["keep_a"])))
                rows.append(row(tb, "pair", f"{a}|{b}", "keep_b", "yes" if gb >= lo else "no", weight=w,
                                question_sha=Q.sha(Q.pair_questions(a, b)["keep_b"])))
    return rows


GRADE_COLS = ["kind", "label_set", "label_id", "item_sha", "design", "grade", "grader", "source",
              "question_sha", "stratum", "weight", "note", "graded_at"]


def store_grades(conn, rows):
    """Upsert grade rows: a later grade of the same item under the same label
    and design replaces an earlier one."""
    with conn.transaction():
        for r in rows:
            conn.execute(
                f"INSERT INTO {db.STORE}.grades ({', '.join(GRADE_COLS)}) VALUES ({', '.join(['%s'] * len(GRADE_COLS))}) "
                f"ON CONFLICT (kind, label_set, label_id, item_sha, design) DO UPDATE SET "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in GRADE_COLS[5:]),
                [r[c] for c in GRADE_COLS])
    conn.commit()
    return len(rows)


def fill(conn, data, seed, path, grader="", log=print):
    """Read a fill file against the seed's tasks, refuse anything off the
    list, store the grades."""
    tasks = read_tasks(data, seed)
    fills = read_fills(path)
    problems = check_fills(tasks, fills)
    if problems:
        for p in problems[:30]:
            log(f"  {p}")
        raise SystemExit(f"check: {len(problems)} problem(s) in {path}; nothing stored")
    rows = grade_rows(tasks, fills, os.path.basename(path), grader)
    n = store_grades(conn, rows)
    log(f"check: {n} grades stored from {len(fills)} answers (seed {seed})")
    return n


def spotcheck(data, seed, fills_path, out_path):
    """A thin workbook for a person to check an agent's fills: one row per
    task with the agent's grade and rationale and a blank human_grade."""
    import openpyxl
    tasks, fills = read_tasks(data, seed), read_fills(fills_path)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "spot check"
    cols = ["task_id", "kind", "label_name", "sentence", "paragraph", "package", "commitment", "grade",
            "rationale", "human_grade", "allowed"]
    ws.append(cols)
    for t in tasks:
        a = fills.get(t["task_id"], {})
        ws.append([t["task_id"], t["kind"], t.get("label_name", ""), t.get("sentence", ""), t.get("paragraph", ""),
                   t.get("package", ""), t.get("commitment", ""), a.get("grade", ""), a.get("rationale", ""), "",
                   "|".join(t["allowed"])])
    ws.freeze_panes = "B2"
    wb.save(out_path)
    return out_path


# ---------------------------------------------------------------- legacy import

def import_legacy(conn, data, log=print):
    """The grades of the file-based sheets (data/labels/*_units.csv,
    pairs.csv, package_assets.csv, package_links.csv) into the store, once."""
    import glob
    rows = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lo = min(panel.grades())
    for path in sorted(glob.glob(where(data, "labels", "*_units.csv"))):
        name = os.path.basename(path)[:-len("_units.csv")]
        types = {t["type_id"] for t in labels.load_types(name)} if name != Q.ASSETS else set()
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("grade", "") == "":
                    continue
                design = "eval" if (r.get("stratum") or "").startswith("eval") else "train"
                base = {"item_sha": r["text_sha256"], "design": design, "grader": "person",
                        "source": r.get("source", path), "question_sha": r.get("question_sha256", ""),
                        "stratum": r.get("stratum", ""), "weight": float(r.get("weight") or 1.0),
                        "note": "", "graded_at": r.get("graded_on") or now}
                if (r.get("stratum") or "").endswith("outside"):
                    if int(r["grade"]) >= lo:
                        rows.append(dict(base, kind="other", label_set=name, label_id="", grade="yes"))
                    continue
                rows.append(dict(base, kind="asset" if name == Q.ASSETS else "measure", label_set=name,
                                 label_id=r["label_id"], grade=r["grade"]))
                t = (r.get("type") or "").strip()
                if t and int(r["grade"]) >= lo and types:
                    yes = set(t.split("|")) - {"", "none"}
                    for tid in sorted(types):
                        rows.append(dict(base, kind="type", label_set=name, label_id=tid,
                                         grade="yes" if tid in yes else "no"))
    for file, kind in (("package_assets.csv", "package_asset"), ("package_links.csv", "link"),
                       ("pairs.csv", "pair")):
        path = where(data, "labels", file)
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                a = (r.get("answer") or r.get("applies") or "").strip().lower()
                base = {"design": "eval" if (r.get("stratum") or "").startswith("eval") else "train",
                        "grader": "person", "source": r.get("source", file),
                        "question_sha": r.get("question_sha256", ""), "stratum": r.get("stratum", ""),
                        "weight": float(r.get("weight") or 1.0), "note": "", "graded_at": r.get("graded_on") or now}
                if kind == "package_asset" and a in ("yes", "no"):
                    rows.append(dict(base, kind=kind, label_set=Q.ASSETS, label_id=r["asset_id"],
                                     item_sha=r["package_sha256"], grade=a))
                elif kind == "link" and a in ("yes", "no"):
                    rows.append(dict(base, kind=kind, label_set=r["label_set"], label_id=Q.load(r["label_set"])[0][0]["id"],
                                     item_sha=p_sha_from(r["text_sha256"], r["package_sha256"]), grade=a))
                elif kind == "pair" and a in ("only a", "only b", "both", "neither"):
                    for side, yes in (("keep_a", a in ("only a", "both")), ("keep_b", a in ("only b", "both"))):
                        rows.append(dict(base, kind=kind, label_set=f"{r['set_a']}|{r['set_b']}", label_id=side,
                                         item_sha=r["text_sha256"], grade="yes" if yes else "no"))
    seen = {}
    for r in rows:                                   # the latest grade of an item wins
        k = (r["kind"], r["label_set"], r["label_id"], r["item_sha"], r["design"])
        if k not in seen or r["graded_at"] >= seen[k]["graded_at"]:
            seen[k] = r
    n = store_grades(conn, list(seen.values()))
    log(f"check: {n} grades imported from the file sheets")
    return n


def p_sha_from(text_sha, package_sha):
    return sha(text_sha + "|" + package_sha)


# ---------------------------------------------------------------- evaluation

def wilson(k, n, z=1.96):
    if not n:
        return None, None
    p, d = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def grades_of(conn, kind, label_set=None):
    sql = f"SELECT label_set, label_id, item_sha, design, grade, weight, stratum FROM {db.STORE}.grades WHERE kind = %s"
    params = [kind]
    if label_set:
        sql += " AND label_set = %s"
        params.append(label_set)
    return db.rows(conn, sql, params)


def yes_of(kind, grade):
    return grade in ("yes",) if kind in FIT_KINDS else int(grade) >= min(panel.grades())


def blocks(conn, corpus, kind, names):
    """{label (set, id): (rows [(item, y, w, projects)], scores {item: raw},
    vectors {item: vec}, question_sha, design {item})} of a kind: every graded
    item that has a score now, matched by the text's checksum."""
    by_sha = {sha(t): u for u, t in corpus.texts.items()}
    out = {}
    if kind in ("measure", "asset"):
        for name in names:
            units = decide.measure_units(conn, corpus, name) if kind == "measure" else None
            amap = decide.asset_map(conn, corpus) if kind == "asset" else None
            qs, _ = Q.graded_questions(name)
            for lset, lid, item_sha, design, grade, w, stratum in grades_of(conn, kind, name):
                u = by_sha.get(item_sha)
                if u is None or lid not in qs:
                    continue
                score = units[u]["score"] if kind == "measure" and u in units else \
                    amap.get(u, {}).get(lid) if kind == "asset" else None
                if score is None:
                    continue
                b = out.setdefault((name, lid), ([], {}, {}, Q.sha(qs[lid]), {}))
                b[0].append((u, 1 if yes_of(kind, grade) else 0, float(w or 1), set(corpus.projects(u))))
                b[1][u] = float(score)
                v = corpus.vector(u)
                if v is not None:
                    b[2][u] = v
                b[4][u] = design
    elif kind == "type":
        for name in names:
            tps = decide.scores(conn, "type", name)
            raw = {(lid, u): float(s) for _, lid, u, s, *_ in tps}
            tq = Q.type_questions(name)
            for lset, tid, item_sha, design, grade, w, stratum in grades_of(conn, kind, name):
                u = by_sha.get(item_sha)
                if u is None or (tid, u) not in raw:
                    continue
                q = next((ts[tid] for ts in tq.values() if tid in ts), None)
                b = out.setdefault((name, tid), ([], {}, {}, Q.sha(q) if q else "", {}))
                b[0].append((u, 1 if grade == "yes" else 0, float(w or 1), set(corpus.projects(u))))
                b[1][u] = raw[(tid, u)]
                v = corpus.vector(u)
                if v is not None:
                    b[2][u] = v
                b[4][u] = design
    elif kind == "pair":
        raw = {(lset, side, u): float(s) for lset, side, u, s, *_ in decide.scores(conn, "pair")}
        for lset, side, item_sha, design, grade, w, stratum in grades_of(conn, kind):
            u = by_sha.get(item_sha)
            if u is None or (lset, side, u) not in raw:
                continue
            a, b_ = lset.split("|")
            b = out.setdefault((lset, side), ([], {}, {}, Q.sha(Q.pair_questions(a, b_)[side]), {}))
            b[0].append((f"{u}#{lset}", 1 if grade == "yes" else 0, float(w or 1), set(corpus.projects(u))))
            b[1][f"{u}#{lset}"] = raw[(lset, side, u)]
            v = corpus.vector(u)
            if v is not None:
                b[2][f"{u}#{lset}"] = v
            b[4][f"{u}#{lset}"] = design
    elif kind in ("package_asset", "link"):
        import checks
        packages = {p["sha"]: p for p in checks.load_packages(corpus.data)}
        if kind == "package_asset":
            raw = {(aid, pid): float(s) for _, aid, pid, s, *_ in decide.scores(conn, "package_asset")}
            pq = Q.package_asset_questions()
            by_pid = {p["package_id"]: p for p in packages.values()}
            for lset, aid, item_sha, design, grade, w, stratum in grades_of(conn, kind):
                p = packages.get(item_sha)
                if not p or (aid, p["package_id"]) not in raw:
                    continue
                b = out.setdefault((Q.ASSETS, aid), ([], {}, {}, Q.sha(pq[aid]), {}))
                b[0].append((p["package_id"], 1 if grade == "yes" else 0, float(w or 1), {p["project_id"]}))
                b[1][p["package_id"]] = raw[(aid, p["package_id"])]
                b[4][p["package_id"]] = design
        else:
            raw = {(lset, item): float(s) for lset, lid, item, s, *_ in decide.scores(conn, "link")}
            link_sha = {}
            for (lset, item) in raw:
                u, pid = item.rsplit("|", 1)
                if u in corpus.texts:
                    link_sha[(lset, p_sha(corpus.texts[u], next((p["sha"] for p in packages.values()
                                                                 if p["package_id"] == pid), "")))] = (lset, item)
            for lset, lid, item_sha, design, grade, w, stratum in grades_of(conn, kind):
                key = link_sha.get((lset, item_sha))
                if not key:
                    continue
                b = out.setdefault(("links", "link"), ([], {}, {}, Q.sha(Q.LINK_QUESTION), {}))
                item = key[1]
                b[0].append((item, 1 if grade == "yes" else 0, float(w or 1),
                             set(corpus.projects(item.rsplit("|", 1)[0]))))
                b[1][item] = raw[key]
                b[4][item] = design
    return out


def pr_table(rows, probs, floor, label):
    """Weighted precision and recall of probs against the grades at a range
    of floors, Wilson intervals on Kish's effective sample size."""
    ys, ws = [r[1] for r in rows], [r[2] for r in rows]
    ps = [probs[r[0]] for r in rows]
    f = lambda x: "-" if x is None else f"{x:.2f}"
    lines = [f"| {label}: floor | precision (95% CI) | recall (95% CI) | rows kept |", "|---|---|---|---|"]
    for t in sorted({0.3, 0.5, 0.6, 0.7, 0.8, 0.9, round(floor, 2)}):
        p, r, kept = posterior.weighted_pr(ps, ys, ws, t)
        nk = posterior.effective_n([w for p_, w in zip(ps, ws) if p_ >= t])
        npos = posterior.effective_n([w for y, w in zip(ys, ws) if y])
        pl, ph = wilson(round((p or 0) * nk), round(nk)) if p is not None and nk else (None, None)
        rl, rh = wilson(round((r or 0) * npos), round(npos)) if r is not None and npos else (None, None)
        mark = " (the floor)" if abs(t - floor) < 1e-9 else ""
        lines.append(f"| {t:.2f}{mark} | {f(p)} ({f(pl)}-{f(ph)}) | {f(r)} ({f(rl)}-{f(rh)}) | "
                     f"{sum(1 for p_ in ps if p_ >= t)} |")
    return lines


def evaluate_kind(conn, corpus, kind, names, log, grid=False):
    """Fit the kind's layers and (for measures and assets) gates from the
    training grades, report every grade out of sample, write the fits."""
    s = posterior.settings()
    bl = blocks(conn, corpus, kind, names)
    train = {k: ([r for r in b[0] if b[4][r[0]] != "eval"], b[1], b[2], b[3]) for k, b in bl.items()}
    train = {k: v for k, v in train.items() if v[0]}
    lines = [f"## {kind}", ""]
    if not bl:
        return lines + ["- no grades with a score", ""], {}
    specs = posterior.fit_pooled(train, s) if s["pool"] else \
        {k: sp for k, (g, sc, v, q) in train.items() if (sp := posterior.fit(g, sc, v, q, k, s))}
    for lset in {k[0] for k in bl}:
        fits.write(conn, "layer", lset, {k[1]: dict(sp, question_sha=sp["question_sha256"])
                                         for k, sp in specs.items() if k[0] == lset})
    oof = posterior.out_of_fold_all(train, s) if train else {}
    layer_on = bool(panel.get("posterior.on", False))
    target = float(panel.require("floor.target_precision"))
    lines.append(f"- {sum(len(b[0]) for b in bl.values())} graded rows over {len(bl)} labels; layer "
                 f"{'fitted for ' + str(len(specs)) + ' labels' if specs else 'not fitted (too few grades)'}; "
                 f"posterior.on is {'on: the layer decides' if layer_on else 'off: the raw score decides'}; "
                 f"fit weights = design weight ** {s['fit_weight_power']}")
    raw_all, oof_all, ys, ws = [], [], [], []
    for k, (rows, score, vecs, qsha, design) in sorted(bl.items()):
        o = oof.get(k, {})
        for item, y, w, _ in rows:
            raw_all.append(score[item])
            oof_all.append(o.get(item, score[item]))
            ys.append(y)
            ws.append(w)
    fl = decide.floor(names[0]) if kind == "measure" and len(names) == 1 else \
        float(panel.require(FLOOR_OF[kind])) if FLOOR_OF.get(kind) else float(panel.require("floor.default"))
    all_rows = [(i, y, w, None) for i, (y, w) in enumerate(zip(ys, ws))]
    lines += ["", "Raw score:"] + pr_table(all_rows, dict(enumerate(raw_all)), fl, kind)
    if oof:
        lines += ["", "Layer, out of fold by project:"] + pr_table(all_rows, dict(enumerate(oof_all)), fl, kind)
        a_raw, a_oof = posterior.wauc(raw_all, ys, ws), posterior.wauc(oof_all, ys, ws)
        lines.append(f"- weighted AUC: raw {a_raw:.3f}, layer {a_oof:.3f}" if a_raw is not None else "")
        sug = posterior.suggest_floor(oof_all if layer_on else raw_all, ys, target, ws)
    else:
        sug = posterior.suggest_floor(raw_all, ys, target, ws)
    if sug and sug[1] is not None:
        f2 = lambda x: "-" if x is None else f"{x:.2f}"
        lines.append(f"- suggested floor at {target:.0%} precision on the {'layer' if layer_on and oof else 'raw score'}: "
                     f"{sug[0]:.2f} (precision {f2(sug[1])}, recall {f2(sug[2])}); the panel's is {fl}")
    if kind in ("measure", "asset"):
        lines += gate_lines(conn, corpus, kind, names, bl)
    if grid and kind == "measure" and train:
        lines += grid_lines(train, s, log)
    return lines + [""], bl


def gate_lines(conn, corpus, kind, names, bl):
    """The skip rule's gate per label from the graded yes (training rows),
    written for the next run."""
    import funnel
    lo = min(panel.grades())
    out = ["", "Skip rule:"]
    by_sha = {sha(t): u for u, t in corpus.texts.items()}
    for name in names:
        run, cos = funnel.best_cosines(name, corpus.data, corpus)
        pats = labels.load_patterns(name)
        gates = {}
        for (lset, lid), (rows, *_rest, design) in bl.items():
            if lset != name:
                continue
            pos = []
            for u, y, w, _ in rows:
                if y and design.get(u) != "eval" and cos.get(lid) is not None:
                    pos.append((float(cos[lid][corpus.row_of[u]]),
                                any(p["regex"].search(corpus.texts[u]) for p in pats if p["label_id"] == lid), w))
            # sentences named on the closing line: graded yes the pool never read
            for ls2, lid2, item_sha, design2, grade, w, stratum in grades_of(conn, "other", name if kind == "measure" else Q.ASSETS):
                u = by_sha.get(item_sha)
                if u is not None and design2 != "eval" and (kind == "asset" and lid2 == lid or kind == "measure") \
                        and cos.get(lid) is not None:
                    pos.append((float(cos[lid][corpus.row_of[u]]), False, float(w or 1)))
            got = skip.fit(pos)
            if got is None:
                out.append(f"- {lset}/{lid}: {len(pos)} graded yes, too few or all pattern-matched: the panel's gate stands")
                continue
            gate, lost, n = got
            bound = skip.loss_bound(lost, [p[2] for p in pos])
            gates[lid] = {"gate": gate, "lost": lost, "lost_upper_95": bound, "positives": n,
                          "question_sha": Q.sha(Q.graded_questions(name)[0][lid]), "search_run": run}
            out.append(f"- {lset}/{lid}: gate {gate:.4f} on the best cosine; {lost:.1%} of {n} graded yes below it "
                       f"(at most {bound:.0%} with 95% confidence); the next run uses it")
        fits.write(conn, "gate", name, gates)
    return out


GRID = {"sharpness": [5.0, 10.0, 20.0, 40.0, 80.0], "ridge": [0.25, 1.0, 4.0], "centroid": [True, False],
        "pool": [False, True], "fit_weight_power": [0.0, 0.5, 1.0]}


def grid_lines(train, s, log):
    """Every GRID combination out of sample, ranked by weighted log loss."""
    import itertools
    results = []
    combos = list(itertools.product(*GRID.values()))
    for i, combo in enumerate(combos):
        st = dict(s, **dict(zip(GRID, combo)))
        oof = posterior.out_of_fold_all(train, st)
        ll, n, aucs = 0.0, 0.0, []
        for k, (rows, score, *_rest) in train.items():
            ps = [oof.get(k, {}).get(r[0], score[r[0]]) for r in rows]
            ys, ws = [r[1] for r in rows], [r[2] for r in rows]
            tot = sum(ws)
            for p, y, w in zip(ps, ys, ws):
                p = min(max(p, 1e-6), 1 - 1e-6)
                ll -= w / tot * len(ws) * (math.log(p) if y else math.log(1 - p))
                n += w / tot * len(ws)
            a = posterior.wauc(ps, ys, ws)
            if a is not None:
                aucs.append(a)
        results.append((ll / n if n else None, combo, sum(aucs) / len(aucs) if aucs else None))
        if (i + 1) % 30 == 0:
            log(f"  grid: {i + 1} of {len(combos)}")
    results.sort(key=lambda r: (r[0] is None, r[0]))
    f = lambda x: "-" if x is None else f"{x:.3f}"
    lines = ["", f"Layer settings, {len(combos)} combinations out of fold (log loss, lower is better):", "",
             "| rank | " + " | ".join(GRID) + " | log loss | mean AUC |", "|---|" + "---|" * (len(GRID) + 2)]
    for k, (ll, combo, a) in enumerate(results[:15], 1):
        lines.append(f"| {k} | " + " | ".join(str(c) for c in combo) + f" | {f(ll)} | {f(a)} |")
    return lines


def evaluate(conn, data, corpus=None, names=None, log=print, grid=False):
    """Every kind against every stored grade; fits written; measure_quality
    and data/reports/evaluate.md written. Returns the report path."""
    corpus = corpus or Q.Corpus(data)
    names = names or [n for n in labels.label_sets() if n != Q.ASSETS]
    lines = ["# Evaluation: every decision against the grades", "",
             "Yes = graded 2 or more (a fit question: yes). Weighted figures use each row's design weight "
             "(one over its chance of being drawn); intervals are 95 per cent Wilson on Kish's effective "
             "sample size. Training rows fit the layers and gates; evaluation rows never do. Figures on "
             "training rows are upper estimates: quote an evaluation draw's.", ""]
    quality = []
    for kind, ns in (("measure", names), ("asset", [Q.ASSETS]), ("type", names), ("pair", names),
                     ("package_asset", names), ("link", names)):
        try:
            got, bl = evaluate_kind(conn, corpus, kind, ns, log, grid=grid and kind == "measure")
        except SystemExit as exc:
            got, bl = [f"## {kind}", "", f"- {exc}", ""], {}
        lines += got
        if kind == "measure":
            for (lset, lid), (rows, score, vecs, qsha, design) in sorted(bl.items()):
                for des in ("train", "eval"):
                    rs = [r for r in rows if design[r[0]] == des]
                    if not rs:
                        continue
                    fl = decide.floor(lset)
                    p, r, _ = posterior.weighted_pr([score[x[0]] for x in rs], [x[1] for x in rs], [x[2] for x in rs], fl)
                    quality.append({"label_set": lset, "version": "", "question_sha256": qsha,
                                    "sheet": "evaluation" if des == "eval" else "training", "graded_units": len(rs),
                                    "precision": p, "recall": r, "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "report_path": "data/reports/evaluate.md"})
    import load_pg
    with conn.transaction():
        load_pg.prepare(conn)
        load_pg.load_rows(conn, "measure_quality", load_pg.QUALITY_COLS, quality, "evaluate")
    conn.commit()
    out = where(data, "reports", "evaluate.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log(f"check: report -> {out}")
    return out
