"""Join keys shared by several stages (src/wbg_portfolio/keys.py). Invented text and ids."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
from wbg_portfolio import keys  # noqa: E402

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


def test_a_stretch_between_country_annexes_with_no_data_sheet_is_programme_wide():
    from wbg_portfolio.keys import project_parts
    text = ("DATASHEET Operation ID P000001 regional\n"
            "ANNEX 1 country one DATASHEET Operation ID P000002 text\n"
            "ANNEX 2 something shared, no data sheet\n"
            "ANNEX 3 country two DATASHEET Operation ID P000003 text\n"
            "ANNEX 4 programme wide\n")
    parts = project_parts(text, "P000001|P000002|P000003")
    hows = [(owners, how) for _, _, owners, how in parts]
    assert hows[0] == ("P000001", "datasheet_operation_id")
    assert hows[1] == ("P000002", "datasheet_operation_id")
    assert hows[2] == ("P000001|P000002|P000003", "programme_wide")
    assert hows[3] == ("P000003", "datasheet_operation_id")
    assert hows[4] == ("P000001|P000002|P000003", "programme_wide")


CLIMATE_ANNEX = (
    "DATASHEET\nOperation ID P000003\nRegional body text.\n"
    "ANNEX 2: Republic of Aland\nDATASHEET\nOperation ID P000001\nAland's components.\n"
    "ANNEX 3: Bergia\nDATASHEET\nOperation ID P000002\nBergia's components.\n"
    "ANNEX 4: Climate action across the programme\nShared climate text.\n"
    "Aland\nAland's climate profile.\n"
    "ANNEX 4: Climate action across the programme — table row\n"
    "More on Aland.\n"
    "Bergia\nBergia's climate profile.\n"
    "ANNEX 5: Gender\nShared gender text.\n")


def test_a_country_heading_in_a_programme_wide_annex_gives_its_section_to_that_country():
    listed = "P000001|P000002|P000003"
    assert _owner(CLIMATE_ANNEX, listed, "Shared climate text") == listed
    assert _owner(CLIMATE_ANNEX, listed, "Aland's climate profile") == "P000001"
    assert _owner(CLIMATE_ANNEX, listed, "More on Aland") == "P000001"
    assert _owner(CLIMATE_ANNEX, listed, "Bergia's climate profile") == "P000002"
    assert _owner(CLIMATE_ANNEX, listed, "Shared gender text") == listed
    parts = keys.project_parts(CLIMATE_ANNEX, listed)
    assert parts[0][0] == 0 and parts[-1][1] == len(CLIMATE_ANNEX)
    assert all(a[1] == b[0] for a, b in zip(parts, parts[1:]))


def test_a_country_named_inside_a_sentence_is_not_a_heading():
    text = CLIMATE_ANNEX.replace("Aland\nAland's", "Work in Aland and Bergia goes on.\nAland's")
    listed = "P000001|P000002|P000003"
    assert _owner(text, listed, "Aland's climate profile") == listed


def test_an_appraisal_document_filed_under_another_operation_goes_to_the_one_it_names():
    text = "Front matter.\nDATASHEET\nOperation ID | Instrument\nP000009 Investment\nBody."
    assert keys.project_parts(text, "P000001", "pad") == \
        [(0, len(text), "P000009", "datasheet_other_operation")]
    # Its own operation, a contents line with no id before the data sheet: unchanged.
    own = "DATASHEET ..... i\nDATASHEET\nOperation ID P000001\nBody."
    assert keys.project_parts(own, "P000001", "pad")[0][2:] == ("P000001", "listed")
    # Only a Project Appraisal Document: a paper filed under its parent stays there.
    assert keys.project_parts(text, "P000001", "additional_financing")[0][2:] == ("P000001", "listed")
    assert keys.project_parts(text, "P000001")[0][2:] == ("P000001", "listed")
