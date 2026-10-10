#!/usr/bin/env python3
"""Clean the procurement tables: packages from the plans, notices and awards.

Reads  data/procurement/{packages_raw,notices_raw,awards_raw}.csv
       data/appraisal/components.csv   to name each package's component
       data/appraisal/text/            the appraisal prose, as a vocabulary for
                                       word repair (src/glue.py)
Writes data/procurement/{packages,notices,awards,superseded_packages}.csv
       data/reports/clean_procurement.txt

The rules here are deliberately the opposite of the appraisal side:

    |               | appraisal prose            | procurement metadata        |
    | case          | preserved                  | folded in a matching copy   |
    | short units   | dropped                    | kept - the unit IS the row  |
    | digits/codes  | stripped as footnote marks | kept - lot 2 is not lot 1   |
    | boilerplate   | dropped                    | flagged, never dropped      |

Appraisal text is long prose recovered from page layout; this is short metadata
typed into a form by a person. The failure modes are opposite, so a rule that
helps one damages the other. Nothing here is dropped for being short.

Characters are folded with the same rules as the appraisal side
(text_rules.fold_chars): the Bank's renderers put the same Symbol and Wingdings
glyphs in both corpora.

Every output is rebuilt from the raw tables in one pass, so a re-run on
unchanged input is byte-identical.
"""
import argparse, csv, hashlib, os, re, sys
import unicodedata
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402

from text_rules import fold_chars                       # noqa: E402
from keys import name_key                                 # noqa: E402
import plan_table                                        # noqa: E402
import glue                                              # noqa: E402
import links                                             # noqa: E402

CLEAN_VERSION = "proc-clean-9"

# Another package's borrower reference inside a description: the parser missed
# a record boundary and stitched the next record on. Used to cut it off here and
# to count what is left in audit_procurement.
REF_INLINE = re.compile(r"[A-Z]{2,}-[A-Z0-9]{1,12}-[0-9]{2,10}-(?:CW|GD|GO|CS|NC)-\w{2,5}"
                        r"|\b(?=[A-Z0-9-]*[0-9])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+){1,5}\s*/\s")


PROC_DIR = "procurement"


WS_RE = re.compile(r"\s+")


def collapse(text):
    """The display string: characters folded, whitespace collapsed.

    U+FFFD is removed. It is not a character the Bank typed: the notices and
    awards interfaces return it where their own encoder lost a byte, exactly as
    the document renditions do. clean.py drops the same defect with
    errors="ignore" rather than emitting a replacement character, and the count
    is reported - see replacement_chars_removed in the clean report."""
    return WS_RE.sub(" ", fold_chars(text or "").replace("\ufffd", "")).strip()


def count_replacement_chars(text):
    return (text or "").count("\ufffd")


def casefold_key(text):
    """A matching copy. Case folding is for matching only; the published
    string is kept verbatim in `description` for display."""
    return collapse(text).casefold()


def match_key(text):
    """The matching copy with ALL whitespace removed as well as case.

    This is what asset terms will be matched against, and it exists because of
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



# The published description sometimes carries the borrower reference in front of
# the words ("AA-AGENCY-123-CS-CQS - Supply of ..."). It has its own column, so it
# is removed from the text - but only when it is followed by real words.
REF_IN_TEXT = re.compile(
    r"^\s*([A-Z]{2}-[A-Z0-9]{1,12}-\d{2,10}-[A-Z]{2}-[A-Z0-9]{2,6})\s*[-:/\u2013]?\s*")

# 'Lot 2', 'Lots 1-3', 'Phase II', 'Lote 3', 'Tranche 2'. The value must look
# like a lot or phase number - digits, a Roman numeral or a single capital - so
# that 'Phase of the project' or a clipped 'Phase s' is not read as one.
_MARK_NO = r"(?:\d{1,3}|[IVX]{1,4}|[A-H])"
_MARK_RUN = (rf"{_MARK_NO}(?:\s*(?:-|\u2013|&|,|to|and|et|e|y|\u00e0|a)\s*{_MARK_NO})*"
             r"(?![\w-])")
LOT_PHASE_RE = re.compile(
    r"(?i:\b(LOTS?|LOTES?|PHASES?|FASES?|ETAPAS?|TRANCHES?))\s*"
    r"(?i:N[O\u00ba\u00b0]?\.?\s*)?[-#:/\u2013]?\s*(" + _MARK_RUN + ")")
REBID_RE = re.compile(r"[\(\[]?\s*\b(RE-?BID(?:DING)?|RE-?TENDER|RELANCE)\b\s*[\)\]]?"
                      r"|\(?\bREBID\b\)?", re.I)


def split_markers(description):
    """Steps 3 and 4: reference out, lot / phase / rebid into their own columns.

    'FIBER OPTIC CABLE SUPPLY - LOT 2 (REBID)' must yield a description, a lot
    and a rebid flag rather than one string - linking a plan package to its award
    depends on it, because
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
    marks = []
    for m in LOT_PHASE_RE.finditer(text):
        word = m.group(1).lower()
        kind = "Lot" if word.startswith("lot") else "Phase"
        plural = "s" if re.search(r"[-\u2013&,]|\b(?:to|and|et|e|y|a|\u00e0)\b", m.group(2)) else ""
        marks.append(f"{kind}{plural} {WS_RE.sub(' ', m.group(2))}")
    rebid = bool(REBID_RE.search(text))
    if rebid:
        text = REBID_RE.sub(" ", text)
    # Taken out of the text only where it opens or closes it, so what is left
    # still reads: 'Phase 1 and Phase 2 works' keeps its words.
    edge = re.compile(r"^\s*\(?(?:" + LOT_PHASE_RE.pattern + r")\)?\s*[-\u2013:.]\s*"
                      r"|\s*(?:[-\u2013:,]\s*|\()(?:" + LOT_PHASE_RE.pattern + r")\s*\)?\s*$")
    for _ in range(2):
        text = edge.sub(" ", text).strip()
    text = re.sub(r"\s*[-\u2013]\s*$", "", WS_RE.sub(" ", text)).strip(" -\u2013,.;")
    return {"description": text or raw, "ref_in_text": ref,
            "lot_or_phase": "; ".join(dict.fromkeys(marks)), "is_rebid": rebid}


# ------------------------------------------------------------ other cells

# A plan row's other cells can end up inside the description: the Loan /
# Credit number between two wrapped lines ('Rur IDA / 12345 al radio'), and on
# some layouts the component, review type, method and market approach after
# it. They are cut out here and the component is kept, in its own column.
LOAN_IN_TEXT = re.compile(r"\s*" + plan_table.LOAN_INLINE.pattern + r"\s*")
_METHOD_WORDS = (r"(?:Request for (?:Bids|Quota|Propos)|Direct Selection|Quality And Cost|"
                 r"Quality Based Selection|Consultant Qualification|Least Cost Selection|"
                 r"Individual Consult|S[eé]lection fond|S[eé]lection au moindre|"
                 r"Demande de prix|Appel d'offres|Entente directe|Passation de march|"
                 r"Consultant individuel)")
_APPROACH_WORDS = r"(?:(?:Open|Limited|Direct)\s*-\s*(?:National|Internationa)|Single Stage)"
_REVIEW_WORDS = r"(?:Post|Prior|A posteriori|A priori|Posterior|Previa|Pr[eé]via)"
CELL_TAIL = re.compile(
    rf"\s(?:{_REVIEW_WORDS}\s+(?=.*?(?:{_METHOD_WORDS}|{_APPROACH_WORDS}))"
    rf"|{_METHOD_WORDS}(?=.*?{_APPROACH_WORDS})|{_APPROACH_WORDS}|{_REVIEW_WORDS}$"
    r"|Estimated\s+Actual)")
CELL_HEAD = re.compile(
    rf"^\s*(?:(?:Single Stage\s*-\s*(?:One|Two)(?:\s*E(?:nvelope)?)?|{_APPROACH_WORDS}l?)\s*)+")
COMP_NUMBER = re.compile(r"^(?:(?:Component|Composante|Componente|Comp\.?)\s*)?"
                         r"(\d{1,2}(?:\.\d{1,2})?)\s*[.:)\-–]?\s+(?=\S)", re.I)
COMP_NAMED = re.compile(r"\b(?:Component|Composante|Componente|Comp\.)\s*"
                        r"(\d{1,2}(?:\.\d{1,2})?)\b", re.I)


def split_cells(desc):
    """(description, text of the other cells that had run into it)."""
    tail = []
    out = desc
    while True:
        m = LOAN_IN_TEXT.search(out)
        if not m:
            break
        after = out[m.end():]
        if after[:1].islower():
            out = out[:m.start()] + " " + after       # the line wrapped round it
        else:
            tail.insert(0, after)
            out = out[:m.start()]
    m = CELL_TAIL.search(out)
    if m:
        tail.insert(0, out[m.start():])
        out = out[:m.start()]
    # The same cells printed before the description on some layouts.
    m = CELL_HEAD.match(out)
    if m and out[m.end():].strip():
        tail.insert(0, out[:m.end()])
        out = out[m.end():]
    return WS_RE.sub(" ", out).strip(), WS_RE.sub(" ", " ".join(tail)).strip()


def _flat(text):
    return re.sub(r"[\W_]+", "", enum_key(text))


class Components:
    """The components of each project, from the appraisal side
    (components.csv), to name the component a procurement package belongs to.

    A restructuring can renumber components, so each name takes its number
    from the most recent document that lists it, and a match says which
    document that was: (number, name, source, doc_id), so a package joins
    components.csv on (doc_id, number). A number-only match has no document."""

    def __init__(self, rows=()):
        self.by_project = defaultdict(list)
        seen = set()
        for r in sorted(rows, key=lambda r: (r.get("disclosure_date") or ""), reverse=True):
            name = re.sub(r"^(?:Component|Sub-?component)\s*\d+(?:\.\d+)?\s*[.:\-\u2013]?\s*",
                          "", r.get("name") or "", flags=re.I).strip()
            for pid in (r.get("project_ids") or "").split("|"):
                key = (pid, name_key(name))
                if not pid or len(key[1]) < 4 or key in seen:
                    continue
                seen.add(key)
                self.by_project[pid].append((r.get("number") or "", name, key[1],
                                             r.get("doc_id") or ""))

    def _by_name(self, pid, flat, partial_ok):
        """The one component whose name the text carries, as (number, name, doc_id)."""
        best, found = 0, {}
        for num, name, f, doc in self.by_project.get(pid, ()):
            if partial_ok:
                n = len(f)
                while n >= 12 and f[:n] not in flat:
                    n -= 1
                if n < 12 or (n < len(f) and n < 15):
                    continue
            else:
                n = len(f) if f.startswith(flat) and len(flat) >= 12 else 0
                if not n:
                    continue
            if n > best:
                best, found = n, {num: (name, doc)}
            elif n == best:
                found.setdefault(num, (name, doc))
        if len(found) != 1:
            return None
        num, (name, doc) = next(iter(found.items()))
        return num, name, doc

    def strip_tail(self, pid, desc):
        """A component at the end of a description: (description, component).

        The component is (number, name, source, doc_id) or None."""
        starts = [m.start() for m in re.finditer(r"(?:(?<=\s)|^)\S", desc)]
        for p in starts[2:]:
            seg = desc[p:]
            m = COMP_NUMBER.match(seg)
            rest = seg[m.end():] if m else seg
            flat = _flat(rest)
            if len(flat) < 12:
                continue
            hit = self._by_name(pid, flat, partial_ok=False)
            if hit:
                return desc[:p].rstrip(" -\u2013,;:/("), (hit[0], hit[1], "name_match", hit[2])
            if m and re.match(r"(?:Component|Composante|Componente|Comp\.)", seg, re.I):
                return desc[:p].rstrip(" -\u2013,;:/("), (m.group(1), "", "number_only", "")
        return desc, None

    def in_cells(self, pid, cells):
        """The component a row's other cells name: by the longest stretch of a
        component's name they carry, else by the number the plan prints."""
        hit = self._by_name(pid, _flat(cells), partial_ok=True)
        if hit:
            return hit[0], hit[1], "name_match", hit[2]
        m = COMP_NAMED.search(cells) or COMP_NUMBER.match(cells)
        if not m:
            return None
        # The plan's own number. When the words after it open the name the
        # appraisal side gives that same number, it is that component; the
        # name itself is usually clipped and interleaved with other cells,
        # so it is not repeated from the plan.
        head = _flat(cells[m.end():])[:12]
        for num, name, f, doc in self.by_project.get(pid, ()):
            if num == m.group(1) and len(head) >= 8 and f.startswith(head[:min(len(head), len(f))]):
                return num, name, "name_match", doc
        return m.group(1), "", "number_only", ""


def read_components(data):
    path = where(data, "appraisal", "components.csv")
    if not os.path.exists(path):
        return Components()
    with open(path, newline="", encoding="utf-8") as fh:
        return Components(list(csv.DictReader(fh)))



SEPARATORS = re.compile(r"[^A-Z0-9]+")


def norm_ref(ref):
    """Case, whitespace and separators, and nothing else.

    Uppercase, every run of non-alphanumerics collapsed to a single hyphen, ends
    trimmed. That recovers 'tz mcit 254784 cw rfb' and 'TZ/MCIT/254784/CW/RFB'
    as the same key while keeping the fields distinguishable - dropping the
    separators entirely would fuse a lot number into a contract number.
    """
    if not ref:
        return ""
    return SEPARATORS.sub("-", collapse(ref).upper()).strip("-")



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
    """Record the language of the description; never translate it.

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



PLACEHOLDER_EXACT = {"tbd", "tba", "n/a", "na", "none", "nil", "unknown", "-",
                     "not applicable", "to be determined", "to be defined",
                     "tbc", "aucun", "aucune", "por definir", "a definir",
                     "à définir", "nao", "não"}
PLACEHOLDER_CATEGORY = {"goods", "works", "services", "consultancy", "consulting",
                        "non consulting services", "supply", "furniture",
                        "consultant services", "civil works", "general"}
REF_ONLY_RE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,12}-\d{2,10}-[A-Z]{2}-[A-Z0-9]{2,6}$")


CELL_WORDS_ONLY = re.compile(
    r"^(?:\s|post(?:erior)?|prior|open|limited|direct|national|international|"
    r"individual|single|stage|one|two|envelope|internationa|international|[-/,.()])+$", re.I)


def is_placeholder(description):
    """Flag, never drop.

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
    # The parser took the wrong cells: the 'description' is the review type,
    # method and market approach - 'Posterior Individual Open - National'.
    if CELL_WORDS_ONLY.match(s):
        return True
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{2,}", s)]
    return len(words) < 2



# The status as STEP prints it, in STEP's own English words. The plans are
# published in four languages; each foreign word is mapped to the English label
# STEP prints for the same state, so the column has one vocabulary. Nothing is
# merged: 'Pending' and 'Pending Implementation' are different STEP values and
# stay different, and 'Completed' is not folded into 'Signed'. Every key below
# was mined from the corpus, not translated at a desk; `collapse` and enum_key
# fold accents first, so the keys are ASCII ('Achevé' arrives as 'acheve').
STATUS_LABELS = ("Pending", "Pending Implementation", "Under Implementation",
                 "Under Review", "Signed", "Completed", "Canceled", "Terminated",
                 "Planned")
STATUS_MAP = {
    "pending": "Pending",
    "pendiente": "Pending",
    "pending implementation": "Pending Implementation",
    "en attente d'execution": "Pending Implementation",
    "ejecucion pendiente": "Pending Implementation",
    "implementacao pendente": "Pending Implementation",
    "under implementation": "Under Implementation",
    "en cours d'execution": "Under Implementation",
    "en ejecucion": "Under Implementation",
    "em fase de implementacao": "Under Implementation",
    "em execucao": "Under Implementation",
    "under review": "Under Review",
    "en cours d'examen": "Under Review",
    "em analise": "Under Review",
    "signed": "Signed",
    "signe": "Signed",
    "assinado": "Signed",
    "firmado": "Signed",
    "completed": "Completed",
    "acheve": "Completed",
    "concluido": "Completed",
    "finalizado": "Completed",
    "canceled": "Canceled",
    "cancelled": "Canceled",
    "annule": "Canceled",
    "cancelado": "Canceled",
    "anulado": "Canceled",
    # Signed, then ended early: its own STEP value, neither signed nor canceled.
    "terminated": "Terminated",
    "resilie": "Terminated",
    "rescindido": "Terminated",
    "planned": "Planned",
    "planifie": "Planned",
}
# A status that says the contract exists. An award in the contracts data is not
# allowed to overwrite one of these with a bare 'Signed'.
CONTRACT_EXISTS = {"Signed", "Completed", "Terminated"}

# The Bank's own procurement method codes, as STEP writes them in the borrower
# reference ('...-GO-RFB', '...-CS-QCBS'), with the name the Procurement
# Regulations give each. The plan's Method cell is mapped to the same code, so
# `method` has one vocabulary whichever of the two supplied it.
METHOD_NAMES = {
    "RFB": "Request for Bids",
    "RFQ": "Request for Quotations",
    "RFP": "Request for Proposals",
    "DIR": "Direct Selection",
    "CDS": "Direct Selection (consulting services)",
    "QCBS": "Quality and Cost-Based Selection",
    "QBS": "Quality-Based Selection",
    "FBS": "Fixed Budget Selection",
    "LCS": "Least Cost Selection",
    "CQS": "Consultant's Qualification-Based Selection",
    "INDV": "Individual Consultant Selection",
    "UN": "Procurement from United Nations agencies",
    "FA": "Framework Agreement",
}
# The Method cell's words, in the four languages the plans use. 'Direct
# Selection' is DIR for goods, works and non-consulting services and CDS for
# consulting services; that is how STEP itself numbers them, and the corpus
# bears it out (4,516 consulting references end in CDS, 103 in DIR).
METHOD_TEXT = {
    "request for bids": "RFB", "appel d'offres": "RFB",
    "request for quotations": "RFQ", "demande de prix": "RFQ",
    "request for proposals": "RFP",
    "direct selection": "DIR", "direct contracting": "DIR",
    "entente directe": "DIR", "passation de marche de gre a gre": "DIR",
    "un agencies (direct)": "UN",
    "quality and cost-based selection": "QCBS",
    "quality and cost based selection": "QCBS",
    "selection fondee sur la qualite et le cout": "QCBS",
    "quality based selection": "QBS",
    "fixed budget selection": "FBS",
    "least cost selection": "LCS", "selection au moindre cout": "LCS",
    "consultant qualification selection": "CQS",
    "consultant qualification  selection": "CQS",
    "selection fondee sur les qualifications des consultants": "CQS",
    "individual consultant selection": "INDV", "consultant individuel": "INDV",
    "individuel": "INDV",
    "framework agreement": "FA",
    # Portuguese
    "selecao baseada na qualidade e custo": "QCBS",
    "selecao baseada nas qualificacoes dos consultores": "CQS",
    "solicitacao de cotacoes": "RFQ", "solicitacao de ofertas": "RFB",
    "solicitacao de propostas": "RFP", "selecao direta": "DIR",
    "consultor individual": "INDV", "consultoria individual": "INDV",
    # Spanish
    "seleccion basada en calidad y costo": "QCBS",
    "seleccion basada en las calificaciones de los consultores": "CQS",
    "seleccion basada en el menor costo": "LCS",
    "solicitud de cotizaciones": "RFQ", "solicitud de ofertas": "RFB",
    "solicitud de propuestas": "RFP", "seleccion directa": "DIR",
}
# The code at the end of a borrower reference, including the older and
# French spellings the corpus carries. 'RF' is a clipped RFB, RFQ or RFP and
# is left unread rather than guessed.
REF_METHOD = {code: code for code in METHOD_NAMES}
REF_METHOD.update({"CI": "INDV", "IC": "INDV", "IND": "INDV", "QCB": "QCBS",
                   "SFQC": "QCBS", "SQC": "CQS", "ED": "DIR"})
REF_METHOD_RE = re.compile(r"(?:^|-)(?:CW|GO|CS|NC)-?([A-Z]+)")


def ref_method(ref):
    """The method code in a borrower reference, or ''. The reference is
    normalised first ('...- CS-CQS' reads as '...-CS-CQS'), and the code may
    run on into a suffix ('-CW-RFBREBID', '-GO-RFQ2'): the longest known code
    it opens with is taken. 'RF' alone is a clipped RFB, RFQ or RFP and stays
    unread."""
    m = REF_METHOD_RE.search(norm_ref(ref))
    if not m:
        return ""
    token = m.group(1)
    # A two-letter code must be the whole token: 'IC' is not the start of 'ICT'.
    return next((REF_METHOD[k] for k in sorted(REF_METHOD, key=lambda c: (-len(c), c))
                 if token == k or (len(k) > 2 and token.startswith(k))), "")

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
    """The four procurement categories: goods, works, consultant services and
    non-consulting services."""
    key = collapse(value).casefold().strip(" .:;")
    if key in CATEGORY_MAP:
        return CATEGORY_MAP[key]
    # STEP writes 'GO-RFB' / 'CW-RFB' inside a reference; the group letters win.
    m = re.search(r"-(GO|CW|CS|NC)-", collapse(value).upper())
    if m:
        return CATEGORY_MAP[m.group(1).lower()]
    return "unknown"


def enum_key(value):
    """The lookup key for a closed value set: collapsed, unaccented, casefolded.

    Separate from `collapse` on purpose. `collapse` produces the string that is
    stored and read by a person, and stripping the accents off a French package
    description to store it would be vandalism. Here the string is never stored
    - it exists only to find a row in STATUS_MAP or METHOD_TEXT - so folding
    'Achevé' to 'acheve' costs nothing and lets those maps stay ASCII.
    """
    stripped = unicodedata.normalize("NFKD", collapse(value))
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return stripped.replace("\u2019", "'").casefold().strip(" .:;")


def norm_status(value):
    """packages.status: the STEP label, or `unknown`, counted.

    `status_raw` keeps the borrower's own word either way."""
    key = enum_key(value)
    if not key:
        return "unknown"
    if key in STATUS_MAP:
        return STATUS_MAP[key]
    # The clipped cell can INSERT a space mid-word - 'Under Implement ation',
    # "En cours d'exécut ion" - so compare with every space removed.
    flat = key.replace(" ", "")
    flat_map = {k.replace(" ", ""): v for k, v in STATUS_MAP.items()}
    if flat in flat_map:
        return flat_map[flat]
    # A cell clipped at the column edge: "En attente d'" or 'Pending Impleme'
    # is the start of exactly one status.
    if len(flat) >= 6:
        hits = {v for k, v in flat_map.items() if k.startswith(flat)}
        if len(hits) == 1:
            return hits.pop()
    return "unknown"


def norm_method(method_raw, category=""):
    """method: the Bank's code (RFB, RFQ, QCBS, INDV ...) or
    `unknown`. Reads the Method cell's words, or a borrower reference's code
    when given one. Never 'other': a method the map does not know is unknown,
    and counted, so the map can be extended from what was actually printed."""
    code = ref_method(method_raw)
    if code:
        return code
    key = enum_key(method_raw)
    flat = re.sub(r"[\s-]+", "", key)
    code = METHOD_TEXT.get(key) or next(
        (v for k, v in METHOD_TEXT.items() if re.sub(r"[\s-]+", "", k) == flat), None)
    if code is None:
        return "unknown"
    if code == "DIR" and category == "consultant_services":
        return "CDS"
    return code


def cut_stitched(desc, own_ref, counters):
    """Drop a stitched-on record: everything from the first borrower reference
    that is not this package's own. The text before it is this package's
    description; the stitched record is another package, with its own row in
    another plan version, and does not belong here."""
    for m in REF_INLINE.finditer(desc):
        if norm_ref(m.group(0).split("/")[0].strip()) == norm_ref(own_ref):
            continue
        # A borrower reference has at least three parts; 'COVID-19 / ' has two
        # and is ordinary text.
        if len(re.split(r"[-–]", m.group(0).split("/")[0].strip())) < 3:
            continue
        # The glued-on reference may be preceded by a stray word fragment
        # ('WarehouseFM-...', 'PATNUCCM-...'); cut at the reference itself.
        start = m.start()
        head = desc[:start].rstrip(" /-;,–")
        # The front of the stitched reference can stay glued to the last word:
        # 'l'internetCG-PATN-0029', 'NetworkMV', 'Emergências1'.
        head = re.sub(r"(?<=[a-zà-ÿ).])(?:[A-Z]{2,}[A-Z0-9]*(?:[-–/][A-Z0-9]+)*|\d{1,2})$",
                      "", head).rstrip(" /-;,–")
        if len(head.split()) >= 3:
            counters["stitched_records_cut"] += 1
            return head
    return desc


# The Method cell clipped at its column edge, as it appears among a row's
# other cells: 'Request for Propo', 'Consultant Qualifi cation'. Compared with
# every space and hyphen removed, earliest in the row wins.
METHOD_PREFIX = (
    ("requestforpropo", "RFP"), ("requestforquota", "RFQ"), ("requestforbid", "RFB"),
    ("individualconsult", "INDV"), ("consultantqualifi", "CQS"),
    ("qualityandcost", "QCBS"), ("qualitybasedsel", "QBS"), ("leastcostsel", "LCS"),
    ("fixedbudgetsel", "FBS"), ("directselection", "DIR"),
    ("selectionfondeesurlaqualiteetlecout", "QCBS"),
    ("selectionfondeesurlesqualifications", "CQS"), ("selectionaumoindrecout", "LCS"),
    ("selectiondeconsultantsparententedirecte", "CDS"),
    ("demandedeprix", "RFQ"), ("appeldoffres", "RFB"), ("ententedirecte", "DIR"),
    ("passationdemarchedegreagre", "DIR"), ("consultantindividuel", "INDV"),
    ("solicituddeoferta", "RFB"), ("solicituddecotiza", "RFQ"),
    ("solicituddepropuesta", "RFP"), ("selecciondirecta", "DIR"),
    ("seleccionbasadaencalidadycosto", "QCBS"),
    ("seleccionbasadaenlascalificaciones", "CQS"),
    ("selecaobaseadanaqualidadeecusto", "QCBS"),
    ("selecaobaseadanasqualificacoes", "CQS"),
    ("solicitacaodecot", "RFQ"), ("solicitacaodeofe", "RFB"),
    ("solicitacaodepro", "RFP"), ("selecaodireta", "DIR"),
    ("consultorindividual", "INDV"), ("consultoriaindividual", "INDV"),
)


def method_in_cells(cells, category=""):
    flat = re.sub(r"[\s\-']+", "", enum_key(cells))
    hits = [(flat.find(k), code) for k, code in METHOD_PREFIX if k in flat]
    if not hits:
        return "unknown"
    code = min(hits)[1]
    return "CDS" if code == "DIR" and category == "consultant_services" else code


def resolve_method(r):
    """(method code, where it came from).

    The plan's Method cell first, because it is what the plan says now; then
    the same cell clipped at its column edge and found among the row's other
    cells ('Request for Propo'); then the code in the borrower reference, which STEP writes when the activity is
    created and which is on almost every package. Both are the Bank's own
    vocabulary, so nothing is inferred from the market approach."""
    code = norm_method(r.get("method_raw") or "", r.get("category") or "")
    if code != "unknown":
        return code, "method_cell"
    code = method_in_cells(r.get("cells_raw") or "", r.get("category") or "")
    if code != "unknown":
        return code, "clipped_method_cell"
    code = norm_method(r.get("borrower_ref") or "")
    if code != "unknown":
        return code, "reference"
    return "unknown", ""


def norm_approach(value):
    """The Market Approach cell as STEP prints it ('Open - National'), with the
    wrapped 'Open - Internationa l' put back together."""
    v = collapse(value)
    v = re.sub(r"Internationa\s+l\b", "International", v)
    return re.sub(r"\s*[-/]\s*", " - ", v).strip()



DATELINE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
                         r"\d{1,2}[- ][A-Za-z]{3}[- ]\d{4})\b")
MONEY_RE = re.compile(r"(?:US\$|USD|\$|EUR|€|GBP|£)\s?([\d.,]+)"
                      r"|([\d.,]+)\s?(?:US\$|USD|EUR|€|GBP|£)")


def desc_sha256(text):
    """SHA-256 of the cleaned description: the key a label is stored under
    (AGENTS.md rule 9), so a label follows the words, not the row."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def extract_amount_and_date(text):
    """Amounts and dates sometimes sit inside the description string. Pull them
    out so they are not matched as text later."""
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
    "description_lang", "lot_or_phase", "component_number", "component",
    "component_source", "component_doc_id",
    "glue_repairs", "is_rebid", "is_placeholder", "superseded_by", "clean_version",
    "category", "method", "method_name", "method_source", "method_raw",
    "market_approach", "status", "status_raw", "status_source", "status_as_of",
    "planned_date", "revised_date", "estimated_amount", "currency",
    "amount_source", "actual_amount",
    "plan_version", "plan_disclosure_date", "in_latest_plan", "fetched_at",
]
NOTICE_COLS = [
    "notice_id", "project_id", "notice_type", "notice_status", "publication_date",
    "deadline_date", "borrower_ref", "borrower_ref_norm", "description",
    "description_clean", "description_match", "description_sha256", "description_lang",
    "is_placeholder", "glue_repairs", "category", "method", "method_name",
    "country_name", "package_id", "package_link", "clean_version",
]
AWARD_COLS = [
    "contract_id", "project_id", "borrower_ref", "borrower_ref_norm", "description",
    "description_clean", "description_match", "description_sha256",
    "description_lang", "is_placeholder", "glue_repairs",
    "clean_version", "signed_date", "no_objection_date", "total_amount", "currency",
    "category", "method", "method_name", "review_type", "supplier_name",
    "supplier_country", "supplier_amount", "region", "sector",
    "package_id", "package_link", "notice_ids",
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


def clean_description(text, vocab, names, lang, own_ref, counters, comps=None, pid=""):
    """The order matters: other cells out, words repaired, then the lot or
    phase read - a marker read before the repair takes a broken word's last
    letters for a lot number ('Phase s').

    Returns (clean text, repairs, markers, cells text, component or None)."""
    text, cells = split_cells(collapse(text))
    if cells:
        counters["descriptions_with_other_cells_cut"] += 1
    text, repairs = glue.repair(text, vocab, names, lang)
    marks = split_markers(text)
    text = cut_stitched(collapse(marks["description"]), own_ref, counters)
    comp = None
    if comps is not None:
        text, comp = comps.strip_tail(pid, text)
        if comp:
            counters["component_cut_from_description"] += 1
    return text, repairs, marks, cells, comp


def clean_packages(rows, counters, vocab=None, names=None, comps=None):
    vocab = vocab or {}
    comps = comps or Components()
    out = []
    for r in rows:
        raw_desc = collapse(r["description"])
        lang = r.get("description_lang") or detect_lang(raw_desc)
        pid = r["project_id"]
        clean, repairs, marks, cells, comp = clean_description(
            r["description"], vocab, names, lang, r.get("borrower_ref") or "",
            counters, comps, pid)
        counters["glue_repairs_packages"] += repairs
        if not comp:
            comp = comps.in_cells(pid, " ".join(
                x for x in (cells, r.get("cells_raw") or "") if x))
        number, comp_name, comp_source, comp_doc = comp or ("", "", "", "")
        counters[f"component_source_{comp_source or 'none'}"] += 1
        stored_amount = r.get("estimated_amount") or ""
        amount_source = "plan" if stored_amount else ""
        amount_in_text, date_in_text = extract_amount_and_date(raw_desc)
        if not stored_amount and amount_in_text:
            stored_amount = amount_in_text
            amount_source = "description"
            counters["amount_read_from_text"] += 1
        planned = r.get("planned_date") or date_in_text
        if not r.get("planned_date") and date_in_text:
            counters["date_read_from_text"] += 1
        counters["packages_in"] += 1
        category = norm_category(r.get("category_raw") or "")
        method, method_source = resolve_method(dict(r, category=category))
        out.append({
            "package_version_id": r.get("package_version_id") or "",
            "project_id": pid,
            "borrower_ref": r["borrower_ref"],
            "borrower_ref_norm": norm_ref(r["borrower_ref"]),
            "description": raw_desc,
            "description_clean": clean,
            "description_match": match_key(clean),
            "description_sha256": desc_sha256(clean),
            "description_lang": lang,
            "lot_or_phase": marks["lot_or_phase"],
            "component_number": number,
            "component": comp_name,
            "component_source": comp_source, "component_doc_id": comp_doc,
            "glue_repairs": repairs,
            "is_rebid": str(marks["is_rebid"]).lower(),
            "is_placeholder": str(is_placeholder(clean)).lower(),
            "superseded_by": "",
            "clean_version": CLEAN_VERSION,
            "category": category,
            "method": method,
            "method_name": METHOD_NAMES.get(method, ""),
            "method_source": method_source,
            "method_raw": r.get("method_raw") or "",
            "market_approach": norm_approach(r.get("market_approach") or ""),
            # Re-mapped here from the borrower's own word, so a change to
            # STATUS_MAP needs a re-clean, not a re-fetch.
            "status": norm_status(r.get("status_raw") or ""),
            "status_raw": r.get("status_raw") or "",
            "planned_date": planned,
            "revised_date": r.get("revised_date") or "",
            "estimated_amount": stored_amount,
            "currency": r.get("currency") or "USD",
            "amount_source": amount_source,
            "actual_amount": r.get("actual_amount") or "",
            "plan_version": r["plan_version"],
            "fetched_at": r["fetched_at"],
            "_plan_disclosure_date": r.get("plan_disclosure_date") or "",
            "plan_disclosure_date": r.get("plan_disclosure_date") or "",
        })
    return out


def dedupe_packages(rows, counters):
    """One row per package: the newest plan version's row, whole.

    Nothing is carried from an older version, neither status nor amount. A
    value is a statement a plan made on its date; filling a field the newest
    plan left blank with an older plan's figure would put a statement on today
    that no current plan makes. Every version stays in packages_raw.csv and
    the older ones in superseded_packages.csv, so the history is not lost.

    Grouped on (project_id, normalised reference), not on the reference alone. The borrower
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
    # The key is the normalised reference ('EDGE- G13' and 'EDGE-G13' are one
    # package). A generic reference that one plan uses for several packages
    # ('CS-INDV' for every individual consultant) also takes the description's
    # matching copy, so those packages are not folded into one another.
    per_version = Counter((r["project_id"], r["plan_version"], r["borrower_ref_norm"])
                          for r in rows)
    shared = {(p, ref) for (p, _v, ref), n in per_version.items() if n > 1}
    # A plan that prints the same reference AND description twice with
    # different figures, status or dates lists two packages (EDGE-G1A at 13.0m
    # and at 13.06m): the second and later in a version are told apart by
    # their order in it, so neither is lost behind the other. A row repeated
    # identically in the same version is one package printed twice and is
    # dropped, so its value is not counted twice.
    occurrence = Counter()
    printed = set()
    groups = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["project_id"], r["plan_version"],
                                         r.get("package_version_id") or "")):
        same = (r["project_id"], r["plan_version"], r["borrower_ref_norm"],
                r.get("description_match"), r.get("estimated_amount"), r.get("actual_amount"),
                r.get("status"), r.get("planned_date"), r.get("method"), r.get("category"))
        if same in printed:
            counters["package_rows_printed_twice_in_a_version"] += 1
            continue
        printed.add(same)
        key = (r["project_id"], r["borrower_ref_norm"])
        suffix = ""
        if key in shared:
            key += (r["description_match"],)
            suffix = ":" + hashlib.sha256(key[2].encode()).hexdigest()[:8]
        occurrence[(r["plan_version"],) + key] += 1
        n = occurrence[(r["plan_version"],) + key]
        if n > 1:
            key += (f"#{n}",)
            suffix += f":{n}"
            counters["packages_repeated_within_a_version"] += 1
        r["package_id"] = ":".join(key[:2]) + suffix
        groups[key].append(r)
    # The project's latest plan: every plan version disclosed on its most
    # recent disclosure date. A package not in it was dropped from the plan,
    # or the latest plan was published in parts; in_latest_plan says which
    # packages the latest plan still lists, and nothing is decided on it here.
    latest_date = {}
    for r in rows:
        d = r["_plan_disclosure_date"]
        if d > latest_date.get(r["project_id"], ""):
            latest_date[r["project_id"]] = d
    kept, superseded = [], []
    for key, versions in groups.items():
        versions.sort(key=lambda x: (x["_plan_disclosure_date"], x["plan_version"]))
        newest = dict(versions[-1])
        newest_version = newest.get("plan_version", "")
        newest["in_latest_plan"] = str(
            newest["_plan_disclosure_date"] == latest_date[newest["project_id"]]).lower()
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
        r["_disclosed"] = r.pop("_plan_disclosure_date", None) or ""
    kept.sort(key=lambda r: (r["project_id"], r["package_id"], r["plan_version"]))
    superseded.sort(key=lambda r: (r["project_id"], r["package_id"], r["plan_version"]))
    counters["packages_out"] = len(kept)
    return kept, superseded


def resolve_status(kept, awards, counters):
    """Give every package the best-evidenced status there is, and say where it
    came from and as of when.

    Two sources, the more recent dated evidence winning:

      plan   the newest plan version's own status, as of its disclosure
      award  a signed contract in the awards data linked to the package
             (links.py), as of signing

    An older plan version's status is never used: it is a fact about that
    plan's date, not about now. With neither source the status stays unknown.
    """
    signed = {}
    for a in awards:
        d = a.get("signed_date") or ""
        if a.get("package_id") and d > signed.get(a["package_id"], ""):
            signed[a["package_id"]] = d
    for r in kept:
        cands = []
        if r["status"] != "unknown":
            cands.append((r.get("_disclosed", ""), r["status"], "plan"))
        d = signed.get(r["package_id"])
        if d and r["status"] not in CONTRACT_EXISTS:
            cands.append((d, "Signed", "award"))
        if cands:
            when, st, src = max(cands)
            r["status"], r["status_source"], r["status_as_of"] = st, src, when
        else:
            r["status_source"], r["status_as_of"] = "none", ""
        counters[f"status_source_{r['status_source']}"] += 1
        r.pop("_disclosed", None)


def clean_notices(rows, counters, vocab=None, names=None):
    vocab = vocab or {}
    out = []
    for r in rows:
        raw = collapse(r["description"])
        lang = detect_lang(raw)
        clean, repairs, _, _, _ = clean_description(
            r["description"], vocab, names, lang, r.get("borrower_ref") or "", counters)
        counters["glue_repairs_notices"] += repairs
        counters["notices_in"] += 1
        counters["replacement_chars_removed"] += count_replacement_chars(r["description"])
        category = norm_category(r.get("category_raw") or "")
        # The notice interface gives the Bank's method code directly.
        method = r.get("method_code") or norm_method(r.get("method_raw") or "", category)
        out.append({
            "notice_id": r["notice_id"], "project_id": r["project_id"],
            "notice_type": r["notice_type"], "notice_status": r.get("notice_status") or "",
            "publication_date": r["publication_date"],
            "deadline_date": r["deadline_date"],
            "borrower_ref": r.get("borrower_ref") or "",
            "borrower_ref_norm": norm_ref(plan_table.chain_ref(r.get("borrower_ref"))),
            "description": raw, "description_clean": clean,
            "description_match": match_key(clean),
            "description_sha256": desc_sha256(clean),
            "description_lang": lang,
            "is_placeholder": str(is_placeholder(clean)).lower(),
            "glue_repairs": repairs,
            "category": category,
            "method": method or "unknown",
            "method_name": METHOD_NAMES.get(method, ""),
            "country_name": r.get("country_name") or "",
            "clean_version": CLEAN_VERSION,
        })
    return out


def clean_awards(rows, counters, vocab=None, names=None):
    vocab = vocab or {}
    out = []
    for r in rows:
        raw = collapse(r["description"])
        lang = detect_lang(raw)
        clean, repairs, _, _, _ = clean_description(
            r["description"], vocab, names, lang, r.get("borrower_ref") or "", counters)
        counters["glue_repairs_awards"] += repairs
        counters["awards_in"] += 1
        counters["replacement_chars_removed"] += count_replacement_chars(r["description"])
        category = norm_category(r.get("category_raw") or "")
        method = norm_method(r.get("method_raw") or "", category)
        out.append({
            "contract_id": r["contract_id"], "project_id": r["project_id"],
            "borrower_ref": r["borrower_ref"],
            "borrower_ref_norm": norm_ref(plan_table.chain_ref(r["borrower_ref"])),
            "description": raw, "description_clean": clean,
            "description_match": match_key(clean),
            "description_sha256": desc_sha256(clean),
            "description_lang": lang,
            "is_placeholder": str(is_placeholder(clean)).lower(),
            "glue_repairs": repairs,
            "clean_version": CLEAN_VERSION,
            "signed_date": r.get("signed_date") or "",
            "no_objection_date": r.get("no_objection_date") or "",
            "total_amount": r.get("total_amount") or "",
            "currency": r.get("currency") or "USD",
            "category": category,
            "method": method,
            "method_name": METHOD_NAMES.get(method, ""),
            "review_type": r.get("review_type") or "",
            "supplier_name": collapse(r.get("supplier_name")),
            "supplier_country": r.get("supplier_country") or "",
            "supplier_amount": r.get("supplier_amount") or "",
            "region": r.get("region") or "", "sector": r.get("sector") or "",
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()

    proc = where(args.data, "procurement")
    pkg_raw = read_csv(os.path.join(proc, "packages_raw.csv"))
    notices_raw = read_csv(os.path.join(proc, "notices_raw.csv"))
    awards_raw = read_csv(os.path.join(proc, "awards_raw.csv"))

    counters = Counter()
    vocab, names = glue.corpus_vocab(
        [r["description"] for r in pkg_raw]
        + [r["description"] for r in notices_raw]
        + [r["description"] for r in awards_raw], args.data)
    comps = read_components(args.data)
    packages = clean_packages(pkg_raw, counters, vocab, names, comps)
    kept, superseded = dedupe_packages(packages, counters)
    notices = clean_notices(notices_raw, counters, vocab, names)
    awards = clean_awards(awards_raw, counters, vocab, names)
    links.link_all(notices, kept, counters, "notices")
    links.link_all(awards, kept, counters, "awards")
    links.link_awards_to_notices(awards, notices, counters)
    resolve_status(kept, awards, counters)
    # Coverage of the closed value sets, on the packages as written.
    for field in ("status", "method", "category"):
        c = Counter(r[field] for r in kept)
        counters[f"{field}_values"] = len(c)
        counters[f"{field}_unknown"] = c.get("unknown", 0)

    write_csv(os.path.join(proc, "packages.csv"), PACKAGE_COLS, kept)
    write_csv(os.path.join(proc, "notices.csv"), NOTICE_COLS, notices)
    write_csv(os.path.join(proc, "awards.csv"), AWARD_COLS, awards)
    write_csv(os.path.join(proc, "superseded_packages.csv"), SUPERSEDED_COLS, superseded)

    report = where(args.data, "reports", "clean_procurement.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"clean_version {CLEAN_VERSION}\n\n")
        fh.write(f"packages raw rows            {counters['packages_in']:,}\n")
        fh.write(f"packages (one row each)      {counters['packages_out']:,}\n")
        fh.write(f"package versions superseded  {counters['package_versions_superseded']:,}\n")
        fh.write(f"packages in >1 plan version  "
                 f"{counters['packages_seen_in_more_than_one_version']:,}\n")
        fh.write(f"notices rows                 {counters['notices_in']:,}\n")
        fh.write(f"awards rows                  {counters['awards_in']:,}\n")
        fh.write(f"amounts read out of the description text: "
                 f"{counters['amount_read_from_text']:,}\n")
        fh.write(f"dates read out of the description text:   "
                 f"{counters['date_read_from_text']:,}\n")
        fh.write("\nvalue coverage on the packages written:\n")
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
