#!/usr/bin/env python3
"""V6: do borrower package references actually join plans to awards?

Reads  data/intermediate/procurement/packages.csv
       data/intermediate/procurement/awards.csv
Writes data/intermediate/procurement/v6_link_test.txt

Methodology section 5, Stage 8 step 4 asks whether the borrower reference can
link a plan package to a signed award, and the default there is 'reference,
normalised'. This measures it on the cohort rather than assuming it, because the
answer decides whether Stage 9's join has to fall back to fuzzy matching on
description, amount and date.

Three rates are reported, in this order:

  exact        the published strings are identical
  normalised   identical after the step-5 normalisation (case, whitespace,
               separators) - the same function the plan-to-plan diff keys on
  project      normalised AND in the same project, which is the join that would
               actually be used

Then the residue: what the unmatched references look like, paired with their
closest counterpart by similarity, so it is visible whether normalisation is
under-reaching or whether the two sides simply number packages differently.

Read-only: this file writes one report and nothing else.
"""
import argparse, csv, difflib, os, re, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clean_procurement import norm_ref                     # noqa: E402

PROC_DIR = os.path.join("intermediate", "procurement")

# Climate-resilience language, used only for the note the card asks for: a prior
# crosstabulation found none of it in the most exposed package classes. This is a
# lexical probe, not a classifier - it is here to say whether the finding holds,
# not to score anything.
#
# These are the eight terms the card names, verbatim: flood, cyclone, typhoon,
# storm, seismic, climate, resilien*, adaptation. "seismic" was missing from the
# earlier set, which carried "earthquake" instead - they are not the same probe
# and the difference matters in a corpus of this kind. "resilien*" is a prefix
# match, which "resilien" as a substring already is.
CLIMATE_TERMS = ("flood", "cyclone", "typhoon", "storm", "seismic", "climate",
                 "resilien", "adaptation")
# The wider lexical set the first run of this test used, kept so the two are
# comparable. Reported on its own line and never as the headline.
CLIMATE_TERMS_WIDE = CLIMATE_TERMS + ("drought", "disaster", "hazard", "sea level",
                                      "erosion", "earthquake", "extreme weather",
                                      "early warning")

# The two exposed classes named in the card, approximated lexically because no
# asset term list is committed yet.
CLASS_TERMS = {
    "fiber": ("fiber", "fibre", "optical cable", "optic cable", "backhaul",
              "duct", "conduit"),
    "tower": ("tower", "mast", "base station", "base transceiver", "bts",
              "antenna", "radio access"),
}


def load(data, table):
    path = os.path.join(data, PROC_DIR, f"{table}.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def near_pairs(unmatched, candidates, limit=20, floor=0.55):
    """Pair each unmatched reference with its closest counterpart, so the residue
    can be read rather than only counted. Ratio is difflib's, on the normalised
    forms, and the pairs come back best-first.

    One SequenceMatcher is reused and each candidate is skipped on
    real_quick_ratio, which is an upper bound on ratio - it cannot change the
    answer, only the work. At full scale this is ~7M comparisons per run and
    rebuilding the matcher for each of them is most of the cost.
    """
    out = []
    sm = difflib.SequenceMatcher()
    for ref, project in unmatched:
        sm.set_seq1(ref)
        best, score = "", 0.0
        for cand, cproject in candidates:
            if project and cproject and project != cproject:
                continue
            sm.set_seq2(cand)
            if sm.real_quick_ratio() <= score:
                continue
            r = sm.ratio()
            if r > score:
                best, score = cand, r
        if best and score >= floor:
            out.append((score, ref, project, best))
    out.sort(reverse=True)
    return out[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--examples", type=int, default=20)
    args = ap.parse_args()

    packages = load(args.data, "packages")
    awards = load(args.data, "awards")
    notices = load(args.data, "notices")

    pkg = [(r["borrower_ref"].strip(), r["borrower_ref_norm"].strip(),
            r["project_id"], r["description_clean"], r["status"])
           for r in packages]
    awd = [(r["borrower_ref"].strip(), r["borrower_ref_norm"].strip(),
            r["project_id"], r["description_clean"], r["contract_id"])
           for r in awards]

    p_raw = [x for x in pkg if x[0]]
    a_raw = [x for x in awd if x[0]]
    p_norm = [x for x in pkg if x[1]]
    a_norm = [x for x in awd if x[1]]

    out = []
    out.append("V6 - do borrower package references join plans to awards?")
    out.append("=" * 74)
    out.append("")
    out.append(f"packages (latest plan version each): {len(packages):,}")
    out.append(f"  with a non-null borrower_ref:      {len(p_raw):,} "
               f"({100.0 * len(p_raw) / max(len(packages), 1):.1f}%)")
    out.append(f"  with a normalised reference:       {len(p_norm):,} "
               f"({100.0 * len(p_norm) / max(len(packages), 1):.1f}%)")
    out.append(f"awards:                              {len(awards):,}")
    out.append(f"  with a non-null borrower_ref:      {len(a_raw):,} "
               f"({100.0 * len(a_raw) / max(len(awards), 1):.1f}%)")
    out.append(f"  with a normalised reference:       {len(a_norm):,} "
               f"({100.0 * len(a_norm) / max(len(awards), 1):.1f}%)")
    out.append("")

    exact_set = {x[0] for x in p_raw}
    exact_hits = [(x[0], x[2]) for x in a_raw if x[0] in exact_set]
    norm_set = {x[1] for x in p_norm}
    norm_index = defaultdict(set)
    for x in p_norm:
        norm_index[x[1]].add(x[2])
    norm_hits = [x for x in a_norm if x[1] in norm_set]
    proj_hits = [x for x in a_norm
                 if x[1] in norm_index and (not x[2] or x[2] in norm_index[x[1]])]

    def rate(n, total):
        return f"{n:,} of {total:,} ({100.0 * n / max(total, 1):.1f}%)"

    out.append("match rates, awards matched to a plan package")
    out.append("-" * 74)
    out.append(f"  exact, published strings        {rate(len(exact_hits), len(a_raw))}")
    out.append(f"  after normalisation             {rate(len(norm_hits), len(a_norm))}")
    out.append(f"  normalised AND same project     {rate(len(proj_hits), len(a_norm))}")
    out.append("")
    out.append(f"  distinct award references with no plan counterpart "
               f"(normalised): {len({x[1] for x in a_norm}) - len({x[1] for x in norm_hits}):,}")
    p_hit_norms = {x[1] for x in proj_hits}
    out.append(f"  distinct package references with no award "
               f"(normalised): {len({x[1] for x in p_norm}) - len(p_hit_norms):,}")

    # residue
    matched_norms = {x[1] for x in norm_hits}
    residue = [(x[1], x[2], x[3], x[0]) for x in a_norm if x[1] not in matched_norms]
    uniq = {}
    for nref, project, desc, raw in residue:
        uniq.setdefault(nref, (project, desc, raw))
    cands = [(x[1], x[2]) for x in p_norm]
    pairs = near_pairs([(n, u[0]) for n, u in uniq.items()], cands, args.examples)
    out.append("")
    out.append(f"residue: {len(uniq):,} distinct award references with no exact or")
    out.append(f"normalised package counterpart. {len(pairs)} of them, with their closest")
    out.append("package reference, best-first:")
    out.append("-" * 74)
    for score, ref, project, best in pairs:
        proj, desc, raw = uniq[ref]
        out.append(f"  {score:.2f}  award {raw!r} -> plan {best!r}   [{proj}]")
        out.append(f"        award: {desc[:110]}")
    if not pairs:
        out.append("  none above the 0.55 similarity floor: the two sides do not")
        out.append("  nearly match on the reference at all.")

    # what a normalisation miss would look like, if any
    norm_only = [x for x in a_raw if x[0] in exact_set or True]
    missed = [(x[0], x[1]) for x in a_raw if x[0] not in exact_set and x[1] in norm_set]
    out.append("")
    out.append(f"references recovered by normalisation alone: {len(missed):,}")
    for raw, nrm in missed[:args.examples]:
        out.append(f"  {raw!r} -> {nrm!r}")

    # the climate-language note
    out.append("")
    out.append("climate language in procurement text (lexical probe, no classifier)")
    out.append("-" * 74)
    out.append(f"  terms, as named in the card: {', '.join(CLIMATE_TERMS)}")
    for label, rows, field in (("packages", packages, "description_clean"),
                               ("notices", notices, "bid_description_clean"),
                               ("awards", awards, "description_clean")):
        n = sum(1 for r in rows
                if any(t in (r.get(field) or "").lower() for t in CLIMATE_TERMS))
        out.append(f"  {label:<10} {n:>6,} of {len(rows):>6,} carry any of them "
                   f"({100.0 * n / max(len(rows), 1):.2f}%)")
    wide = sum(1 for r in packages
               if any(t in (r["description_clean"] or "").lower()
                      for t in CLIMATE_TERMS_WIDE))
    out.append(f"  packages carrying any of the wider {len(CLIMATE_TERMS_WIDE)}-term "
               f"set the first run used: {wide:,} of {len(packages):,} "
               f"({100.0 * wide / max(len(packages), 1):.2f}%)")
    for name, terms in CLASS_TERMS.items():
        cls = [r for r in packages
               if any(t in (r["description_clean"] or "").lower() for t in terms)]
        n = sum(1 for r in cls
                if any(t in (r["description_clean"] or "").lower() for t in CLIMATE_TERMS))
        out.append(f"  {name}-classified packages: {len(cls):,}; carrying climate "
                   f"language: {n:,}")

    # where the references actually differ, structurally
    out.append("")
    out.append("what the residue actually is")
    out.append("-" * 74)
    odds = []
    for r in awards:
        raw = r["borrower_ref"].strip()
        for c in raw:
            if not c.isalnum() and c not in "-":
                odds.append((raw, f"U+{ord(c):04X}"))
                break
    out.append(f"  award references carrying a character that is not a hyphen "
               f"where a separator belongs: {len(odds):,} of {len(a_raw):,}")
    for raw, cp in odds[:10]:
        out.append(f"    {raw!r}  {cp}")

    def strip_suffix(n):
        """A plan package often carries a trailing package letter - EDGE-IC24C -
        that the award for it does not. Reported as its own rate so it is clear
        whether the gap is formatting or a different key."""
        return n[:-1] if len(n) > 3 and n[-1].isalpha() and n[-2].isdigit() else n

    p_strip = defaultdict(set)
    for x in p_norm:
        p_strip[strip_suffix(x[1])].add(x[2])
    strip_hits = [x for x in a_norm
                  if x[1] in p_strip and (not x[2] or x[2] in p_strip[x[1]])]
    out.append(f"  match rate, normalised AND ignoring a trailing package letter "
               f"on the plan side: {rate(len(strip_hits), len(a_norm))}")

    # A third level, measured rather than assumed, because the residue above is
    # dominated by one borrower writing 'CSINDV' where the award writes
    # 'CS-INDV'. Stripping every separator recovers those, at the cost of
    # collisions - two different packages mapping to one key. Both numbers are
    # reported so the choice is made on evidence.
    def unsep(n):
        return re.sub(r"[^A-Z0-9]", "", n)

    p_unsep = defaultdict(set)
    for x in p_norm:
        p_unsep[unsep(x[1])].add(x[1])
    unsep_hits = [x for x in a_norm
                  if unsep(x[1]) in p_unsep
                  and (not x[2] or x[2] in {p for n in p_unsep[unsep(x[1])] for p in
                                            norm_index.get(n, set())})]
    collide_p = sum(1 for k, v in p_unsep.items() if len(v) > 1)
    a_unsep = defaultdict(set)
    for x in a_norm:
        a_unsep[unsep(x[1])].add(x[1])
    collide_a = sum(1 for k, v in a_unsep.items() if len(v) > 1)
    # Per project, because the whole-cohort rate confounds two different things:
    # a project whose plan parsed no package row at all cannot match anything,
    # and that is a fetch/parse gap rather than a join failure.
    out.append("")
    out.append("per project: awards, matched, and whether the plan parsed")
    out.append("-" * 74)
    matched_pairs = {(x[1], x[2]) for x in norm_hits}
    proj_pkgs = defaultdict(int)
    for x in p_norm:
        proj_pkgs[x[2]] += 1
    proj_awd = defaultdict(int)
    proj_hit = defaultdict(int)
    for x in a_norm:
        proj_awd[x[2]] += 1
        if (x[1], x[2]) in matched_pairs or x[1] in {p[1] for p in p_norm}:
            proj_hit[x[2]] += 1
    blank = [p for p in sorted(proj_awd) if not proj_pkgs.get(p)]
    out.append(f"  projects with awards but no parsed package: {len(blank)} "
               f"({sum(proj_awd[p] for p in blank):,} awards, all unmatched by "
               f"construction)")
    out.append(f"  projects with both: "
               f"{len([p for p in proj_awd if proj_pkgs.get(p)]):,}")
    out.append(f"  {'project':<12}{'packages':>10}{'awards':>8}{'matched':>9}")
    for p in sorted(proj_awd, key=lambda k: -proj_awd[k])[:25]:
        out.append(f"  {p:<12}{proj_pkgs.get(p, 0):>10}{proj_awd[p]:>8}"
                   f"{proj_hit[p]:>9}")

    # The award reference sometimes carries the PIU's own name in front of the
    # package reference - 'BJ-UCP / PADA-484216-CS-INDV' - so the last
    # reference-shaped token is tried on both sides. This is cheap and it is the
    # only one of the three extra rules that recovers anything at all.
    def last_token(n):
        return n.split(" / ")[-1].strip() or n

    p_last = defaultdict(set)
    for x in p_norm:
        p_last[norm_ref(last_token(x[0]))].add(x[2])
    last_hits = [x for x in a_norm
                 if norm_ref(last_token(x[0])) in p_last
                 and (not x[2] or x[2] in p_last[norm_ref(last_token(x[0]))])]
    out.append("")
    out.append("a fourth rule, for the award side that embeds the PIU name")
    out.append("-" * 74)
    out.append(f"  last reference-shaped token on both sides: "
               f"{rate(len(last_hits), len(a_norm))}")
    out.append("  (packages whose reference is a bare method code - 'CQS-2',")
    out.append("   'CS-QCBS' - have no contract-number counterpart at all and")
    out.append("   are not recovered by any rule on the reference)")

    out.append("")
    out.append("a third level, for the residue that normalisation does not reach")
    out.append("-" * 74)
    out.append(f"  separators stripped, match rate: {rate(len(unsep_hits), len(a_norm))}")
    out.append(f"  distinct keys that then collide - packages: {collide_p:,} of "
               f"{len(p_unsep):,}; awards: {collide_a:,} of {len(a_unsep):,}")

    out.append("")
    out.append("shape of the two reference sets (normalised, by segment count)")
    out.append("-" * 74)
    for label, rows in (("plan", p_norm), ("award", a_norm)):
        segs = Counter(len(x[1].split("-")) for x in rows)
        out.append(f"  {label:<6}" + "  ".join(f"{k}seg:{v}" for k, v in sorted(segs.items())))

    report = "\n".join(out) + "\n"
    path = os.path.join(args.data, PROC_DIR, "v6_link_test.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(report)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
