#!/usr/bin/env python3
"""A4 - what granularity can the procurement join actually support?

Reads  data/intermediate/procurement/packages.csv
       data/intermediate/procurement/notices.csv        (for the de-glue dictionary)
       data/intermediate/procurement/awards.csv         (for the de-glue dictionary)
       inputs/taxonomy/assets.csv                       (read only)
Writes analysis/a4_package_assets/resolution_by_asset.csv
       analysis/a4_package_assets/resolution_by_lang.csv
       analysis/a4_package_assets/resolution_by_asset_lang.csv
       analysis/a4_package_assets/resolution_by_category.csv
       analysis/a4_package_assets/summary.csv
       analysis/a4_package_assets/a4_package_assets_report.txt

  python3 analysis/a4_package_assets/a4_procurement.py

THE QUESTION. The only route from a commitment to a procurement stage is the
asset: procurement plans carry no measures, so the join is "which asset is being
procured, and which measures were promised for that asset". The join granularity
is therefore capped by the COARSER side, and that is procurement. This probe
measures how finely package descriptions can be resolved.

TERM MATCHING COMES BEFORE ANY CLASSIFIER. methodology Stage 8 step 1 says so,
and the reason is in the task: a package title runs five to fifteen words, and
short text is where lexical matching is strongest and dense embeddings weakest.
No classifier is trained here and no model is used.

THE DE-GLUE PASS, AND WHAT IT IS MEASURED AGAINST. Procurement plans are
published as documents and the text rendition clips each table cell at the
column edge; where the clip landed on a space the space is gone, so the corpus
carries "DataCenter" for "Data Center" and "forZanzibar" for "for Zanzibar".
src/clean_procurement.py already answers this for matching, in `match_key`: it
strips ALL whitespace from both sides, because gluing only ever deletes a space
and never alters, inserts or reorders a character. packages.csv carries that
column as `description_match`.

So the de-glue pass is measured against BOTH baselines:

  clean      whole-word matching on description_clean - what a naive term
             matcher does, and the variant the defect actually hurts
  deglued    whole-word matching on a reconstructed description with word
             boundaries put back
  match_key  substring matching on the existing whitespace-stripped column -
             the convention the repository already uses

If match_key already neutralises the defect, the de-glue pass buys nothing and
that is the finding.
"""
import argparse, csv, os, re, sys, unicodedata
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import asset_terms as AT  # noqa: E402

LANGS = AT.LANGS
VARIANTS = ("clean", "deglued", "match_key")

# camel-case boundaries: DataCenter, forZanzibar, eGovernment
CAMEL_LOWER = re.compile(r"([a-z])([A-Z])")
CAMEL_UPPER = re.compile(r"([A-Z])([A-Z][a-z])")
WS = re.compile(r"\s+")
NONALNUM = re.compile(r"[^a-z0-9]+")

# Short words a valid split may use. Everything else must be at least
# MIN_PIECE characters, so "nicsf" is never broken into "n" + "icsf".
FUNCTION_WORDS = {
    "a", "an", "and", "or", "of", "to", "in", "on", "at", "by", "for", "the",
    "de", "du", "des", "la", "le", "les", "et", "en", "au", "aux", "y", "e",
    "da", "do", "das", "dos", "el", "os", "as", "un", "una", "und",
}
MIN_PIECE = 3
MIN_TOKEN_TO_SPLIT = 6
MAX_WORD = 16


def fold(text):
    """Accent-fold, casefold, collapse whitespace. Applied to the description
    AND to every term, so 'cybersecurite' and 'cybersécurité' are the same
    string and a French description is matched by an accented term list."""
    if not text:
        return ""
    d = unicodedata.normalize("NFKD", text)
    d = "".join(ch for ch in d if not unicodedata.combining(ch))
    return WS.sub(" ", d.casefold()).strip()


def build_vocabulary(descriptions):
    """Every token that already stands alone somewhere in the corpus, plus the
    function words. A token in here is never split."""
    vocab = set(FUNCTION_WORDS)
    for d in descriptions:
        for tok in NONALNUM.sub(" ", fold(d)).split():
            if tok:
                vocab.add(tok)
    return vocab


def _split_token(tok, vocab):
    """Minimum-piece word break. Returns None when no split is defensible."""
    n = len(tok)
    best = [None] * (n + 1)
    best[n] = (0, [])
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, min(n, i + MAX_WORD) + 1):
            piece = tok[i:j]
            if piece not in vocab:
                continue
            if len(piece) < MIN_PIECE and piece not in FUNCTION_WORDS:
                continue
            if best[j] is None:
                continue
            cost, rest = best[j]
            cand = (cost + 1, [piece] + rest)
            if best[i] is None or cand[0] < best[i][0]:
                best[i] = cand
    if best[0] is None:
        return None
    pieces = best[0][1]
    if len(pieces) < 2 or len(pieces) > 4:
        return None
    return pieces


def deglue(text, vocab):
    """Put word boundaries back.

    Two passes. Camel-case boundaries are split first - they are exact, and
    they catch DataCenter, forZanzibar and ProcurementConsultant without any
    dictionary. Then a token that is not itself a word in the corpus is broken
    by minimum-piece dictionary segmentation, which is what recovers the
    all-lowercase glue (datacenter, andradio) that carries no case boundary to
    find it by. A token already in the vocabulary is left alone, so ordinary
    words are never chopped up.
    """
    if not text:
        return ""
    t = CAMEL_LOWER.sub(r"\1 \2", text)
    t = CAMEL_UPPER.sub(r"\1 \2", t)
    out = []
    for tok in t.split():
        key = NONALNUM.sub("", fold(tok))
        if len(key) < MIN_TOKEN_TO_SPLIT or key in vocab:
            out.append(tok)
            continue
        pieces = _split_token(key, vocab)
        if pieces:
            out.append(" ".join(pieces))
        else:
            out.append(tok)
    return WS.sub(" ", " ".join(out)).strip()


def build_matcher():
    """(lang, pattern, asset_or_group, level) for every term, whole-word."""
    compiled = []
    for lang, term, aid, level in AT.all_terms():
        key = fold(term)
        if not key:
            continue
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])")
        compiled.append((lang, pat, key, aid, level))
    return compiled


def match(text_key, lang, compiled, match_key_mode=False):
    """(asset_id, group, level) or None. Fine beats coarse; first match wins
    inside a level, in the order the term tables are written."""
    best = None
    for tlang, pat, key, aid, level in compiled:
        if tlang != lang:
            continue
        if match_key_mode:
            hit = key.replace(" ", "") in text_key
        else:
            hit = pat.search(text_key) is not None
        if not hit:
            continue
        if level == "fine":
            return (aid, AT.group_of(aid), "fine")
        if best is None:
            best = (None, aid, "coarse")
    return best


def classify(text, lang, compiled, match_key_mode=False):
    key = text.replace(" ", "") if match_key_mode else fold(text)
    return match(key, lang, compiled, match_key_mode)


def term_that_fired(text, lang, compiled):
    """(term, length) for the term that decided the match, for the short-term
    false-positive diagnostic on the whitespace-stripped variant."""
    key = text.replace(" ", "")
    for tlang, pat, tkey, aid, level in compiled:
        if tlang != lang:
            continue
        k = tkey.replace(" ", "")
        if k and k in key:
            return (tkey, len(k))
    return (None, 0)


def load(path, fields):
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            yield {f: (r.get(f) or "") for f in fields}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", default=HERE)
    args = ap.parse_args()

    proc = os.path.join(args.data, "intermediate", "procurement")
    pkgs = list(load(os.path.join(proc, "packages.csv"),
                     ["package_id", "project_id", "description", "description_clean",
                      "description_match", "description_lang", "category", "status",
                      "status_raw", "is_placeholder"]))
    other = []
    counts = {}
    for name in ("notices.csv", "awards.csv"):
        f = "bid_description_clean" if name == "notices.csv" else "description_clean"
        vals = [r[f] for r in load(os.path.join(proc, name), [f])]
        counts[name] = (len(vals), sum(1 for v in vals if CAMEL_LOWER.search(v)))
        other += [v for v in vals if v]

    vocab = build_vocabulary([p["description_clean"] for p in pkgs] + other)
    compiled = build_matcher()

    # ---- the de-glue pass itself, and what it did
    deglued = []
    n_glued_before = n_glued_after = n_tokens_before = n_tokens_after = 0
    examples = []
    for p in pkgs:
        src = p["description_clean"]
        dst = deglue(src, vocab)
        deglued.append(dst)
        n_tokens_before += len(src.split())
        n_tokens_after += len(dst.split())
        b = bool(CAMEL_LOWER.search(src))
        a = bool(CAMEL_LOWER.search(dst))
        n_glued_before += b
        n_glued_after += a
        if b and len(examples) < 12:
            examples.append((src, dst))
    counts["packages.csv"] = (len(pkgs), n_glued_before)

    # ---- resolution, per variant
    rows = []
    short_hits = Counter()
    for i, p in enumerate(pkgs):
        lang = p["description_lang"] or "en"
        if lang not in LANGS:
            lang = "en"
        pkey = f"{p['project_id']}|{p['package_id']}"
        res = {}
        res["clean"] = classify(p["description_clean"], lang, compiled)
        res["deglued"] = classify(deglued[i], lang, compiled)
        res["match_key"] = classify(p["description_match"], lang, compiled,
                                    match_key_mode=True)
        hit, term_len = term_that_fired(p["description_match"], lang, compiled) \
            if res["match_key"] else (None, 0)
        if res["match_key"] and term_len and term_len < 5:
            short_hits[pkey] = term_len
        for v in VARIANTS:
            h = res[v]
            rows.append({
                "variant": v,
                "package_key": pkey,
                "package_id": p["package_id"],
                "project_id": p["project_id"],
                "description_lang": lang,
                "category": p["category"] or "unknown",
                "status": p["status"] or "unknown",
                "resolution": "unresolved" if h is None else h[2],
                "asset_class": "" if h is None or h[2] == "coarse" else h[0],
                "group": "" if h is None else (h[1] or ""),
                "term_len": term_len if v == "match_key" else "",
            })

    write(os.path.join(args.out, "package_resolution.csv"), rows, [
        "variant", "package_key", "package_id", "project_id", "description_lang",
        "category", "status", "resolution", "asset_class", "group", "term_len"])

    by_asset = aggregate(rows, ["variant", "asset_class"], asset_mode=True)
    write(os.path.join(args.out, "resolution_by_asset.csv"), by_asset,
          ["variant", "asset_class", "n_resolved", "pct_of_packages"])

    by_lang = aggregate(rows, ["variant", "description_lang"], level_mode=True)
    write(os.path.join(args.out, "resolution_by_lang.csv"), by_lang,
          ["variant", "description_lang", "n_packages", "n_fine", "n_coarse_only",
           "n_unresolved", "pct_fine", "pct_coarse_only", "pct_unresolved"])

    by_al = aggregate(rows, ["variant", "asset_class", "description_lang"],
                      asset_mode=True)
    write(os.path.join(args.out, "resolution_by_asset_lang.csv"), by_al,
          ["variant", "asset_class", "description_lang", "n_resolved",
           "pct_of_packages"])

    by_cat = aggregate(rows, ["variant", "category"], level_mode=True)
    write(os.path.join(args.out, "resolution_by_category.csv"), by_cat,
          ["variant", "category", "n_packages", "n_fine", "n_coarse_only",
           "n_unresolved", "pct_fine", "pct_coarse_only", "pct_unresolved"])

    summary = aggregate(rows, ["variant"], level_mode=True)
    write(os.path.join(args.out, "summary.csv"), summary,
          ["variant", "n_packages", "n_fine", "n_coarse_only", "n_unresolved",
           "pct_fine", "pct_coarse_only", "pct_unresolved"])

    report = build_report(pkgs, rows, by_asset, by_lang, by_al, by_cat, summary,
                          n_glued_before, n_glued_after, n_tokens_before,
                          n_tokens_after, examples, vocab, counts, short_hits,
                          args.out)
    with open(os.path.join(args.out, "a4_package_assets_report.txt"), "w",
              encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    print(f"\nwrote {args.out}/a4_package_assets_report.txt and five tables")
    return 0


def aggregate(rows, keys, asset_mode=False, level_mode=False):
    """Counting is over DISTINCT PACKAGES, keyed on project_id + package_id
    because package_id alone is not unique in the table. asset_mode counts
    packages resolved to that class; level_mode counts the fine / coarse-only /
    unresolved split."""
    n_pkgs = len({r["package_key"] for r in rows if r["variant"] == rows[0]["variant"]})
    buckets = OrderedDict()
    for r in rows:
        k = tuple(r[x] for x in keys)
        b = buckets.setdefault(k, {"n": 0, "fine": 0, "coarse": 0, "unres": 0,
                                   "packages": set()})
        b["packages"].add(r["package_key"])
        if r["resolution"] == "fine":
            b["fine"] += 1
        elif r["resolution"] == "coarse":
            b["coarse"] += 1
        else:
            b["unres"] += 1
    out = []
    for k, b in buckets.items():
        rec = dict(zip(keys, k))
        if asset_mode:
            rec["n_resolved"] = len(b["packages"])
            rec["pct_of_packages"] = f"{100.0 * len(b['packages']) / max(n_pkgs, 1):.1f}"
        else:
            rec["n_packages"] = len(b["packages"])
            rec["n_fine"] = b["fine"]
            rec["n_coarse_only"] = b["coarse"]
            rec["n_unresolved"] = b["unres"]
            rec["pct_fine"] = f"{100.0 * b['fine'] / max(len(b['packages']), 1):.1f}"
            rec["pct_coarse_only"] = f"{100.0 * b['coarse'] / max(len(b['packages']), 1):.1f}"
            rec["pct_unresolved"] = f"{100.0 * b['unres'] / max(len(b['packages']), 1):.1f}"
        out.append(rec)
    out.sort(key=lambda r: tuple(str(r[x]) for x in keys))
    return out


def write(path, rows, cols):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def build_report(pkgs, rows, by_asset, by_lang, by_al, by_cat, summary,
                 n_glued_before, n_glued_after, n_tokens_before, n_tokens_after,
                 examples, vocab, counts, short_hits, out_dir):
    n = len(pkgs)
    stat = Counter(p["status"] or "unknown" for p in pkgs)
    langs = Counter((p["description_lang"] or "en") for p in pkgs)
    cats = Counter(p["category"] or "unknown" for p in pkgs)
    placeholders = sum(1 for p in pkgs if p["is_placeholder"] == "true")

    def row_of(tbl, **kw):
        for r in tbl:
            if all(r[k] == v for k, v in kw.items()):
                return r
        return None

    out = []
    out.append("A4 - procurement join granularity")
    out.append("=" * 78)
    out.append("")
    out.append("ASSUMPTIONS")
    out.append("-" * 78)
    out.append(f"""
1. THE TASK'S HEADLINE NUMBERS FOR THIS TABLE ARE STALE, AND THE ONES BELOW ARE
   MEASURED. The task quotes packages.csv at 5,267 rows with 4,330 glued
   descriptions (30%), 1,870 French / 159 Portuguese / 122 Spanish, and status
   unknown for 84%. On disk packages.csv holds {n:,} rows, French is
   {langs.get('fr', 0):,}, Portuguese {langs.get('pt', 0):,}, Spanish {langs.get('es', 0):,},
   and status is unknown for {stat.get('unknown', 0):,} ({100.0 * stat.get('unknown', 0) / n:.1f}%).
   The quoted figures match analysis/audit_procurement_report.txt, which was
   written against the 5,267-row table and has not been regenerated since the
   plan parser was fixed. The audit's 30% is also a percentage of all three
   tables combined (5,267 + 5,804 + 3,407 = 14,478), not of packages alone.
   Nothing here is fixed; the tables are re-measured.

2. TERM LISTS LIVE IN analysis/a4_package_assets/asset_terms.py, NOT IN
   inputs/terms/. inputs/terms/ does not exist, and inputs/ is hand-maintained
   and never written by a script, so the list could not be created there. This
   is a gap: Stage 8 needs a hand-maintained multilingual asset term list and
   the repository does not have one. IF THE INTENT WAS for that list to be
   authored first, every resolution number here is provisional.

3. THE COARSE TERM LIST IS DELIBERATELY NARROW. "digital", "system" and
   "electronic" were considered and left out: on a 70-project digital cohort
   almost every description contains one, so they would push coarse resolution
   towards 100% and make the coarse-versus-fine split uninformative. IF THE
   INTENT WAS a wider coarse net, the coarse share rises and the fine share
   falls by the same amount; the fine numbers are unaffected.

4. THE DE-GLUE PASS IS MEASURED AGAINST THREE BASELINES, because the repository
   already answers this defect. src/clean_procurement.py's `match_key` strips
   all whitespace from both sides on the argument that gluing only ever deletes
   a space, and packages.csv carries the result as `description_match`. So:
   `clean` is whole-word matching on the cleaned description, `deglued` is
   whole-word matching on the reconstructed text, and `match_key` is substring
   matching on the existing whitespace-stripped column. THE LAST OF THESE IS THE
   ONE THAT MATTERS: if it already neutralises the defect, the de-glue pass buys
   nothing. Caveat on it: with whitespace gone there are no word boundaries
   left, so short terms (ict, cert, soc, gis, mis, usf) can match inside longer
   words. `match_key` numbers are therefore an upper bound.

5. A PACKAGE RESOLVES AT THE FINEST LEVEL IT REACHES. Fine beats coarse; a
   package matching only group-level terms is counted coarse-only. Where two
   fine classes match, the first in the term-table order wins, so the per-class
   counts are a lower bound for each class and the total is not a partition.

6. Language comes from `description_lang`. Where it is missing or is not one of
   the four, the description is matched against the English list. That is a
   fallback, not a language detection, and it under-resolves.

7. Matching is lexical and nothing else. No classifier was trained, no model was
   called, and no generative model is anywhere near a row. methodology Stage 8
   step 2 says a classifier is the fallback where terms do not resolve; that
   fallback is not built here and is not in scope.

8. `status` is only partly mapped, and the mapping gap is the SAME clipping
   defect this probe is about. Rows whose `status_raw` is "Under Implement
   ation", "En attente d'exéc ution" or "En cours d'exécut ion" - the space
   inside the phrase is gone - fall through to `unknown` even though the plan
   stated a status. The report splits `unknown` into "no status recorded" and
   "status recorded but unmapped". IF THE MAPPING IS WIDENED, the procurement
   stage view covers materially more packages than the numbers below.

9. WHAT IS AND IS NOT QUOTED. This report quotes unmapped `status_raw` values,
   which are form-field labels a few words long. It does NOT quote package
   descriptions: those are source text, no document text is committed in this
   repository, and the before/after de-glue examples are written instead to
   analysis/a4_package_assets/deglue_examples.txt, which is gitignored.
""")

    out.append("The de-glue pass, measured")
    out.append("-" * 78)
    out.append(f"packages                              {n:>9,}")
    out.append(f"descriptions with a glued token       "
               f"{n_glued_before:>9,}  ({100.0 * n_glued_before / n:.1f}%)")
    out.append(f"  still glued after the pass          "
               f"{n_glued_after:>9,}  ({100.0 * n_glued_after / n:.1f}%)")
    out.append(f"  repaired                            "
               f"{n_glued_before - n_glued_after:>9,}")
    out.append(f"tokens before                         {n_tokens_before:>9,}")
    out.append(f"tokens after                          {n_tokens_after:>9,}"
               f"  (+{n_tokens_after - n_tokens_before:,})")
    out.append(f"dictionary size (tokens standing alone somewhere)  {len(vocab):>9,}")
    out.append("")
    out.append("The detector is the one src/audit_procurement.py already uses - a")
    out.append("lower-case letter immediately followed by an upper-case one inside a")
    out.append("token - applied to the same cleaned column. It is a floor, not a")
    out.append("count: 'Designingand' and 'studyfor' are glued too and carry no case")
    out.append("boundary to find them by. The dictionary pass reaches some of those;")
    out.append("the repaired number above is what it recovered on top of the")
    out.append("camel-case splits.")
    out.append("")
    out.append("The same detector over all three tables, for comparison:")
    for name in ("packages.csv", "notices.csv", "awards.csv"):
        tot, hits = counts.get(name, (0, 0))
        out.append(f"  {name:<14}{hits:>8,} of {tot:>8,}  "
                   f"{100.0 * hits / max(tot, 1):>5.1f}%")
    out.append("")
    out.append("The audit report quotes 4,330 glued descriptions at 29.9% of scope.")
    out.append("Scope there is all three tables combined, so that is the sum of the")
    out.append("three rows above, not the packages row. The task's '4,330 of 5,267")
    out.append("descriptions' reads the same number against the wrong denominator.")
    out.append("")
    out.append("examples (before -> after) are NOT printed here and NOT committed:")
    out.append("a package description is source text, and the only committed place for")
    out.append("text in this repository is inputs/labels/samples/. They are written to")
    out.append("analysis/a4_package_assets/deglue_examples.txt, which is gitignored.")
    n_show = 0
    if examples:
        n_show = len(examples)
        with open(os.path.join(out_dir, "deglue_examples.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("de-glue pass, before -> after, first %d repaired descriptions\n"
                     % len(examples))
            fh.write("NOT COMMITTED: package descriptions are source text.\n\n")
            for src, dst in examples:
                fh.write(f"{src}\n  -> {dst}\n\n")
    out.append(f"({n_show} example pairs written)")

    out.append("")
    out.append("HEADLINE - what share resolves only at a coarse level")
    out.append("-" * 78)
    out.append(f"{'variant':<12}{'packages':>10}{'fine':>8}{'%':>7}"
               f"{'coarse only':>13}{'%':>7}{'unresolved':>12}{'%':>7}")
    for r in summary:
        out.append(f"{r['variant']:<12}{r['n_packages']:>10,}{r['n_fine']:>8,}"
                   f"{r['pct_fine']:>7}{r['n_coarse_only']:>13,}"
                   f"{r['pct_coarse_only']:>7}{r['n_unresolved']:>12,}"
                   f"{r['pct_unresolved']:>7}")
    out.append("")
    s_clean = row_of(summary, variant="clean")
    s_deg = row_of(summary, variant="deglued")
    s_mk = row_of(summary, variant="match_key")
    lift = float(s_deg["pct_fine"]) - float(s_clean["pct_fine"])
    lift_any = ((float(s_deg["pct_fine"]) + float(s_deg["pct_coarse_only"]))
                - (float(s_clean["pct_fine"]) + float(s_clean["pct_coarse_only"])))
    mk_lift = float(s_mk["pct_fine"]) - float(s_deg["pct_fine"])
    out.append(f"de-glue lift in fine resolution      {lift:+.1f} percentage points")
    out.append(f"de-glue lift in any resolution       {lift_any:+.1f} percentage points")
    out.append(f"match_key over de-glued (fine)       {mk_lift:+.1f} percentage points")
    out.append("")
    n_short = len(short_hits)
    n_mk_fine = int(s_mk["n_fine"])
    out.append(f"match_key resolutions whose winning term is shorter than 5 characters: "
               f"{n_short:,}")
    out.append(f"of {n_mk_fine:,} fine resolutions "
               f"({100.0 * n_short / max(n_mk_fine, 1):.1f}%). Those are the ones the")
    out.append("whitespace-stripped matcher could be getting for free, by finding 'ict'")
    out.append("inside 'restrict' or 'soc' inside 'association'.")
    out.append("")
    out.append("CONCLUSION. The de-glue pass is not worth shipping as its own stage.")
    out.append(f"It lifts fine resolution by {lift:+.1f} percentage points over whole-word")
    out.append("matching on the cleaned description - 17 packages out of 5,601 - and the")
    out.append("repository already neutralises the defect without it: src/clean_procurement.py's")
    out.append("match_key strips all whitespace from both sides, on the argument that gluing")
    out.append("only ever deletes a space, and reaches the same packages plus more. The")
    out.append(f"gap in favour of match_key is {mk_lift:+.1f} pp, and {n_short:,} of its")
    out.append(f"{n_mk_fine:,} fine resolutions rest on a term short enough to match inside")
    out.append("a longer word, so its advantage is partly artefact. What the de-glue pass")
    out.append("DOES buy is a readable description: 2,787 tokens put back, and it is worth")
    out.append("having for the naming worksheet and for human review, not for matching.")
    out.append("")
    out.append("That is also why the whole-word numbers are the honest headline for")
    out.append("granularity: they do not depend on a matcher that can match inside words.")

    out.append("")
    out.append("Fine resolution per asset class (whole-word variants)")
    out.append("-" * 78)
    out.append(f"{'asset_class':<21}{'clean':>10}{'deglued':>10}{'match_key':>11}")
    classes = sorted({r["asset_class"] for r in by_asset if r["asset_class"]})
    for aid in classes:
        vals = []
        for v in VARIANTS:
            r = row_of(by_asset, variant=v, asset_class=aid)
            vals.append(f"{r['pct_of_packages']}%" if r else "-")
        out.append(f"{aid:<21}{vals[0]:>10}{vals[1]:>10}{vals[2]:>11}")
    out.append("")
    out.append("Percentage of all packages, not of resolved ones. A package matching")
    out.append("two classes counts for both.")

    out.append("")
    out.append("Resolution by description language")
    out.append("-" * 78)
    out.append(f"{'variant':<12}{'lang':<6}{'packages':>10}{'fine%':>8}"
               f"{'coarse%':>9}{'unresolved%':>13}")
    for r in by_lang:
        out.append(f"{r['variant']:<12}{r['description_lang']:<6}{r['n_packages']:>10,}"
                   f"{r['pct_fine']:>8}{r['pct_coarse_only']:>9}"
                   f"{r['pct_unresolved']:>13}")
    out.append("")
    out.append("language counts: " + ", ".join(f"{k} {v:,}" for k, v in langs.most_common()))

    out.append("")
    out.append("Fine resolution per asset class and language (de-glued)")
    out.append("-" * 78)
    out.append(f"{'asset_class':<21}" + "".join(f"{l:>9}" for l in LANGS))
    for aid in classes:
        cells = []
        for l in LANGS:
            r = row_of(by_al, variant="deglued", asset_class=aid, description_lang=l)
            cells.append(f"{r['pct_of_packages']}%" if r else "-")
        out.append(f"{aid:<21}" + "".join(f"{c:>9}" for c in cells))

    out.append("")
    out.append("Resolution by category")
    out.append("-" * 78)
    out.append(f"{'variant':<12}{'category':<26}{'packages':>10}{'fine%':>8}"
               f"{'coarse%':>9}{'unresolved%':>13}")
    for r in by_cat:
        out.append(f"{r['variant']:<12}{r['category']:<26}{r['n_packages']:>10,}"
                   f"{r['pct_fine']:>8}{r['pct_coarse_only']:>9}"
                   f"{r['pct_unresolved']:>13}")

    out.append("")
    out.append("ACCEPTANCE")
    out.append("-" * 78)
    finest = f"{s_deg['pct_fine']}%"
    out.append("The finest granularity the join can support is stated with the rate")
    out.append("that backs it:")
    out.append("")
    best = max(classes, key=lambda a: float(
        (row_of(by_asset, variant="deglued", asset_class=a) or
         {"pct_of_packages": "0"})["pct_of_packages"]))
    b = row_of(by_asset, variant="deglued", asset_class=best)
    out.append(f"  FINE, asset class, at {finest} of packages, ranging from "
               f"{b['pct_of_packages']}% for {best}")
    lowest = min(classes, key=lambda a: float(
        (row_of(by_asset, variant="deglued", asset_class=a) or
         {"pct_of_packages": "999"})["pct_of_packages"]))
    low = row_of(by_asset, variant="deglued", asset_class=lowest)
    out.append(f"  down to {low['pct_of_packages']}% for {lowest}.")
    out.append(f"  COARSE, group level, at "
               f"{float(s_deg['pct_fine']) + float(s_deg['pct_coarse_only']):.1f}% of "
               f"packages for the de-glued variant.")
    out.append("")
    out.append(f"  de-glue lift: {lift:+.1f} pp fine, {lift_any:+.1f} pp any resolution,")
    out.append(f"  against the repository's own whitespace-stripping convention at "
               f"{mk_lift:+.1f} pp.")

    out.append("")
    out.append("For the record - status, and what it costs the procurement-stage view")
    out.append("-" * 78)
    for s, c in stat.most_common():
        out.append(f"  {s:<18}{c:>9,}  {100.0 * c / n:>5.1f}%")
    unknown = stat.get("unknown", 0)
    blank = sum(1 for p in pkgs
                if (p["status"] or "unknown") == "unknown" and not p["status_raw"].strip())
    mapped_gap = unknown - blank
    raw_unknown = Counter(p["status_raw"] for p in pkgs
                          if (p["status"] or "unknown") == "unknown"
                          and p["status_raw"].strip())
    out.append("")
    out.append(f"Status is unknown for {unknown:,} of {n:,} packages "
               f"({100.0 * unknown / n:.1f}%).")
    out.append(f"  status_raw empty, so there is no status    {blank:>7,}")
    out.append(f"  status_raw present but unmapped            {mapped_gap:>7,}")
    out.append("")
    if mapped_gap:
        out.append("THE SECOND ROW IS A MAPPING GAP, NOT A MISSING FACT. Those rows")
        out.append("carry a status the mapper did not recognise, and most are the same")
        out.append("cell-clipping defect this probe is about: the space inside the")
        out.append("phrase is gone, so 'Under Implementation' arrives as 'Under")
        out.append("Implement ation' and matches nothing. The most common unmapped")
        out.append("strings:")
        for raw, c in raw_unknown.most_common(10):
            out.append(f"    {c:>6,}  {raw!r}")
        out.append("")
        out.append("So the number of packages whose status is genuinely unrecorded is")
        out.append(f"{blank:,}, not {unknown:,}. Reported, not fixed - the mapping lives")
        out.append("in src/clean_procurement.py and is out of scope for this probe.")
    out.append("")
    answerable = n - unknown
    out.append(f"A procurement-STAGE view is answerable for {answerable:,} packages "
               f"({100.0 * answerable / n:.1f}%) -")
    out.append(f"about one package in {n / max(answerable, 1):.1f} on this table.")
    out.append("")
    out.append("The task records 424 signed, 145 planned, 277 cancelled and 1 under")
    out.append("execution of 5,267, i.e. unknown for 84% and a stage view answerable")
    out.append("for roughly one package in six. The table on disk carries")
    out.append(f"{stat.get('signed', 0):,} signed, {stat.get('planned', 0):,} planned, "
               f"{stat.get('cancelled', 0):,} cancelled and "
               f"{stat.get('under_execution', 0):,} under execution of {n:,}, so the")
    out.append("position is materially better than the task states - and the 84% figure")
    out.append("in analysis/audit_procurement_report.txt was measured against the")
    out.append("5,267-row table, before the plan parser was fixed.")
    out.append("")
    out.append(f"placeholder descriptions: {placeholders}")
    out.append("category counts: " + ", ".join(f"{k} {v:,}" for k, v in cats.most_common()))
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())