import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from dates import iso_date  # noqa: E402


def test_every_interface_shape_reads_to_iso():
    assert iso_date("2023-03-31T00:00:00Z") == "2023-03-31"
    assert iso_date("2023-03-31") == "2023-03-31"
    assert iso_date("14-Sep-2026") == "2026-09-14"
    assert iso_date("14 Sep 2026") == "2026-09-14"
    assert iso_date("10/31/2028 12:00:00 AM", "mdy") == "2028-10-31"
    assert iso_date("31/10/2028") == "2028-10-31"
    assert iso_date("12-03-24", "dmy") == "2024-03-12"


def test_nothing_unreadable_or_ambiguous_passes_through():
    assert iso_date("") == ""
    assert iso_date(None) == ""
    assert iso_date("9/14/2026") == "2026-09-14"      # only one reading fits
    assert iso_date("03/06/2024") == ""                # two readings fit
    assert iso_date("03/06/2024", "mdy") == "2024-03-06"
    assert iso_date("2024-13-01") == ""                # not a date
    assert iso_date("soon") == ""
