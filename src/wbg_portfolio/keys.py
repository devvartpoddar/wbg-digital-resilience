"""Join keys shared by more than one stage, kept out of text_rules.py so that changing
one does not change the PDF reader's version.
"""
import re
import unicodedata

_COMPONENT_PREFIX = re.compile(
    r"^(?:Component|Sub-?component)\s*\d+(?:\.\d+)?\s*[.:\-–]?\s*", re.I)


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
