"""One date reader for every interface: an ISO date (YYYY-MM-DD) or blank, never a
date passed through as printed.

  iso_date("14-Sep-2026") -> "2026-09-14";  iso_date("10/31/2028", "mdy") -> "2028-10-31"
  iso_date("30/06/2024")  -> "2024-06-30";  iso_date("03/06/2024") -> "" (ambiguous)
"""
import datetime
import re

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DMON = re.compile(r"^(\d{1,2})[- ]([A-Za-z]{3})[a-z]*[- ,]+(\d{4})$")
_NUMERIC = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$")


def _valid(y, m, d):
    try:
        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return ""


def iso_date(value, order=None):
    """The ISO date in value, or ''. `order` says how a numeric date reads
    when both parts could be a month: 'mdy' (US interfaces), 'dmy'; with
    neither, an ambiguous date is blank."""
    s = str(value or "").strip()
    if not s:
        return ""
    head = s.split()[0] if " " in s and not _DMON.match(s) else s
    m = _ISO.match(head)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DMON.match(s)
    if m and m.group(2).lower() in _MONTHS:
        return _valid(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    m = _NUMERIC.match(head)
    if not m:
        return ""
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    if a > 12 and b <= 12:
        d, mo = a, b
    elif b > 12 and a <= 12:
        mo, d = a, b
    elif order == "mdy":
        mo, d = a, b
    elif order == "dmy":
        d, mo = a, b
    else:
        return ""
    return _valid(y, mo, d)
