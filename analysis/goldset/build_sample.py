#!/usr/bin/env python3
"""Build the hand-annotation workbook for the gold set.

WHAT THIS PRODUCES. One xlsx with two sheets. `paragraphs` holds one row per
sampled paragraph with its full cleaned text, its id and a `read` tick.
`rows` holds the annotation table, pre-seeded with blank rows that already
carry the right `paragraph_id`, so the annotator never types an id and the
join back to the corpus cannot silently fail. Column layout is fixed by
analysis/gold-set-spec.md.

THE SAMPLE IS A MINIATURE OF THE CORPUS, NOT THREE EXPERIMENTS. The winning
method runs bank-wide, so the sample it is chosen on has to look like what it
will face. Every prose paragraph falls into exactly one stratum, by section
heading and nothing else:

  climate     climate, adaptation, co-benefit, resilience, disaster or hazard
              in the heading - the co-benefit annexes, where measures are
              enumerated explicitly
  components  component, project description, technical design or annex - where
              the financed activity is described in running prose, and where
              cybersecurity, data centres and digital public infrastructure
              actually live, because no PAD heading names them
  other       everything else - fiduciary, implementation arrangements, results
              frameworks, risks. Mostly empty, and that is the point

Strata are assigned first-match-wins, so they are disjoint. The draw is
over-weighted toward `climate` and `components` because reading a hundred empty
paragraphs wastes a day, so **precision must be estimated stratum-weighted**,
never raw. Every stratum caps paragraphs per project, because a slice of four
consecutive paragraphs from one document is one sample, not four.

THE PROBE IS NOT PART OF THE RANKING. One extra group is selected by keywords
in the paragraph body, to answer a different question: is a method blind to
cybersecurity, data centres or digital public infrastructure? It is enriched by
construction, so it cannot give an unbiased recall figure and is never pooled
into one. It can catch a method that returns nothing there at all, which the
stratified sample alone would leave to chance. Keyword selection is safe here
only because no candidate method is keyword matching; the bias it carries -
toward paragraphs that name the topic outright - is disclosed in
gold-set-spec.md and is conservative, since a method that fails on the explicit
cases will not do better on the implicit ones.

No group is selected by an embedding, a cluster or a classifier.

THE HASH CHECK IS THE POINT, as it is in analysis/discovery/embed_clauses.py.
paragraphs.csv stores offsets, not text. If a clean file moved, the offsets
still resolve and still yield something - just not what was measured. Checking
`text_sha256` turns that into an abort rather than a workbook of text the
annotator reads and the scorer cannot find.

THE TRACKER IS OPTIONAL AND NEVER COMMITTED. Without --tracker the flagged
slice is skipped and the run says so. The file itself is hand-labelling owned
by the task team; .gitignore keeps it out of the repository.
"""
import argparse, csv, hashlib, os, random, re, sys
from collections import Counter, defaultdict

PROSE_BLOCKS = ("narrative", "annex")
NGRAM = 8               # words; ~3 distinct hits is far past coincidence
MIN_NGRAM_HITS = 3
PREVIEW_CHARS = 120

# Her adaptation categories -> a suggested asset and direction. Suggestions the
# annotator overwrites, never a label. Keyed on a lowercase substring of the
# category heading so a reworded heading still lands.
CATEGORY_HINTS = [
    ("resilient telecom",            "telecom network",      "resilience_of_asset"),
    ("resilient data center",        "data centre",          "resilience_of_asset"),
    ("site-specific climate risk",   "data centre",          "resilience_of_asset"),
    ("resilient it equipment",       "IT equipment",         "resilience_of_asset"),
    ("business continuity",          "",                     "resilience_of_asset"),
    ("buildings/labs/tech hubs",     "buildings",            "resilience_of_asset"),
    ("digitization of dpi",          "DPI",                  "digital_for_resilience"),
    ("climate applications",         "climate application",  "digital_for_resilience"),
    ("digital skills",               "",                     "digital_for_resilience"),
    ("devices and internet",         "access/connectivity",  "digital_for_resilience"),
    ("enabling environment",         "",                     ""),
]

# Strata, first match wins, so they are disjoint. The order is the design: a
# climate co-benefit annex is a climate paragraph, not a component one.
STRATA = (
    ("climate", r"climate|adapt|co-?benefit|resilien|disaster|hazard"),
    ("components", r"component|project description|technical design|"
                   r"detailed change|proposed change|annex|results chain"),
    ("other", None),
)

# Selection terms for the blind-spot probe ONLY - never a detector, never part
# of a ranking figure. Two regexes because the acronyms must stay case
# sensitive: a case-insensitive \bcert\b matches "certification" and \bsoc\b
# matches "social" once the boundary is gone.
PROBE_PHRASES = (r"cyber\s*security|cyber-security|security operations cent|"
                 r"incident response|computer emergency response|"
                 r"data\s*cent(er|re)|data hosting|colocation|co-location|"
                 r"server room|disaster recovery|business continuity|"
                 r"digital public infrastructure|digital identity|"
                 r"digital identification|foundational id|interoperability layer|"
                 r"payment switch")
PROBE_ACRONYMS = r"\bCSIRT\b|\bCERT\b|\bCIRT\b|\bSOC\b|\bDPI\b|\bPKI\b"

ROW_COLUMNS = ["paragraph_id", "where", "kind", "quote", "label", "direction",
               "asset", "asset_in_para", "hazard", "stem"]
KINDS = ["measure", "asset", "activity", "context"]
DIRECTIONS = ["resilience_of_asset", "digital_for_resilience"]


def norm_words(text):
    """Lowercase word list with punctuation flattened, for n-gram matching."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def ngrams(words, n=NGRAM):
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def prepared(data_dir, name):
    """prepared/ if the pipeline wrote there, else the flat data dir."""
    p = os.path.join(data_dir, "intermediate", "prepared", name)
    return p if os.path.exists(p) else os.path.join(data_dir, name)


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def col(row, *names):
    """The first of `names` the row carries a value under.

    docs/data-model.md names the columns `block_type`, `section_label` and
    `token_count`; paragraphs.csv on disk (src/clean.py's PARA_COLS) carries
    `block`, `section_title` and `n_tokens` instead. src/segment.py already
    reads either name for the block column, and says why: the schema and the
    table have not been reconciled. A sampler that reads one name only does not
    crash on the other - it finds no prose paragraphs at all and writes an empty
    workbook, which is worse. An absent `in_scope` is read as in scope: the
    table on disk carries no scope flag, and the prose filter is what defines
    the pool (analysis/a2_segmentation/check_segmentation.py assumption 2).
    """
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return ""


def doc_projects(data_dir, paras):
    """doc_id -> project_id, for the project grouping the sample reports on.

    docs/data-model.md names `span_project.csv` as the home of this link, and
    that is the table the fixture writes. The table src/clean.py actually
    produces carries the link on the row instead, as `project_ids`,
    pipe-delimited when one document serves several operations. Prefer the
    modelled table, fall back to the column, and leave the id empty rather than
    aborting: the id is only needed to match the tracker, which is not on this
    box, and losing the run over it would be worse than losing the grouping.
    """
    path = prepared(data_dir, "span_project.csv")
    if os.path.exists(path):
        out = {}
        for r in read_csv(path):
            out.setdefault(r["doc_id"], r["project_id"])
        return out
    out = {}
    for r in paras:
        first = (r.get("project_ids") or "").split("|")[0].strip()
        out.setdefault(r["doc_id"], first)
    return out


def load_corpus(data_dir, log):
    """paragraph rows (prose, in scope) with text resolved and hash-checked."""
    paras = read_csv(prepared(data_dir, "paragraphs.csv"))
    doc_project = doc_projects(data_dir, paras)

    bodies, out, skipped, bad_hash = {}, [], Counter(), []
    for r in paras:
        block = col(r, "block_type", "block")
        if block not in PROSE_BLOCKS:
            skipped["not prose"] += 1
            continue
        if "in_scope" in r and not truthy(r["in_scope"]):
            skipped["out of scope"] += 1
            continue
        doc = r["doc_id"]
        if doc not in bodies:
            path = os.path.join(data_dir, "clean", f"{doc}.txt")
            if not os.path.exists(path):
                skipped["clean file missing"] += 1
                bodies[doc] = None
            else:
                with open(path, encoding="utf-8") as fh:
                    bodies[doc] = fh.read()
        if bodies[doc] is None:
            continue
        text = bodies[doc][int(r["char_start"]):int(r["char_end"])]
        want = r.get("text_sha256") or ""
        if want and hashlib.sha256(text.encode("utf-8")).hexdigest() != want:
            bad_hash.append(r["paragraph_id"])
            continue
        if not text.strip():
            skipped["resolved empty"] += 1
            continue
        out.append({
            "paragraph_id": r["paragraph_id"],
            "doc_id": doc,
            "project_id": doc_project.get(doc, ""),
            "section_label": col(r, "section_label", "section_title"),
            "block_type": block,
            "token_count": int(col(r, "token_count", "n_tokens") or 0),
            "text": text,
        })
    if bad_hash:
        raise SystemExit(
            f"build_sample: {len(bad_hash)} paragraphs do not match their "
            f"recorded text_sha256 (first: {', '.join(bad_hash[:3])}).\n"
            f"  paragraphs.csv and data/clean/ have diverged - re-run "
            f"src/clean.py and src/segment.py before building the sample.")
    for why, n in sorted(skipped.items()):
        log(f"  skipped {n:,} paragraphs: {why}")
    log(f"  usable prose paragraphs: {len(out):,} "
        f"across {len({p['project_id'] for p in out if p['project_id']})} projects")
    return out


def read_tracker(path, log):
    """[(project_id, excerpt, [category headings])] from '2. Activities Review'.

    Column letters are read from the header row rather than hard-coded, so a
    reordered or newly inserted column does not silently shift the read.
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = next((s for s in wb.sheetnames if "activities review" in s.lower()), None)
    if sheet is None:
        raise SystemExit(f"build_sample: no 'Activities Review' sheet in {path}")
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next(i for i, r in enumerate(rows)
                 if r and any(str(c).strip() == "Project ID" for c in r if c))
    hdr = [str(c).strip() if c is not None else "" for c in rows[hdr_i]]
    idx = {name: i for i, name in enumerate(hdr) if name}

    def need(name):
        if name not in idx:
            raise SystemExit(f"build_sample: tracker has no '{name}' column")
        return idx[name]

    c_proj, c_text = need("Project ID"), need("Measures / Activities")
    # every column between the Adaptation flag and the Mitigation flag
    lo, hi = need("Adaptation"), need("Mitigation")
    cat_cols = [(i, hdr[i]) for i in range(lo + 1, hi) if hdr[i]]

    out = []
    for r in rows[hdr_i + 1:]:
        if not r or c_text >= len(r) or not r[c_text]:
            continue
        text = str(r[c_text]).strip()
        if not text:
            continue
        m = re.search(r"P\d{5,}", str(r[c_proj] or ""))
        cats = [name for i, name in cat_cols if i < len(r) and r[i]]
        out.append((m.group(0) if m else "", text, cats))
    log(f"  tracker sheet {sheet!r}: {len(out)} excerpt rows, "
        f"{len({p for p, _, _ in out if p})} projects, "
        f"{len(cat_cols)} category columns")
    log(f"  excerpt rows with no category at all: "
        f"{sum(1 for _, _, c in out if not c)}")
    return out


def hint_for(categories):
    """The first category that yields an actual hint, not the first that matches.

    Some categories are listed in CATEGORY_HINTS with empty values on purpose -
    `enabling environment` is policy work and its direction is genuinely
    ambiguous, so guessing one would be inventing a label. Returning that empty
    pair and stopping would then discard a real hint from a second category on
    the same row, which is what happened on the first live run: four rows
    tagged both `Enabling Environment` and `Resilient telecom` were reported as
    having no hint at all.
    """
    for cat in categories:
        low = cat.lower()
        for key, asset, direction in CATEGORY_HINTS:
            if key in low and (asset or direction):
                return asset, direction
    return "", ""


def targeted_pool(paras, args, log):
    """The paragraphs the targeted slice may draw from, and how they were found.

    Two location-based selectors, unioned. Neither reads paragraph text, so
    neither hands a method under test an advantage it did not earn.

    SECTION HEADINGS BARELY WORK ON THIS CORPUS and that is a finding, not a
    tuning problem. Of 17,719 prose paragraphs, four sit under a heading naming
    cybersecurity, data centres, digital public infrastructure or digital
    identification; `climate` appears in 378. PAD headings name the document's
    structure, not its subject matter, so the topics that never got a heading
    need the second selector.

    NAMED PROJECTS ARE THE SECOND SELECTOR, and the list is a person's
    judgement, never a script's: which operations finance a security operations
    centre or a data centre is exactly the expert call AGENTS.md says lives
    with people. Within those projects the draw is uniform over the sections
    that describe what is financed, so the slice is still selected by location.
    """
    pool, why = {}, []
    if args.targeted_sections:
        rx = re.compile(args.targeted_sections, re.I)
        hit = [p for p in paras if rx.search(p["section_label"] or "")]
        for p in hit:
            pool[p["paragraph_id"]] = p
        why.append(f"{len(hit)} by heading /{args.targeted_sections}/")
    if args.targeted_projects:
        want = {s.strip().upper() for s in args.targeted_projects.split(",") if s.strip()}
        rx = re.compile(args.targeted_project_sections, re.I)
        hit = [p for p in paras if p["project_id"].upper() in want
               and rx.search(p["section_label"] or "")]
        for p in hit:
            pool[p["paragraph_id"]] = p
        seen = {p["project_id"] for p in hit}
        why.append(f"{len(hit)} from {len(seen)} of {len(want)} named projects")
        for pid in sorted(want - seen):
            log(f"  no paragraphs for named project {pid} - check the id")
    if not pool:
        log("targeted slice: nothing selected "
            "(pass --targeted-sections and/or --targeted-projects)")
    return sorted(pool.values(), key=lambda p: p["paragraph_id"]), "; ".join(why)


def pick(pool, n, rng, exclude):
    """Deterministic sample of up to n, skipping already-taken ids."""
    avail = sorted((p for p in pool if p["paragraph_id"] not in exclude),
                   key=lambda p: p["paragraph_id"])
    rng.shuffle(avail)
    return avail[:n]


def stratum_of(paragraph, compiled):
    """The first stratum whose heading regex matches; `other` catches the rest."""
    label = paragraph["section_label"] or ""
    for name, rx in compiled:
        if rx is None or rx.search(label):
            return name
    return "other"


def draw(pool, n, cap, rng, taken, log, what):
    """Up to n paragraphs, at most `cap` from any one project.

    THE CAP IS THE POINT. A first attempt at this sample returned four
    consecutive paragraphs of one document as its cybersecurity slice; that is
    one observation wearing four hats, and it is how a sample silently stops
    measuring anything. Drawing round-robin over projects also spreads the
    draw without needing a second pass.
    """
    by_project = defaultdict(list)
    for p in sorted(pool, key=lambda p: p["paragraph_id"]):
        if p["paragraph_id"] not in taken:
            by_project[p["project_id"]].append(p)
    for plist in by_project.values():
        rng.shuffle(plist)
    projects = sorted(by_project)
    rng.shuffle(projects)

    got = []
    for round_i in range(cap):
        for proj in projects:
            if len(got) >= n:
                break
            plist = by_project[proj]
            if round_i < len(plist):
                got.append(plist[round_i])
        if len(got) >= n:
            break
    taken |= {p["paragraph_id"] for p in got}

    spread = Counter(p["project_id"] for p in got)
    worst = spread.most_common(1)[0] if spread else ("-", 0)
    log(f"{what}: {len(got)} of {len(pool):,} available, "
        f"{len(spread)} projects, at most {worst[1]} from any one")
    if len(got) < n:
        log(f"  SHORT by {n - len(got)}")
    return got


def probe_pool(paras, log):
    """Paragraphs whose BODY names cybersecurity, data centres or DPI.

    Body text, deliberately, and the one place in this file that reads it. See
    the module docstring: this group answers whether a method is blind to these
    topics, and never contributes to a recall or precision figure.
    """
    import re as _re
    phr = _re.compile(PROBE_PHRASES, _re.I)
    acr = _re.compile(PROBE_ACRONYMS)
    hit = [p for p in paras if phr.search(p["text"]) or acr.search(p["text"])]
    log(f"probe pool: {len(hit):,} paragraphs name one of the probe topics "
        f"({len(hit) / max(1, len(paras)):.1%} of prose)")
    return hit


def build(args, log):
    log("corpus")
    paras = load_corpus(args.data, log)
    rng = random.Random(args.seed)
    compiled = [(name, re.compile(rx, re.I) if rx else None) for name, rx in STRATA]

    buckets = defaultdict(list)
    for p in paras:
        buckets[stratum_of(p, compiled)].append(p)
    log("\nstrata over the whole corpus (first match wins)")
    for name, _ in STRATA:
        log(f"  {len(buckets[name]):6,}  {name}")

    wanted = {"climate": args.n_climate, "components": args.n_components,
              "other": args.n_other}
    chosen, slice_of, taken = [], {}, set()
    log("\ndrawing")
    for name, _ in STRATA:
        got = draw(buckets[name], wanted[name], args.per_project_cap, rng,
                   taken, log, f"  {name}")
        for p in got:
            chosen.append(p); slice_of[p["paragraph_id"]] = name

    if args.n_probe:
        got = draw(probe_pool(paras, log), args.n_probe, args.probe_cap, rng,
                   taken, log, "  probe")
        for p in got:
            chosen.append(p); slice_of[p["paragraph_id"]] = "probe"

    # A person named projects: top up `components` from them, still by section.
    if args.targeted_projects:
        pool, why = targeted_pool(paras, args, log)
        got = draw(pool, args.targeted, args.per_project_cap, rng, taken, log,
                   f"  named projects ({why})")
        for p in got:
            chosen.append(p); slice_of[p["paragraph_id"]] = "components"

    write_workbook(args.out, chosen, slice_of, {}, args, log)

    log(f"\ntotal paragraphs: {len(chosen)}")
    counts = Counter(slice_of.values())
    for name in [s[0] for s in STRATA] + ["probe"]:
        if counts.get(name):
            sub = [p for p in chosen if slice_of[p["paragraph_id"]] == name]
            sp = Counter(p["project_id"] for p in sub)
            log(f"  {counts[name]:4d}  {name:<11} {len(sp)} projects, "
                f"max {sp.most_common(1)[0][1]} per project")
    log(f"blank annotation rows: "
        f"{sum(blank_rows(slice_of[p['paragraph_id']], args) for p in chosen)}")
    log("\nPrecision must be estimated stratum-weighted against the corpus "
        "counts above, never raw: the draw over-weights climate and components "
        "on purpose. The probe group is excluded from every ranking figure.")
    return 0


def blank_rows(slice_name, args):
    return args.rows_sparse if slice_name == "other" else args.rows_rich


def write_workbook(path, chosen, slice_of, hint_of, args, log):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter as gcl
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    head = Font(bold=True)
    grey = PatternFill("solid", fgColor="EEEEEE")
    wrap = Alignment(wrap_text=True, vertical="top")

    ws = wb.active
    ws.title = "paragraphs"
    cols = ["paragraph_id", "project_id", "doc_id", "section_label", "slice",
            "tokens", "read", "text"]
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        ws.cell(1, c).font = head
        ws.cell(1, c).fill = grey
    for p in chosen:
        ws.append([p["paragraph_id"], p["project_id"], p["doc_id"],
                   p["section_label"], slice_of[p["paragraph_id"]],
                   p["token_count"], "", p["text"]])
    for w, c in zip((26, 12, 12, 30, 10, 8, 7, 150), range(1, len(cols) + 1)):
        ws.column_dimensions[gcl(c)].width = w
    for r in range(2, len(chosen) + 2):
        ws.cell(r, 8).alignment = wrap
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{gcl(len(cols))}{len(chosen) + 1}"
    yn = DataValidation(type="list", formula1='"Y"', allow_blank=True)
    ws.add_data_validation(yn)
    yn.add(f"G2:G{len(chosen) + 1}")

    rs = wb.create_sheet("rows")
    rs.append(ROW_COLUMNS)
    for c in range(1, len(ROW_COLUMNS) + 1):
        rs.cell(1, c).font = head
        rs.cell(1, c).fill = grey
    n = 0
    for p in chosen:
        pid = p["paragraph_id"]
        asset, direction = hint_of.get(pid, ("", ""))
        preview = re.sub(r"\s+", " ", p["text"])[:PREVIEW_CHARS]
        for _ in range(blank_rows(slice_of[pid], args)):
            rs.append([pid, preview, "", "", "", direction, asset, "", "", ""])
            n += 1
    for w, c in zip((26, 46, 12, 64, 30, 22, 22, 13, 18, 40),
                    range(1, len(ROW_COLUMNS) + 1)):
        rs.column_dimensions[gcl(c)].width = w
    rs.freeze_panes = "C2"
    rs.auto_filter.ref = f"A1:{gcl(len(ROW_COLUMNS))}{n + 1}"
    dv_kind = DataValidation(type="list", formula1=f'"{",".join(KINDS)}"',
                             allow_blank=True)
    dv_dir = DataValidation(type="list", formula1=f'"{",".join(DIRECTIONS)}"',
                            allow_blank=True)
    dv_yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    for dv, col in ((dv_kind, "C"), (dv_dir, "F"), (dv_yn, "H")):
        rs.add_data_validation(dv)
        dv.add(f"{col}2:{col}{n + 1}")
    for r in range(2, n + 2):
        rs.cell(r, 4).alignment = wrap
        rs.cell(r, 10).alignment = wrap

    tmp = path + ".part"
    wb.save(tmp)
    os.replace(tmp, path)
    log(f"\nwrote {path}")


def inspect(args, log):
    """Report what there is to sample from, before any slice is chosen."""
    paras = load_corpus(args.data, log)
    secs = Counter((p["section_label"] or "(blank)").strip() for p in paras)
    log(f"\ndistinct section labels: {len(secs)}")
    log("the 40 largest:")
    for name, n in secs.most_common(40):
        log(f"  {n:6,}  {name[:88]}")
    probe = ["cyber", "security", "data cent", "datacent", "identif", "dpi",
             "digital public", "connectiv", "resilien", "climate", "annex",
             "component"]
    log("\nparagraphs whose section heading contains:")
    for k in probe:
        rx = re.compile(k, re.I)
        log(f"  {sum(1 for p in paras if rx.search(p['section_label'] or '')):6,}  {k}")
    if args.tracker:
        read_tracker(args.tracker, log)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="goldset_sample.xlsx")
    ap.add_argument("--seed", type=int, default=20260918)
    ap.add_argument("--n-climate", type=int, default=60)
    ap.add_argument("--n-components", type=int, default=70)
    ap.add_argument("--n-other", type=int, default=50)
    ap.add_argument("--n-probe", type=int, default=20,
                    help="blind-spot probe; excluded from every ranking figure")
    ap.add_argument("--per-project-cap", type=int, default=3,
                    help="most paragraphs any one project may contribute to a "
                         "stratum; four consecutive paragraphs of one document "
                         "is one observation, not four")
    ap.add_argument("--probe-cap", type=int, default=2)
    ap.add_argument("--targeted", type=int, default=0,
                    help="extra component paragraphs from --targeted-projects")
    ap.add_argument("--targeted-sections", default="",
                    help="regex over section_label for the named-project top-up")
    ap.add_argument("--targeted-projects", default="",
                    help="comma-separated project ids a person named; their "
                         "paragraphs are drawn uniformly from the sections "
                         "matching --targeted-project-sections")
    ap.add_argument("--targeted-project-sections",
                    default=r"component|detailed project description|annex|"
                            r"technical design",
                    help="which sections of a named project may be drawn from")
    ap.add_argument("--rows-rich", type=int, default=4,
                    help="blank annotation rows per climate/components/probe row")
    ap.add_argument("--rows-sparse", type=int, default=2,
                    help="blank rows for `other`, which is mostly empty")
    ap.add_argument("--inspect", action="store_true",
                    help="report section labels and tracker shape; writes nothing")
    args = ap.parse_args()

    def log(m):
        print(m, flush=True)

    return inspect(args, log) if args.inspect else build(args, log)


if __name__ == "__main__":
    raise SystemExit(main())
