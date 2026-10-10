"""Join keys shared by several stages (src/keys.py). Invented text and ids."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import keys  # noqa: E402

COMBINED = (
    "DATASHEET\nOperation ID P000003\nRegional body text.\n"
    "ANNEX 1: Results Framework for the regional operation\n"
    "ANNEX 2: Aland\nDATASHEET\nOperation ID P000001\nAland's components.\n"
    "ANNEX 3: Bergia\nDATASHEET\nOperation ID P000002\nBergia's components.\n"
    "ANNEX 4: Climate action across the programme\nProgramme-wide text.\n")


def _owner(text, listed, phrase):
    return keys.project_at(keys.project_parts(text, listed), text.index(phrase))


def test_a_combined_document_is_split_at_its_data_sheets():
    listed = "P000001|P000002|P000003"
    assert _owner(COMBINED, listed, "Regional body") == "P000003"
    assert _owner(COMBINED, listed, "Results Framework") == "P000003"
    assert _owner(COMBINED, listed, "ANNEX 2: Aland") == "P000001"
    assert _owner(COMBINED, listed, "Aland's components") == "P000001"
    assert _owner(COMBINED, listed, "Bergia's components") == "P000002"
    assert _owner(COMBINED, listed, "Programme-wide") == listed


def test_the_parts_cover_the_whole_text_in_order():
    parts = keys.project_parts(COMBINED, "P000001|P000002|P000003")
    assert parts[0][0] == 0 and parts[-1][1] == len(COMBINED)
    assert all(a[1] == b[0] for a, b in zip(parts, parts[1:]))


def test_one_data_sheet_gives_the_whole_document_to_its_operation():
    text = "DATASHEET\nOperation ID P000002\nBody."
    assert keys.project_parts(text, "P000001|P000002") == \
        [(0, len(text), "P000002", "datasheet_operation_id")]


def test_without_a_matching_data_sheet_the_listing_stands():
    # A parent project and its additional financing: no single operation.
    text = "BASIC INFORMATION - PARENT (P000001)\nBody."
    assert keys.project_parts(text, "P000001|P000002") == \
        [(0, len(text), "P000001|P000002", "listed")]
    other = "DATASHEET\nOperation ID P000009\nBody."
    assert keys.project_parts(other, "P000001|P000002")[0][3] == "listed"


def test_name_key_ignores_number_case_and_punctuation():
    assert keys.name_key("Sub-component 2.1: Data Centre") == keys.name_key("data centre")


def test_an_annex_title_repeated_on_table_rows_does_not_end_the_annex():
    text = ("DATASHEET\nOperation ID P000003\nBody.\n"
            "ANNEX 2: Aland\nDATASHEET\nOperation ID P000001\n"
            "ANNEX 2: Aland — Component 1: Masts · 10.00\n"
            "ANNEX 2: Aland — Component 2: Radios · 5.00\n"
            "ANNEX 3: Programme\nShared.\n")
    assert _owner(text, "P000001|P000003", "Component 2: Radios") == "P000001"
    assert _owner(text, "P000001|P000003", "Shared.") == "P000001|P000003"
