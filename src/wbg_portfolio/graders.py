"""Three grader models mark every item of an evaluation sample (docs/method/evaluation.md).

  grade(conn, data, seed, graders, api_key, ...)   asks what the store lacks, under --max-spend
  answers(conn, data, seed, graders)              -> {item: {grader: fields}} read from the store, no call
  truth(kind, by_grader)                          -> the truth rules, computed, never stored
  export_for_person(data, seed, n) / read_person(conn, data, seed, path, graders)

Each item is asked through the chat asker (src/wbg_portfolio/chat.py), so every answer is stored once in
<store>.answers under the grader, the item's state and the question, the ledger records what it cost, a
stop and a restart never pay twice, an unreadable reply is tried again the house way (second.max_attempts)
and never counted as a grade. The questions are the trial ruler's (questions.grader_*): a measure item is
two requests, the 0-3 grade alone in the ruler's exact wording (kind measure) and the digital and
general-aim questions (kind measure_extra), whose answers are read together. The request is the
trial's too (GRADER_REQUEST: replies of up to 1,500 tokens, the model's own reasoning left as it is), so the
grades read like the trial's.

The grader models are a command-line option (default GRADERS): adding them to the control panel would mean
editing a file people keep.
"""
import csv
import os
from collections import Counter, defaultdict

from wbg_portfolio import api
from wbg_portfolio import chat
from wbg_portfolio import common
from wbg_portfolio import db
from wbg_portfolio import labels
from wbg_portfolio import questions as Q
from wbg_portfolio import sampling
from wbg_portfolio.grading import uniform

GRADERS = ("openai/gpt-5.6-luna", "openai/gpt-5.4-mini", "anthropic/claude-haiku-5.5")
GRADER_REQUEST = {"max_tokens": 1500, "reasoning": True}
PILOT = 30                  # items a grader with no measured price is asked first, to measure it
PRICED = 10                 # stored answers a grader needs for a measured price
ITEM_KINDS = ("measure", "measure_extra", "types", "package_measure", "link", "asset")


# ------------------------------------------------------------------ the items

def _sentence_state(r):
    lead = r.get("lead_in") or ""
    return {"section": r.get("section") or "", "component": r.get("component") or "(none)",
            "lead_in_line": f"List lead-in (introduces the sentence): {lead}\n" if lead else "",
            "paragraph": r.get("paragraph") or ""}


def _package_state(r):
    return {"package": r["package"], "package_component": r.get("package_component") or "(unknown)",
            "category": r.get("category") or ""}


def _link_state(r):
    return {"sentence_component": r.get("component") or "(unknown)", "lead_in": r.get("lead_in") or "(none)",
            "sentence": r["sentence"], **_package_state(r), "method": r.get("method") or "",
            "amount": r.get("amount") or "unknown"}


class _Questions:
    """The questions of the label files as they are now, built once."""

    def __init__(self):
        self.cache = {}

    def __call__(self, kind, name=""):
        if (kind, name) not in self.cache:
            self.cache[(kind, name)] = self._build(kind, name)
        return self.cache[(kind, name)]

    @staticmethod
    def _build(kind, name):
        if kind == "asset":
            return Q.grader_asset_question(*Q.asset_catalogue())
        try:
            labs, scale = Q.load(name)
        except SystemExit:
            return None
        if not labs:
            return None
        lab = labs[0]
        if kind == "measure":
            return Q.grader_measure_question(lab, scale)
        if kind == "measure_extra":
            return Q.grader_measure_extra_question()
        if kind == "types":
            types = [t for t in labels.load_types(name) if t["label_id"] == lab["id"]]
            return Q.grader_types_question(lab, types) if types else None
        if kind == "package_measure":
            return Q.grader_package_measure_question(lab)
        return Q.grader_link_question(lab)


def items(data, seed):
    """{item kind: [(item_id, state, question, row)]} of a drawn sample, as the graders are asked them;
    an item whose measure is no longer in the label files is left out (counted under None)."""
    qs, out, gone = _Questions(), defaultdict(list), 0
    for kind in sampling.KINDS:
        for r in sampling.read(data, seed, kind):
            todo = [(kind, _sentence_state(r) if kind in ("measure", "asset") else
                     _package_state(r) if kind == "package_measure" else _link_state(r))]
            if kind == "measure":
                todo.append(("measure_extra", _sentence_state(r)))
            if kind == "measure" and r.get("ask_types"):
                todo.append(("types", _sentence_state(r)))
            for k, state in todo:
                q = qs(k, r.get("label_set", ""))
                if q is None:
                    gone += 1
                    continue
                out[k].append((f"{k}|{r['key']}", state, q, r))
    out[None] = gone
    return out


# ------------------------------------------------------------------ asking

def measured_price(conn, mdl):
    """US$ a grader's answer has cost on average, from its stored answers to grader questions (None with
    fewer than PRICED of them)."""
    n, avg = db.one(conn, f"SELECT count(*), avg(a.cost_usd) FROM {db.STORE}.answers a "
                          f"JOIN {db.STORE}.questions q ON q.sha = a.question_sha "
                          f"WHERE a.model = %s AND q.question ? 'template'", (mdl,))
    return float(avg) if n >= PRICED and avg is not None else None


def _flat(by_kind):
    return [(i, s, q) for k in ITEM_KINDS for i, s, q, _ in by_kind.get(k, [])]


def _ask(conn, its, mdl, *, api_key, dry_run, workers, log, price, seed):
    return chat.ask(conn, its, stage="grade", label_set=f"sample s{seed}", api_key=api_key, dry_run=dry_run,
                    workers=workers, log=log, mdl=mdl, request=GRADER_REQUEST, price=price)


def grade(conn, data, seed, graders=GRADERS, *, api_key="", dry_run=False, workers=10, log=print):
    """Ask every grader about every item of the sample the store lacks. Prints the estimate first (items x
    graders x measured price) and refuses to start when it passes what --max-spend leaves. Returns True
    when every request went through (run again to resume otherwise)."""
    by_kind = items(data, seed)
    its = _flat(by_kind)
    if not its:
        raise SystemExit(f"check grade: no sample drawn for seed {seed}; run check sample first")
    log(f"check grade: {len(its):,} item(s): " + ", ".join(f"{k} {len(by_kind.get(k, [])):,}" for k in ITEM_KINDS)
        + (f"; {by_kind[None]:,} left out (their measure is not in the label files)" if by_kind[None] else ""))
    quiet = lambda *_: None
    plan, total = [], 0.0
    for g in graders:
        price = measured_price(conn, g)
        _, st = _ask(conn, its, g, api_key="", dry_run=True, workers=workers, log=quiet, price=price, seed=seed)
        plan.append((g, price, st))
        total += st["estimate_usd"]
        log(f"  {g}: {st['stored']:,} stored, {st['to_ask']:,} to ask, "
            + (f"measured US${price:.5f} an item" if price is not None else
               f"no measured price yet (first {PILOT} asked to measure it; upper bound used)")
            + f": about US${st['estimate_usd']:.2f}" + (f"; gave up on {st['gave_up']:,}" if st["gave_up"] else ""))
    log(f"  estimate: about US${total:.2f} in all")
    if dry_run:
        return False
    left = api.cap_left(conn)
    if left is not None and total > left and all(p is not None for _, p, _ in plan):
        raise SystemExit(f"check grade: the estimate (US${total:.2f}) is more than --max-spend leaves "
                         f"(US${max(left, 0):.2f}); nothing asked")
    ok = True
    for g, price, st in plan:
        if not st["to_ask"]:
            continue
        if price is None:                                   # measure the price on a few items first
            missing = _missing(conn, its, g, seed)          # once: it reads every stored answer
            pending = [it for it in its if it[0] in missing]
            pending.sort(key=lambda it: uniform(seed, it[0]))
            _, s1 = _ask(conn, pending[:PILOT], g, api_key=api_key, dry_run=False, workers=workers, log=log,
                         price=None, seed=seed)
            ok = ok and not s1["failures"]
            price = measured_price(conn, g)
            _, st = _ask(conn, its, g, api_key="", dry_run=True, workers=workers, log=quiet, price=price, seed=seed)
            left = api.cap_left(conn)
            log(f"  {g}: measured " + (f"US${price:.5f} an item" if price is not None else "no price (too few answers)")
                + f"; {st['to_ask']:,} left to ask, about US${st['estimate_usd']:.2f}")
            if left is not None and st["estimate_usd"] > left:
                raise SystemExit(f"check grade: {g} would need about US${st['estimate_usd']:.2f} more, and "
                                 f"--max-spend leaves US${max(left, 0):.2f}; stopped (the answers so far are stored)")
        _, st = _ask(conn, its, g, api_key=api_key, dry_run=False, workers=workers, log=log, price=price, seed=seed)
        log(chat.estimate_line(f"grade {g}", st))
        if st["failures"]:
            ok = False
            log(f"  {len(st['failures'])} request(s) failed; run again to resume.")
    return ok


def _missing(conn, its, g, seed):
    got, _ = _ask(conn, its, g, api_key="", dry_run=True, workers=1, log=lambda *_: None, price=None, seed=seed)
    return {i for i, _, _ in its if i not in got}


def answers(conn, data, seed, graders=GRADERS):
    """({item kind: [(item_id, row, {grader: fields})]}, left out) from the store, asking nothing."""
    by_kind = items(data, seed)
    its = _flat(by_kind)
    got = {}
    for g in graders:
        a, _ = _ask(conn, its, g, api_key="", dry_run=True, workers=1, log=lambda *_: None, price=None, seed=seed)
        got[g] = a
    out = {}
    for k in ITEM_KINDS:
        out[k] = [(i, r, {g: got[g][i]["fields"] for g in graders if i in got[g] and "fields" in got[g][i]})
                  for i, _, _, r in by_kind.get(k, [])]
    # a measure item's two requests are read as one: the grade with the digital and general-aim answers
    extra = {i.split("|", 1)[1]: by for i, _, by in out.pop("measure_extra", [])}
    out["measure"] = [(i, r, {g: dict(f, **extra.get(i.split("|", 1)[1], {}).get(g, {})) for g, f in by.items()})
                      for i, r, by in out["measure"]]
    return out, by_kind[None]


# ------------------------------------------------------------------ the truth rules

def _votes(values, yes, strict_yes, bad=()):
    n = len(values)
    if n < 2:
        return None
    return {"lenient": sum(1 for v in values if yes(v)) >= 2,
            "strict": sum(1 for v in values if strict_yes(v)) >= 2 and not any(v in bad for v in values),
            "unanimous": n == 3 and len(set(map(str, values))) == 1, "readable": n}


def truth(kind, by_grader):
    """The truth of one item from the graders' readable answers ({grader: fields}), or None with fewer than
    two. measure: lenient = two or more give 2 or 3, strict = two or more give 3, plus digital (two or more
    grade it 2 or 3 on the digital scale) and general_aim (two or more yes), from the graders that answered
    the second request; package_measure and link: lenient = two or more yes, strict = two or more yes and none
    unsure; asset: the first asset (and type) two or more name, else split, and every asset two or more name
    first or second (assets); types: the types two or more list (lenient) or every reader lists (strict).
    digital and general_aim are None (no truth) when fewer than two graders answered them."""
    vals = list(by_grader.values())
    if kind == "measure":
        t = _votes([v["grade"] for v in vals], lambda g: g >= 2, lambda g: g == 3)
        if t:
            # from the graders that answered the second request only; fewer than two: no truth (None),
            # never "not digital" or "not a general aim"
            dig = [v["digital"] for v in vals if isinstance(v.get("digital"), int) and not isinstance(v["digital"], bool)]
            aim = [v["general_aim"] for v in vals if v.get("general_aim") in ("yes", "no")]
            t["digital"] = sum(1 for d in dig if d >= 2) >= 2 if len(dig) >= 2 else None
            t["general_aim"] = sum(1 for a in aim if a == "yes") >= 2 if len(aim) >= 2 else None
            t["grades"] = sorted(v["grade"] for v in vals)
        return t
    if kind in ("package_measure", "link"):
        return _votes([v["verdict"] for v in vals], lambda x: x == "yes", lambda x: x == "yes", bad=("unsure",))
    if len(vals) < 2:
        return None
    if kind == "asset":
        a, n = Counter(v["asset"] for v in vals).most_common(1)[0]
        asset = a if n >= 2 else None
        types = Counter(v["type"] for v in vals if v["asset"] == asset).most_common(1)
        named = Counter(a for v in vals for a in {v["asset"], v.get("second_asset", "none")} - {"none"})
        return {"asset": asset, "split": asset is None,
                "type": types[0][0] if asset and asset != "none" and types and types[0][1] >= 2 else None,
                "assets": sorted(a for a, k in named.items() if k >= 2),
                "unanimous": len(vals) == 3 and n == 3, "readable": len(vals)}
    c = Counter(t for v in vals for t in v["types"])
    return {"lenient": sorted(t for t, n in c.items() if n >= 2),
            "strict": sorted(t for t, n in c.items() if n == len(vals)), "readable": len(vals)}


# ------------------------------------------------------------------ a person's grades

PERSON_COLS = ["item_id", "region", "measure", "definition", "scale", "section", "component", "lead_in", "paragraph",
               "grade", "digital", "general_aim", "note"]


def person_path(data, seed):
    return os.path.join(sampling.folder(data, seed), "person.csv")


def export_for_person(data, seed, n=100, log=print):
    """n measure items across the regions (round robin, in hash order within a region) with blank grade
    columns and no model grade, for the owner to grade. Refuses to overwrite a file that may hold grades."""
    path = person_path(data, seed)
    if os.path.exists(path):
        raise SystemExit(f"check grade: {path} exists and may hold grades; read it back with --read-person")
    rows = sampling.read(data, seed, "measure")
    by = defaultdict(list)
    for r in sorted(rows, key=lambda r: uniform(seed, "person\t" + r["key"])):
        by[r["region"]].append(r)
    picked, queues = [], [by[k] for k in sorted(by)]
    while len(picked) < n and any(queues):
        for qu in queues:
            if qu and len(picked) < n:
                picked.append(qu.pop(0))
    out = []
    for r in picked:
        labs, scale = Q.load(r["label_set"])
        lab = labs[0]
        out.append({"item_id": f"measure|{r['key']}", "region": r["region"], "measure": lab["name"],
                    "definition": lab["definition"],
                    "scale": " / ".join(f"{i}: {g.replace('{name}', lab['name'])}" for i, g in enumerate(scale)),
                    "section": r.get("section", ""), "component": r.get("component", ""),
                    "lead_in": r.get("lead_in", ""), "paragraph": r.get("paragraph", "")})
    common.write_csv(path, PERSON_COLS, out)
    log(f"check grade: {len(out)} item(s) for a person to grade -> {path} (fill grade 0-3, digital yes/no, "
        f"general_aim yes/no; leave a row blank to skip it)")
    return path


def read_person(conn, data, seed, path, graders=GRADERS, log=print):
    """How the graders agree with a person's grades in the export file. Returns the figures."""
    person = {r["item_id"]: r for r in common.read_csv(path) if (r.get("grade") or "").strip() != ""}
    got, _ = answers(conn, data, seed, graders)
    model = {i: by for i, _, by in got["measure"]}
    res = {"items": 0, "graders": {}, "truth": Counter()}
    rows = []
    for i, p in person.items():
        try:
            g = int(p["grade"].strip())
        except ValueError:
            raise SystemExit(f"check grade: {path}: item {i}: grade {p['grade']!r} is not 0 to 3")
        rows.append((i, g, (p.get("digital") or "").strip().lower(), model.get(i, {})))
    res["items"] = len(rows)
    for gr in graders:
        pairs = [(g, by[gr]["grade"]) for _, g, _, by in rows if gr in by]
        dig = [(d, "yes" if by[gr].get("digital", -1) >= 2 else "no") for _, _, d, by in rows
               if gr in by and d in ("yes", "no") and "digital" in by[gr]]
        res["graders"][gr] = {
            "n": len(pairs), "exact": sum(a == b for a, b in pairs),
            "lenient_agree": sum((a >= 2) == (b >= 2) for a, b in pairs),
            "more_generous": sum(a < 2 <= b for a, b in pairs), "less_generous": sum(b < 2 <= a for a, b in pairs),
            "digital_n": len(dig), "digital_agree": sum(a == b for a, b in dig)}
    for _, g, _, by in rows:
        t = truth("measure", by)
        if t is None:
            res["truth"]["no truth"] += 1
        else:
            res["truth"][("person right" if g >= 2 else "person not right") + ", "
                         + ("graders right" if t["lenient"] else "graders not right")] += 1
    log(f"check grade: {res['items']} item(s) graded by a person in {path}")
    for gr, x in res["graders"].items():
        if x["n"]:
            log(f"  {gr}: {x['n']} compared; same grade {x['exact']/x['n']:.0%}; same right/not right "
                f"{x['lenient_agree']/x['n']:.0%}; says right where the person does not {x['more_generous']}, "
                f"the other way {x['less_generous']}"
                + (f"; digital agrees {x['digital_agree']}/{x['digital_n']}" if x["digital_n"] else ""))
    for k, v in sorted(res["truth"].items()):
        log(f"  two-of-three reading against the person: {k}: {v}")
    return res
