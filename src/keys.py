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


def project_parts(text, listed):
    """Which project each stretch of a document belongs to, as a list of
    (start, end, project_ids, how) covering the whole text."""
    projects = [p for p in (listed or "").split("|") if p]
    whole = [(0, len(text or ""), "|".join(projects), "listed")]
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
    return parts


def project_at(parts, pos):
    """The project_ids of the part holding character pos."""
    for start, end, owners, _how in parts:
        if start <= pos < end:
            return owners
    return parts[-1][2] if parts else ""
