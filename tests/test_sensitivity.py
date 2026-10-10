"""The sensitivity report's fast checks (src/sensitivity.py) on an invented store."""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import sensitivity  # noqa: E402
from clean import PARA_COLS  # noqa: E402

TEXT = ("Component 1: Rural radio masts\n"
        "The masts will be raised on high ground in five districts. They will carry radios.\n"
        "Project beneficiaries\n"
        "Farmers in the five districts will hear the forecasts set out in Fig. 4 of the annex.\n")


def _store(tmp_path):
    data = tmp_path / "data"
    (data / "appraisal" / "text").mkdir(parents=True)
    (data / "appraisal" / "text" / "D1.txt").write_text(TEXT, encoding="utf-8")
    rows, pos = [], 0
    for n, (line, block, path) in enumerate([
            (TEXT.splitlines()[0], "heading", "II.B"),
            (TEXT.splitlines()[1], "narrative", "II.B"),
            (TEXT.splitlines()[2], "heading", "II.C"),
            (TEXT.splitlines()[3], "narrative", "II.C")], 1):
        start = TEXT.index(line, pos)
        pos = start + len(line)
        row = {c: "" for c in PARA_COLS}
        row.update(paragraph_id=f"D1:p{n:05d}", doc_id="D1", ordinal=n, block=block,
                   section_path=path, char_start=start, char_end=pos,
                   for_model="true" if block == "narrative" else "false")
        rows.append(row)
    with open(data / "appraisal" / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PARA_COLS)
        w.writeheader()
        w.writerows(rows)
    return str(data)


def test_tags_that_run_on_reach_the_next_section(tmp_path):
    t = sensitivity.component_closing(_store(tmp_path))
    # The beneficiaries paragraph is under no component by default, and under
    # Component 1 when tags run on.
    assert (t["paragraphs"], t["tagged_default"], t["tagged_run_on"], t["differ"]) == (2, 1, 2, 1)


def test_sentence_variants_are_counted_against_the_default(tmp_path):
    res, n = sensitivity.sentence_rules(_store(tmp_path))
    assert n == 2
    default = res["at least 3 words (default)"]
    assert default == (3, 0)
    # Without the abbreviation list, "Fig." ends a sentence.
    assert res["no abbreviation list"] == (4, 1)
