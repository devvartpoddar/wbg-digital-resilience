"""The evaluation sample: the Poisson draw, the graders and the tuning (docs/method/evaluation.md).
Everything here is invented; no model is called (the chat model is the fake of conftest.py)."""
import json
import os
import random
import re
from collections import Counter

import numpy
import pytest

from wbg_portfolio import chat, decide, sampling, tuning
from wbg_portfolio import graders as G
from wbg_portfolio import questions as Q


# ------------------------------------------------------------------ a synthetic population

def population(n_projects=60, seed=0):
    """Measure pairs over 8 measures and n_projects projects, with scores spread like the real ones:
    many low first scores, fewer in the band, some near certain."""
    rng = random.Random(seed)
    out = []
    for i in range(30000):
        m = "rare" if i % 1000 == 0 else f"m{i % 8}"
        first = rng.choice([rng.random() * 0.3] * 6 + [0.3 + rng.random() * 0.2] * 2 + [0.5 + rng.random() * 0.35] * 3
                           + [0.85 + rng.random() * 0.15] * 2)
        ctx = rng.random() if first >= 0.5 else None
        grade = rng.randint(0, 3) if first >= 0.5 else None
        dig = rng.random() if first >= 0.5 else None
        proj = "BIG" if i % 7 == 0 else f"P{rng.randrange(n_projects)}"
        out.append({"key": f"{m}|u{i}", "group": m, "project": proj,
                    "region": sampling.measure_region(first, ctx, grade, dig), "first": first})
    return out


def test_pi_is_between_zero_and_one_the_minimum_and_the_cap_hold():
    units = population()
    short = sampling.chances(units, sampling.TARGETS["measure"], minimum=sampling.MEASURE_MIN, group="group")
    assert all(0 < u["pi"] <= 1 for u in units)
    total = sum(u["pi"] for u in units)
    by_p = Counter()
    for u in units:
        by_p[u["project"]] += u["pi"]
    assert max(by_p.values()) <= sampling.PROJECT_CAP * total * 1.0001
    by_m = Counter()
    n_m = Counter()
    for u in units:
        if u["region"] not in sampling.NOT_RAISED:
            by_m[u["group"]] += u["pi"]
            n_m[u["group"]] += 1
    for m, e in by_m.items():
        assert e >= sampling.MEASURE_MIN - 0.5 or m in short or n_m[m] < sampling.MEASURE_MIN
    assert "rare" in by_m and by_m["rare"] == pytest.approx(min(n_m["rare"], 25), abs=1)


def test_the_draw_is_deterministic_and_matches_the_expected_count():
    units = population()
    sampling.chances(units, sampling.TARGETS["measure"], minimum=sampling.MEASURE_MIN, group="group")
    a = sampling.drawn(units, 7, "measure")
    b = sampling.drawn(units, 7, "measure")
    assert [u["key"] for u in a] == [u["key"] for u in b]
    assert [u["key"] for u in sampling.drawn(units, 8, "measure")] != [u["key"] for u in a]
    e = sum(u["pi"] for u in units)
    sd = sum(u["pi"] * (1 - u["pi"]) for u in units) ** 0.5
    for seed in range(5):
        assert abs(len(sampling.drawn(units, seed, "measure")) - e) < 4 * sd
    # per region too
    by = Counter(u["region"] for u in a)
    for r in set(u["region"] for u in units):
        er = sum(u["pi"] for u in units if u["region"] == r)
        sr = sum(u["pi"] * (1 - u["pi"]) for u in units if u["region"] == r) ** 0.5
        assert abs(by[r] - er) <= 4 * sr + 1


def test_the_tuning_split_is_independent_of_inclusion():
    units = population()
    sampling.chances(units, sampling.TARGETS["measure"], minimum=sampling.MEASURE_MIN, group="group")
    got = [u for s in range(6) for u in sampling.drawn(units, s, "measure")]
    share = sum(u["set"] == "tuning" for u in got) / len(got)
    assert abs(share - sampling.TUNING) < 0.03
    hi = [u for u in got if u["pi"] >= 0.05]
    lo = [u for u in got if u["pi"] < 0.05]
    assert abs(sum(u["set"] == "tuning" for u in hi) / len(hi) - sum(u["set"] == "tuning" for u in lo) / len(lo)) < 0.06


def test_horvitz_thompson_recovers_known_rates_over_many_seeds():
    units = population()
    sampling.chances(units, sampling.TARGETS["measure"], minimum=sampling.MEASURE_MIN, group="group")
    rng = random.Random(3)
    for u in units:                                  # the truth: right more often at higher first scores
        u["right"] = rng.random() < (0.05 + 0.9 * u["first"])
        u["kept"] = u["first"] >= 0.7
    true_p = sum(u["right"] and u["kept"] for u in units) / sum(u["kept"] for u in units)
    true_total = sum(u["right"] for u in units)
    est_p, est_total = [], []
    for s in range(150):
        d = sampling.drawn(units, s, "measure")
        f = tuning.Frame(d)
        kept = f.col(lambda r: r["kept"])
        right = f.col(lambda r: r["right"])
        est_p.append(f.ratio(kept * right, kept))
        est_total.append(float(f.w @ right))
    assert numpy.mean(est_p) == pytest.approx(true_p, abs=0.01)
    assert numpy.mean(est_total) == pytest.approx(true_total, rel=0.03)
    lo, hi = tuning.Frame(sampling.drawn(units, 1, "measure")).boot(
        *(lambda f: (f.col(lambda r: r["kept"]) * f.col(lambda r: r["right"]), f.col(lambda r: r["kept"])))(
            tuning.Frame(sampling.drawn(units, 1, "measure"))))
    assert lo <= hi


def test_tuning_reads_the_published_rule(monkeypatch):
    calls = []
    real = decide.band_rule
    monkeypatch.setattr(decide, "band_rule", lambda *a: calls.append(a) or real(*a))
    f = tuning.measure_frame([{"score_first": 0.7, "score_context": 0.8, "second_grade": 3, "digital_p": 0.9,
                               "weight": 1, "project": "P", "truth": {"lenient": True, "strict": True, "digital": True}}])
    assert list(tuning.measure_kept(f, {"keep": 0.9, "low": 0.5, "context": 0.7, "second_grade": 2, "digital": 0.5})) == [1.0]
    assert calls and calls[0][3]["context"] == 0.7


def test_the_tuned_rule_is_the_band_rule():
    rng = random.Random(5)
    rows = []
    for i in range(400):
        first = rng.random()
        ctx = rng.choice([None, rng.random()])
        g = rng.choice([None, 0, 1, 2, 3])
        dig = rng.choice([None, rng.random()])
        rows.append({"score_first": first, "score_context": ctx, "second_grade": g, "digital_p": dig, "weight": 1,
                     "project": "P", "truth": {"lenient": True, "strict": False, "digital": True}})
    f = tuning.measure_frame(rows)
    for rule in ({"keep": 0.9, "low": 0.5, "context": 0.7, "second_grade": 2, "digital": 0.5},
                 {"keep": 0.85, "low": 0.6, "context": 0.0, "second_grade": 1, "digital": 0.3}):
        b = dict(rule)
        got = tuning.measure_kept(f, rule)
        for r, k in zip(rows, got):
            want = decide.band_rule(r["score_first"], r["score_context"], r["second_grade"], b)[0] and \
                decide.is_digital(r["digital_p"], rule["digital"])
            assert bool(k) == bool(want)


def test_the_truth_rules():
    t = G.truth("measure", {"a": {"grade": 3, "digital": 3, "general_aim": "no"},
                            "b": {"grade": 2, "digital": 2, "general_aim": "no"},
                            "c": {"grade": 0, "digital": 0, "general_aim": "yes"}})
    assert t["lenient"] and not t["strict"] and t["digital"] and not t["general_aim"] and not t["unanimous"]
    t = G.truth("measure", {"a": {"grade": 3, "digital": 1, "general_aim": "no"},
                            "b": {"grade": 3, "digital": 3, "general_aim": "no"}})
    assert t["lenient"] and t["strict"] and not t["digital"]             # one grader in two reads it digital
    t = G.truth("measure", {"a": {"grade": 3}, "b": {"grade": 3}})       # the second request not answered yet
    assert t["strict"] and t["digital"] is None and t["general_aim"] is None   # no truth, never "not digital"
    t = G.truth("measure", {"a": {"grade": 3, "digital": 0, "general_aim": "no"}, "b": {"grade": 3},
                            "c": {"grade": 2}})                               # one grader answered it: no truth
    assert t["digital"] is None and t["general_aim"] is None
    assert G.truth("measure", {"a": {"grade": 3, "digital": 0, "general_aim": "no"}}) is None
    t = G.truth("link", {"a": {"verdict": "yes"}, "b": {"verdict": "yes"}, "c": {"verdict": "unsure"}})
    assert t["lenient"] and not t["strict"]
    t = G.truth("link", {"a": {"verdict": "yes"}, "b": {"verdict": "yes"}, "c": {"verdict": "yes"}})
    assert t["strict"] and t["unanimous"]
    t = G.truth("asset", {"a": {"asset": "fiber", "type": "backbone", "second_asset": "towers", "second_type": "mast"},
                          "b": {"asset": "fiber", "type": "backbone", "second_asset": "towers", "second_type": "none"},
                          "c": {"asset": "none", "type": "none", "second_asset": "none", "second_type": "none"}})
    assert t["asset"] == "fiber" and t["type"] == "backbone" and not t["split"] and t["assets"] == ["fiber", "towers"]
    t = G.truth("asset", {"a": {"asset": "fiber", "type": "x"}, "b": {"asset": "towers", "type": "y"}})
    assert t["split"]
    t = G.truth("types", {"a": {"types": ["site"]}, "b": {"types": ["site", "route"]}, "c": {"types": []}})
    assert t["lenient"] == ["site"] and t["strict"] == []


def test_a_fields_reply_is_read_strictly():
    spec = [["grade", {"max": 3}], ["digital", {"options": ["yes", "no"]}], ["types", {"options": ["a", "b"], "many": True}]]
    q = {"reply": {"form": "fields", "fields": spec}}
    got = chat.parse('{"grade": 2, "digital": "Yes", "types": ["b", "none"]}', q["reply"])
    assert got["grade"] == 2 and got["fields"] == {"grade": 2, "digital": "yes", "types": ["b"]}
    for bad in ('{"grade": 4, "digital": "yes", "types": []}', '{"grade": 2, "types": []}',
                '{"grade": 2, "digital": "maybe", "types": []}', '{"grade": 2, "digital": "no", "types": ["c"]}'):
        with pytest.raises(ValueError):
            chat.parse(bad, q["reply"])


def test_component_numbers_are_compared_at_the_top_level_and_the_link_regions_follow_the_published_level():
    assert decide.top_component("Component 2.1: Data") == "2"
    assert decide.top_component("2b") == "2"
    assert decide.top_component("II. Platforms") == "2"
    assert decide.top_component("IV") == "4"
    assert decide.top_component("") == ""
    assert sampling.link_region("type_and_component", "same") == "2 same type, same component"
    assert sampling.link_region("asset", "unknown") == "5 same asset, component unknown"
    assert sampling.link_region("asset", "different") == "6 same asset, component differs"


def test_the_measure_grade_is_asked_alone_in_the_rulers_words_and_the_rest_in_a_second_request():
    lab, scale = Q.load("cra")[0][0], Q.load("cra")[1]
    one = Q.grader_measure_question(lab, scale)
    assert one["template"].endswith('Reply with JSON only: {{"grade": 0-3}}') and "digital" not in one["template"]
    assert [f for f, _ in one["reply"]["fields"]] == ["grade"] and one["system"] == Q.GRADER_SYSTEM
    two = Q.grader_measure_extra_question()
    assert "How far is the marked sentence about a digital technology?" in two["template"]
    assert "0: Not at all." in two["template"] and "3: It is what the sentence is about." in two["template"]
    assert [f for f, _ in two["reply"]["fields"]] == ["digital", "general_aim"]
    a = Q.grader_asset_question(*Q.asset_catalogue())
    assert [f for f, _ in a["reply"]["fields"]] == ["asset", "type", "second_asset", "second_type"]
    assert "Pick a second only if the sentence itself names two." in a["template"]


# ------------------------------------------------------------------ the three commands on an invented store

def _reply(prompt):
    if '"grade": 0-3' in prompt:
        return json.dumps({"grade": 2})
    if '"digital": 0-3' in prompt:
        return json.dumps({"digital": 3, "general_aim": "no"})
    if '"types": [' in prompt:
        return json.dumps({"types": ["site"]})
    if '"asset": "<asset id>"' in prompt:
        return json.dumps({"asset": "towers", "type": "mast", "second_asset": "none", "second_type": "none"})
    return json.dumps({"verdict": "yes"})


def _publish(pg, data):
    from wbg_portfolio import db, labels, load_pg
    lab = labels.LABEL_DIRS[0]
    with open(os.path.join(lab, "assets_patterns.csv"), "w", encoding="utf-8") as fh:
        fh.write("asset_id,pattern_id,pattern,flags,active\nfiber,backbone_01,\\broads?\\b,IGNORECASE,true\n")
    c = Q.Corpus(data, vectors=False)
    uids = [r["unit_id"] for r in c.index]
    scores = [(0.97, None, None, 0.9), (0.7, 0.8, 3, 0.8), (0.4, None, None, None), (0.1, None, None, None)]
    mf = []
    for uid, (first, ctx, g, dig) in zip(uids, scores):
        committed = first >= 0.9 or (first >= 0.5 and ctx and ctx >= 0.7 and g >= 2)
        mf.append({"label_set": "cra", "label_id": "cra", "unit_id": uid, "project_ids": "P1", "component_number": "1",
                   "score_first": first, "score_context": "" if ctx is None else ctx,
                   "second_grade": "" if g is None else g, "digital_p": "" if dig is None else dig,
                   "committed": str(committed).lower(), "is_digital": str(bool(dig and dig >= 0.5)).lower(),
                   "decided_by": "score" if first >= 0.9 else "", "text": c.texts[uid]})
    ua = [{"unit_id": uids[0], "asset_rank": 1, "asset_id": "towers", "subtype_id": "mast", "asset_level": "unit",
           "own_best_probability": 0.95, "project_ids": "P1"},
          {"unit_id": uids[1], "asset_rank": 1, "asset_id": "fiber", "subtype_id": "backbone",
           "asset_level": "second_look", "own_best_probability": 0.4, "second_look_asset_id": "fiber", "project_ids": "P1"}]
    links = [{"project_id": "P1", "label_set": "cra", "unit_id": uids[0], "package_id": "P1-1", "strength": "asset",
              "shared_asset_ids": "towers", "level": "type_and_component", "level_rank": 2, "same_type": "true",
              "component_relation": "same", "sentence_component": "1", "package_component": "1",
              "package_cancelled": "false"},
             {"project_id": "P1", "label_set": "cra", "unit_id": uids[1], "package_id": "P1-2", "strength": "asset",
              "shared_asset_ids": "fiber", "level": "asset", "level_rank": 5, "same_type": "false",
              "component_relation": "unknown", "sentence_component": "1", "package_component": "",
              "package_cancelled": "false"}]
    commitments = [{"commitment_id": "c1", "project_id": "P1", "label_set": "cra", "instrument": "investment",
                    "commitment_key": "asset_type", "asset_id": "towers", "subtype_id": "mast", "sentences": 1}]
    members = [{"commitment_id": "c1", "project_id": "P1", "label_set": "cra", "unit_id": uids[0]}]
    with pg.transaction():
        load_pg.prepare(pg)
        load_pg.load_rows(pg, "measure_findings", load_pg.FINDING_COLS, mf, "t")
        load_pg.load_rows(pg, "unit_assets", load_pg.UNIT_ASSET_COLS, ua, "t")
        load_pg.load_rows(pg, "commitment_package_links", load_pg.STRENGTH_COLS, links, "t")
        load_pg.load_rows(pg, "commitments", load_pg.COMMITMENT_COLS, commitments, "t")
        load_pg.load_rows(pg, "commitment_members", load_pg.MEMBER_COLS, members, "t")
        for kind, lset, lid, item, score, answer in (
                ("package_measure", "cra", "cra", "P1-2", 0.8, {}),
                ("package_asset_subtype", "assets", "towers", "P1-1", 1, {"labels": ["mast"]}),
                ("type", "cra", "site", uids[0], 0.7, {}), ("type", "cra", "route", uids[0], 0.2, {}),
                ("asset", "assets", "towers", uids[0], 0.95, {})):
            qsha = decide.question_shas(kind, lset if kind in ("type", "package_measure") else None)[(lset, lid)]
            pg.execute(f"INSERT INTO {db.SCHEMA}.scores (kind, label_set, label_id, item_id, question_sha, score, "
                       f"answer) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (kind, lset, lid, item, qsha, score, json.dumps(answer)))
    pg.commit()
    return uids


def test_sample_grade_and_tune_end_to_end(pg, corpus, fake_chat, monkeypatch, capsys):
    from wbg_portfolio import api, db
    data = corpus
    uids = _publish(pg, data)
    assert sampling.draw(pg, data, 1, dry_run=True) == {}
    assert not os.path.exists(sampling.folder(data, 1))
    rows = sampling.draw(pg, data, 1, log=lambda *_: None)
    assert {r["unit_id"] for r in rows["measure"]} == set(uids)            # a tiny population: every pi is 1
    assert all(r["pi"] == 1 and r["set"] in ("tuning", "holdout") for k in rows for r in rows[k])
    with pytest.raises(SystemExit):
        sampling.draw(pg, data, 1, log=lambda *_: None)                  # a drawn seed is never drawn over
    links = {r["package_id"]: r for r in rows["link"]}
    assert links["P1-1"]["same_type"] and links["P1-1"]["region"] == "2 same type, same component"
    assert links["P1-1"]["commitment_ids"] == ["c1"] and links["P1-1"]["commitment_keys"] == ["asset_type"]
    assert links["P1-2"]["region"] == "5 same asset, component unknown" and links["P1-2"]["commitment_ids"] == []
    pm = {r["package_id"]: r for r in rows["package_measure"]}
    assert pm["P1-2"]["region"] == "score0.7-0.9" and not pm["P1-2"]["has_asset"]   # scored without an asset
    assert pm["P1-1"]["region"].startswith("never asked")
    assert [r["unit_id"] for r in rows["asset"]] == [uids[0], uids[1]]
    assert rows["asset"][0]["own_scores"] == {"towers": pytest.approx(0.95)}
    first = next(r for r in rows["measure"] if r["unit_id"] == uids[0])
    assert first["type_scores"] == {"site": pytest.approx(0.7), "route": pytest.approx(0.2)} and first["ask_types"]

    fake_chat.reply = _reply
    with pytest.raises(SystemExit):                       # the cap: nothing asked when the estimate passes it
        monkeypatch.setattr(G, "measured_price", lambda conn, mdl: 0.01)
        monkeypatch.setenv("WBG_SPEND_CAP_USD", "0.001")
        monkeypatch.setenv("WBG_SPEND_SINCE", "1970-01-01")
        G.grade(pg, data, 1, api_key="k", log=lambda *_: None)
    assert fake_chat.calls == []
    monkeypatch.setattr(G, "measured_price", lambda conn, mdl: None)
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "0.0001")
    with pytest.raises(SystemExit):                       # an unmeasured grader: the pilot's upper bound passes it
        G.grade(pg, data, 1, api_key="k", log=lambda *_: None)
    assert fake_chat.calls == []
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "5")
    assert G.grade(pg, data, 1, api_key="k", log=lambda *_: None)
    n = len(fake_chat.calls)
    n_items = sum(len(v) for k, v in G.items(data, 1).items() if k)
    assert n == 3 * n_items
    assert all(b["messages"][0]["role"] == "system" and b["max_tokens"] == 1500 and "reasoning" not in b
               for b in fake_chat.calls)
    assert G.grade(pg, data, 1, api_key="k", log=lambda *_: None)    # a restart asks nothing again
    assert len(fake_chat.calls) == n
    spent = db.one(pg, f"SELECT sum(cost_usd), sum(calls) FROM {db.STORE}.spend")
    assert int(spent[1]) == n

    out, no_truth, _ = tuning.load(pg, data, 1, G.GRADERS)
    assert not no_truth and all(r["truth"]["lenient"] for r in out["measure"])
    report = tuning.tune(pg, data, 1, log=lambda *_: None, settings=["band.keep=0.85"])
    text = open(report, encoding="utf-8").read() + open(os.path.join(os.path.dirname(report), "holdout_s1.json")).read()
    for uid in uids:
        assert Q.Corpus(data, vectors=False).texts[uid].split("\n")[-1] not in text
    assert "Supply of towers" not in text and "Consulting on roads" not in text
    q = db.rows(pg, f"SELECT label_set, sheet, scope FROM {db.SCHEMA}.measure_quality")
    if any(r["set"] == "holdout" for r in rows["measure"]):
        assert q == [("cra", "holdout", "overall")]
    calls = len(fake_chat.calls)
    tuning.tune(pg, data, 1, log=lambda *_: None)                      # tuning asks nothing, any time
    assert len(fake_chat.calls) == calls

    path = G.export_for_person(data, 1, n=3, log=lambda *_: None)
    import csv
    with open(path, newline="", encoding="utf-8") as fh:
        got = list(csv.DictReader(fh))
    assert len(got) == 3 and all(r["grade"] == "" for r in got)
    assert all("grade" not in r["paragraph"] for r in got)
    got[0]["grade"], got[1]["grade"], got[1]["digital"] = "0", "3", "yes"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=G.PERSON_COLS)
        w.writeheader()
        w.writerows(got)
    res = G.read_person(pg, data, 1, path, log=lambda *_: None)
    assert res["items"] == 2
    for x in res["graders"].values():
        assert x["n"] == 2 and x["exact"] == 0 and x["lenient_agree"] == 1 and x["more_generous"] == 1 and x["digital_agree"] == 1
    with pytest.raises(SystemExit):
        G.export_for_person(data, 1, n=3, log=lambda *_: None)          # never over a file that may hold grades


def test_the_operating_points():
    base = {"lenient_precision": 0.90, "lenient_recall_asked": 0.45}
    pts = [({"t": 0.9}, base), ({"t": 0.85}, {"lenient_precision": 0.91, "lenient_recall_asked": 0.50}),
           ({"t": 0.8}, {"lenient_precision": 0.88, "lenient_recall_asked": 0.60}),
           ({"t": 0.7}, {"lenient_precision": 0.80, "lenient_recall_asked": 0.70})]
    pf, bal = tuning.operating_points(pts, base, near={"t": 0.9})
    assert pf[0] == {"t": 0.85} and bal[0] == {"t": 0.8}


def test_the_holdout_sheet_and_the_person_graded_sheets_keep_each_other(pg):
    from wbg_portfolio import db, load_pg
    row = lambda sheet, p: {"label_set": "cra", "question_sha256": "q", "sheet": sheet, "graded_units": 40,
                            "precision": p, "recall": 0.5, "evaluated_at": "2026-11-21T00:00:00Z", "scope": "measure"}
    with pg.transaction():
        load_pg.replace_quality(pg, [row("evaluation", 0.8)], {"evaluation", "training"}, "evaluate")
    with pg.transaction():
        load_pg.replace_quality(pg, [row("holdout", 0.9)], {"holdout"}, "tune")
    with pg.transaction():
        load_pg.replace_quality(pg, [row("evaluation", 0.7)], {"evaluation", "training"}, "evaluate")
    got = db.rows(pg, f"SELECT sheet, precision FROM {db.SCHEMA}.measure_quality ORDER BY sheet")
    assert [(s, float(p)) for s, p in got] == [("evaluation", 0.7), ("holdout", 0.9)]


def test_a_pair_with_no_digital_truth_is_left_out_of_the_digital_figures():
    row = lambda dig_truth: {"score_first": 0.95, "score_context": None, "second_grade": None, "digital_p": 0.9,
                             "weight": 1, "project": "P",
                             "truth": {"lenient": True, "strict": True, "digital": dig_truth}}
    rule = {"keep": 0.9, "low": 0.5, "context": 0.7, "second_grade": 2, "digital": 0.5}
    known = tuning.measure_figures(tuning.measure_frame([row(True), row(False)]), rule)
    with_unknown = tuning.measure_figures(tuning.measure_frame([row(True), row(False), row(None), row(None)]), rule)
    assert with_unknown["lenient_recall_digital"] == known["lenient_recall_digital"] == 1.0
    f = tuning.measure_frame([row(True), row(None)])
    assert list(f.dig_known) == [1.0, 0.0] and list(f.dig_t) == [1.0, 0.0]


def test_a_unit_that_cannot_be_shown_is_left_out_before_the_draw_and_reported(pg, corpus, fake_chat):
    from wbg_portfolio import db
    uids = _publish(pg, corpus)
    pg.execute(f"INSERT INTO {db.SCHEMA}.measure_findings (label_set, label_id, unit_id, project_ids, score_first, "
               f"is_committed, is_digital) SELECT label_set, label_id, 'X:not-in-this-corpus', project_ids, "
               f"score_first, is_committed, is_digital FROM {db.SCHEMA}.measure_findings WHERE unit_id = %s",
               (uids[0],))
    pg.commit()
    logged = []
    rows = sampling.draw(pg, corpus, 2, log=logged.append)
    assert "X:not-in-this-corpus" not in {r["unit_id"] for r in rows["measure"]}
    assert {r["unit_id"] for r in rows["measure"]} == set(uids)          # every pi still 1: nothing biased low
    manifest = json.load(open(os.path.join(sampling.folder(corpus, 2), "sample.json")))
    assert manifest["left_out_before_draw"]["measure"] == 1
    assert any("left out of the population before the draw" in line for line in logged)
