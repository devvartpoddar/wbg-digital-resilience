#!/usr/bin/env python3
"""One question about the committed units of every measure, asked once the
measures have run.

  pairs  A unit two measures both commit is asked, for each side, the form
         every decision asks (classify.graded_question) on that measure's
         scale: how the sentence relates to the measure, with the other
         measure's boundary beside it (the `boundary` column of
         analysis/labels/<set>.csv, else its definition). Each measure is
         graded on its own, so nothing else holds the boundaries between
         neighbours. Each side's score goes through the measure's pair layer
         once fitted (src/judged.py): p_keep_a and p_keep_b; the views keep
         the unit in a measure when its side reaches floor.pair_check
         (dashboard.apply_measure_pair_checks). Until 2026-10-22 one choice
         of four (only A, only B, both, neither); on Dev's graded sentences
         that choice raised recall at 0.90 precision from 0.78 to 0.87 over
         the probability layer alone, untested in the graded form.

Graded questions to the decision model, stored like every answer
(src/answers.py) and re-read on a re-run.

Reads  data/measures/<set>-<version>/units.csv (via src/kinds.py)
       analysis/labels/<set>.csv (name, boundary)
Writes data/checks/checks-<version>/{pairs.csv,manifest.json}

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
import prior  # noqa: E402

ASSET_SET = "assets"
PAIR_OPTIONS = ("only_a", "only_b", "both", "neither")
PAIR_COLS = ["unit_id", "set_a", "set_b", "decision", "score_a", "score_b", "p_keep_a",
             "p_keep_b", "model_served", "cost",
             "prior_probability_a", "prior_phrase_cosine_a", "prior_probability_b",
             "prior_phrase_cosine_b"]


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
    """The form every decision asks (classify.graded_question), once per side
    on the side's own scale: how the sentence relates to the measure, with
    its definition and the other measure's boundary beside it. keep_a and
    keep_b."""
    out = {}
    for key, s, o in (("keep_a", a, b), ("keep_b", b, a)):
        labs, scale = classify.load_labels(s, labels.folder(s))
        on, ob = boundary_of(o)
        lab = labs[0]
        out[key] = classify.graded_question(
            {"id": lab["id"], "name": lab["name"],
             "definition": f"{lab['definition']} The sentence is also counted for {on}: {ob} "
                           f"Grade only {lab['name']}."}, scale)
    return out


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
    """(pairs [(unit_id, set_a, set_b, request)], out folder, {(set, unit_id):
    unit row}). The folder is keyed on the measure runs, the model and every
    request, so a changed boundary or a new run is a new folder."""
    sets, runs = committed(data, projects)
    by_unit, text = defaultdict(set), {}
    for name, us in sets.items():
        for uid, u in us.items():
            by_unit[uid].add(name)
            text[uid] = u["text"]
    pairs = []
    for uid in sorted(by_unit):
        for a, b in combinations(sorted(by_unit[uid]), 2):
            pairs.append((uid, a, b, ({"sentence": text[uid]}, pair_question(a, b))))
    import judged
    h = hashlib.sha256(f"{model}\t{sorted(runs)}\nprior={prior.VERSION}\n"
                       f"layers={judged.digest(data, 'pairs', keep_shas(sets))}\n"
                       f"floor={floor()}\n".encode())
    for _, _, _, (state, qs) in pairs:
        h.update(classify.request_sha(classify.request_body(state, qs, model)).encode())
    known = {(name, uid): u for name, us in sets.items() for uid, u in us.items()}
    return pairs, where(data, "checks", f"checks-{h.hexdigest()[:12]}"), known


def keep_shas(sets):
    """{keep:<set>: SHA-256 of the set's own graded question}: a pair layer
    for a measure applies while its definition and scale read the same."""
    import judged
    out = {}
    for s in sets:
        labs, scale = classify.load_labels(s, labels.folder(s))
        out[f"keep:{s}"] = judged.sha(classify.graded_question(labs[0], scale))
    return out


def floor():
    import panel
    return float(panel.get("floor.pair_check", 0.5))


def decision(keep_a, keep_b, at=None):
    """only_a, only_b, both or neither, from the two sides' posteriors."""
    at = floor() if at is None else at
    return {(True, True): "both", (True, False): "only_a", (False, True): "only_b",
            (False, False): "neither"}[(keep_a >= at, keep_b >= at)]


def current_run(data, projects=None):
    """The checks folder for the measure runs as they stand, or None."""
    try:
        out = plan(data, projects)[1]
    except SystemExit:
        return None
    return out if os.path.exists(os.path.join(out, "manifest.json")) else None


def prior_of(known, uid, name, side):
    """What is known about the unit for one measure before the pair is asked
    (src/prior.py): the measure's probability for it (the probability layer's,
    else the model's score) and its cosine to the measure's phrases. Unlike
    the other questions' priors this one holds an earlier answer: the pair
    question is asked only because both measures already committed the unit."""
    import measure
    u = known.get((name, uid), {})
    p = u.get("p_committed_calibrated") or (f"{measure.score_of(u):.4f}" if u else "")
    return {f"prior_probability_{side}": p,
            f"prior_phrase_cosine_{side}": u.get("prior_phrase_cosine", "")}


def run(data, *, api_key="", projects=None, dry_run=False, log=print, workers=classify.WORKERS,
        model=classify.MODEL):
    import answers
    pairs, out, known = plan(data, projects, model)
    log(f"checks: {len(pairs):,} unit pair(s) two measures both commit -> {out}")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log("  already written")
        return out
    requests_ = [r for *_, r in pairs]
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
    # Each side's score through the side's pair layer (src/judged.py) when
    # fitted: the posterior that the sentence is that measure, with the other
    # beside it; the views keep it in the measure at floor.pair_check.
    import judged
    lays = judged.layers(data, "pairs", keep_shas({s for _, a, b, _ in pairs for s in (a, b)}))
    pair_rows = []
    for (uid, a, b, _), g in zip(pairs, got):
        sa, sb = classify.graded_score(g["keep_a"]), classify.graded_score(g["keep_b"])
        item = f"{uid}#{a}|{b}"
        ka = judged.calibrated(lays, f"keep:{a}", item, sa)
        kb = judged.calibrated(lays, f"keep:{b}", item, sb)
        pair_rows.append({"unit_id": uid, "set_a": a, "set_b": b, "decision": decision(ka, kb),
                          "score_a": f"{sa:.3f}", "score_b": f"{sb:.3f}",
                          "p_keep_a": f"{ka:.3f}", "p_keep_b": f"{kb:.3f}",
                          "model_served": "|".join(sorted({g[k]["model"] for k in g})),
                          "cost": f"{sum(float(g[k].get('cost_usd') or 0) for k in g):.6f}",
                          **prior_of(known, uid, a, "a"), **prior_of(known, uid, b, "b")})
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "pairs.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PAIR_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(pair_rows)
    decisions = defaultdict(int)
    for r in pair_rows:
        decisions[(r["set_a"], r["set_b"], r["decision"])] += 1
    lines = [f"checks: {len(pair_rows):,} pair answers by {model}",
             "  pairs (set_a / set_b: decision count):"]
    for (a, b, d), n in sorted(decisions.items()):
        lines.append(f"    {a} / {b}: {d} {n}")
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"model": model, "pairs": len(pair_rows),
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
