"""Every question builder's output and the states are fixed byte for byte: a stored answer is keyed on
their hashes, so moving a builder from one module to another must not change one of them.

The three NEW kinds' wording is allowed to change only by a deliberate edit here (nothing has been
asked of a real model for them yet); the old kinds (measure, asset, type, pair, package_asset, link) never.
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
        "digital": Q.flag_question("digital"),
        "type": Q.type_questions("cra")["cra"]["site"],
        "pair_a": Q.pair_questions("cra", "assets")["keep_a"],
        "package_asset": Q.package_asset_questions()["towers"],
        "link": Q.LINK_QUESTION,
        "grade_second": Q.second_grade_question(lab, scale),
        "policy": Q.policy_question(),
        "asset_second": Q.asset_second_question(assets, subtypes),
        "asset_subtype_towers": Q.asset_subtype_question(ast["towers"], subtypes["towers"]),
        "asset_subtype_fiber": Q.asset_subtype_question(ast["fiber"], subtypes["fiber"]),
        "package_asset_second_towers": Q.package_asset_second_question(ast["towers"]),
        "package_asset_second_fiber": Q.package_asset_second_question(ast["fiber"]),
        "activity": Q.activity_question(lab),
        "package_measure": Q.package_measure_question(lab),
    }
    u = Q.Corpus(corpus).index[0]["unit_id"]
    c = Q.Corpus(corpus)
    out["state"], out["context_state"], out["chat_state"] = c.state(u), c.context_state(u), c.chat_state(u)
    out["package_state"] = Q.package_state({"text": "Supply of towers", "component": "Connectivity"})
    return out


EXPECTED = {
    "activity": "58325e681f9b565b0e00cbe6a8ff8a30ff97abc9e98b74c12dfbb71dde87a6b7",
    "asset_second": "a94286fdaafba85f418587d4af68f81439dafdc82dd8bed76828017d87fde631",
    "asset_subtype_fiber": "6c522ea529ab20629976529cce68a779c6d86bf7702613c8996b2ef8b3620224",
    "asset_subtype_towers": "5293b63cc0bb527948d00cd070b02a29c9d195780ece3b60b7014518808ddac4",
    "chat_state": "5546ece881662533110c3e26b36fe7211a1d65f11d3ec335de5e1fca9492b9bf",
    "context_state": "20de94d93a4a2f7bc16725fff10f6c70e426555a3bbb848d6ff644108c48eda2",
    "digital": "c152d16abeba8ba225b8cb00517fcdef09ac369314c3c8bb98965613418205e1",
    "fit": "1b4a5ef9a771ed8d48da8e0605858951d1fe0b4a9a048feccc374ad1867c36eb",
    "grade_second": "e02a6bbcc1a4dc54e61cf4ee02a38b1144070f11153dabd2a7bf67b388c5a621",
    "graded": "1379624693ebd551d13e6097fc0b359e37c20f1e726f7875de3d619fe7470f20",
    "in_context": "0316a2b2573d82b2777aeab9bef7f82d1fd635216062a13434f8fc62dc9a3eb8",
    "link": "a412390bbec95d8294310168e9e6c60aef22f589a672dd8e2d26d94e3b89e104",
    "package_asset": "f3198cc63b5825a9a082a1a13c9d533c433098b8763e01e3ab8e242a50175101",
    "package_asset_second_fiber": "dd64eb66b1e35897e2213782822a6b4af0720a455c360af1401c9fd951a61390",
    "package_asset_second_towers": "765bed26a06d65a9c5f4bf800e868cd1da0e5dac536f120519ec7c3d4101d4d4",
    "package_measure": "3fec361e52c31d666b517e5ba5833836e5d84c85e4ce269f9871664861f70bc8",
    "package_state": "d4257434f97726b894ed55bcf40164e69e6f69e96fbff78012208cac51572ca6",
    "pair_a": "f17a7df46f9f976936d772cc625c20b3fbc88d8aaaf3f65bf466b33f666f6297",
    "policy": "57548f4ada26ec0d4f86587dabb4e4acbf459a5c810a6445e1d4d1f9cae57526",
    "state": "5546ece881662533110c3e26b36fe7211a1d65f11d3ec335de5e1fca9492b9bf",
    "type": "3148324395e8b5ef4295136de0af9aa923210cc6dbca38dfe6bf2f1700da40bf",
}


def test_every_question_and_state_hashes_as_before(corpus):
    got = {k: Q.sha(v) for k, v in questions_of(corpus).items()}
    assert got == EXPECTED

