"""The data folder's layout and the move-only migration into it."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import paths  # noqa: E402


def _touch(path, body="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)


def test_migration_moves_everything_into_its_home(tmp_path):
    d = str(tmp_path)
    for rel in ("raw/D1.txt", "raw/pdf/D1.pdf", "documents.csv", "api_fetch_log.csv",
                "clean/D1.txt", "clean/D1.layout.json", "paragraphs.csv",
                "intermediate/procurement/packages.csv",
                "intermediate/procurement/clean_procurement_report.txt", "clean_report.txt"):
        _touch(os.path.join(d, rel))
    moved, clashes = paths.migrate(d)
    assert not clashes
    for rel in ("raw/text/D1.txt", "raw/pdf/D1.pdf", "raw/documents.csv",
                "raw/fetch_log.csv", "appraisal/text/D1.txt",
                "appraisal/cache/D1.layout.json", "appraisal/paragraphs.csv",
                "procurement/packages.csv", "reports/clean_procurement.txt",
                "reports/clean.txt"):
        assert os.path.isfile(os.path.join(d, rel)), rel
    assert sorted(os.listdir(d)) == ["appraisal", "procurement", "raw", "reports"]


def test_migration_twice_does_nothing_the_second_time(tmp_path):
    d = str(tmp_path)
    _touch(os.path.join(d, "paragraphs.csv"))
    paths.migrate(d)
    assert paths.migrate(d) == ([], [])


def test_migration_never_overwrites(tmp_path):
    d = str(tmp_path)
    _touch(os.path.join(d, "paragraphs.csv"), "old")
    _touch(os.path.join(d, "appraisal", "paragraphs.csv"), "new")
    moved, clashes = paths.migrate(d)
    assert len(clashes) == 1 and not moved
    assert open(os.path.join(d, "appraisal", "paragraphs.csv")).read() == "new"
    assert open(os.path.join(d, "paragraphs.csv")).read() == "old"


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
