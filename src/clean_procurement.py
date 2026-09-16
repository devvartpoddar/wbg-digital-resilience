#!/usr/bin/env python3
"""Prepare procurement text: the ten steps of methodology section 5, Stage 2b.

Reads  data/intermediate/procurement/packages_raw.csv
       data/intermediate/procurement/notices_raw.csv
       data/intermediate/procurement/awards_raw.csv
Writes data/intermediate/procurement/packages.csv
       data/intermediate/procurement/notices.csv
       data/intermediate/procurement/awards.csv
       data/intermediate/procurement/superseded_packages.csv
       data/intermediate/procurement/clean_procurement_report.txt

The rules here are deliberately the opposite of src/clean.py section 5:

    |               | clean.py (appraisal prose)  | this (procurement metadata) |
    | case          | preserved                   | folded in a matching copy   |
    | short units   | dropped (10,307 of them)    | kept - the unit IS the row  |
    | digits/codes  | stripped as footnote marks  | kept - lot 2 is not lot 1   |
    | boilerplate   | dropped                     | flagged, never dropped      |

Appraisal text is long prose recovered from page layout; this is short metadata
typed into a form by a person. The failure modes are opposite, so a rule that
helps one damages the other. Nothing here is dropped for being short.

Character normalisation is NOT reimplemented: CHAR_MAP and fold_chars come from
clean.py, because the Bank's renderers put the same Symbol and Wingdings glyphs
in both corpora and that fix cost two rounds on the appraisal side already.

The normalisers in this module are also imported by src/fetch_procurement.py, so
the key the plan-to-plan diff uses and the key V6 will use are the same function
rather than two that drift.

The only file this stage ever writes that is not derived here is nothing - every
output is rebuilt from the raw tables in one pass, so a re-run is byte-identical.
"""
import argparse, csv, hashlib, os, re, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clean import CHAR_MAP, fold_chars                  # noqa: E402  (step 1)

CLEAN_VERSION = "proc-clean-1"


PROC_DIR = os.path.join("intermediate", "procurement")

# ------------------------------------------------------------------ step 1, 2

WS_RE = re.compile(r"\s+")


def collapse(text):
    """Step 2's display string: characters folded, whitespace collapsed.

    U+FFFD is removed. It is not a character the Bank typed: the notices and
    awards interfaces return it where their own encoder lost a byte, exactly as
    the document renditions do. clean.py drops the same defect with
    errors="ignore" rather than emitting a replacement character, and the count
    is reported - see replacement_chars_removed in the clean report."""
    return WS_RE.sub(" ", fold_chars(text or "").replace("\ufffd", "")).strip()


def count_replacement_chars(text):
    return (text or "").count("\ufffd")


def casefold_key(text):
    """Step 2's matching copy. Case folding is for matching only; the published
    string is kept verbatim in `description` for display."""
    return collapse(text).casefold()


def match_key(text):
    """Step 2's matching copy with ALL whitespace removed as well as case.

    This is what Stage 8 matches asset terms against, and it exists because of
    one specific defect. Procurement plans are published only as documents, and
    the text rendition clips each table cell at the column edge. Rejoining the
    fragments is unavoidable, and where the clip landed exactly on a space that
    space is gone: the corpus carries "DataCenter" for "Data Center",
    "forZanzibar" for "for Zanzibar". Nothing in the rendition distinguishes
    that case from an ordinary mid-word clip, so the parser does not guess -
    see _join_wrapped in fetch_procurement.py.

    It does not need to, because the damage is one-directional. Gluing only ever
    DELETES a space. It never alters a character, inserts one, or reorders
    anything. So removing every space from both sides puts them back in step:

        "DataCenter"  -> "datacenter"
        "Data Center" -> "datacenter"

    and the term matches. Without this, "data center" misses a package whose
    whole purpose is data centre infrastructure, which is the kind of silent
    false negative that never shows up as an error.

    It works the same for a package awarded years ago and one still only
    planned, which matters: the alternative - taking the description from the
    linked award, where the reference joins - is clean but only available for
    packages that have already been awarded, and the forward-looking ones are
    the point of the analysis.

    The trade, stated rather than hidden: despacing can collide two genuinely
    different phrases. For multi-word technical terms that is rare, and
    audit_procurement.py counts the collisions so it stays a measured number.
    """
    return WS_RE.sub("", collapse(text).casefold())


# ---------------------------------------------------------------- step 3 and 4

# The published description sometimes carries the borrower reference in front of
# the words ("TZ-MCIT-123-CS-CQS - Supply of ..."). It has its own column, so it
# is removed from the text - but only when it is followed by real words.
REF_IN_TEXT = re.compile(
    r"^\s*([A-Z]{2}-[A-Z0-9]{1,12}-\d{2,10}-[A-Z]{2}-[A-Z0-9]{2,6})\s*[-:/\u2013]?\s*")

LOT_RE = re.compile(r"(?:\bLOTS?\b|\bLOTE)\s*(?:N[O\u00ba.\u00b0]?\s*)?"
                    r"[-#:/\u2013]?\s*([A-Z0-9]{1,4})\b", re.I)
PHASE_RE = re.compile(r"\b(?:PHASE|FASE|ETAPA|TRANCHE)\s*(?:N[O\u00ba.\u00b0]?\s*)?"
                      r"[-#:/\u2013]?\s*([A-Z0-9IVX]{1,4})\b", re.I)
REBID_RE = re.compile(r"[\(\[]?\s*\b(RE-?BID(?:DING)?|RE-?TENDER|RELANCE)\b\s*[\)\]]?"
                      r"|\(?\bREBID\b\)?", re.I)


def split_markers(description):
    """Steps 3 and 4: reference out, lot / phase / rebid into their own columns.

    'FIBER OPTIC CABLE SUPPLY - LOT 2 (REBID)' must yield a description, a lot
    and a rebid flag rather than one string - that is what V6 depends on, because
    lot 2 and lot 1 of the same package are not the same package.

    The markers are removed only from the matching copy; `description` keeps the
    published string. Digits inside the description are never touched: in this
    corpus 'LOT 2' distinguishes a package, where in an appraisal paragraph a
    digit is usually a footnote marker.
    """
    raw = collapse(description)
    text = raw
    ref = ""
    m = REF_IN_TEXT.match(text)
    if m:
        ref = m.group(1)
        text = text[m.end():].strip()
    lot = phase = ""
    m = LOT_RE.search(text)
    if m:
        lot = m.group(1)
        text = (text[:m.start()] + " " + text[m.end():]).strip()
    m = PHASE_RE.search(text)
    if m:
        phase = m.group(1)
        text = (text[:m.start()] + " " + text[m.end():]).strip()
    rebid = bool(REBID_RE.search(text))
    if rebid:
        text = REBID_RE.sub(" ", text)
    text = re.sub(r"\s*[-\u2013]\s*$", "", WS_RE.sub(" ", text)).strip(" -\u2013,.;")
    return {"description": text or raw, "ref_in_text": ref,
            "lot": lot, "phase": phase, "is_rebid": rebid}


# ------------------------------------------------------------------ step 5

SEPARATORS = re.compile(r"[^A-Z0-9]+")


def norm_ref(ref):
    """Step 5. Case, whitespace and separators, and nothing else.

    Uppercase, every run of non-alphanumerics collapsed to a single hyphen, ends
    trimmed. That recovers 'tz mcit 254784 cw rfb' and 'TZ/MCIT/254784/CW/RFB'
    as the same key while keeping the fields distinguishable - dropping the
    separators entirely would fuse a lot number into a contract number.
    """
    if not ref:
        return ""
    return SEPARATORS.sub("-", collapse(ref).upper()).strip("-")


# ------------------------------------------------------------------ step 6

# Function words that separate the four languages this corpus actually carries.
# A description is five to fifteen words, so a handful of markers is a strong
# signal and a word list would not be: nothing here translates anything.
LANG_MARKERS = {
    "en": {"the", "of", "and", "for", "supply", "services", "installation",
           "construction", "provision", "with", "to", "a", "study", "support"},
    "fr": {"de", "des", "du", "des", "et", "pour", "la", "le", "les", "d",
           "fourniture", "travaux", "recrutement", "prestation", "acquisition",
           "construction", "l", "au", "aux", "dans"},
    "es": {"de", "del", "y", "para", "la", "el", "los", "las", "suministro",
           "obras", "servicios", "consultoría", "adquisición", "con", "en",
           "construcción"},
    "pt": {"de", "do", "da", "e", "para", "a", "o", "os", "as", "fornecimento",
           "fornecimento", "obras", "serviços", "aquisição", "com", "em",
           "construção", "contratação"},
}

WORD_RE = re.compile(r"[a-záàâãéêíóôõúüçñ]+", re.I)


def detect_lang(text, default="en"):
    """Step 6. Record the language of the description; never translate it.

    Scored, not guessed: the language with the most marker hits wins, and a tie
    or no hit at all falls back to the corpus default `en`. Only the four
    languages the interface publishes in are candidates.
    """
    if not text:
        return default
    toks = [t.lower() for t in WORD_RE.findall(text)]
    if not toks:
        return default
    scores = Counter()
    for lang, markers in LANG_MARKERS.items():
        scores[lang] = sum(1 for t in toks if t in markers)
    best = scores.most_common(2)
    if not best or best[0][1] == 0:
        return default
    if len(best) > 1 and best[1][1] == best[0][1]:
        return default
    return best[0][0]


# ------------------------------------------------------------------ step 7

PLACEHOLDER_EXACT = {"tbd", "tba", "n/a", "na", "none", "nil", "unknown", "-",
                     "not applicable", "to be determined", "to be defined",
                     "tbc", "aucun", "aucune", "por definir", "a definir",
                     "à définir", "nao", "não"}
PLACEHOLDER_CATEGORY = {"goods", "works", "services", "consultancy", "consulting",
                        "non consulting services", "supply", "furniture",
                        "consultant services", "civil works", "general"}
REF_ONLY_RE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,12}-\d{2,10}-[A-Z]{2}-[A-Z0-9]{2,6}$")


def is_placeholder(description):
    """Step 7. Flag, never drop.

    A placeholder is worth flagging because a package whose description is 'TBD'
    cannot be matched to an asset by any method, and the count of those is a
    finding. It is not worth dropping, because the row still evidences that a
    package exists and moves through the plan.
    """
    s = collapse(description)
    if not s:
        return True
    key = s.casefold().strip(" .:;")
    if key in PLACEHOLDER_EXACT or key in PLACEHOLDER_CATEGORY:
        return True
    if REF_ONLY_RE.match(s.upper().replace(" ", "")):
        return True
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{2,}", s)]
    return len(words) < 2


# ------------------------------------------------------------------ step 9

STATUS_MAP = {
    "signed": "signed",
    "contract signed": "signed",
    "canceled": "cancelled", "cancelled": "cancelled", "cancelled/": "cancelled",
    "under implementation": "under_execution",
    "under execution": "under_execution",
    "under way": "under_execution",
    "implementation": "under_execution",
    "in progress": "under_execution",
    "under evaluation": "under_execution",
    "advertised": "under_execution",
    "under preparation": "under_preparation",
    "in preparation": "under_preparation",
    "planned": "planned",
    "not started": "planned",
    "pending": "planned",
    "completed": "signed",
    "contract completed": "signed",
}

METHOD_MAP = {
    "open - international": ("open_international", "request for bids"),
    "open - national": ("open_national", "request for bids"),
    "limited - international": ("other", "limited"),
    "limited - national": ("other", "limited"),
    "request for bids": ("open_national", "request for bids"),
    "request for quotations": ("request_for_quotations", "request for quotations"),
    "direct selection": ("direct_selection", "direct selection"),
    "direct contracting": ("direct_selection", "direct contracting"),
    "consultant qualification selection": ("consultant_qualification", ""),
    "consultant qualification  selection": ("consultant_qualification", ""),
    "quality and cost based selection": ("quality_cost_based", ""),
    "quality cost based": ("quality_cost_based", ""),
    "least cost selection": ("least_cost", ""),
    "individual consultant selection": ("other", "individual consultant"),
    "single source selection": ("direct_selection", "single source"),
    "framework agreement": ("other", "framework"),
    "competitive dialogue": ("other", "competitive dialogue"),
}

CATEGORY_MAP = {
    "goods": "goods", "go": "goods", "g": "goods",
    "works": "works", "cw": "works", "w": "works",
    "consulting services": "consultant_services", "consultant services":
        "consultant_services", "cs": "consultant_services",
    "consultancy": "consultant_services",
    "consulting firms": "consultant_services",
    "individual consultants": "consultant_services",
    "non consulting services": "non_consulting_services",
    "non-consulting services": "non_consulting_services",
    "non consulting service": "non_consulting_services",
    "nc": "non_consulting_services",
}


def norm_category(value):
    """Step 9 for the four-value category set (business rules A1.23)."""
    key = collapse(value).casefold().strip(" .:;")
    if key in CATEGORY_MAP:
        return CATEGORY_MAP[key]
    # STEP writes 'GO-RFB' / 'CW-RFB' inside a reference; the group letters win.
    m = re.search(r"-(GO|CW|CS|NC)-", collapse(value).upper())
    if m:
        return CATEGORY_MAP[m.group(1).lower()]
    return "unknown"


def norm_status(value):
    """Step 9 for packages.status (A1.24). Unmapped goes to `unknown`."""
    key = collapse(value).casefold().strip(" .:;")
    if not key:
        return "unknown"
    if key in STATUS_MAP:
        return STATUS_MAP[key]
    for k, v in STATUS_MAP.items():
        if key.startswith(k):
            return v
    return "unknown"


def norm_method(method_raw, market_approach=""):
    """Step 9 for packages.method and awards.method (A1.25).

    STEP splits the answer across two cells - a method name and a market
    approach - and neither alone reaches a value in the closed set: 'Request for
    Bids' is open national or open international depending on the approach cell
    beside it.
    """
    m = collapse(method_raw).casefold()
    a = collapse(market_approach).casefold()
    if a in METHOD_MAP:
        base, flavour = METHOD_MAP[a]
        if not m:
            return base
        if flavour and flavour not in m and m not in ("", "other"):
            return base if base != "other" else "other"
        return base
    if m in METHOD_MAP:
        return METHOD_MAP[m][0]
    for k, v in METHOD_MAP.items():
        if k in m:
            return v[0]
    m2 = collapse(method_raw).upper()
    if m2.endswith("-RFB") or m2.endswith("-GO-RFB") or m2.endswith("-CW-RFB"):
        return "open_national"
    if m2.endswith("-RFQ"):
        return "request_for_quotations"
    if m2.endswith("-DIR"):
        return "direct_selection"
    if m2.endswith("-QCBS"):
        return "quality_cost_based"
    if m2.endswith("-LCS"):
        return "least_cost"
    if m2.endswith("-INDV"):
        return "other"
    if m2.endswith("-QBS") or m2.endswith("-CQS"):
        return "consultant_qualification"
    return "unknown"


# ------------------------------------------------------------------ step 10

DATELINE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
                         r"\d{1,2}[- ][A-Za-z]{3}[- ]\d{4})\b")
MONEY_RE = re.compile(r"(?:US\$|USD|\$|EUR|€|GBP|£)\s?([\d.,]+)"
                      r"|([\d.,]+)\s?(?:US\$|USD|EUR|€|GBP|£)")


def desc_sha256(text):
    """Step 10: hash the description. Raw and cleaned are hashed separately, so a
    reclassification can be driven off `description` without recomputing the
    clean version, and a change to the cleaning rules is visible as a mismatch
    between the two rather than as a silent rewrite."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def extract_amount_and_date(text):
    """Amounts and dates sometimes sit inside the description string. Pull them
    out so they are not matched as text later (methodology 2b, final note)."""
    amount = ""
    m = MONEY_RE.search(text or "")
    if m:
        raw = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            amount = f"{float(raw):.2f}"
        except ValueError:
            amount = ""
    date = ""
    m = DATELINE_RE.search(text or "")
    if m:
        date = m.group(1)
    return amount, date


# ------------------------------------------------------------------ assemble

PACKAGE_COLS = [
    "package_id", "project_id", "borrower_ref", "borrower_ref_norm", "description",
    "description_clean", "description_match", "description_sha256",
    "description_lang", "lot", "phase",
    "is_rebid", "is_placeholder", "superseded_by", "clean_version", "category",
    "method", "status", "status_raw", "planned_date", "revised_date",
    "estimated_amount", "currency", "actual_amount", "plan_version",
    "plan_disclosure_date", "carried_from", "fetched_at",
]
NOTICE_COLS = [
    "notice_id", "project_id", "notice_type", "publication_date", "deadline_date",
    "bid_description", "bid_description_clean", "bid_description_match",
    "description_lang", "is_placeholder",
    "clean_version", "procurement_category", "country_code", "sector", "url",
]
AWARD_COLS = [
    "contract_id", "project_id", "borrower_ref", "borrower_ref_norm", "description",
    "description_clean", "description_match", "description_sha256",
    "description_lang", "is_placeholder",
    "clean_version", "signed_date", "no_objection_date", "total_amount", "currency",
    "procurement_group", "method", "review_type", "supplier_name", "supplier_country",
    "supplier_amount", "region", "sector",
]
SUPERSEDED_COLS = ["package_id", "project_id", "plan_version", "borrower_ref",
                   "borrower_ref_norm", "description", "status", "estimated_amount",
                   "superseded_by", "clean_version"]


def read_csv(path):
    if not os.path.exists(path):
        raise SystemExit(f"clean_procurement: {path} is missing - run "
                         f"src/fetch_procurement.py first")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def clean_packages(rows, counters):
    out = []
    for r in rows:
        raw_desc = collapse(r["description"])
        marks = split_markers(r["description"])
        clean = collapse(marks["description"])
        stored_amount = r.get("estimated_amount") or ""
        amount_in_text, date_in_text = extract_amount_and_date(raw_desc)
        if not stored_amount and amount_in_text:
            stored_amount = amount_in_text
            counters["amount_read_from_text"] += 1
        planned = r.get("planned_date") or date_in_text
        if not r.get("planned_date") and date_in_text:
            counters["date_read_from_text"] += 1
        counters["packages_in"] += 1
        out.append({
            "package_id": r["borrower_ref"],
            "project_id": r["project_id"],
            "borrower_ref": r["borrower_ref"],
            "borrower_ref_norm": norm_ref(r["borrower_ref"]),
            "description": raw_desc,
            "description_clean": clean,
            "description_match": match_key(clean),
            "description_sha256": desc_sha256(clean),
            "description_lang": r.get("description_lang") or detect_lang(clean),
            "lot": marks["lot"], "phase": marks["phase"],
            "is_rebid": str(marks["is_rebid"]).lower(),
            "is_placeholder": str(is_placeholder(clean)).lower(),
            "superseded_by": "",
            "clean_version": CLEAN_VERSION,
            "category": r.get("category") or "unknown",
            "method": r.get("method") or "unknown",
            "status": r.get("status") or "unknown",
            "status_raw": r.get("status_raw") or "",
            "planned_date": planned,
            "revised_date": r.get("revised_date") or "",
            "estimated_amount": stored_amount,
            "currency": r.get("currency") or "USD",
            "actual_amount": "",
            "plan_version": r["plan_version"],
            "fetched_at": r["fetched_at"],
            "_plan_disclosure_date": r.get("plan_disclosure_date") or "",
            "plan_disclosure_date": r.get("plan_disclosure_date") or "",
        })
    return out


# Fields carried forward from the most recent plan version that actually
# carried a value. See dedupe_packages.
CARRY_FORWARD = ("status", "status_raw", "method", "method_raw", "category",
                 "category_raw", "market_approach", "planned_date", "revised_date",
                 "estimated_amount", "currency")

# Normalised enum values that mean "this version did not tell us" rather than
# being an answer in their own right.
_NOT_AN_ANSWER = {"status": "unknown", "method": "unknown", "category": "unknown"}


def absent(field, value):
    """Did this plan version actually state a value for this field?

    A blank is absent. So is a normalised enum that fell through to `unknown`,
    because that is the parser saying it could not read the column rather than
    the borrower saying the answer is unknown. `status_raw` is deliberately not
    in that set: there it would be the borrower's own word.

    A zero amount is absent too, and that one is a judgement rather than a fact.
    In this corpus a package routinely sits at 0.00 for its first several plan
    versions before a real figure appears - CS-FIRME-9 ran eight versions at
    zero, then 200,000 - so zero reads as "not costed yet". The cost of the
    judgement is real and worth stating: a package genuinely revised DOWN to
    zero, defunded rather than un-costed, keeps its old figure. carried_from
    records which version each surviving value came from, so that case is
    visible rather than silent, and packages_raw.csv still holds every version
    verbatim.
    """
    v = (value or "").strip()
    if not v:
        return True
    if _NOT_AN_ANSWER.get(field) == v:
        return True
    if field.endswith("_amount"):
        try:
            return float(v) == 0.0
        except ValueError:
            return True
    return False


def dedupe_packages(rows, counters):
    """Step 8: one row per package, with each field taken from the most recent
    plan version that stated it, and supersession recorded.

    NOT simply the newest version's row. The newest plan is not reliably the
    best-parsed one: a package can carry an amount and a status for years and
    then appear blank in the latest plan because that plan's table was laid out
    differently and those columns were lost. Keeping the newest row wholesale
    lets a parse failure overwrite good data, which is how a known 200,000
    becomes an empty cell. On eight countries this recovered status for 729
    packages, raising it from 25% populated to 87%.

    So the row is assembled field by field. The newest version supplies the
    identity, the description and the plan_version; for every field in
    CARRY_FORWARD, if the newest version did not state it, the most recent
    version that did is used instead. A later real value always beats an
    earlier one - 2,000,000 then blank keeps 2,000,000, but 2,000,000 then
    blank then 1,000,000 keeps 1,000,000.

    `carried_from` names the plan version each carried field came from, so
    nothing is taken on trust. Fields the newest version stated itself do not
    appear there.

    Grouped on (project_id, package_id), not on package_id. The borrower
    reference is only unique within a project, and generic ones recur across
    projects - 'CS-INDV', 'GO-RFB' and 'CS-QCBS' each appear under three of the
    70, and 8 references in total are shared by two or more projects. Keyed on
    the reference alone the later project's package is folded into the earlier
    project's row and leaves no trace: one project's package vanishes, the
    other's description is overwritten by a stranger's. 10 packages were being
    lost that way.

    The superseded rows are written whole to superseded_packages.csv, each
    pointing at the version that replaced it, and counted - a supersession that
    leaves no trace is how a revised amount becomes invisible.
    """
    groups = defaultdict(list)
    for r in rows:
        groups[(r["project_id"], r["package_id"])].append(r)
    kept, superseded = [], []
    for key, versions in groups.items():
        versions.sort(key=lambda x: (x["_plan_disclosure_date"], x["plan_version"]))
        newest = dict(versions[-1])
        newest_version = newest.get("plan_version", "")

        carried = []
        for field in CARRY_FORWARD:
            if field not in newest or not absent(field, newest.get(field)):
                continue
            for older in reversed(versions[:-1]):
                if not absent(field, older.get(field)):
                    newest[field] = older[field]
                    carried.append(f"{field}:{older.get('plan_version', '')}")
                    counters[f"carried_{field}"] += 1
                    break
        newest["carried_from"] = "|".join(carried)
        if carried:
            counters["packages_with_a_carried_field"] += 1
        kept.append(newest)

        for old in versions[:-1]:
            superseded.append({
                "package_id": old["package_id"], "project_id": old["project_id"],
                "plan_version": old["plan_version"], "borrower_ref": old["borrower_ref"],
                "borrower_ref_norm": old["borrower_ref_norm"],
                "description": old["description"], "status": old["status"],
                "estimated_amount": old["estimated_amount"],
                "superseded_by": newest_version, "clean_version": CLEAN_VERSION,
            })
        if len(versions) > 1:
            counters["packages_seen_in_more_than_one_version"] += 1
            counters["package_versions_superseded"] += len(versions) - 1
    for r in kept:
        r.pop("_plan_disclosure_date", None)
    kept.sort(key=lambda r: (r["project_id"], r["package_id"], r["plan_version"]))
    superseded.sort(key=lambda r: (r["project_id"], r["package_id"], r["plan_version"]))
    counters["packages_out"] = len(kept)
    return kept, superseded


def clean_notices(rows, counters):
    out = []
    for r in rows:
        raw = collapse(r["bid_description"])
        clean = collapse(split_markers(r["bid_description"])["description"])
        counters["notices_in"] += 1
        counters["replacement_chars_removed"] += count_replacement_chars(
            r.get("bid_description"))
        out.append({
            "notice_id": r["notice_id"], "project_id": r["project_id"],
            "notice_type": r["notice_type"],
            "publication_date": r["publication_date"],
            "deadline_date": r["deadline_date"],
            "bid_description": raw, "bid_description_clean": clean,
            "bid_description_match": match_key(clean),
            "description_lang": r.get("description_lang") or detect_lang(clean),
            "is_placeholder": str(is_placeholder(clean)).lower(),
            "clean_version": CLEAN_VERSION,
            "procurement_category": r.get("procurement_category") or "unknown",
            "country_code": r.get("country_code") or "",
            "sector": r.get("sector") or "", "url": r.get("url") or "",
        })
    return out


def clean_awards(rows, counters):
    out = []
    for r in rows:
        raw = collapse(r["description"])
        clean = collapse(split_markers(r["description"])["description"])
        counters["awards_in"] += 1
        counters["replacement_chars_removed"] += count_replacement_chars(
            r.get("description"))
        out.append({
            "contract_id": r["contract_id"], "project_id": r["project_id"],
            "borrower_ref": r["borrower_ref"],
            "borrower_ref_norm": norm_ref(r["borrower_ref"]),
            "description": raw, "description_clean": clean,
            "description_match": match_key(clean),
            "description_sha256": desc_sha256(clean),
            "description_lang": r.get("description_lang") or detect_lang(clean),
            "is_placeholder": str(is_placeholder(clean)).lower(),
            "clean_version": CLEAN_VERSION,
            "signed_date": r.get("signed_date") or "",
            "no_objection_date": r.get("no_objection_date") or "",
            "total_amount": r.get("total_amount") or "",
            "currency": r.get("currency") or "USD",
            "procurement_group": r.get("procurement_group") or "unknown",
            "method": r.get("method") or "unknown",
            "review_type": r.get("review_type") or "",
            "supplier_name": collapse(r.get("supplier_name")),
            "supplier_country": r.get("supplier_country") or "",
            "supplier_amount": r.get("supplier_amount") or "",
            "region": r.get("region") or "", "sector": r.get("sector") or "",
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    args = ap.parse_args()

    proc = os.path.join(args.data, PROC_DIR)
    pkg_raw = read_csv(os.path.join(proc, "packages_raw.csv"))
    notices_raw = read_csv(os.path.join(proc, "notices_raw.csv"))
    awards_raw = read_csv(os.path.join(proc, "awards_raw.csv"))

    counters = Counter()
    packages = clean_packages(pkg_raw, counters)
    # step 9's unmapped counts, before dedup so the whole raw corpus is measured
    for field in ("status", "method", "category"):
        c = Counter(r[field] for r in packages)
        counters[f"{field}_values"] = len(c)
        counters[f"{field}_unknown"] = c.get("unknown", 0)

    kept, superseded = dedupe_packages(packages, counters)
    notices = clean_notices(notices_raw, counters)
    awards = clean_awards(awards_raw, counters)

    write_csv(os.path.join(proc, "packages.csv"), PACKAGE_COLS, kept)
    write_csv(os.path.join(proc, "notices.csv"), NOTICE_COLS, notices)
    write_csv(os.path.join(proc, "awards.csv"), AWARD_COLS, awards)
    write_csv(os.path.join(proc, "superseded_packages.csv"), SUPERSEDED_COLS, superseded)

    report = os.path.join(proc, "clean_procurement_report.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"clean_version {CLEAN_VERSION}\n\n")
        fh.write(f"packages raw rows            {counters['packages_in']:,}\n")
        fh.write(f"packages kept (latest only)  {counters['packages_out']:,}\n")
        fh.write(f"package versions superseded  {counters['package_versions_superseded']:,}\n")
        fh.write(f"packages in >1 plan version  "
                 f"{counters['packages_seen_in_more_than_one_version']:,}\n")
        fh.write(f"notices rows                 {counters['notices_in']:,}\n")
        fh.write(f"awards rows                  {counters['awards_in']:,}\n")
        fh.write(f"amounts read out of the description text: "
                 f"{counters['amount_read_from_text']:,}\n")
        fh.write(f"dates read out of the description text:   "
                 f"{counters['date_read_from_text']:,}\n")
        fh.write("\nnormalised value coverage (business rules A1.23-A1.25):\n")
        for field in ("category", "method", "status"):
            fh.write(f"  {field:<10} distinct={counters[f'{field}_values']:<5}"
                     f" unmapped={counters[f'{field}_unknown']:,}\n")
        fh.write("\nall counters:\n")
        for key in sorted(counters):
            if key.endswith("_values"):
                continue
            fh.write(f"  {key:<44}{counters[key]:,}\n")

    print(f"packages {counters['packages_out']:,} (from {counters['packages_in']:,} "
          f"rows), superseded {counters['package_versions_superseded']:,}")
    print(f"notices {counters['notices_in']:,}  awards {counters['awards_in']:,}")
    print(f"wrote {proc}/{{packages,notices,awards,superseded_packages}}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
