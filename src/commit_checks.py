#!/usr/bin/env python3
"""Two questions about the committed units of every measure, asked once the
measures have run.

  pairs  A unit two measures both commit (the same sentence graded 3 by each)
         is asked one choice question per pair: does it commit the project to
         only A, only B, both, or neither, each measure stated by its one-line
         boundary (the `boundary` column of analysis/labels/<set>.csv). Each
         measure is graded on its own, so nothing else holds the boundaries
         between neighbours: on a test of 35 such units, backup power and
         renewable energy went to renewable energy only in 5 of 7, climate
         information and early warning to early warning in 3 of 3
         (2026-10-21). The views drop a unit from a measure a pair answer
         says it is not (only the other, or neither).

  scope  Every committed unit, once: a requirement on everything of a kind the
         project buys or builds (a rule), one specific investment or activity,
         or not a commitment at all. A rule rightly links to many packages, an
         investment to a few; the views link an investment to its best
         dashboard_settings.investment_package_top packages and a rule to all.
         "Not a commitment" is a flag, never a cut: on the test it also caught
         real lists behind a cross-reference lead-in.

Both are choice questions to the decision model, stored like every answer
(src/answers.py) and re-read on a re-run.

Reads  data/measures/<set>-<version>/units.csv (via src/kinds.py)
       analysis/labels/<set>.csv (name, boundary)
Writes data/checks/checks-<version>/{pairs.csv,scope.csv,manifest.json}

  ./wbg run --only measure        runs it after the measures
  python src/commit_checks.py [--dry-run] [--projects P1,P2]
"""
import argparse, csv, hashlib, json, os, sys, time
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import classify  # noqa: E402
import labels  # noqa: E402

ASSET_SET = "assets"
PAIR_OPTIONS = ("only_a", "only_b", "both", "neither")
SCOPE = {
    "rule": ("A requirement that applies to everything of a kind the project buys or builds "
             "(for example: all IT equipment procured will be energy efficient; all sites will "
             "be designed to withstand floods)."),
    "investment": ("One specific investment or activity the project finances (for example: a "
                   "data centre, a backup site, an early warning system, an assessment of named "
                   "sites)."),
    "not_commitment": ("Not something the project commits to: background, context, the "
                       "country's situation, lessons, existing systems, or the World Bank's own "
                       "process."),
}
PAIR_COLS = ["unit_id", "set_a", "set_b", "decision", "probability", "p_only_a", "p_only_b",
             "p_both", "p_neither", "model_served", "cost"]
SCOPE_COLS = ["unit_id", "scope", "probability", "p_rule", "p_investment", "p_not_commitment",
              "model_served", "cost"]


def boundary_of(name):
    """(name, boundary) of a measure set: its first active label's name and
    boundary, the definition when no boundary is written."""
    path = os.path.join(labels.folder(name), f"{name}.csv")
    for r in labels.rows(path):
        if labels.is_active(r):
            nm = next(r[c] for c in r if c.endswith("_name")).strip()
            b = " ".join((r.get("boundary") or r["definition"]).split())
            return nm, b
    raise SystemExit(f"checks: {name} has no active label")


def pair_question(a, b):
    (na, ba), (nb, bb) = a, b
    return {"pair": {
        "type": "choice",
        "instructions": (f"A is {na}: {ba} B is {nb}: {bb} Which does the sentence in the "
                         f"'sentence' field commit the project to? Choose exactly one."),
        "criteria": {"only_a": f"Only A ({na}).", "only_b": f"Only B ({nb}).",
                     "both": f"Both: it commits separately to {na} and to {nb}.",
                     "neither": "Neither."}}}


def scope_question():
    return {"scope": {
        "type": "choice",
        "instructions": ("What kind of statement is the sentence in the 'sentence' field? "
                         "Choose exactly one."),
        "criteria": dict(SCOPE)}}


def committed(data, projects=None):
    """({set: {unit_id: unit row}}, [run folder names]) of every measure set
    with a current run."""
    import kinds
    out, runs = {}, []
    for name in labels.label_sets():
        if name == ASSET_SET:
            continue
        try:
            folder, units = kinds.committed_units(name, data, projects=projects)
        except SystemExit:
            continue
        runs.append(os.path.basename(folder))
        out[name] = {u["unit_id"]: u for us in units.values() for u in us}
    return out, runs


def plan(data, projects=None, model=classify.MODEL):
    """(pairs [(unit_id, set_a, set_b, request)], scope [(unit_id, request)],
    out folder). The folder is keyed on the measure runs, the model and
    every request, so a changed boundary or a new run is a new folder."""
    sets, runs = committed(data, projects)
    by_unit, text = defaultdict(set), {}
    for name, us in sets.items():
        for uid, u in us.items():
            by_unit[uid].add(name)
            text[uid] = u["text"]
    bound = {name: boundary_of(name) for name in sets}
    pairs = []
    for uid in sorted(by_unit):
        for a, b in combinations(sorted(by_unit[uid]), 2):
            pairs.append((uid, a, b, ({"sentence": text[uid]}, pair_question(bound[a], bound[b]))))
    scope = [(uid, ({"sentence": text[uid]}, scope_question())) for uid in sorted(text)]
    h = hashlib.sha256(f"{model}\t{sorted(runs)}\n".encode())
    for _, _, _, (state, qs) in pairs:
        h.update(classify.request_sha(classify.request_body(state, qs, model)).encode())
    for _, (state, qs) in scope:
        h.update(classify.request_sha(classify.request_body(state, qs, model)).encode())
    return pairs, scope, where(data, "checks", f"checks-{h.hexdigest()[:12]}")


def current_run(data, projects=None):
    """The checks folder for the measure runs as they stand, or None."""
    try:
        out = plan(data, projects)[2]
    except SystemExit:
        return None
    return out if os.path.exists(os.path.join(out, "manifest.json")) else None


def _probs(entry):
    return {k: float(v) for k, v in entry["answer"]["probabilities"].items()}


def run(data, *, api_key="", projects=None, dry_run=False, log=print, workers=classify.WORKERS,
        model=classify.MODEL):
    import answers
    pairs, scope, out = plan(data, projects, model)
    log(f"checks: {len(pairs):,} unit pair(s) two measures both commit, {len(scope):,} "
        f"committed unit(s) for scope -> {out}")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log("  already written")
        return out
    requests_ = [r for *_, r in pairs] + [r for _, r in scope]
    got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers,
                          dry_run=True)
    log(f"  stored already: {sum(1 for g in got if g):,}; to ask: {st['to_ask']:,} "
        f"(about US${st['estimate_usd']:.3f})")
    if dry_run:
        log("  --dry-run: nothing asked, nothing written")
        return None
    if st["to_ask"]:
        if not api_key:
            raise SystemExit("checks: no credential: set $WBG_OPENROUTER_KEY")
        got, st = answers.ask(model, requests_, data, api_key=api_key, log=log, workers=workers)
        classify.record_spend(data, "checks", "all", model, st["calls"], st["cost_usd"],
                              st["seconds"], os.path.basename(out))
        if st["failures"] or not all(got):
            log(f"  {len(st['failures'])} request(s) failed; nothing written. Run again to resume.")
            return None
    pair_rows, scope_rows = [], []
    for (uid, a, b, _), g in zip(pairs, got[:len(pairs)]):
        p = _probs(g["pair"])
        best = max(p, key=p.get)
        pair_rows.append({"unit_id": uid, "set_a": a, "set_b": b, "decision": best,
                          "probability": f"{p[best]:.3f}",
                          **{f"p_{k}": f"{p.get(k, 0.0):.3f}" for k in PAIR_OPTIONS},
                          "model_served": g["pair"]["model"],
                          "cost": f"{float(g['pair'].get('cost_usd') or 0):.6f}"})
    for (uid, _), g in zip(scope, got[len(pairs):]):
        p = _probs(g["scope"])
        best = max(p, key=p.get)
        scope_rows.append({"unit_id": uid, "scope": best, "probability": f"{p[best]:.3f}",
                           **{f"p_{k}": f"{p.get(k, 0.0):.3f}" for k in SCOPE},
                           "model_served": g["scope"]["model"],
                           "cost": f"{float(g['scope'].get('cost_usd') or 0):.6f}"})
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    for file, cols, rows in (("pairs.csv", PAIR_COLS, pair_rows), ("scope.csv", SCOPE_COLS, scope_rows)):
        with open(os.path.join(part, file), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    decisions = defaultdict(int)
    for r in pair_rows:
        decisions[(r["set_a"], r["set_b"], r["decision"])] += 1
    scopes = defaultdict(int)
    for r in scope_rows:
        scopes[r["scope"]] += 1
    lines = [f"checks: {len(pair_rows):,} pair answers, {len(scope_rows):,} scope answers by {model}",
             "  scope: " + ", ".join(f"{k} {v}" for k, v in sorted(scopes.items(), key=lambda x: -x[1])),
             "  pairs (set_a / set_b: decision count):"]
    for (a, b, d), n in sorted(decisions.items()):
        lines.append(f"    {a} / {b}: {d} {n}")
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"model": model, "pairs": len(pair_rows), "scope": len(scope_rows),
                   "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  fh, indent=1, sort_keys=True)
    os.replace(part, out)
    report = where(data, "reports", "checks.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + f"\n  -> {out}\n")
    for line in lines:
        log(line)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--projects", default="", help="comma-separated project ids: the run's scope")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    import embed
    key = "" if args.dry_run else embed.read_key("")
    out = run(args.data, api_key=key, projects=classify.scope(args.projects), dry_run=args.dry_run)
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
