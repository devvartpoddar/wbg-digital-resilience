"""Linking notices and awards to plan packages (src/links.py). Invented
references and descriptions throughout."""
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import links  # noqa: E402

PACKAGES = [
    {"package_id": "P1:ab-g-1", "project_id": "P1", "borrower_ref_norm": "ab-g-1",
     "description_match": "supply of radios"},
    {"package_id": "P1:cs-indv:aaaa", "project_id": "P1", "borrower_ref_norm": "cs-indv",
     "description_match": "procurement specialist"},
    {"package_id": "P1:cs-indv:bbbb", "project_id": "P1", "borrower_ref_norm": "cs-indv",
     "description_match": "financial management specialist"},
    {"package_id": "P2:ab-g-1", "project_id": "P2", "borrower_ref_norm": "ab-g-1",
     "description_match": "supply of masts"},
]


@pytest.mark.parametrize("record,expected", [
    ({"project_id": "P1", "borrower_ref_norm": "ab-g-1"}, ("P1:ab-g-1", "reference")),
    # The same reference under another project is another package.
    ({"project_id": "P2", "borrower_ref_norm": "ab-g-1"}, ("P2:ab-g-1", "reference")),
    ({"project_id": "P1", "borrower_ref_norm": "cs-indv",
      "description_match": "financial management specialist"},
     ("P1:cs-indv:bbbb", "reference_and_description")),
    ({"project_id": "P1", "borrower_ref_norm": "cs-indv",
      "description_match": "an auditor"}, ("", "ambiguous")),
    ({"project_id": "P1", "borrower_ref_norm": "zz-9"}, ("", "not_in_plan")),
    ({"project_id": "P1", "borrower_ref_norm": ""}, ("", "no_reference")),
])
def test_each_record_links_to_one_package_or_says_why_not(record, expected):
    assert links.link_one(record, links.package_index(PACKAGES)) == expected


def test_link_all_writes_the_columns_and_counts_them():
    rows = [{"project_id": "P1", "borrower_ref_norm": "ab-g-1"},
            {"project_id": "P1", "borrower_ref_norm": ""}]
    counters = Counter()
    links.link_all(rows, PACKAGES, counters, "notices")
    assert [r["package_link"] for r in rows] == ["reference", "no_reference"]
    assert counters["notices_link_reference"] == 1


@pytest.mark.parametrize("printed,ref", [
    ("BJ-UCP / PADA-111854-CS-INDV", "PADA-111854-CS-INDV"),
    ("AA-AGENCY-555555-CW-RFB", "AA-AGENCY-555555-CW-RFB"),
    ("EDGE –IC26", "EDGE –IC26"),
    ("", ""),
])
def test_a_notice_reference_chain_gives_the_plan_reference(printed, ref):
    import plan_table
    assert plan_table.chain_ref(printed) == ref


def test_an_award_names_the_notices_of_its_reference_and_package():
    notices = [{"notice_id": "N1", "project_id": "P1", "borrower_ref_norm": "ab-g-1",
                "package_id": "P1:ab-g-1"},
               {"notice_id": "N2", "project_id": "P1", "borrower_ref_norm": "zz-9",
                "package_id": ""},
               {"notice_id": "N3", "project_id": "P1", "borrower_ref_norm": "cs-indv",
                "package_id": "P1:cs-indv:aaaa"},
               {"notice_id": "N4", "project_id": "P2", "borrower_ref_norm": "zz-9",
                "package_id": ""}]
    awards = [{"project_id": "P1", "borrower_ref_norm": "ab-g-1", "package_id": "P1:ab-g-1"},
              # Not in any plan, still meets its tender.
              {"project_id": "P1", "borrower_ref_norm": "zz-9", "package_id": ""},
              # Another package under the same generic reference: not N3.
              {"project_id": "P1", "borrower_ref_norm": "cs-indv",
               "package_id": "P1:cs-indv:bbbb"}]
    links.link_awards_to_notices(awards, notices)
    assert [a["notice_ids"] for a in awards] == ["N1", "N2", ""]
