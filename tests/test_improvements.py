"""The accuracy additions: the template (boilerplate) score on search units,
the Platt calibration and the embedding context
prefix. Invented text throughout."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import check_units  # noqa: E402
import embed  # noqa: E402
import measure  # noqa: E402
import units  # noqa: E402


def _unit(uid, projects, text, texts):
    texts[uid] = text
    return {"unit_id": uid, "doc_id": uid, "project_ids": projects, "char_start": "0",
            "char_end": str(len(text)), "lead_in_start": "", "lead_in_end": ""}


def test_template_score_counts_other_projects_printing_the_same_words():
    texts = {}
    standard = "The project has been screened for climate and disaster risks."
    us = [_unit("a", "P1", standard, texts), _unit("b", "P2", standard.upper(), texts),
          _unit("c", "P3", standard + " ", texts),
          _unit("d", "P1|P9", "A site-specific climate risk assessment of the data centre "
                               "will be financed under component two of this operation.", texts),
          _unit("e", "P4", "The project has been screened for climate and disaster risks, "
                           "and measures follow.", texts)]
    units.template_scores(us, lambda d: texts[d])
    by = {u["unit_id"]: u for u in us}
    assert by["a"]["template_projects"] == 2 and by["b"]["template_projects"] == 2
    assert by["d"]["template_projects"] == 0 and by["d"]["template_share"] == "0.00"
    # e shares the standard sentence's word runs with three other projects
    assert float(by["e"]["template_share"]) > 0.5 and by["e"]["template_projects"] == 0


def test_template_score_reads_a_list_item_with_its_lead_in():
    lead = "Under the first component the project will finance:\n"
    texts = {"x": lead + "(a) towers;", "y": lead + "(b) cables;", "z": lead + "(c) masts;"}
    us = [{"unit_id": u, "doc_id": u, "project_ids": f"P{i}", "char_start": str(len(lead)),
           "char_end": str(len(lead) + 11), "lead_in_start": "0", "lead_in_end": str(len(lead) - 1)}
          for i, u in enumerate("xyz")]
    units.template_scores(us, lambda d: texts[d])
    # the items differ, so neither is a copy of the other; the shared lead-in
    # gives a share below one
    assert us[0]["template_projects"] == 0 and 0 < float(us[0]["template_share"]) < 1


def test_section_prefix_uses_the_paragraphs_own_context():
    paras = {"D:p1": {"section_title": "Project Components", "component_name": "Backbone",
                      "subcomponent_name": "Last mile"}}
    row = {"paragraph_id": "D:p1", "subcomponent_number": "1.2"}
    assert embed.context_prefix(row, paras) == "Section: Project Components. Component: Last mile."
    assert embed.context_prefix({"paragraph_id": "D:p1"}, paras) == \
        "Section: Project Components. Component: Backbone."
    assert embed.context_prefix({"paragraph_id": "D:p9"}, paras) == ""


def test_a_long_state_is_asked_in_several_requests_never_truncated(monkeypatch):
    import answers
    import classify
    monkeypatch.setattr(classify, "MAX_REQUEST_CHARS", 900)
    state = {"section": "x", "sentence": "w " * 150}
    qs = {f"q{i}": {"type": "noul", "instructions": "a" * 120, "criteria": {"true": "t", "false": "f"}}
          for i in range(6)}
    bodies = answers.bodies_for(state, qs, "m")
    assert len(bodies) > 1
    asked = [q for b in bodies for q in b["questions"]]
    assert asked == list(qs)                              # every question once, in order
    assert all(len(__import__("json").dumps(b)) <= 900 for b in bodies)
    assert answers.bodies_for(state, dict(list(qs.items())[:1]), "m")[0]["questions"].keys() == {"q0"}
