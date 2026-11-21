"""Every question builder's output and the states are fixed byte for byte: a stored answer is keyed on
their hashes, so moving a builder from one module to another must not change one of them.

A hash here changes only by a deliberate edit. The rewrite of 2026-11-21 changed, on purpose and only:
digital (graded on the taxonomy scale), in_context (the paragraph note), asset_towers (the first-pass asset
question, new key), package_asset (graded), asset_second (the asset alone), asset_subtype_* (types by name)
and package_asset_subtype_towers (new); the activity question is gone with its stage. The measure first pass (graded), fit, type, grade_second, policy,
package_measure and package_asset_second questions, and the states, are as before.
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
    }
    u = Q.Corpus(corpus).index[0]["unit_id"]
    c = Q.Corpus(corpus)
    out["state"], out["context_state"], out["chat_state"] = c.state(u), c.context_state(u), c.chat_state(u)
    out["package_state"] = Q.package_state({"text": "Supply of towers", "component": "Connectivity"})
    return out


EXPECTED = {
    "asset_second": "60ad858ec0b097e24297ddbd83bb9b79ead5db6c5a0594425f2a3900dd41411d",
    "asset_subtype_fiber": "91e90e584b4c6998b04b81cd722bf141caddd3b44939909361d8ebf2b6906477",
    "asset_subtype_towers": "2fe9237a43e09eba3c3739efb1665124dd19802168dffb42c92f57777743b19b",
    "asset_towers": "733708780d98e64ab97ae3afea3e16e69dc6c41290748492c2b6c32eb48e6bec",
    "chat_state": "5546ece881662533110c3e26b36fe7211a1d65f11d3ec335de5e1fca9492b9bf",
    "context_state": "20de94d93a4a2f7bc16725fff10f6c70e426555a3bbb848d6ff644108c48eda2",
    "digital": "bc56ccf946bc00147c8f2d411fe746f586cb3d1f7056f739a07da58800db27d3",
    "fit": "1b4a5ef9a771ed8d48da8e0605858951d1fe0b4a9a048feccc374ad1867c36eb",
    "grade_second": "e02a6bbcc1a4dc54e61cf4ee02a38b1144070f11153dabd2a7bf67b388c5a621",
    "graded": "1379624693ebd551d13e6097fc0b359e37c20f1e726f7875de3d619fe7470f20",
    "in_context": "9c290edeebc8a6c1beebda86634cb9f33b3f395bc4a288a1a94510a981776126",
    "package_asset": "51944732f9b8c0e0daf1777a94be7733152cbe9c091bf477663168e93d5b4667",
    "package_asset_second_fiber": "dd64eb66b1e35897e2213782822a6b4af0720a455c360af1401c9fd951a61390",
    "package_asset_subtype_towers": "329959a46c67869a0f2ff4b9e75912c69551b130683292d8f4a3389608e7fb82",
    "package_asset_second_towers": "765bed26a06d65a9c5f4bf800e868cd1da0e5dac536f120519ec7c3d4101d4d4",
    "package_measure": "3fec361e52c31d666b517e5ba5833836e5d84c85e4ce269f9871664861f70bc8",
    "package_state": "d4257434f97726b894ed55bcf40164e69e6f69e96fbff78012208cac51572ca6",
    "policy": "57548f4ada26ec0d4f86587dabb4e4acbf459a5c810a6445e1d4d1f9cae57526",
    "state": "5546ece881662533110c3e26b36fe7211a1d65f11d3ec335de5e1fca9492b9bf",
    "type": "3148324395e8b5ef4295136de0af9aa923210cc6dbca38dfe6bf2f1700da40bf",
}


def test_every_question_and_state_hashes_as_before(corpus):
    got = {k: Q.sha(v) for k, v in questions_of(corpus).items()}
    assert got == EXPECTED

