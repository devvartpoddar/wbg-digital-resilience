#!/usr/bin/env python3
"""Grade a measure's candidate units with the decision model, through the
funnel: search and word patterns find the candidates (src/candidates.py), the
asset map says which of them sit with a digital asset (src/classify.py on
the assets set), and the decision model reads what is left.

Nothing is cut off by default: every candidate is graded, and the asset link
is a column, so the gate's cost in missed commitments can be measured before
a threshold is chosen (src/evaluate_activities.py). --gate paragraph or
--gate component with --asset-min sends only the units whose paragraph (or
component) the asset map scores at least that high: the setting at scale,
once the measurement has been made.

The question names the measure and its definition and never the asset: the
decision model reads literally, and a question about "climate risk
assessment of digital infrastructure" scored a tower-siting assessment at
0.17 where the same question without the asset phrase scored it 0.97. The
asset link is computed by the asset map instead.

Reads  data/candidates/<set>-<version>/candidates.csv
       data/classify/assets-<version>/scores.csv        the asset map (optional)
       data/appraisal/{paragraphs,components}.csv, data/appraisal/text/
       analysis/labels/<set>.csv, <set>_scale.csv
Writes data/classify/cache/<model>/...                   every response, once
       data/measures/<set>-<version>/units.csv           one row per graded unit
       data/measures/<set>-<version>/manifest.json
       data/reports/measure_<set>.txt

<version> is a SHA-256 over the candidates version, the asset map version,
the exact questions, the model, the context design and the gate, so any
change in what is asked or of whom is a new run, and an unchanged one reads
the store and asks nothing.

  ./wbg measure cra                              every candidate, paragraph context
  ./wbg measure cra --context sentence           the sentence alone
  ./wbg measure cra --gate paragraph --asset-min 0.5
  ./wbg measure cra --dry-run
"""
import argparse, csv, hashlib, json, os, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import candidates  # noqa: E402
import classify  # noqa: E402
import commitment  # noqa: E402
import search  # noqa: E402

# The dated snapshot, not the alias: the alias moves when the vendor ships a
# release, and a run must be repeatable. The response names the snapshot that
# served it; a mismatch is reported.
MODEL = "typesafe/jev-1.13-20260917"
ASSET_SET = "assets"
CONTEXTS = ("paragraph", "sentence")
GATES = ("none", "paragraph", "component")
UNIT_COLS = ["label_id", "unit_id", "kind", "paragraph_id", "doc_id", "project_ids",
             "component_number", "subcomponent_number", "component_key", "copies", "route",
             "rank", "score", "patterns", "p_commitment", "firmness", "read_order",
             "asset_para", "asset_p_para", "asset_comp", "asset_p_comp", "sent",
             "grade", "expected", "confidence", "p0", "p1", "p2", "p3", "p4",
             "model_served", "cost", "text"]
# Asset-map thresholds the report tabulates (nothing is chosen here).
REPORT_THRESHOLDS = (0.3, 0.5, 0.7, 0.9)


def questions(labels, scale, context):
    """One graded question per label about the sentence, the paragraph given
    as context when the design says so."""
    out = {}
    where_ = ("the 'sentence' field, read with the 'paragraph' it sits in,"
              if context == "paragraph" else "the 'sentence' field,")
    for lab in labels:
        out[lab["id"]] = {
            "type": "score",
            "instructions": (f"How does the sentence from a World Bank project appraisal "
                             f"document in {where_} relate to {lab['name']}? "
                             f"{lab['name']} means: {lab['definition']}"),
            "criteria": [g.replace("{name}", lab["name"]) for g in scale],
        }
    return out


# ------------------------------------------------------------------ the inputs

def asset_map(data):
    """({paragraph_id: (label, p_at_least_2)}, {(doc_id, component_number):
    (label, p)}, run name) from the current assets classification: for each
    paragraph the asset with the highest probability of grade 2 or more
    (supported or financed), and for each component the best of its
    paragraphs. Empty without a run."""
    folder = classify.current_run(ASSET_SET, data)
    if not folder:
        return {}, {}, ""
    para, comp = {}, {}
    with open(os.path.join(folder, "scores.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            p = float(r["p2"] or 0) + float(r["p3"] or 0)
            pid = r["paragraph_id"]
            if p > para.get(pid, ("", -1.0))[1]:
                para[pid] = (r["label_id"], p)
            key = (r["doc_id"], r.get("component_number") or "")
            if key[1] and p > comp.get(key, ("", -1.0))[1]:
                comp[key] = (r["label_id"], p)
    return para, comp, os.path.basename(folder)


def paragraph_rows(data):
    """{paragraph_id: row} for every paragraph, with the text resolved."""
    out, texts = {}, {}
    path = where(data, "appraisal", "paragraphs.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["paragraph_id"]] = r
    return out


def _text(cache, data, doc_id):
    if doc_id not in cache:
        with open(where(data, "text", f"{doc_id}.txt"), encoding="utf-8") as fh:
            cache[doc_id] = fh.read()
    return cache[doc_id]


def component_names(data):
    names = {}
    path = where(data, "appraisal", "components.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in sorted(csv.DictReader(fh), key=lambda r: r["source"] == "heading"):
                names.setdefault((r["doc_id"], r["number"]), r["name"])
    return names


def state_for(row, para, unit_text, para_text, names, context):
    sub = row.get("subcomponent_number") or ""
    comp = sub or row.get("component_number") or ""
    name = names.get((row["doc_id"], comp)) or \
        (para.get("subcomponent_name") if sub else para.get("component_name")) or ""
    state = {"section": para.get("section_title", ""), "sentence": unit_text}
    if comp:
        state["component"] = f"{comp} {name}".strip()
    if context == "paragraph" and para_text != unit_text:
        state["paragraph"] = para_text
    return state


def gated(row, gate, asset_min):
    if gate == "none":
        return True
    p = row["asset_p_para"] if gate == "paragraph" else row["asset_p_comp"]
    return p != "" and float(p) >= asset_min


# ------------------------------------------------------------------- the plan

def plan(name, data, *, model=MODEL, context="paragraph", gate="none", asset_min=0.0,
         top=candidates.TOP, log=print):
    """Everything a run would send: (labels, scale, questions, rows, bodies,
    shas, out, provenance). rows are every candidate with its asset columns
    and `sent`; bodies and shas only for the sent ones."""
    if context not in CONTEXTS or gate not in GATES:
        raise SystemExit(f"measure: --context is one of {CONTEXTS}, --gate one of {GATES}")
    cand = candidates.build(name, data, top=top, log=log)
    labels, scale = classify.load_labels(name, None)
    qs = questions(labels, scale, context)
    para_assets, comp_assets, asset_run = asset_map(data)
    if gate != "none" and not asset_run:
        raise SystemExit(f"measure: --gate {gate} needs the asset map: run ./wbg classify "
                         f"{ASSET_SET} first")
    paras = paragraph_rows(data)
    names = component_names(data)
    run, _ = search.run_folder(name, data)
    _, _, index = search.load_run(run, data)
    unit_texts = commitment.unit_texts(data, index)
    texts = {}
    rows, bodies, shas = [], [], []
    with open(os.path.join(cand, "candidates.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            para = paras[r["paragraph_id"]]
            a_para = para_assets.get(r["paragraph_id"], ("", ""))
            a_comp = comp_assets.get((r["doc_id"], r.get("component_number") or ""), ("", ""))
            row = dict(r, asset_para=a_para[0],
                       asset_p_para="" if a_para[1] == "" else f"{a_para[1]:.4f}",
                       asset_comp=a_comp[0],
                       asset_p_comp="" if a_comp[1] == "" else f"{a_comp[1]:.4f}")
            row["sent"] = "true" if gated(row, gate, asset_min) else "false"
            row["text"] = unit_texts[r["unit_id"]]
            if row["sent"] == "true":
                doc = _text(texts, data, r["doc_id"])
                para_text = doc[int(para["char_start"]):int(para["char_end"])]
                one = {lab["id"]: qs[lab["id"]] for lab in labels if lab["id"] == r["label_id"]}
                body = classify.request_body(
                    state_for(r, para, row["text"], para_text, names, context), one, model)
                if len(json.dumps(body, ensure_ascii=False)) > classify.MAX_REQUEST_CHARS:
                    raise SystemExit(f"measure: {r['unit_id']} makes a request over "
                                     f"{classify.MAX_REQUEST_CHARS:,} characters")
                bodies.append(body)
                shas.append(classify.request_sha(body))
            rows.append(row)
    h = hashlib.sha256()
    h.update(f"{os.path.basename(cand)}\t{asset_run}\t{model}\t{context}\t{gate}\t"
             f"{asset_min}\n".encode())
    h.update(json.dumps(qs, sort_keys=True).encode())
    for row, sha in zip((r for r in rows if r["sent"] == "true"), shas):
        h.update(f"{row['unit_id']}:{sha}\n".encode())
    out = where(data, "measures", f"{name}-{h.hexdigest()[:12]}")
    prov = {"candidates": os.path.basename(cand), "asset_map": asset_run, "model": model,
            "context": context, "gate": gate, "asset_min": asset_min, "top": top}
    return labels, scale, qs, rows, bodies, shas, out, prov


def current_run(name, data, **kw):
    out = plan(name, data, log=lambda *_: None, **kw)[6]
    return out if os.path.exists(os.path.join(out, "manifest.json")) else None


# -------------------------------------------------------------------- the run

def _grade_cols(answer):
    probs = [float(answer["probabilities"][k]) for k in sorted(answer["probabilities"], key=int)]
    total = sum(probs) or 1.0
    out = {"grade": max(range(len(probs)), key=lambda i: probs[i]),
           "expected": f"{sum(i * q for i, q in enumerate(probs)) / total:.3f}",
           "confidence": f"{float(answer.get('confidence', 0)):.3f}"}
    out.update({f"p{i}": (f"{probs[i]:.4f}" if i < len(probs) else "") for i in range(5)})
    return out


def report_lines(name, rows, scale, prov, calls, cost, seconds, served):
    sent = [r for r in rows if r["sent"] == "true"]
    lines = [f"{name}: {len(rows):,} candidates, {len(sent):,} graded by {prov['model']} "
             f"(context {prov['context']}, gate {prov['gate']}"
             + (f" >= {prov['asset_min']}" if prov["gate"] != "none" else "") + ")",
             f"  candidates {prov['candidates']}; asset map {prov['asset_map'] or 'none'}",
             f"  this run: {calls:,} calls, US${cost:.4f}, {seconds:.0f}s; "
             f"stored responses cover every graded unit; models served: {served}"]
    for lab in sorted({r["label_id"] for r in rows}):
        mine = [r for r in sent if r["label_id"] == lab]
        counts = [sum(1 for r in mine if int(r["grade"]) == g) for g in range(len(scale))]
        top = [r for r in mine if int(r["grade"]) == len(scale) - 1]
        lines.append(f"\n  {lab}: graded units by most likely grade {counts}; "
                     f"grade {len(scale) - 1} (committed) in {len(top):,} units, "
                     f"{len({p for r in top for p in r['project_ids'].split('|')})} projects; "
                     f"prevalence in the pool {len(top) / max(len(mine), 1):.1%}")
        by_route = defaultdict(lambda: [0, 0])
        for r in mine:
            by_route[r["route"]][0] += 1
            by_route[r["route"]][1] += int(r["grade"]) == len(scale) - 1
        lines.append("    by route (graded, committed): " + ", ".join(
            f"{k} {v[0]:,}/{v[1]:,}" for k, v in sorted(by_route.items())))
        if prov["asset_map"]:
            lines.append("    committed units kept by an asset gate on the paragraph / "
                         "the component, at thresholds " + ", ".join(
                             f"{t}: {sum(1 for r in top if r['asset_p_para'] and float(r['asset_p_para']) >= t)}"
                             f"/{sum(1 for r in top if r['asset_p_comp'] and float(r['asset_p_comp']) >= t)}"
                             for t in REPORT_THRESHOLDS) + f" of {len(top)}")
        proj = defaultdict(int)
        for r in top:
            for p in r["project_ids"].split("|"):
                proj[p] += 1
        lines.append("    committed units by project: " + ", ".join(
            f"{p} {n}" for p, n in sorted(proj.items(), key=lambda x: (-x[1], x[0]))))
    return lines


def measure(name, data, *, api_key="", dry_run=False, log=print, workers=classify.WORKERS, **kw):
    labels, scale, qs, rows, bodies, shas, out, prov = plan(name, data, log=log, **kw)
    sent = [r for r in rows if r["sent"] == "true"]
    log(f"{name}: {len(rows):,} candidates, {len(sent):,} to grade, {len(labels)} label(s) x "
        f"{len(scale)} grades, context {prov['context']}, gate {prov['gate']}")
    if os.path.exists(os.path.join(out, "manifest.json")):
        log(f"  already written: {out}")
        return out
    root = classify.cache_root(data, prov["model"])
    missing = [(s, b) for s, b in zip(shas, bodies) if classify.read_cached(root, s) is None]
    todo = list({s: (s, b) for s, b in missing}.values())
    chars = sum(len(json.dumps(b, ensure_ascii=False)) for _, b in todo)
    log(f"  stored already: {len(sent) - len(missing):,}; to ask: {len(todo):,} "
        f"(about {chars / 4 / 1e6:.2f}M tokens, US${chars / 4 / 1e6 * classify.PRICE_PER_MTOK:.3f})")
    if dry_run:
        log("  --dry-run: no request made, nothing written")
        return None
    t0 = time.time()
    calls, cost = 0, 0.0
    if todo:
        if not api_key:
            raise SystemExit("measure: no credential: set $WBG_OPENROUTER_KEY")
        calls, failures, cost = classify.fetch_missing(todo, root, qs, len(scale),
                                                       api_key=api_key, log=log, workers=workers)
        if failures:
            log(f"  {len(failures)} request(s) failed; nothing written. Run again to resume.")
            for sha, err in failures[:5]:
                log(f"    {sha[:12]}: {err}")
            return None
    seconds = time.time() - t0
    served = defaultdict(int)
    it = iter(shas)
    for r in rows:
        if r["sent"] != "true":
            r.update({"grade": "", "expected": "", "confidence": "", "model_served": "",
                      "cost": "", **{f"p{i}": "" for i in range(5)}})
            continue
        response = classify.read_cached(root, next(it))
        r.update(_grade_cols(response["answers"][r["label_id"]]))
        r["model_served"] = response.get("model", "")
        r["cost"] = f"{float((response.get('usage') or {}).get('cost') or 0):.6f}"
        served[r["model_served"]] += 1
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "units.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=UNIT_COLS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    lines = report_lines(name, rows, scale, prov, calls, cost, seconds, dict(served))
    manifest = dict(prov, label_set=name, version=os.path.basename(out).rsplit("-", 1)[1],
                    candidates_total=len(rows), graded=len(sent), questions=qs,
                    question_sha256=classify.question_shas(qs), scale=scale,
                    models_served=dict(served), calls_this_run=calls,
                    cost_this_run_usd=round(cost, 6), seconds_this_run=round(seconds, 1),
                    cost_stored_usd=round(sum(float(r["cost"] or 0) for r in sent), 6),
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False, sort_keys=True)
    os.replace(part, out)
    report = where(data, "reports", f"measure_{name}.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + f"\n  -> {out}\n")
    for line in lines:
        log(line)
    if any(m != prov["model"] for m in served):
        log(f"  warning: a snapshot other than the one requested served some units: {dict(served)}")
    log(f"  wrote {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label_set")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--context", default="paragraph", choices=CONTEXTS)
    ap.add_argument("--gate", default="none", choices=GATES)
    ap.add_argument("--asset-min", type=float, default=0.0,
                    help="with --gate: the asset map's P(grade >= 2) a paragraph or "
                         "component needs for its units to be sent")
    ap.add_argument("--top", type=int, default=candidates.TOP)
    ap.add_argument("--workers", type=int, default=classify.WORKERS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    import embed
    key = "" if args.dry_run else embed.read_key("")
    out = measure(args.label_set, args.data, api_key=key, dry_run=args.dry_run,
                  workers=args.workers, model=args.model, context=args.context,
                  gate=args.gate, asset_min=args.asset_min, top=args.top)
    return 0 if out or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
