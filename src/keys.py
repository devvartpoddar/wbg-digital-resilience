"""Join keys shared by more than one stage.

Kept apart from text_rules.py on purpose: text_rules is part of the appraisal
parser's version, so a change there re-reads every PDF. A join key is not."""
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


# "Operation ID P502532", or a table whose heading row reads "Operation ID |
# Financing Instrument | ..." and whose next row opens with the id.
_OPERATION_ID = re.compile(r"Operation ID\b[^P]{0,200}?\b(P\d{6})\b")
_DATASHEET = re.compile(r"\bDATASHEET\b")
_ANNEX = re.compile(r"^ANNEX\s+(\d+)\b", re.M)
# How far after "DATASHEET" its Operation ID is printed.
_DATASHEET_SPAN = 3000


def project_parts(text, listed):
    """Which project each stretch of a document belongs to, as a list of
    (start, end, project_ids, how) covering the whole text.

    The documents interface files a combined appraisal document under every
    operation it appraises. The IDEA document is the regional operation's
    (its first data sheet, P502532), then one annex per country, each opening
    with that country's own data sheet (DRC, Angola, Malawi), then annexes for
    the whole programme. So:

      before the second data sheet's annex   the first data sheet's operation
      each later data sheet's annex          that data sheet's operation, to
                                             the next annex heading
      after the last such annex              every listed project (programme-wide)

    A document listed under one project, or one whose data sheets name none
    of its listed projects, is the listing throughout: [(0, len, listed,
    'listed')]. A parent project and its additional financing share one paper
    that names no single operation, and stay together."""
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
    # (position, number) of each annex heading. A table inside an annex can
    # repeat the annex's title on every row, so an annex ends at the next
    # heading with a different number, not at the next repeat.
    annexes = [(m.start(), m.group(1)) for m in _ANNEX.finditer(text)]
    first_op = sheets[0][1]
    parts, cursor = [], 0
    for start, op in sheets[1:]:
        # The annex holding this data sheet opens at the last annex heading
        # before it, and runs to the next one.
        before = [(a, n) for a, n in annexes if a <= start]
        number = before[-1][1] if before else None
        opens = min([a for a, n in before if n == number and
                     not any(b[1] != number for b in before if a <= b[0])] or [start])
        closes = min([a for a, n in annexes if a > start and n != number] or [len(text)])
        if opens > cursor:
            parts.append((cursor, opens, first_op if not parts or parts[-1][2] == first_op
                          else "|".join(projects), "datasheet_operation_id"))
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
