"""The data folder's layout."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import paths  # noqa: E402


def test_where_names_every_home():
    assert paths.where("/d", "procurement", "packages.csv") == "/d/procurement/packages.csv"
    assert paths.where("/d", "pdf", "1.pdf") == "/d/raw/pdf/1.pdf"


def test_the_fetch_report_reads_only_columns_the_fetch_writes():
    """The text-rendition fetch was dropped while its report still read
    `has_markers`, and the box run stopped on it. Every r['...'] the report
    reads must be a column fetch.py writes."""
    import re
    import fetch
    src = open(os.path.join(ROOT, "src", "fetch.py"), encoding="utf-8").read()
    report = src[src.index("with open(report"):]
    read = set(re.findall(r"""r\[["'](\w+)["']\]""", report))
    assert read <= set(fetch.DOC_COLS), read - set(fetch.DOC_COLS)
