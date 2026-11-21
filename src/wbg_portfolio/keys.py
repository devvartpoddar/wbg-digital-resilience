"""Join keys shared by more than one stage, kept out of text_rules.py so that changing
one does not change the PDF reader's version.
"""
import re
import unicodedata

# A component's number as a document prints it: 2, 2.1, or lettered (A, B.1, A1) or roman
# (II, III). A lone capital letter or a run of I, V, X only: "Component ICT" has no number.
_COMPONENT_PREFIX = re.compile(
    r"^(?:Component|Sub-?component)\s*(?:\d+(?:\.\d+)?|(?-i:[A-Z]|[IVX]{2,4})(?:\.?\d+)?"
    r"(?![A-Za-z]))\s*[.:\-–]?\s*", re.I)
COMPONENT_PREFIX = _COMPONENT_PREFIX

ROMAN = re.compile(r"^X{0,3}(?:IX|IV|V?I{0,3})$")
_ROMAN = ROMAN


def normal_number(number):
    """A component number in one spelling: 'A1' is 'A.1'; letters upper case."""
    n = (number or "").strip().rstrip(".")
    m = re.match(r"^([A-Za-z]{1,4})\.?(\d{1,2})$", n)
    if m:
        return f"{m.group(1).upper()}.{m.group(2)}"
    return n.upper() if re.match(r"^[A-Za-z]{1,4}$", n) else n


def uses_roman(numbers):
    """Whether a document's component numbers are roman (II, III, IV...) rather than
    letters (A, B, C...): decided by the document's own sequence. A lone I, V or X is
    ambiguous by itself; a number of two or more such letters ('II', 'IV') is not."""
    return any(len(p) > 1 and p.isalpha() and _ROMAN.match(p)
               for n in numbers for p in (n or "").split("."))


def _roman_value(s):
    vals, total = {"I": 1, "V": 5, "X": 10}, 0
    for a, b in zip(s, s[1:] + " "):
        total += -vals[a] if vals.get(b, 0) > vals[a] else vals[a]
    return total


# The top-level component of a plan's or an appraisal document's component, as published on a link
# (decide.link_strength_rows): a number ('Component 2.1', '2b', 'II', 'IIa', 'iii)' -> '2', '2', '2', '2',
# '3') or a letter A to H ('D', 'C1', 'Component C.1', '(a)', 'Component (b)', 'Part A' -> 'D', 'C', 'C',
# 'A', 'B', 'A'); '' when there is none. A roman numeral is a number.
_TOP_WORD = re.compile(r"^(?:component|composante|componente|sub-?component|part)\s*", re.I)
_TOP_DIGITS = re.compile(r"(\d+)")
_TOP_ROMAN = re.compile(r"([ivx]+)[a-h]?(?![a-z])", re.I)
_TOP_LETTER = re.compile(r"([a-h])(?![a-z])", re.I)


def top_component(x):
    """The top-level component of a component number or heading: a number as digits, or a letter A
    to H in capitals; '' when there is none (see _TOP_WORD and the examples above)."""
    t = _TOP_WORD.sub("", (x or "").strip()).lstrip("( ")
    m = _TOP_DIGITS.match(t)
    if m:
        return str(int(m.group(1)))
    m = _TOP_ROMAN.match(t)
    if m and ROMAN.match(m.group(1).upper()):
        return str(_roman_value(m.group(1).upper()))
    m = _TOP_LETTER.match(t)
    return m.group(1).upper() if m else ""


def component_relation(a, b):
    """same or different when both top-level components are known and of one scheme (both numbers or
    both letters); unknown when either is missing, or when one side is lettered and the other numbered
    (a plan's 'Component A' may well be the document's 1)."""
    if not a or not b or a.isdigit() != b.isdigit():
        return "unknown"
    return "same" if a == b else "different"


# Two component names (name_key) are one component when they are equal, or one starts the other and the
# shorter has at least this many characters (a restructuring paper shortens a heading; a short key would
# match too much). The views' wbg.same_component reads the same number, put in at publish.
SAME_COMPONENT_MIN_CHARS = 12


def same_component(a, b):
    """Whether two name keys name one component (SAME_COMPONENT_MIN_CHARS)."""
    short, long_ = sorted((a or "", b or ""), key=len)
    return bool(short) and (short == long_ or (len(short) >= SAME_COMPONENT_MIN_CHARS and long_.startswith(short)))


def number_key(number, roman=False):
    """Sort key for a component number of any kind: numbers by value, roman numerals
    by value when the document is roman, letters alphabetically after numbers."""
    out = []
    for part in (number or "").split("."):
        if part.isdigit():
            out.append((0, int(part), ""))
        elif roman and part.isalpha() and _ROMAN.match(part):
            out.append((0, _roman_value(part), ""))
        else:
            out.append((1, 0, part))
    return out


def name_key(name):
    """The key two printings of one component name share: its "Component N:"
    prefix dropped, accents, case and everything but letters and digits removed.
    'Sub-component 2.1: Data Centre' and 'Data centre' give 'datacentre'.
    components.csv carries it, and procurement matches component names on it."""
    text = _COMPONENT_PREFIX.sub("", " ".join((name or "").split()))
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", "", text.casefold())


# "Operation ID P502532", or a table whose heading row reads "Operation ID | Financing
# Instrument | ..." and whose next row opens with the id.
_OPERATION_ID = re.compile(r"Operation ID\b[^P]{0,200}?\b(P\d{6})\b")
_DATASHEET = re.compile(r"\bDATASHEET\b")
_ANNEX = re.compile(r"^ANNEX\s+(\d+)\b", re.M)
# How far after "DATASHEET" its Operation ID is printed.
_DATASHEET_SPAN = 3000


def project_parts(text, listed, kind=None):
    """Which project each stretch of a document belongs to, as a list of
    (start, end, project_ids, how) covering the whole text. kind is the
    document's kind ('pad', ...), when the caller knows it."""
    projects = [p for p in (listed or "").split("|") if p]
    whole = [(0, len(text or ""), "|".join(projects), "listed")]
    if kind == "pad" and len(projects) == 1 and text:
        # The documents interface can file one operation's appraisal document under
        # another (40114862, Mozambique's P516379, is listed under Madagascar's P507944).
        # An appraisal document's first data sheet names its own operation: when that is
        # not the one project it is listed under, the document is the named operation's.
        # Only a Project Appraisal Document: an additional-financing paper filed under
        # its parent names its own id and stays with the parent.
        sheet = _DATASHEET.search(text)
        while sheet:
            op = _OPERATION_ID.search(text, sheet.start(), sheet.start() + _DATASHEET_SPAN)
            if op:
                if op.group(1) != projects[0]:
                    return [(0, len(text), op.group(1), "datasheet_other_operation")]
                break
            sheet = _DATASHEET.search(text, sheet.end())
    if len(projects) < 2 or not text:
        return whole
    sheets = []
    for m in _DATASHEET.finditer(text):
        op = _OPERATION_ID.search(text, m.start(), m.start() + _DATASHEET_SPAN)
        if op and op.group(1) in projects and (not sheets or sheets[-1][1] != op.group(1)):
            sheets.append((m.start(), op.group(1)))
    if not sheets:
        return whole
    # (position, number) of each annex heading.
    annexes = [(m.start(), m.group(1)) for m in _ANNEX.finditer(text)]
    first_op = sheets[0][1]
    parts, cursor = [], 0
    for start, op in sheets[1:]:
        # The annex holding this data sheet opens at the last annex heading before it, and
        # runs to the next one.
        before = [(a, n) for a, n in annexes if a <= start]
        number = before[-1][1] if before else None
        opens = min([a for a, n in before if n == number and
                     not any(b[1] != number for b in before if a <= b[0])] or [start])
        closes = min([a for a, n in annexes if a > start and n != number] or [len(text)])
        if opens > cursor:
            # Before the first country annex the text is the first data sheet's operation; a
            # stretch between two country annexes that holds no data sheet belongs to the
            # programme as a whole.
            if not parts or parts[-1][2] == first_op:
                parts.append((cursor, opens, first_op, "datasheet_operation_id"))
            else:
                parts.append((cursor, opens, "|".join(projects), "programme_wide"))
        parts.append((opens, closes, op, "datasheet_operation_id"))
        cursor = closes
    if cursor < len(text):
        tail_owner = first_op if len(sheets) == 1 else "|".join(projects)
        parts.append((cursor, len(text), tail_owner,
                      "datasheet_operation_id" if len(sheets) == 1 else "programme_wide"))
    # The title of each country annex ("ANNEX 5: Republic of Malawi") names its operation.
    titles = {}
    for start, end, owners, how in parts:
        if how == "datasheet_operation_id" and owners != first_op:
            heading = _ANNEX_LINE.match(text, start)
            if heading:
                titles[owners] = heading.group(2)
    out = []
    for part in parts:
        out.extend(_country_sections(text, part, titles, annexes)
                   if part[3] == "programme_wide" else [part])
    return out


_ANNEX_LINE = re.compile(r"ANNEX\s+(\d+)\s*[:.\-–]?\s*([^\n]*)")
# A heading line inside a programme-wide annex that names one country ("Malawi"); longer
# lines are prose or table rows, not headings.
_HEADING_MAX_CHARS = 60


def _norm(s):
    return " ".join(re.sub(r"[^\w\s]", " ", s.casefold()).split())


def _names_country(line, title):
    """Whether a line is the country of an annex title: the title itself, or its last
    words ("Malawi" for "Republic of Malawi")."""
    line, title = _norm(line), _norm(title)
    return len(line) >= 4 and (line == title or title.endswith(" " + line))


def _country_sections(text, part, titles, annexes):
    """A programme-wide stretch split at heading lines that name one country annex's
    country (an annex on climate action with a section per country): each section belongs
    to that country's operation, up to the next such heading or the next annex; the rest
    stays programme-wide."""
    start, end, owners, how = part
    if not titles:
        return [part]
    cuts = []                              # (position, owner, how)
    section_annex = None                   # the annex number a country section sits in
    pos = start
    for line in text[start:end].split("\n"):
        stripped = line.strip()
        annex = _ANNEX.match(stripped)
        if annex:
            # A new annex ends a country section; a table row repeating the same annex's
            # title does not.
            if section_annex is not None and annex.group(1) != section_annex:
                cuts.append((pos, owners, how))
                section_annex = None
        elif stripped and len(stripped) <= _HEADING_MAX_CHARS:
            hit = [op for op, title in titles.items() if _names_country(stripped, title)]
            if len(hit) == 1:
                cuts.append((pos, hit[0], "country_heading"))
                before = [n for a, n in annexes if a <= pos]
                section_annex = before[-1] if before else ""
        pos += len(line) + 1
    if not cuts:
        return [part]
    out, cursor, owner, label = [], start, owners, how
    for at, new_owner, new_how in cuts:
        if (new_owner, new_how) == (owner, label):
            continue
        if at > cursor:
            out.append((cursor, at, owner, label))
        cursor, owner, label = at, new_owner, new_how
    out.append((cursor, end, owner, label))
    return out


def project_at(parts, pos):
    """The project_ids of the part holding character pos."""
    for start, end, owners, _how in parts:
        if start <= pos < end:
            return owners
    return parts[-1][2] if parts else ""
