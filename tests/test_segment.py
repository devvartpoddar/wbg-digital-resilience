"""Unit tests for Stage 4 clause segmentation.

The failure this guards against is silent in both directions. A boundary
condition that stops matching produces fewer, longer clauses and no error - the
patterns are dependency labels, so a rule written against the wrong label set
returns nothing at all and looks like a corpus that simply has no such
sentences. And a boundary that fires too eagerly cuts a measure in half, which
no downstream count notices either.

So the tests here assert the boundary conditions one at a time on text written
to trigger exactly one of them, assert that a coordinated NOUN phrase is
deliberately left alone, and assert the offsets still resolve to the substring
they claim.
"""
import csv
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import segment as S  # noqa: E402


@pytest.fixture(scope="module")
def nlp():
    parser, _version, _settings = S.load_parser("en_core_web_sm")
    return parser


def clauses_of(nlp, text):
    return [(text[a:b], rule) for a, b, rule in S.segment_text(nlp, text)]


# --------------------------------------------------------------- the worked case

def test_worked_example_yields_at_least_four_clauses(nlp):
    """34338772:p00680 states five measures in one bullet."""
    text = ("Climate resilience measures for the broadband connectivity "
            "infrastructure (backbone, backhaul, and last-mile networks) will "
            "follow recommendations from the ITU Standardization Sector on "
            "adaptation 73 to determine the choice of technology, such as "
            "between microwave, underground, and aerial fiber optic cables; "
            "deploy weather-resistant fiber optic, weather-proofing ducts, "
            "poles, switches, sockets, and appliances in the network; and embed "
            "elevation in the communication towers to prevent damage from "
            "floods and heavy precipitation.")
    got = clauses_of(nlp, text)
    assert len(got) >= 4
    joined = " ".join(c for c, _ in got)
    # every measure survives somewhere in the split
    for fragment in ("ITU Standardization Sector", "between microwave",
                     "deploy weather-resistant", "embed elevation"):
        assert fragment in joined


# -------------------------------------------------------- one rule at a time

def test_semicolon_splits(nlp):
    got = clauses_of(nlp, "Deploy fiber to the schools; install backup power.")
    assert [r for _c, r in got].count("semicolon") == 1
    assert len(got) == 2
    assert got[0][0].endswith("schools")
    assert got[1][0].startswith("install")


def test_semicolon_before_a_coordinating_conjunction_keeps_and_with_its_clause(nlp):
    got = clauses_of(nlp, "Harden the ducts; and embed elevation in the towers.")
    assert len(got) == 2
    assert got[1][0].startswith("and embed")


def test_coordinating_conjunction_between_verb_subtrees_splits(nlp):
    got = clauses_of(nlp, "The project will deploy fiber and harden the towers.")
    assert "coord_conj" in [r for _c, r in got]
    assert len(got) == 2


def test_coordinated_noun_phrase_is_left_alone(nlp):
    """The known limit: two measures in one clause, deliberately not split."""
    got = clauses_of(nlp, "The project will weather-proof the ducts, poles and switches.")
    assert len(got) == 1
    assert "coord_conj" not in [r for _c, r in got]


def test_subordinating_conjunction_splits(nlp):
    got = clauses_of(nlp, "Because flooding is frequent, the towers will be elevated.")
    assert "subord_conj" in [r for _c, r in got]
    assert len(got) == 2


def test_relative_pronoun_splits(nlp):
    got = clauses_of(nlp, "The cable that carries the traffic is buried.")
    assert "relative" in [r for _c, r in got]


def test_verb_headed_subtree_splits_and_keeps_the_to(nlp):
    got = clauses_of(nlp, "The project will install sensors to monitor river levels.")
    assert "verb_subtree" in [r for _c, r in got]
    assert any(c.startswith("to monitor") for c, _r in got)


def test_sentence_boundary_is_reported_as_sentence(nlp):
    got = clauses_of(nlp, "The network is fragile. The project will install backup links.")
    assert got[0][1] == "sentence"
    assert len(got) >= 2


def test_every_split_rule_is_one_of_the_business_rule_values(nlp):
    text = ("Deploy fiber; because flooding is frequent, raise the towers, which "
            "carry the traffic, and back up the power supply.")
    allowed = {"sentence", "semicolon", "coord_conj", "subord_conj", "relative",
               "verb_subtree"}
    got = clauses_of(nlp, text)
    assert got, "nothing was produced at all"
    assert {r for _c, r in got} <= allowed


# ------------------------------------------------------------- spans and ids

def test_offsets_resolve_and_are_trimmed(nlp):
    text = "• Deploy fiber to the schools; install backup power."
    spans = S.segment_text(nlp, text)
    for a, b, _rule in spans:
        piece = text[a:b]
        assert piece == piece.strip()
        assert piece[0].isalnum() and piece[-1].isalnum() or piece[-1] == ")"


def test_spans_never_overlap_and_stay_inside_the_paragraph(nlp):
    text = ("The project will deploy weather-resistant fiber, back up the "
            "switch, and harden the towers; it will also monitor river levels.")
    spans = S.segment_text(nlp, text)
    last = -1
    for a, b, _rule in spans:
        assert 0 <= a < b <= len(text)
        assert a >= last
        last = b


def test_clause_id_carries_the_splitter_version():
    cid = S.clause_id("34338772:p00680", 3)
    assert cid == f"34338772:p00680:c03:{S.SPLITTER_VERSION}"
    assert cid.endswith(S.SPLITTER_VERSION)


def test_no_text_column_is_written():
    assert "text" not in S.COLS
    assert not any(c.endswith("_text") for c in S.COLS)


def test_doc_checksum_moves_with_every_input_it_depends_on():
    paras = [("d1:p1", "d1", "narrative", 0, 5, "abc123")]
    base = S.doc_checksum(paras, "parser", "split-1", "clean-1")
    assert S.doc_checksum(paras, "parser", "split-2", "clean-1") != base
    assert S.doc_checksum(paras, "parser2", "split-1", "clean-1") != base
    assert S.doc_checksum(paras, "parser", "split-1", "clean-2") != base
    assert S.doc_checksum(
        [("d1:p1", "d1", "narrative", 0, 5, "zzz999")], "parser", "split-1",
        "clean-1") != base
    assert S.doc_checksum(paras, "parser", "split-1", "clean-1") == base


def test_a_list_marker_is_not_a_clause(nlp):
    got = clauses_of(nlp, "1. Deploy fiber to the schools. 2. Install backup power.")
    assert all(len(c) > 3 for c, _r in got)
    assert not any(c.strip().rstrip(".").isdigit() for c, _r in got)


def test_segmentation_is_deterministic(nlp):
    text = ("The project will deploy fiber, harden the towers, and install "
            "backup power; because flooding is frequent, it will raise the "
            "equipment, which sits at ground level.")
    assert S.segment_text(nlp, text) == S.segment_text(nlp, text)


# ------------------------------------------------------------------- the stage

PARAS = [
    ("d1:p00001", "d1", "narrative", "Deploy fiber; install backup power."),
    ("d1:p00002", "d1", "narrative", "The project will monitor river levels."),
    ("d1:p00003", "d1", "table", "fiber 100 km"),
    ("d2:p00001", "d2", "annex", "Harden the towers, which carry the traffic."),
    ("d2:p00002", "d2", "heading", "ANNEX 6"),
]


def build_corpus(tmp):
    """A two-document corpus with known offsets, laid out like the real one."""
    data = os.path.join(str(tmp), "data")
    os.makedirs(os.path.join(data, "clean"), exist_ok=True)
    rows = []
    by_doc = {}
    for pid, did, block, body in PARAS:
        by_doc.setdefault(did, []).append((pid, block, body))
    for did, items in by_doc.items():
        text, parts = "", []
        for pid, block, body in items:
            start = len(text)
            text += (" " if text else "") + body
            parts.append((pid, block, start, start + len(body)))
        with open(os.path.join(data, "clean", f"{did}.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        for pid, block, a, b in parts:
            import hashlib
            rows.append({"paragraph_id": pid, "doc_id": did, "block": block,
                         "char_start": a, "char_end": b,
                         "text_sha256": hashlib.sha256(
                             text[a:b].encode("utf-8")).hexdigest()})
    with open(os.path.join(data, "paragraphs.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["paragraph_id", "doc_id", "block",
                                           "char_start", "char_end", "text_sha256"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return data


@pytest.fixture
def ran(tmp_path, monkeypatch):
    data = build_corpus(tmp_path)
    out = os.path.join(str(tmp_path), "out")
    monkeypatch.setattr(sys, "argv", [
        "segment.py", "--data", data, "--out-dir", out, "--run-id", "test-run"])
    S.main()
    return data, out


def read_clauses(out):
    with open(os.path.join(out, "clauses.csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_only_prose_blocks_are_segmented(ran):
    _data, out = ran
    rows = read_clauses(out)
    ids = {r["paragraph_id"] for r in rows}
    assert "d1:p00003" not in ids      # table cell
    assert "d2:p00002" not in ids      # heading
    assert {"d1:p00001", "d1:p00002", "d2:p00001"} <= ids


def test_ordinals_are_contiguous_from_one(ran):
    _data, out = ran
    by_para = {}
    for r in read_clauses(out):
        by_para.setdefault(r["paragraph_id"], []).append(int(r["ordinal"]))
    for pid, ords in by_para.items():
        assert ords == list(range(1, len(ords) + 1)), pid


def test_clause_id_embeds_the_splitter_version(ran):
    _data, out = ran
    for r in read_clauses(out):
        assert r["clause_id"] == S.clause_id(r["paragraph_id"], int(r["ordinal"]))
        assert r["splitter_version"] == S.SPLITTER_VERSION


def test_row_offsets_resolve_to_the_clause_text(ran):
    data, out = ran
    para = {}
    with open(os.path.join(data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            para[r["paragraph_id"]] = r
    cache = {}
    for r in read_clauses(out):
        p = para[r["paragraph_id"]]
        did = p["doc_id"]
        if did not in cache:
            with open(os.path.join(data, "clean", f"{did}.txt"), encoding="utf-8") as fh:
                cache[did] = fh.read()
        a = int(p["char_start"]) + int(r["char_start"])
        b = int(p["char_start"]) + int(r["char_end"])
        assert 0 <= int(r["char_start"]) < int(r["char_end"]) <= int(p["char_end"]) - int(p["char_start"])
        assert cache[did][a:b].strip()


def test_rerun_is_byte_identical_and_reuses_every_document(ran, monkeypatch):
    data, out = ran
    with open(os.path.join(out, "clauses.csv"), "rb") as fh:
        first = fh.read()
    monkeypatch.setattr(sys, "argv", [
        "segment.py", "--data", data, "--out-dir", out, "--run-id", "test-run-2"])
    S.main()
    with open(os.path.join(out, "clauses.csv"), "rb") as fh:
        assert fh.read() == first
    import json
    with open(os.path.join(out, "segment_manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    assert man["documents_reused"] == man["documents_processed"]
    assert man["documents_parsed"] == 0