#!/usr/bin/env python3
"""How likely each searched unit is to state something the project does
(a commitment) rather than context, for any measure, with nothing graded.

The documents label themselves. A sentence saying "will" or "shall" in a
section that describes the project's components is nearly always an
activity; a sentence in a country or sector context section nearly never
is. A logistic regression on the units' embeddings, trained on those two
groups, scores every unit: p_commitment. Section titles are the only labels.
Checked against 312 sentences a person graded on six measures (two routes
of search, so not a random sample): F1 0.81-0.82, precision 75 per cent,
recall 89 per cent; it was weakest on ID used in shocks, where most
sentences state an outcome.

Each unit also gets a firmness reading from its wording, which needs no
model: 'activity' (the project finances, builds, requires, integrates...),
'outcome' ("will enable", "will help", "will improve": what should follow,
not what is done), or 'none'. It is a column to filter on, not a decision:
on the same 312 sentences, dropping 'outcome' sentences moved precision from
75 to 80 per cent and recall from 89 to 84. Appraisal documents use "would"
for planned works, so "would" is not read as a hedge.

  data/commitment/<version>/scores.csv   unit_id, p_commitment, firmness,
                                         training (positive | negative | '')
  data/commitment/<version>/manifest.json

<version> is a SHA-256 over the sentence index, the matrix, the training
rules and this file's settings: the same index gives the same scores.

  python src/commitment.py   (./wbg candidates and ./wbg measure run it when needed)
"""
import argparse, csv, hashlib, json, os, re, sys, time

import numpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402

# Section titles whose sentences describe what the project does, and those
# that describe the country and sector it works in. Matched at the start of
# the title, case-insensitive.
COMPONENT_SECTIONS = re.compile(
    r"^(project components|detailed project description|project description|"
    r"technical design and component descriptions|description of proposed changes|"
    r"description of additional financing)", re.I)
CONTEXT_SECTIONS = re.compile(
    r"^(country context|sectoral and institutional context|sectoral context|lessons learned|"
    r"strategic context|project strategic context|rationale for (?:world )?bank involvement|"
    r"project status|relevance to higher level objectives|background)", re.I)
FIRM = re.compile(r"\b(?:will|shall)\b", re.I)
PER_CLASS = 3000        # training units drawn from each group
RIDGE = 1.0             # L2 penalty on the summed, class-balanced log loss
MIN_TRAINING = 100      # fewer training units in either group is an error

# Wording. An activity phrase anywhere wins over an outcome phrase.
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
    """'activity', 'outcome' or 'none' (see the module docstring)."""
    if ACTIVITY.search(text):
        return "activity"
    if OUTCOME.search(text):
        return "outcome"
    return "none"


def fit_logistic(X, y, ridge=RIDGE, iterations=25):
    """Weights (w, b) of an L2-regularised logistic regression with each
    class weighted to equal total weight, fitted by Newton's method. numpy
    only (the box carries no scikit-learn); deterministic."""
    X = numpy.asarray(X, dtype="float64")
    y = numpy.asarray(y, dtype="float64")
    n, d = X.shape
    weight = numpy.where(y == 1, n / (2 * y.sum()), n / (2 * (n - y.sum())))
    A = numpy.hstack([X, numpy.ones((n, 1))])
    beta = numpy.zeros(d + 1)
    penalty = numpy.full(d + 1, ridge)
    penalty[-1] = 0.0                      # the intercept is not penalised
    for _ in range(iterations):
        p = 1.0 / (1.0 + numpy.exp(-(A @ beta)))
        grad = A.T @ (weight * (p - y)) + penalty * beta
        H = (A * (weight * p * (1 - p))[:, None]).T @ A + numpy.diag(penalty)
        step = numpy.linalg.solve(H, grad)
        beta -= step
        if numpy.abs(step).max() < 1e-8:
            break
    return beta[:-1], beta[-1]


def predict(X, w, b, block=20_000):
    out = numpy.empty(X.shape[0], dtype="float64")
    for s in range(0, X.shape[0], block):
        z = numpy.asarray(X[s:s + block], dtype="float64") @ w + b
        out[s:s + len(z)] = 1.0 / (1.0 + numpy.exp(-z))
    return out


def _draw(ids, k):
    """Up to k ids, chosen by a hash of the id so the draw is the same on
    every run and barely moves when units are added."""
    return sorted(ids, key=lambda u: hashlib.sha256(u.encode()).hexdigest())[:k]


def training_sets(index, sections, texts):
    """(positive unit ids, negative unit ids) from section titles alone."""
    pos, neg = [], []
    for r in index:
        title = sections.get(r["paragraph_id"], "").strip()
        if COMPONENT_SECTIONS.match(title) and FIRM.search(texts[r["unit_id"]]):
            pos.append(r["unit_id"])
        elif CONTEXT_SECTIONS.match(title):
            neg.append(r["unit_id"])
    return _draw(pos, PER_CLASS), _draw(neg, PER_CLASS)


def _sha_file(path):
    """The file's SHA-256, remembered beside it (paths.file_sha256)."""
    from paths import file_sha256
    return file_sha256(path)


def version(data):
    h = hashlib.sha256()
    for p in (where(data, "embeddings", "sentence_index.csv"),
              where(data, "embeddings", "sentence_emb.npy"), os.path.abspath(__file__)):
        h.update(_sha_file(p).encode())
    return h.hexdigest()[:12]


def run_folder(data):
    return where(data, "commitment", version(data))


def unit_texts(data, index):
    """{unit_id: text as embedded} (a list item with its lead-in)."""
    import units
    every = {u["unit_id"]: u for u in units.search_units(data)}
    cache, out = {}, {}
    for r in index:
        u = every[r["unit_id"]]
        d = u["doc_id"]
        if d not in cache:
            with open(where(data, "text", f"{d}.txt"), encoding="utf-8") as fh:
                cache[d] = fh.read()
        body = cache[d][int(u["char_start"]):int(u["char_end"])]
        if u["lead_in_start"]:
            body = cache[d][int(u["lead_in_start"]):int(u["lead_in_end"])] + "\n" + body
        out[r["unit_id"]] = body
    return out


def score(data, log=print):
    """Score every searched unit; returns the run folder."""
    idx_path = where(data, "embeddings", "sentence_index.csv")
    npy_path = where(data, "embeddings", "sentence_emb.npy")
    if not (os.path.exists(idx_path) and os.path.exists(npy_path)):
        raise SystemExit("commitment: no sentence embeddings yet - run ./wbg search first")
    out = run_folder(data)
    if os.path.exists(os.path.join(out, "manifest.json")):
        log(f"commitment: {out} already scored")
        return out
    with open(idx_path, newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    matrix = numpy.load(npy_path, mmap_mode="r")
    if matrix.shape[0] != len(index):
        raise SystemExit("commitment: sentence_emb.npy and sentence_index.csv disagree on rows")
    with open(where(data, "appraisal", "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        sections = {p["paragraph_id"]: p.get("section_title", "") for p in csv.DictReader(fh)}
    texts = unit_texts(data, index)
    pos, neg = training_sets(index, sections, texts)
    if len(pos) < MIN_TRAINING or len(neg) < MIN_TRAINING:
        raise SystemExit(f"commitment: too few training units ({len(pos)} component, "
                         f"{len(neg)} context): the section titles did not match")
    row = {r["unit_id"]: i for i, r in enumerate(index)}
    X = numpy.asarray(matrix[[row[u] for u in pos + neg]], dtype="float64")
    y = numpy.r_[numpy.ones(len(pos)), numpy.zeros(len(neg))]
    w, b = fit_logistic(X, y)
    p = predict(matrix, w, b)
    role = dict.fromkeys(pos, "positive")
    role.update(dict.fromkeys(neg, "negative"))
    part = out + ".part"
    os.makedirs(part, exist_ok=True)
    with open(os.path.join(part, "scores.csv"), "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh, lineterminator="\n")
        wr.writerow(["unit_id", "p_commitment", "firmness", "training"])
        for i, r in enumerate(index):
            u = r["unit_id"]
            wr.writerow([u, f"{p[i]:.4f}", firmness(texts[u]), role.get(u, "")])
    with open(os.path.join(part, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": os.path.basename(out), "units": len(index),
                   "positives": len(pos), "negatives": len(neg), "ridge": RIDGE,
                   "index_sha256": _sha_file(idx_path),
                   "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(part, out)
    log(f"commitment: {len(index):,} units scored from {len(pos):,} component and "
        f"{len(neg):,} context sentences -> {out}")
    return out


def current_scores(data):
    """{unit_id: (p_commitment, firmness)} for the current index, or {}."""
    path = os.path.join(run_folder(data), "scores.csv")
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["unit_id"]: (float(r["p_commitment"]), r["firmness"])
                for r in csv.DictReader(fh)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    score(args.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
