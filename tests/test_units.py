"""Search units (src/units.py) on an invented store."""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import units  # noqa: E402
from clean import PARA_COLS, SENT_COLS  # noqa: E402


def _write(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def test_units_are_model_sentences_and_whole_footnotes_with_their_context(tmp_path):
    data = str(tmp_path)
    ap = os.path.join(data, "appraisal")
    _write(os.path.join(ap, "paragraphs.csv"), PARA_COLS, [
        {"paragraph_id": "D:p00001", "doc_id": "D", "project_ids": "P1", "block": "narrative",
         "for_model": "true", "component_number": "2", "char_start": 0, "char_end": 40},
        {"paragraph_id": "D:p00002", "doc_id": "D", "project_ids": "P1", "block": "footnote",
         "for_model": "true", "char_start": 41, "char_end": 60, "n_tokens": 3,
         "text_sha256": "f"},
        {"paragraph_id": "D:p00003", "doc_id": "D", "project_ids": "P1", "block": "table",
         "for_model": "false", "char_start": 61, "char_end": 70}])
    _write(os.path.join(ap, "sentences.csv"), SENT_COLS, [
        {"sentence_id": "D:p00001:s001", "paragraph_id": "D:p00001", "doc_id": "D",
         "char_start": 0, "char_end": 20, "n_tokens": 4, "text_sha256": "a"},
        {"sentence_id": "D:p00003:s001", "paragraph_id": "D:p00003", "doc_id": "D",
         "char_start": 61, "char_end": 70, "n_tokens": 2, "text_sha256": "b"}])
    _write(os.path.join(ap, "components.csv"), ["doc_id", "source", "level", "number",
                                                "name_key"], [
        {"doc_id": "D", "source": "heading", "level": "component", "number": "2",
         "name_key": "masts"},
        {"doc_id": "D", "source": "datasheet", "level": "component", "number": "",
         "name_key": "unallocated"}])
    got = units.search_units(data)
    assert [(u["unit_id"], u["kind"], u["component_key"]) for u in got] == [
        ("D:p00001:s001", "sentence", "masts"),
        # A footnote is one unit; untagged, it takes no unnumbered component's key.
        ("D:p00002", "footnote", "")]


def test_a_list_item_carries_its_lead_in():
    text = "36. Sub-component 1.2 will finance:\n(a) towers;\n(b) fiber. It also pays staff."
    para = {"char_start": "0", "char_end": str(len(text)), "list_item": "false"}

    def sent(i, piece):
        a = text.index(piece)
        return {"sentence_id": f"s{i}", "char_start": str(a), "char_end": str(a + len(piece))}
    sents = [sent(1, "36. Sub-component 1.2 will finance:"), sent(2, "(a) towers;"),
             sent(3, "(b) fiber."), sent(4, "It also pays staff.")]
    lead = (0, len("36. Sub-component 1.2 will finance:"))
    assert units.lead_ins(para, sents, text) == {"s2": lead, "s3": lead, "s4": lead}
    # An inline list: the sentence before the enumerators is the lead-in.
    text = "The project draws on: (a) civil registration; (b) national ID."
    para = {"char_start": "0", "char_end": str(len(text)), "list_item": "false"}
    sents = [sent(1, "The project draws on:"), sent(2, "(a) civil registration;"),
             sent(3, "(b) national ID.")]
    assert units.lead_ins(para, sents, text) == {"s2": (0, 21), "s3": (0, 21)}
    # A paragraph that is itself a list item has no lead-in.
    assert units.lead_ins(dict(para, list_item="true"), sents, text) == {}


def test_references_and_fragments_are_left_out_but_commitments_never():
    assert units.left_out("footnote", "World Bank. 2021. Climate Risk Country Profile: Rwanda. [link]", False) == "reference"
    assert units.left_out("footnote", "Caribbean Digital Transformation Project (P171528)", False) == "reference"
    # A footnote saying what the project will do is searched, however short.
    assert units.left_out("footnote", "Survey work will take account of climate-related risks.", False) == ""
    assert units.left_out("footnote", "Tier 3 data centres are designed for 99.98 percent availability "
                          "and have redundant power and cooling paths throughout.", False) == ""
    assert units.left_out("sentence", "14. Project Risk Assessment and Mitigation.", False) == "fragment"
    assert units.left_out("sentence", "(iii) governance.", False) == "fragment"
    # A list item is read with its lead-in, so it is never a fragment.
    assert units.left_out("sentence", "(b) towers;", True) == ""
    assert units.left_out("sentence", "Towers will be raised.", False) == ""


def test_a_repeated_text_is_searched_once_and_its_copies_are_named(tmp_path):
    data = str(tmp_path)
    ap = os.path.join(data, "appraisal")
    line = "The project will bury fibre cables along flood-prone routes."
    os.makedirs(os.path.join(ap, "text"))
    for d in ("A", "B"):
        with open(os.path.join(ap, "text", f"{d}.txt"), "w") as fh:
            fh.write(line)
    _write(os.path.join(ap, "paragraphs.csv"), PARA_COLS, [
        {"paragraph_id": f"{d}:p00001", "doc_id": d, "project_ids": "P1", "block": "narrative",
         "for_model": "true", "char_start": 0, "char_end": len(line)} for d in ("A", "B")])
    _write(os.path.join(ap, "sentences.csv"), SENT_COLS, [
        {"sentence_id": f"{d}:p00001:s001", "paragraph_id": f"{d}:p00001", "doc_id": d,
         "char_start": 0, "char_end": len(line), "n_tokens": 10, "text_sha256": "a"}
        for d in ("A", "B")])
    got = units.search_units(data)
    assert [(u["unit_id"], u["left_out"], u["copy_of"]) for u in got] == [
        ("A:p00001:s001", "", ""), ("B:p00001:s001", "", "A:p00001:s001")]
    assert units.copies(got) == {"A:p00001:s001": ["B:p00001:s001"]}


def test_a_table_row_is_searched_when_its_cells_read_as_prose():
    assert units.prose_row("Climate Action — Activity: Areas covered by the programme will target "
                           "provinces most affected by floods and droughts, with resilient designs for towers.")
    assert not units.prose_row("Table 1. Project Costs — Component 1: 2.4 13.3 9.8 0.0 11.0 0.0 2.4 24.3")
    assert not units.prose_row("Results — Indicator: Share of schools connected (Percentage)")
