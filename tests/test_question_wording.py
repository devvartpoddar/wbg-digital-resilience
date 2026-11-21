"""Every question builder's output and the states are fixed byte for byte: a stored answer is keyed on
their hashes, so moving a builder from one module to another must not change one of them.

A hash here changes only by a deliberate edit, and each edit is listed here with what it asks again.
2026-11-21 (the questions' rewrite): digital, in_context, asset_towers, package_asset, asset_second,
asset_subtype_* and package_asset_subtype_towers; the activity question went with its stage.
2026-11-21 (the review fixes), on purpose and only:
- type (and type_proposed, new here): "which type it is" for "which kind it is", one builder for the
  person's and the proposed types, a proposed type's name sent without "OTH.";
- policy: the one paragraph note (CONTEXT_NOTE) for POLICY_NOTE;
- asset_towers and package_asset: "{name} means:" for "{name} are:";
- asset_subtype_* and package_asset_subtype_towers: each type sent with its definition, "- id: name:
  definition" (and the word "kind" is "type" in the asset types file);
- grader_package_measure (new here): the pipeline's strong-link core, "what the package procures is".
Unchanged: graded (every measure's first pass), fit, in_context, digital, grade_second, package_measure,
asset_second, package_asset_second_*, and the states (a context state changes only for a paragraph
longer than second.context_chars, now centred on the sentence: see tests/test_context_window.py).
"""
from wbg_portfolio import questions as Q


def questions_of(corpus):
    lab = Q.load("cra")[0][0]
    scale = Q.load("cra")[1]
    assets, subtypes = Q.asset_catalogue()
    ast = {a["id"]: a for a in assets}
    out = {
        "graded": Q.graded_question(lab, scale),
        "fit": Q.fit_question(lab),
        "in_context": Q.in_context(Q.graded_question(lab, scale)),
        "digital": Q.digital_question(),
        "type": Q.type_questions("cra")["cra"]["site"],
        "package_asset": Q.package_asset_questions()["towers"],
        "grade_second": Q.second_grade_question(lab, scale),
        "policy": Q.policy_question(),
        "asset_second": Q.asset_second_question(assets),
        "asset_towers": Q.graded_questions(Q.ASSETS)[0]["towers"],
        "package_asset_subtype_towers": Q.package_subtype_questions()["towers"],
        "asset_subtype_towers": Q.asset_subtype_question(ast["towers"], subtypes["towers"]),
        "asset_subtype_fiber": Q.asset_subtype_question(ast["fiber"], subtypes["fiber"]),
        "package_asset_second_towers": Q.package_asset_second_question(ast["towers"]),
        "package_asset_second_fiber": Q.package_asset_second_question(ast["fiber"]),
        "package_measure": Q.package_measure_question(lab),
        "type_proposed": Q.type_question(lab, {"type_id": "OTH.drills", "type_name": "OTH. Emergency drills",
                                               "definition": "drills for an emergency"}),
        "grader_package_measure": Q.grader_package_measure_question(lab),
    }
    u = Q.Corpus(corpus).index[0]["unit_id"]
    c = Q.Corpus(corpus)
    out["state"], out["context_state"], out["chat_state"] = c.state(u), c.context_state(u), c.chat_state(u)
    out["package_state"] = Q.package_state({"text": "Supply of towers", "component": "Connectivity"})
    return out


EXPECTED = {
    "asset_second": "60ad858ec0b097e24297ddbd83bb9b79ead5db6c5a0594425f2a3900dd41411d",
    "asset_subtype_fiber": "83c7356b90fa258cadb48e28e4457243311d1c6f68f628b1855c987189b79428",
    "asset_subtype_towers": "d267b843d63ae7e1089ed37744d199a0603aed73d73a5da4fddb6c73661eac05",
    "asset_towers": "ffc5a538c3b2533e2a5d2b728f8c2c7c7f83f48c05acc8602b3b61bc8e946b43",
    "chat_state": "5546ece881662533110c3e26b36fe7211a1d65f11d3ec335de5e1fca9492b9bf",
    "context_state": "20de94d93a4a2f7bc16725fff10f6c70e426555a3bbb848d6ff644108c48eda2",
    "digital": "bc56ccf946bc00147c8f2d411fe746f586cb3d1f7056f739a07da58800db27d3",
    "fit": "1b4a5ef9a771ed8d48da8e0605858951d1fe0b4a9a048feccc374ad1867c36eb",
    "grade_second": "e02a6bbcc1a4dc54e61cf4ee02a38b1144070f11153dabd2a7bf67b388c5a621",
    "graded": "1379624693ebd551d13e6097fc0b359e37c20f1e726f7875de3d619fe7470f20",
    "grader_package_measure": "4161d965b42cd42f8f08a092cf23bff226ff1fcec32f8d50d0ad0f4a839eb1bb",
    "in_context": "9c290edeebc8a6c1beebda86634cb9f33b3f395bc4a288a1a94510a981776126",
    "package_asset": "1034fff6bbd48ecc1ee5bb39aacc692313a5065cd4fac47ab5d714e15fe64431",
    "package_asset_second_fiber": "dd64eb66b1e35897e2213782822a6b4af0720a455c360af1401c9fd951a61390",
    "package_asset_second_towers": "765bed26a06d65a9c5f4bf800e868cd1da0e5dac536f120519ec7c3d4101d4d4",
    "package_asset_subtype_towers": "9874eaa5adebd41f3d8e0183963e29af388c395f7fa37e76c6e11d1a657b8028",
    "package_measure": "3fec361e52c31d666b517e5ba5833836e5d84c85e4ce269f9871664861f70bc8",
    "package_state": "d4257434f97726b894ed55bcf40164e69e6f69e96fbff78012208cac51572ca6",
    "policy": "996a0a1125cc3acfedf1634833a42c1d6f49feae455d5a569cfcbff48a346147",
    "state": "5546ece881662533110c3e26b36fe7211a1d65f11d3ec335de5e1fca9492b9bf",
    "type": "ee77ba06cf61922da68e4f1390fb9d46364b1b709972864f3d70627b8abd11fb",
    "type_proposed": "6373820214ff8d9a7e0556ac12406c54319468c255491ad5b47b44c4edc9eb39",
}


def test_every_question_and_state_hashes_as_before(corpus):
    got = {k: Q.sha(v) for k, v in questions_of(corpus).items()}
    assert got == EXPECTED

