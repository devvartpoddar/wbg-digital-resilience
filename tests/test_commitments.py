"""The commitment rule (src/wbg_portfolio/decide.py commitment_keys, commitment_rows): no model call, the
kept sentence x measure pairs grouped by asset type, measure type or paragraph. Invented rows only."""
from wbg_portfolio import decide


def finding(uid, instrument="investment", types=(), paragraph="D:p1", committed=True, project="P1", comp="1",
            doc="D", measure="m"):
    return {"label_set": measure, "unit_id": uid, "committed": str(committed).lower(), "types_yes": "|".join(types),
            "instrument": instrument, "project_ids": project, "paragraph_id": paragraph, "doc_id": doc,
            "component_number": comp}


NAMES = ({"a": "Asset A", "b": "Asset B"}, {("a", "x"): "Type x", ("b", "y"): "Type y"}, {("m", "t1"): "Measure type 1"})


def test_the_keys_of_one_sentence():
    k = decide.commitment_keys
    assert k("investment", [("a", "x"), ("b", "y")], ["t1"], "p") == [("asset_type", "a", "x", "", ""),
                                                                    ("asset_type", "b", "y", "", "")]
    assert k("investment", [("a", "x"), ("b", "unspecified")], [], "p") == [("asset_type", "a", "x", "", "")]
    assert k("investment", [("a", "unspecified")], ["t1"], "p") == [("paragraph", "", "", "", "p")]
    assert k("investment", [("a", "")], [], "p") == [("paragraph", "", "", "", "p")]       # type not asked yet
    assert k("investment", [], ["t1", "t2"], "p") == [("type", "", "", "t1", ""), ("type", "", "", "t2", "")]
    assert k("investment", [], [], "p") == [("paragraph", "", "", "", "p")]
    assert k("policy", [("a", "x")], ["t1"], "p") == [("type", "", "", "t1", "")]           # a policy: its type
    assert k("policy", [("a", "x")], [], "p") == [("paragraph", "", "", "", "p")]
    assert k("", [("a", "x")], [], "p") == [("asset_type", "a", "x", "", "")]                # not asked yet: as investment


def test_commitments_group_sentences_across_paragraphs_and_documents_and_carry_their_links():
    findings = [finding("u1", types=["t1"], paragraph="D:p1"), finding("u2", paragraph="D:p2", doc="E", comp="2"),
                finding("u3", paragraph="D:p3"), finding("u4", instrument="policy", types=["t1"]),
                finding("u5", instrument="policy"), finding("u6", committed=False)]
    assets = {"u1": [("a", "x"), ("b", "y")], "u2": [("a", "x")], "u3": [("a", "unspecified")]}
    links = [{"project_id": "P1", "label_set": "m", "unit_id": "u1", "package_id": "K1", "level_rank": 3},
             {"project_id": "P1", "label_set": "m", "unit_id": "u2", "package_id": "K1", "level_rank": 1},
             {"project_id": "P1", "label_set": "m", "unit_id": "u2", "package_id": "K2", "level_rank": 5}]
    rows, members = decide.commitment_rows(findings, assets, links, NAMES)
    by = {(r["commitment_key"], r["asset_id"], r["subtype_id"], r["type_id"], r["instrument"]): r for r in rows}
    ax = by[("asset_type", "a", "x", "", "investment")]
    assert (ax["sentences"], ax["paragraphs"], ax["documents"], ax["components"]) == (2, 2, 2, "1|2")
    assert ax["commitment_label"] == "Asset A: Type x" and ax["type_ids"] == "t1"
    assert (ax["linked_packages"], ax["strong_packages"], ax["best_link_level"], ax["best_link_rank"]) == (2, 1, "strong", 1)
    by_ = by[("asset_type", "b", "y", "", "investment")]                  # u1 is in two commitments
    assert by_["sentences"] == 1 and by_["best_link_level"] == "type"
    assert by[("paragraph", "", "", "", "investment")]["paragraph_id"] == "D:p3"
    assert by[("type", "", "", "t1", "policy")]["commitment_label"] == "Measure type 1"
    assert by[("paragraph", "", "", "", "policy")]["commitment_label"] == decide.PARAGRAPH_LABEL
    assert len(rows) == 5 and "u6" not in {m["unit_id"] for m in members}                # not kept: in none
    assert sorted(m["unit_id"] for m in members if m["commitment_id"] == ax["commitment_id"]) == ["u1", "u2"]
    again, _ = decide.commitment_rows(findings, assets, links, NAMES)
    assert [r["commitment_id"] for r in again] == [r["commitment_id"] for r in rows]       # the same ids every time


def test_a_sentence_in_two_projects_is_in_a_commitment_of_each():
    rows, members = decide.commitment_rows([finding("u1", project="P1|P2")], {"u1": [("a", "x")]}, [], NAMES)
    assert sorted(r["project_id"] for r in rows) == ["P1", "P2"] and len({r["commitment_id"] for r in rows}) == 2
    assert all(r["best_link_level"] == "" and r["linked_packages"] == 0 for r in rows)


def test_a_sentence_with_no_instrument_answer_yet_keeps_its_commitment_when_the_answer_arrives():
    before, _ = decide.commitment_rows([finding("u1", instrument=""), finding("u2")], {"u1": [("a", "x")],
                                       "u2": [("a", "x")]}, [], NAMES)
    after, _ = decide.commitment_rows([finding("u1"), finding("u2")], {"u1": [("a", "x")], "u2": [("a", "x")]},
                                      [], NAMES)
    assert len(before) == 1 and before[0]["sentences"] == 2 and before[0]["instrument"] == "investment"
    assert [r["commitment_id"] for r in before] == [r["commitment_id"] for r in after]


def test_a_proposed_type_is_labelled_by_its_name(monkeypatch):
    from wbg_portfolio import questions as Q
    monkeypatch.setattr(Q, "asset_catalogue", lambda: ([], {}))
    monkeypatch.setattr(decide, "measure_sets", lambda: ["m"])
    monkeypatch.setattr(decide.labels, "type_resolution", lambda name: ({}, {}))
    monkeypatch.setattr(Q, "oth_types", lambda name, data=None: [
        {"label_id": "m", "type_id": "OTH.drills", "type_name": "OTH. Emergency drills", "definition": "d"}])
    names = decide.commitment_names()
    rows, _ = decide.commitment_rows([finding("u1", instrument="policy", types=["OTH.drills"])], {}, [], names)
    assert rows[0]["commitment_label"] == "Emergency drills (proposed)"
