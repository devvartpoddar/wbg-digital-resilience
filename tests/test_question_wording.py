"""Every question builder's output and the states are fixed byte for byte: a stored answer is keyed on
their hashes, so moving a builder from one module to another must not change one of them.

The three NEW kinds' wording is allowed to change only by a deliberate edit here (nothing has been
asked of a real model for them yet); the old kinds (measure, asset, type, pair, package_asset, link) never.
"""
from wbg_portfolio import activities
from wbg_portfolio import chat
from wbg_portfolio import checks
from wbg_portfolio import labels
from wbg_portfolio import questions as Q
from wbg_portfolio import second


def builder(name, *modules):
    """A question builder wherever it lives now (so the test survives moving it)."""
    for m in modules:
        if hasattr(m, name):
            return getattr(m, name)
    raise AttributeError(name)


def questions_of(corpus):
    lab = Q.load("cra")[0][0]
    scale = Q.load("cra")[1]
    ast = {a["id"]: a for a in Q.load(Q.ASSETS)[0]}
    out = {
        "graded": Q.graded_question(lab, scale),
        "fit": Q.fit_question(lab),
        "in_context": Q.in_context(Q.graded_question(lab, scale)),
        "digital": Q.flag_question("digital"),
        "type": Q.type_questions("cra")["cra"]["site"],
        "pair_a": Q.pair_questions("cra", "assets")["keep_a"],
        "package_asset": Q.package_asset_questions()["towers"],
        "link": Q.LINK_QUESTION,
        "grade_second": builder("grade_question", Q, chat)(lab, scale),
        "policy": builder("policy_question", Q, second)(),
        "asset_second": builder("asset_second_question", Q, second)(),
        "asset_subtype_towers": builder("asset_subtype_question", Q, second)("towers"),
        "asset_subtype_fiber": builder("asset_subtype_question", Q, second)("fiber"),
        "package_asset_second_towers": builder("package_asset_question", Q, second)(ast["towers"]),
        "package_asset_second_fiber": builder("package_asset_question", Q, second)(ast["fiber"]),
        "activity": builder("activities_question", Q, activities)(lab) if hasattr(Q, "activities_question")
        else activities.question(lab),
        "package_measure": builder("package_measure_question", Q, checks)(lab),
    }
    u = Q.Corpus(corpus).index[0]["unit_id"]
    c = Q.Corpus(corpus)
    out["state"], out["context_state"], out["chat_state"] = c.state(u), c.context_state(u), c.chat_state(u)
    out["package_state"] = Q.package_state({"text": "Supply of towers", "component": "Connectivity"})
    return out


EXPECTED = {
    "activity": "58325e681f9b565b0e00cbe6a8ff8a30ff97abc9e98b74c12dfbb71dde87a6b7",
    "asset_second": "bdc844768fcbdff0ca93ff4441bf5d404c66e2be1b0550f162e269ee246be3ac",
    "asset_subtype_fiber": "e68c460ea3d3d9b79681a8c49a2555ee51414e4c11eeacf47ba3732b36daeaed",
    "asset_subtype_towers": "9fb1f97e20035198b3ed14b187821acd5b87f8b56420bda7c9a2428dcb33580f",
    "chat_state": "5546ece881662533110c3e26b36fe7211a1d65f11d3ec335de5e1fca9492b9bf",
    "context_state": "20de94d93a4a2f7bc16725fff10f6c70e426555a3bbb848d6ff644108c48eda2",
    "digital": "c152d16abeba8ba225b8cb00517fcdef09ac369314c3c8bb98965613418205e1",
    "fit": "1b4a5ef9a771ed8d48da8e0605858951d1fe0b4a9a048feccc374ad1867c36eb",
    "grade_second": "d40fe5e3187aa26d20286dd6f41703cf8ce8ed307e7fb17faef1fa206962899e",
    "graded": "1379624693ebd551d13e6097fc0b359e37c20f1e726f7875de3d619fe7470f20",
    "in_context": "0316a2b2573d82b2777aeab9bef7f82d1fd635216062a13434f8fc62dc9a3eb8",
    "link": "a412390bbec95d8294310168e9e6c60aef22f589a672dd8e2d26d94e3b89e104",
    "package_asset": "f3198cc63b5825a9a082a1a13c9d533c433098b8763e01e3ab8e242a50175101",
    "package_asset_second_fiber": "47064907a783c02bec2d98236b4a1ccdbf13f53ca66d3d632aaaeab7eb66af44",
    "package_asset_second_towers": "41f886f96af3d55b857247af25fc400dc6b42d38dd343bdce77d30031d163492",
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
