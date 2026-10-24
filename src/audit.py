#!/usr/bin/env python3
"""Count known defects in the cleaned appraisal paragraphs and the prepared
procurement tables, on every row rather than a sample.

Reads  data/appraisal/paragraphs.csv, data/appraisal/text/{doc_id}.txt
       data/procurement/{packages,notices,awards}.csv (and the *_raw.csv vocabulary)
Writes data/reports/audit.txt, data/reports/audit_procurement.txt

Each check reports a count, a share of its scope and a few examples.
tests/test_corpus_quality.py and tests/test_clean_procurement.py gate the rates.

  python3 src/audit.py                    # both audits
  python3 src/audit.py --appraisal --examples 8 --block narrative
  python3 src/audit.py --procurement
"""
import argparse, csv, os, re, sys, unicodedata
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
from clean_procurement import norm_ref, REF_INLINE  # noqa: E402
import glue  # noqa: E402

# Blocks meant to read as prose; tables and front matter are not, so the prose
# checks run on these only.
PROSE_BLOCKS = ("narrative", "annex")
PROCUREMENT_TABLES = ("packages", "notices", "awards")

# Model tokens per word for openai/text-embedding-3-large on this corpus, as
# billed (3,027,632 tokens for 2,287,945 words); used for an estimate column.
TOKENS_PER_WORD = 3_027_632 / 2_287_945

# {"appraisal": [(name, scope, severity, fn)], "procurement": [...]}. An
# appraisal check takes a paragraph's text and is scoped to blocks (None: all);
# a procurement check takes a row and is scoped to tables.
CHECKS = {"appraisal": [], "procurement": []}

# Set by load_vocab(): the run's vocabulary, so the word checks judge by the
# words the cleaner used.
VOCAB, NAMES = {}, None


def check(name, kind="appraisal", scope=None, severity="defect"):
    if scope is None:
        scope = PROSE_BLOCKS if kind == "appraisal" else ("packages",)

    def wrap(fn):
        CHECKS[kind].append((name, scope, severity, fn))
        return fn
    return wrap


# ------------------------------------------------------------ characters
# One predicate per character defect, run on a paragraph's text and on the
# named fields of a procurement row.

CHAR_CHECKS = (
    ("unicode replacement character", lambda c: c == "�", ("description",)),
    ("control or format character",
     lambda c: unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\t", ("description",)),
    # A Private Use Area code point means whatever the emitting font decided;
    # text_rules.CHAR_MAP maps the known ones, so a new one shows up here.
    ("private use area character",
     lambda c: 0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0x10FFFD,
     ("description", "supplier_name", "sector")),
)

for _name, _bad, _fields in CHAR_CHECKS:
    check(_name, scope=())(lambda t, bad=_bad: [c for c in t if bad(c)])
    check(_name, "procurement", PROCUREMENT_TABLES)(
        lambda row, bad=_bad, fields=_fields: [f"{f}:U+{ord(c):04X}" for f in fields
                                               for c in row.get(f) or "" if bad(c)])


# ------------------------------------------------------------ pattern rules
# (name, severity, regex): a hit is every match. Appraisal patterns run on
# prose paragraphs (the unfolded-character one on every block); procurement
# patterns on the packages' cleaned description.

APPRAISAL_PATTERNS = (
    # project34, shocks46: a reference number fused onto the preceding word.
    ("footnote marker glued to a word", "defect", r"\b[a-z]{3,}(\d{1,3})\b"),
    # '38Vietnam provides...': a page-foot footnote emitted as a paragraph.
    ("paragraph opens as a footnote body", "defect", r"^\s*(\d{1,3})[A-Z][a-z]"),
    ("page number left inline", "defect", r"(?:^|\s)Page \d{1,4}(?:\s|$)"),
    ("whitespace run of 3-5 spaces", "defect", r"\S {3,5}\S"),
    # A wide run is a column gutter that survived: table content, not prose.
    ("whitespace run of 6+ spaces (column gutter)", "defect", r"\S {6,}\S"),
    # What text_rules.fold_chars should have folded; U+2010 looks like '-' and
    # tokenises as something else.
    ("smart quote, ligature or hyphen left unfolded", "defect",
     "[‘’“”–‐‑­ﬁﬂ ]"),
)
ALL_BLOCKS = {"smart quote, ligature or hyphen left unfolded"}

for _name, _sev, _rx in APPRAISAL_PATTERNS:
    check(_name, scope=() if _name in ALL_BLOCKS else PROSE_BLOCKS, severity=_sev)(
        lambda t, rx=re.compile(_rx): rx.findall(t))


PROCUREMENT_PATTERNS = (
    # Two packages stitched into one: the next record's reference survived.
    ("description carries a second borrower reference", "defect", REF_INLINE),
    # A repeated column heading taken as content at a record boundary.
    ("table header text leaked into a description", "defect",
     r"(Activity Reference No\.|Description\s+Component|"
     r"Project Implementation agency|Loan / Credit No)"),
)

for _name, _sev, _rx in PROCUREMENT_PATTERNS:
    check(_name, "procurement", severity=_sev)(
        lambda row, rx=re.compile(_rx): rx.findall(row.get("description_clean") or ""))


# ------------------------------------------------------- appraisal checks

@check("tabular: numeric tokens over a third")
def _numeric(t):
    toks = re.findall(r"[\w'’-]+", t)
    if len(toks) < 12:
        return []
    numeric = sum(1 for x in toks if re.fullmatch(r"[\d.,%()$-]+", x))
    return ["ratio"] if numeric / len(toks) >= 0.33 else []


@check("starts mid-sentence", severity="soft")
def _midsentence(t):
    """A lowercase opening is usually the tail of a paragraph split across a
    page; a list item ('a. Mobile broadband') is structure, not a split."""
    s = t.lstrip()
    if not s or not s[0].islower():
        return []
    if re.match(r"^(?:\(?[a-z]|\(?[ivxlc]{1,4})[\.\)]\s+\S", s):
        return []
    return ["frag"]


@check("no sentence-ending punctuation", severity="soft")
def _unterminated(t):
    s = t.rstrip()
    return ["open"] if s and s[-1] not in ".!?:;\"')]" else []


# ----------------------------------------------------- procurement checks

def _desc(row):
    return row.get("description_clean") or ""


def _vocab(row):
    return VOCAB if (row.get("description_lang") or "en") == "en" else \
        getattr(VOCAB, "foreign", VOCAB)


@check("whitespace not collapsed", "procurement", PROCUREMENT_TABLES)
def _spacing(row):
    """Descriptions only: supplier names are interface metadata, not rewritten."""
    v = row.get("description") or ""
    return ["description"] if v != " ".join(v.split()) else []


@check("word glued to the next inside the description", "procurement", PROCUREMENT_TABLES,
       severity="soft")
def _glued_inside(row):
    """A case boundary inside a token (DataCenter, forZanzibar) that glue.repair
    declined to split. A floor, not a count: 'studyfor' has no case boundary."""
    return [m.group(0) for m in re.finditer(r"\b\w*[a-z][A-Z]\w*\b", _desc(row))
            if glue._best_cut(m.group(0), _vocab(row), NAMES) is not None][:3]


@check("word broken by a space inside the description", "procurement", PROCUREMENT_TABLES)
def _broken_word(row):
    """'S upply': fragments that are one corpus word, which glue.repair's last
    two steps (rejoin, then unfuse) would still change."""
    d, v = _desc(row), _vocab(row)
    joined, n = glue.rejoin(d, v)
    fixed, _ = glue.unfuse(joined, v, VOCAB)
    return [f"{n} fragment pair(s)"] if n and fixed != d else []


@check("description ends mid-word", "procurement")
def _midword(row):
    """A trailing token of more than three letters with no vowel is a fragment
    ('Toolsfor' cut short); an all-capitals token is an acronym."""
    d = _desc(row).rstrip(" .()")
    toks = re.findall(r"[A-Za-z]+", d) if len(d) >= 3 else []
    last = toks[-1] if toks else ""
    if not last or last.isupper():
        return []
    return [last] if len(last) > 3 and not re.search(r"[aeiouAEIOU]", last) else []


@check("borrower reference absent", "procurement")
def _no_ref(row):
    return [] if (row.get("borrower_ref") or "").strip() else ["null"]


@check("borrower reference not normalised", "procurement")
def _ref_norm(row):
    ref = row.get("borrower_ref") or ""
    return [ref] if ref and norm_ref(ref) != (row.get("borrower_ref_norm") or "") else []


@check("placeholder description", "procurement", severity="soft")
def _placeholder(row):
    """A flag, counted so its share is visible."""
    return ["placeholder"] if (row.get("is_placeholder") or "") == "true" else []


@check("category unmapped", "procurement")
def _category(row):
    return [] if row.get("category") != "unknown" else ["unknown"]


@check("method unmapped", "procurement", severity="soft")
def _method(row):
    return [] if row.get("method") != "unknown" else ["unknown"]


@check("status unmapped", "procurement", severity="soft")
def _status(row):
    return [] if row.get("status") != "unknown" else ["unknown"]


@check("status raw present but unmapped", "procurement", severity="soft")
def _status_raw(row):
    raw = (row.get("status_raw") or "").strip()
    return [raw] if raw and row.get("status") == "unknown" else []


@check("description too short to match on", "procurement", severity="soft")
def _short(row):
    """Never a reason to drop a row: the count of rows no text matcher reaches."""
    words = re.findall(r"[A-Za-zÀ-ɏ]{2,}", row.get("description") or "")
    return ["short"] if len(words) < 3 else []


@check("planned date after revised date", "procurement", severity="source")
def _dates(row):
    p, r = (row.get("planned_date") or ""), (row.get("revised_date") or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", p) and re.match(r"^\d{4}-\d{2}-\d{2}$", r):
        return ["inverted"] if p > r else []
    return []


@check("signed package with no amount", "procurement", severity="soft")
def _signed_amount(row):
    if (row.get("status") or "") not in ("Signed", "Completed"):
        return []
    amt = (row.get("estimated_amount") or "").strip()
    return ["zero_or_absent"] if amt in ("", "0.00", "0") else []


@check("lot or phase marker left at the edge of the description", "procurement")
def _marker_left(row):
    """A marker opening or closing the description belongs in lot_or_phase; one
    in the middle of the words stays ('Phase 1 and Phase 2 works')."""
    d = _desc(row)
    found = re.findall(r"\(\s*RE-?BID\s*\)", d, re.I)
    found += re.findall(r"(?:^|[-–:(]\s*)(?:LOTS?|PHASE)\s*[-#:]?\s*(?:\d+|[IVX]+)\s*\)?\s*$"
                        r"|^\s*(?:LOTS?|PHASE)\s*[-#:]?\s*(?:\d+|[IVX]+)\s*[-–:]", d, re.I)
    return found


@check("another cell of the plan row left in the description", "procurement",
       ("packages", "awards"))
def _cells_left(row):
    """A Loan / Credit number, or review type, method and market approach, read
    in from a neighbouring column."""
    d = _desc(row)
    found = re.findall(r"\b(?:IDA|IBRD|TF|COFN)\s*/\s*[A-Z]?\d{3,}", d)
    found += re.findall(r"(?:Open|Limited|Direct)\s*-\s*(?:National|Internationa)"
                        r"|Single Stage\s*-\s*(?:One|Two)", d)
    return found


@check("component not found", "procurement", severity="soft")
def _component(row):
    """Coverage, not a defect: not every plan prints a component column."""
    return [] if row.get("component_number") else ["none"]


@check("supplier amount exceeds the contract amount", "procurement", ("awards",),
       severity="source")
def _supplier_over(row):
    try:
        total = float(row.get("total_amount") or 0)
        supp = float(row.get("supplier_amount") or 0)
    except ValueError:
        return []
    return ["over"] if supp > total + 0.01 else []


@check("notice with no description", "procurement", ("notices",), severity="source")
def _notice_desc(row):
    return [] if (row.get("description") or "").strip() else ["empty"]


# --------------------------------------------------------------- running

def scan(kind, units):
    """(hits, scope) for one audit. units yields (where, key, value): where is
    a block or a table, value what the checks take."""
    hits, scope = defaultdict(list), Counter()
    for at, key, value in units:
        for name, only, _sev, fn in CHECKS[kind]:
            if only and at not in only:
                continue
            scope[name] += 1
            found = fn(value)
            if found:
                hits[name].append((key, found, value))
    return hits, scope


def check_table(kind, hits, scope):
    out = [f"{'check':<48}{'hits':>8}{'% of scope':>12}  severity", "-" * 86]
    for name, _only, severity, _fn in CHECKS[kind]:
        n = len(hits[name])
        out.append(f"{name:<48}{n:>8}{100.0 * n / (scope[name] or 1):>11.2f}%  {severity}")
    out.append("-" * 86)
    return out


def write_report(lines, path, head=None):
    """Write a report and print it (or its first `head` lines)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines if head is None else lines[:head] + ["..."]))
    print(f"wrote {path}")


# --------------------------------------------------------- the appraisal

def appraisal(data, examples=4, block=""):
    path = where(data, "appraisal", "paragraphs.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if not block or r["block"] == block]
    texts = {}

    def text_of(row):
        did = row["doc_id"]
        if did not in texts:
            with open(where(data, "text", f"{did}.txt"), encoding="utf-8") as fh:
                texts[did] = fh.read()
        return texts[did][int(row["char_start"]):int(row["char_end"])]

    hits, scope = scan("appraisal", ((r["block"], r, text_of(r)) for r in rows))
    blocks = Counter(r["block"] for r in rows)
    prose = sum(v for k, v in blocks.items() if k in PROSE_BLOCKS)
    out = [f"paragraphs scanned: {len(rows)}   prose blocks (narrative+annex): {prose}", ""]

    words, chars = Counter(), Counter()
    for row in rows:
        words[row["block"]] += int(row["n_tokens"])
        chars[row["block"]] += int(row["char_end"]) - int(row["char_start"])

    def line(label, n, w, c):
        return f"{label:<34}{n:>8}{w:>12,}{c:>12,}{int(w * TOKENS_PER_WORD):>26,}"
    out += [f"{'block':<34}{'paras':>8}{'words':>12}{'chars':>12}{'model tokens (est)':>26}",
            "-" * 92]
    out += [line(b, blocks[b], words[b], chars[b]) for b in sorted(blocks, key=lambda b: -words[b])]
    model = [r for r in rows if r.get("for_model") == "true"]
    pw = sum(int(r["n_tokens"]) for r in model)
    pc = sum(int(r["char_end"]) - int(r["char_start"]) for r in model)
    out += ["-" * 92, line("GIVEN TO THE MODEL (for_model)", len(model), pw, pc),
            line("ALL BLOCKS", len(rows), sum(words.values()), sum(chars.values())), "",
            f"chars per word: {pc / max(pw, 1):.2f} over what the model is given "
            f"(ordinary English is nearer 5.3; the token column is measured, "
            f"not derived from this)", ""]

    out += check_table("appraisal", hits, scope)
    clean = {r["paragraph_id"] for r in rows if r["block"] in PROSE_BLOCKS}
    for name, _only, severity, _fn in CHECKS["appraisal"]:
        if severity == "defect":
            clean -= {row["paragraph_id"] for row, _f, _t in hits[name]}
    out.append(f"prose paragraphs with NO defect-severity hit: {len(clean)} "
               f"of {prose} ({100.0 * len(clean) / max(prose, 1):.1f}%)")

    for name, _only, severity, _fn in CHECKS["appraisal"]:
        if not hits[name]:
            continue
        out += ["", "=" * 82, f"{name}  ({len(hits[name])} hits, {severity})", "=" * 82]
        for row, found, body in hits[name][:examples]:
            flat = re.sub(r"\s+", " ", body)
            needle = found[0] if isinstance(found[0], str) and len(found[0]) > 1 else ""
            span = flat.find(needle) if needle else None
            if span is not None:
                lo = max(0, span - 90)
                snippet = ("..." if lo else "") + flat[lo:span + 120]
            else:
                snippet = flat[:210]
            out.append(f"  {row['paragraph_id']}  block={row['block']} "
                       f"section={row['section_path'] or '-'}  matched={found[:3]}")
            out.append(f"    {snippet}")
    return out


# ------------------------------------------------------- the procurement

ENUM_FIELDS = {
    "packages": ["category", "method", "method_source", "method_raw", "market_approach",
                 "status", "status_raw", "description_lang", "is_placeholder",
                 "is_rebid", "lot_or_phase", "component_source", "amount_source",
                 "status_source", "currency"],
    "notices": ["notice_type", "notice_status", "category", "method", "description_lang",
                "is_placeholder", "country_name"],
    "awards": ["category", "method", "review_type", "description_lang",
               "is_placeholder", "currency"],
}


def load(data, table):
    path = where(data, "procurement", f"{table}.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_vocab(data):
    """Build the run's vocabulary exactly as clean_procurement does."""
    global VOCAB, NAMES
    raw = []
    for name in ("packages_raw", "notices_raw", "awards_raw"):
        rows = load(data, name) or []
        raw += [r.get("description") or "" for r in rows]
    VOCAB, NAMES = glue.corpus_vocab(raw, data)


def procurement(data, examples=5):
    tables = {t: load(data, t) for t in PROCUREMENT_TABLES}
    if any(v is None for v in tables.values()):
        return None
    load_vocab(data)
    hits, scope = scan("procurement", ((t, (t, row), row) for t in PROCUREMENT_TABLES
                                       for row in tables[t]))
    out = ["procurement audit - prepared tables", ""]
    out += [f"{t:<10}{len(tables[t]):>8,} rows" for t in PROCUREMENT_TABLES]
    out += [""] + check_table("procurement", hits, scope)
    out += ["", "examples (the first few, in row order)", "-" * 86]
    for name, *_ in CHECKS["procurement"]:
        if not hits[name]:
            continue
        out.append(f"{name}:")
        for (t, row), found, _row in hits[name][:examples]:
            key = row.get("package_id") or row.get("notice_id") or row.get("contract_id") or "?"
            out.append(f"  [{t}] {key}  {found[:3]}")
            out.append(f"      {_desc(row)[:160]}")
        out.append("")

    # Despacing can make two different descriptions one match key; the count
    # says what the despaced matching copy costs.
    out += ["despacing collisions - descriptions that differ but despace alike", "=" * 86]
    for t in PROCUREMENT_TABLES:
        rows = tables[t]
        if not rows or "description_match" not in rows[0]:
            continue
        groups = defaultdict(set)
        for r in rows:
            if (r.get("description_match") or "").strip():
                groups[r["description_match"]].add((r.get("description_clean") or "").strip())
        collided = {k: v for k, v in groups.items() if len(v) > 1}
        pct = 100.0 * len(collided) / max(len(groups), 1)
        out.append(f"  {t:<10} {len(collided):>5} of {len(groups):,} distinct "
                   f"match keys collide ({pct:.2f}%)")
        for v in list(collided.values())[:3]:
            out += [f"      {sorted(v)[0][:70]}", f"      {sorted(v)[1][:70]}"]
    out += ["", "value profile - top twenty distinct values per enum-ish field", "=" * 86]
    for t, fields in ENUM_FIELDS.items():
        for f in fields:
            if f not in (tables[t][0] if tables[t] else {}):
                continue
            c = Counter((r.get(f) or "").strip() for r in tables[t])
            out.append(f"\n{t}.{f}  ({len(c)} distinct)")
            out += [f"  {n:>7,}  {val[:110]}" for val, n in c.most_common(20)]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--appraisal", action="store_true", help="the appraisal audit only")
    ap.add_argument("--procurement", action="store_true", help="the procurement audit only")
    ap.add_argument("--examples", type=int, default=0,
                    help="examples per check (default 4 appraisal, 5 procurement)")
    ap.add_argument("--block", default="", help="appraisal: one block only")
    ap.add_argument("--out", default="", help="report path (with one audit only)")
    args = ap.parse_args(argv)
    both = args.appraisal == args.procurement
    if args.out and both:
        raise SystemExit("audit: --out needs --appraisal or --procurement")
    ran = 0
    if both or args.appraisal:
        out = appraisal(args.data, args.examples or 4, args.block)
        if out is None and not both:
            raise SystemExit("audit: run src/clean.py first")
        if out is not None:
            write_report(out, args.out or where(args.data, "reports", "audit.txt"))
            ran += 1
    if both or args.procurement:
        out = procurement(args.data, args.examples or 5)
        if out is None and not both:
            raise SystemExit("audit: run src/clean_procurement.py first")
        if out is not None:
            write_report(out, args.out or where(args.data, "reports", "audit_procurement.txt"),
                         head=40)
            ran += 1
    if not ran:
        raise SystemExit("audit: nothing to audit; run src/clean.py or "
                         "src/clean_procurement.py first")
    return 0


if __name__ == "__main__":
    sys.exit(main())
