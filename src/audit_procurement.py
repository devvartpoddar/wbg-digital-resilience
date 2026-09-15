#!/usr/bin/env python3
"""Scan the prepared procurement tables for defects and count them.

Reads  data/intermediate/procurement/{packages,notices,awards}.csv
Writes data/intermediate/procurement/audit_procurement_report.txt

Same shape as src/audit.py: named checks, a count, a share of scope, and a few
example keys, so the rates can be gated in tests/test_clean_procurement.py and
read by a person. One deliberate difference: these checks take the whole cleaned
row rather than a text string, because half the defects procurement text has are
missing or unmapped fields rather than bad characters.

The check set was written AFTER profiling the prepared tables, not before. What
is here is what the data actually shows; the profile section of the report
prints the top twenty distinct values of every enum-ish field, so a reader can
see the real distribution rather than the one the checks assume.
"""
import argparse, csv, os, re, sys, unicodedata
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clean import CHAR_MAP                                # noqa: E402
from clean_procurement import norm_ref, is_placeholder     # noqa: E402

PROC_DIR = os.path.join("intermediate", "procurement")

CHECKS = []


def check(name, tables=("packages",), severity="defect"):
    def wrap(fn):
        CHECKS.append((name, tables, severity, fn))
        return fn
    return wrap


# --------------------------------------------------------------- characters

@check("private use area character", tables=("packages", "notices", "awards"))
def _private_use(row):
    """The same Symbol and Wingdings glyphs that cost two rounds on the
    appraisal corpus. CHAR_MAP in clean.py folds the ten that were found there;
    anything else in a Private Use Area is a code point whose only meaning lived
    in a font that is now gone."""
    out = []
    for field in ("description", "bid_description", "supplier_name", "sector"):
        for c in row.get(field) or "":
            if 0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0x10FFFD:
                out.append(f"{field}:U+{ord(c):04X}")
    return out


@check("unicode replacement character", tables=("packages", "notices", "awards"))
def _replacement(row):
    return [f for f in ("description", "bid_description")
            if "\ufffd" in (row.get(f) or "")]


@check("control or format character", tables=("packages", "notices", "awards"))
def _control(row):
    out = []
    for field in ("description", "bid_description"):
        for c in row.get(field) or "":
            if unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\t":
                out.append(f"{field}:U+{ord(c):04X}")
    return out


@check("whitespace not collapsed", tables=("packages", "notices", "awards"))
def _spacing(row):
    """Descriptions only. Supplier names are interface metadata this stage does
    not rewrite, so a double space in one is not a cleaning defect."""
    out = []
    for field in ("description", "bid_description"):
        v = row.get(field) or ""
        if v != " ".join(v.split()):
            out.append(field)
    return out


# ------------------------------------------------------------ the plan parse

# A second borrower reference inside a description means the record boundary was
# missed and two packages were stitched into one. It is the only check that can
# see the plan parser's worst failure, and it is exact rather than heuristic.
REF_INLINE = re.compile(r"[A-Z]{2,}-[A-Z0-9]{1,12}-[0-9]{2,10}-(?:CW|GD|GO|CS|NC)-\w{2,5}"
                        r"|\b(?=[A-Z0-9-]*[0-9])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+){1,5}\s*/\s")
REF_SHAPE = re.compile(r"^[A-Z0-9][A-Z0-9\u2013-]*$")


@check("description carries a second borrower reference")
def _double_ref(row):
    """Two packages stitched into one: the next record's reference survived
    inside this description. Checked on the MATCHING copy, which is the string
    the parser produced and the one a later stage will match on - the published
    string is display only and is allowed to be messy."""
    return REF_INLINE.findall(row.get("description_clean") or "")


@check("description ends mid-word")
def _midword(row):
    """A wrap join that dropped a space: 'Toolsfor'. Detected by a trailing
    word-shaped token with no vowel, which is a fragment rather than a word.
    The system word list is not assumed to exist, so this is shape-based."""
    d = (row.get("description_clean") or "").rstrip(" .()")
    if len(d) < 3:
        return []
    toks = re.findall(r"[A-Za-z]+", d)
    if not toks:
        return []
    last = toks[-1]
    if len(last) > 3 and not re.search(r"[aeiouAEIOU]", last):
        return [last]
    return []


@check("borrower reference absent")
def _no_ref(row):
    return [] if (row.get("borrower_ref") or "").strip() else ["null"]


@check("borrower reference not normalised")
def _ref_norm(row):
    ref = row.get("borrower_ref") or ""
    return [ref] if ref and norm_ref(ref) != (row.get("borrower_ref_norm") or "") else []


# ------------------------------------------------------------- step 7 and 9

@check("placeholder description", severity="soft")
def _placeholder(row):
    """Not a defect - it is a flag, and it is counted here so the share is
    visible rather than inferred from a boolean column nobody totals."""
    return ["placeholder"] if (row.get("is_placeholder") or "") == "true" else []


@check("category unmapped", severity="defect")
def _category(row):
    return [] if row.get("category") != "unknown" else ["unknown"]


@check("method unmapped", severity="soft")
def _method(row):
    return [] if row.get("method") != "unknown" else ["unknown"]


@check("status unmapped", severity="soft")
def _status(row):
    return [] if row.get("status") != "unknown" else ["unknown"]


@check("status raw present but unmapped", severity="soft")
def _status_raw(row):
    raw = (row.get("status_raw") or "").strip()
    return [raw] if raw and row.get("status") == "unknown" else []


@check("description too short to match on", severity="soft")
def _short(row):
    """Short is NOT a reason to drop a procurement row - the short record is the
    row. It is a reason to know how many rows no text matcher can reach."""
    words = re.findall(r"[A-Za-z\u00c0-\u024f]{2,}", row.get("description") or "")
    return ["short"] if len(words) < 3 else []


@check("language not determined")
def _lang(row):
    return [] if (row.get("description_lang") or "") in ("en", "fr", "es", "pt") else ["?"]


# ----------------------------------------------------------- cross-field

@check("planned date after revised date")
def _dates(row):
    p, r = (row.get("planned_date") or ""), (row.get("revised_date") or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", p) and re.match(r"^\d{4}-\d{2}-\d{2}$", r):
        return ["inverted"] if p > r else []
    return []


@check("signed package with no amount", severity="soft")
def _signed_amount(row):
    if (row.get("status") or "") != "signed":
        return []
    amt = (row.get("estimated_amount") or "").strip()
    return ["zero_or_absent"] if amt in ("", "0.00", "0") else []


@check("lot or phase marker left in the matching copy")
def _marker_left(row):
    """The published string keeps its markers on purpose - lot and phase are
    information, not noise - so this reads the matching copy, where a leftover
    marker means step 4 missed a form."""
    d = (row.get("description_clean") or "")
    return re.findall(r"\(\s*RE-?BID\s*\)|(?:\bLOT\b|\bPHASE\b)\s*[-#:]?\s*\w", d, re.I)


@check("supplier amount exceeds the contract amount", tables=("awards",))
def _supplier_over(row):
    try:
        total = float(row.get("total_amount") or 0)
        supp = float(row.get("supplier_amount") or 0)
    except ValueError:
        return []
    return ["over"] if supp > total + 0.01 else []


@check("table header text leaked into a description")
def _header_leak(row):
    """The rendition repeats the column headings down the page in a few plans.
    Found by reading the output, not predicted: 'Activity Reference No.' or
    'Project Implementation agency' inside a description means the record
    boundary took a header line as content."""
    d = row.get("description_clean") or ""
    return re.findall(r"(Activity Reference No\.|Description\s+Component|"
                      r"Project Implementation agency|Loan / Credit No)", d)


@check("notice with no description", tables=("notices",))
def _notice_desc(row):
    return [] if (row.get("bid_description") or "").strip() else ["empty"]


# ---------------------------------------------------------------- the profile

ENUM_FIELDS = {
    "packages": ["category", "category_raw", "method", "method_raw", "market_approach",
                 "status", "status_raw", "description_lang", "is_placeholder",
                 "is_rebid", "lot", "phase", "currency"],
    "notices": ["notice_type", "procurement_category", "description_lang",
                "is_placeholder", "country_code"],
    "awards": ["procurement_group", "method", "review_type", "description_lang",
               "is_placeholder", "currency"],
}


def load(data, table):
    path = os.path.join(data, PROC_DIR, f"{table}.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--examples", type=int, default=5)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tables = {t: load(args.data, t) for t in ("packages", "notices", "awards")}
    if any(v is None for v in tables.values()):
        raise SystemExit("audit_procurement: run src/clean_procurement.py first")

    hits = defaultdict(list)
    scope = Counter()
    for name, wanted, severity, fn in CHECKS:
        for t in wanted:
            for row in tables[t]:
                scope[name + "__scope"] += 1
                found = fn(row)
                if found:
                    key = row.get("package_id") or row.get("notice_id") or \
                        row.get("contract_id") or "?"
                    hits[name].append((t, key, str(found[:3]),
                                       (row.get("description")
                                        or row.get("bid_description") or "")[:160]))
    out = []
    out.append("procurement audit - prepared tables")
    out.append("")
    for t in ("packages", "notices", "awards"):
        out.append(f"{t:<10}{len(tables[t]):>8,} rows")
    out.append("")
    out.append(f"{'check':<48}{'hits':>8}{'% of scope':>12}  severity")
    out.append("-" * 86)
    for name, wanted, severity, _fn in CHECKS:
        n = len(hits[name])
        s = scope[name + "__scope"] or 1
        out.append(f"{name:<48}{n:>8}{100.0 * n / s:>11.2f}%  {severity}")
    out.append("")
    out.append("examples (the first few, in row order)")
    out.append("-" * 86)
    for name, wanted, severity, _fn in CHECKS:
        if not hits[name]:
            continue
        out.append(f"{name}:")
        for t, key, found, text in hits[name][:args.examples]:
            out.append(f"  [{t}] {key}  {found}")
            out.append(f"      {text}")
        out.append("")

    out.append("value profile - top twenty distinct values per enum-ish field")
    out.append("=" * 86)
    for t, fields in ENUM_FIELDS.items():
        for f in fields:
            if f not in (tables[t][0] if tables[t] else {}):
                continue
            c = Counter((r.get(f) or "").strip() for r in tables[t])
            out.append(f"\n{t}.{f}  ({len(c)} distinct)")
            for val, n in c.most_common(20):
                out.append(f"  {n:>7,}  {val[:110]}")

    report = "\n".join(out) + "\n"
    if args.out:
        path = args.out
    else:
        path = os.path.join(args.data, PROC_DIR, "audit_procurement_report.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report)
    print("\n".join(out[:40]))
    print(f"...\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
