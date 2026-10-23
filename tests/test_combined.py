"""The combined sheets (src/combined.py): one draw for every decision, one
block per sentence and per package, and the answers written back to the
label files each decision reads, on invented rows."""
import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import check_units  # noqa: E402
import combined  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


def test_an_items_chance_is_the_largest_its_decisions_give_it():
    members = {"a": ["m:near", "asset:far"], "b": ["m:near"], "c": ["asset:far"],
               "d": ["asset:far"], "e": ["random"]}
    targets = {"m:near": 1, "asset:far": 3, "random": 0}
    got = combined.inclusion(members, targets)
    assert got["a"] == 1.0                   # asset:far wants all 3 of its 3
    assert got["b"] == 0.5                   # m:near wants 1 of 2
    assert "e" not in got                    # a stratum nobody wants draws nothing


def test_the_draw_is_repeatable_and_near_its_expected_size():
    chance = {f"u{i}": 0.1 for i in range(5000)}
    a, b = combined.poisson(chance, 7), combined.poisson(chance, 7)
    assert a == b
    assert abs(len(a) - 500) < 70            # 3 standard deviations of a binomial(5000, 0.1)
    assert combined.poisson(chance, 8) != a


def test_a_closed_blocks_blanks_are_no_and_an_open_block_is_not_read():
    lines = [{"line": "S1.01", "kind": "measure", "label_set": "cra"},
             {"line": "S1.02", "kind": "type", "label_set": "cra", "type_id": "T1"},
             {"line": "S1.03", "kind": "type", "label_set": "cra", "type_id": "T2"},
             {"line": "S1.04", "kind": "asset", "asset_id": "fiber"},
             {"line": "S1.05", "kind": "asset", "asset_id": "satellite"},
             {"line": "S1.06", "kind": "other"}]
    b1 = {"block": "S1", "lines": lines}
    b2 = {"block": "S2", "lines": [dict(x, line=x["line"].replace("S1", "S2")) for x in lines]}
    answers = {"S1.01": "3", "S1.02": "yes", "S1.05": "unsure", "S1.06": "backup_power|data_hosting",
               "S2.01": "2"}
    assert [b["block"] for b in combined.closed([b1, b2], answers)] == ["S1"]
    m, assets, unsure, types, others = combined.block_answers(
        b1, answers, ["fiber", "satellite", "data_hosting"])
    assert m == {"cra": 3}
    assert assets == {"data_hosting"}        # named on the last line; fiber left blank = 0
    assert unsure == {"satellite"}
    assert types == {"cra": ({"T1"}, set())}
    assert others == {"backup_power"}


def test_two_measure_grades_answer_the_pair_check():
    assert combined.pair_answer(3, 0) == "only A"
    assert combined.pair_answer(1, 2) == "only B"
    assert combined.pair_answer(2, 2) == "both"
    assert combined.pair_answer(0, 1) == "neither"
    assert combined.pair_answer(None, 3) == "unsure"


class _Corpus:
    def __init__(self, uids):
        self.texts = {u: f"Invented sentence {u} about the network." for u in uids}
        self.index = [{"unit_id": u, "project_ids": "P000001"} for u in uids]


def _frames():
    uids = [f"u{i:03d}" for i in range(60)]
    unit = lambda u, p, c="false": {"unit_id": u, "text": f"Invented sentence {u} about the network.",
                                    "sent": "true", "p_committed_calibrated": f"{p:.3f}",
                                    "committed": c, "paragraph_id": "par1",
                                    "asset_links": "fiber", "asset_link_level": "unit"}
    cra = {u: unit(u, 0.9 if i < 10 else 0.05) for i, u in enumerate(uids[:40])}
    bp = {u: unit(u, 0.6 if i < 5 else 0.0) for i, u in enumerate(uids[20:50])}
    m = lambda name, units, assigned: {
        "units": units, "label_id": name, "name": name.upper(), "qsha": f"q-{name}",
        "floor": 0.5, "posterior": check_units._posterior, "dir": f"{name}-run",
        "assigned": assigned, "types": {"T1": ("Type one", "One."), "T2": ("Type two", "Two.")}}
    amap = {u: {"fiber": 0.4, "satellite": 0.1} for u in uids[:30]}
    return {"measures": {"cra": m("cra", cra, {u: {"p_T1": "0.7", "p_T2": "0.2"}
                                               for u in uids[:10]}),
                         "backup_power": m("backup_power", bp, {})},
            "assets": {"amap": amap, "winners": {u: ([("fiber", 0.4)], "unit") for u in uids[:30]},
                       "ids": ["fiber", "satellite", "data_hosting"],
                       "names": {"fiber": "Fiber", "satellite": "Satellite",
                                 "data_hosting": "Data hosting"},
                       "floor": 0.3, "qsha": "q-assets"},
            "pairs": [{"unit_id": u, "set_a": "backup_power", "set_b": "cra", "p_keep_a": "0.6",
                       "p_keep_b": "0.6"} for u in uids[20:25]],
            "pairs_run": "checks-run",
            "packages": {"assets": [{"package_id": "k1", "label_id": "fiber", "p": 0.8}],
                         "links": [{"package_id": "k1", "label_set": "cra", "unit_id": "u000",
                                    "p": 0.75}],
                         "packages": {"k1": {"package_id": "k1", "project_id": "P000001",
                                             "text": "Invented supply of fibre cable.",
                                             "sha": "pk1", "component": "1"}},
                         "units": {("cra", "u000"): cra["u000"]}, "run": "links-run"},
            "corpus": _Corpus(uids), "corpus_units": uids}


def _context(frames, data, blocks):
    for b in blocks:
        b.update(passage="An invented paragraph. " + b["text"], what="passage",
                 where="P000001", sentence=b["text"], paragraph_id="par1")


def _fill(path, answers):
    wb = openpyxl.load_workbook(path)
    for title in (combined.SHEET, combined.PACKAGES):
        ws = wb[title]
        for row in ws.iter_rows(min_row=2):
            if row[0].value in answers:
                row[4].value = answers[row[0].value]
    wb.save(path)


def test_both_sheets_are_drawn_once_and_read_back_into_every_decisions_labels(tmp_path,
                                                                               monkeypatch):
    data = str(tmp_path)
    frames = _frames()
    monkeypatch.setattr(combined, "load_frames", lambda *a, **k: frames)
    monkeypatch.setattr(combined, "context", _context)
    xlsx, n, k = combined.build(data, 3, "train")
    assert n > 0 and k == 1                  # the one package carries a link and an asset
    key = list(csv.DictReader(open(combined.key_path(data, "sentences", 3), encoding="utf-8")))
    blocks = combined.blocks_from_key(key)
    # every sentence is one block, with its measures, types and assets under it
    assert len(blocks) == n
    assert len({b["unit_id"] for b in blocks}) == n
    with pytest.raises(SystemExit):
        combined.build(data, 3, "train")     # a drawn sheet is never overwritten
    # grade: every block closed; cra 3 with type T1, fiber 2; backup_power 0
    answers = {}
    for b in blocks:
        for ln in b["lines"]:
            if ln["kind"] == "measure":
                answers[ln["line"]] = "3" if ln["label_set"] == "cra" else "0"
            elif ln["kind"] == "type" and ln["type_id"] == "T1":
                answers[ln["line"]] = "yes"
            elif ln["kind"] == "asset" and ln["asset_id"] == "fiber":
                answers[ln["line"]] = "2"
        answers[b["lines"][-1]["line"]] = "none"
    pkey = list(csv.DictReader(open(combined.key_path(data, "packages", 3), encoding="utf-8")))
    for r in pkey:
        answers[r["row_id"]] = {"package_asset": "yes", "package_link": "no",
                              "other": "none"}[r["kind"]]
    _fill(xlsx, answers)
    g = combined.Graded(data, 3)
    cra = g.measure_grades("cra")
    lines = [v for rid, v in cra.items() if not rid.endswith(".o")]
    # fiber where the sentence was asked about it, none where no asset was listed
    assert lines and all(v[0] == 3 and v[1] in ("fiber", "none") for v in lines)
    assert any(v[1] == "fiber" for v in lines)
    typed = [v for v in lines if v[2]]
    assert typed and all(v[2] == "T1" for v in typed)
    # a sentence cra never read is an 'outside' row: 0, as nobody named cra on it
    assert all(v[0] == 0 for rid, v in cra.items() if rid.endswith(".o"))
    assert {v[1] for v in g.asset_grades().values()} <= {"fiber", "none"}
    pairs = g.pair_answers()
    assert pairs and set(pairs.values()) == {"only B"}       # backup_power 0, cra 3
    assert set(g.package_answers("package_asset").values()) == {"yes"}
    assert set(g.package_answers("package_link").values()) == {"no"}
    # the measure's key reads through check_units as a sheet of its own would
    kpath, graded, _ = check_units.graded_sheet("cra", data, 3)
    assert graded == cra
    keyrows = list(csv.DictReader(open(kpath, encoding="utf-8")))
    check_units.store_labels("cra", data, keyrows, graded, "combined")
    stored = list(csv.DictReader(open(os.path.join(data, "labels", "cra_units.csv"),
                                      encoding="utf-8")))
    assert len(stored) == len(graded)
    weights = {r["text_sha256"]: float(r["weight"]) for r in stored}
    assert all(w >= 1.0 for w in weights.values())          # one over a chance
    # the pair checks and the package lines go to the checks' own label files
    import judged
    assert judged.evaluate_tab(data, 3, "pairs", pairs) == len(pairs)
    assert judged.evaluate_tab(data, 3, "package_assets", g.package_answers("package_asset")) == 1
    assert {r["answer"] for r in judged.read_labels(data, "pairs")} == {"only B"}
    out, n_links = check_units.evaluate_links(data, 3, g.package_answers("package_link"))
    links = list(csv.DictReader(open(os.path.join(data, "labels", "package_links.csv"),
                                     encoding="utf-8")))
    assert n_links == 1 and links[0]["applies"] == "no" and float(links[0]["weight"]) >= 1.0
    # the sentence's assets as labels of the set 'assets'
    assert combined.store_assets(data, 3, g) == len(blocks)


def test_the_headline_scores_measure_and_asset_together():
    blocks = [{"block": "S1", "unit_id": "u1", "weight": 2.0, "lines": []},
              {"block": "S2", "unit_id": "u2", "weight": 1.0, "lines": []},
              {"block": "S3", "unit_id": "u3", "weight": 1.0, "lines": []}]

    class G:
        sblocks = blocks
        said = {"S1": ({"cra": 3}, {"fiber"}, set(), {}, set()),      # right in both
                "S2": ({"cra": 3}, {"satellite"}, set(), {}, set()),  # right measure, wrong asset
                "S3": ({"cra": 0}, {"fiber"}, set(), {}, set())}      # wrong measure
    unit = lambda u: {"unit_id": u, "sent": "true", "p_committed_calibrated": "0.9",
                      "asset_links": "fiber",
                      "asset_link_level": "unit"}
    got = combined.headline_items(G, {"cra": {u: unit(u) for u in ("u1", "u2", "u3")}},
                                  {"unit", "paragraph"})
    p, lo, hi, n = combined.weighted(got["joint"])
    assert p == pytest.approx(2 / 4)         # weight 2 right, weights 1 and 1 wrong
    assert combined.weighted(got["measure"])[0] == pytest.approx(3 / 4)
    assert lo <= p <= hi


def test_an_evaluation_sheets_answers_never_fit_a_check(tmp_path):
    import judged
    data = str(tmp_path)
    judged.store(data, "pairs", judged.PAIR_LABEL_COLS, [
        {"unit_id": "u1", "set_a": "a", "set_b": "b", "text_sha256": "t1",
         "question_sha256": "q", "answer": "both", "stratum": "train:pairs"},
        {"unit_id": "u2", "set_a": "a", "set_b": "b", "text_sha256": "t2",
         "question_sha256": "q", "answer": "both", "stratum": "eval:pairs"}],
        ("text_sha256", "set_a", "set_b", "question_sha256"))
    assert [r["unit_id"] for r in judged.fitting_rows(data, "pairs")] == ["u1"]


def test_a_dry_run_says_the_size_and_writes_nothing(tmp_path, monkeypatch, capsys):
    data = str(tmp_path)
    frames = _frames()
    monkeypatch.setattr(combined, "load_frames", lambda *a, **k: frames)
    _, n, k = combined.build(data, 3, "eval", dry_run=True)
    assert n > 0 and "rows wanted" in capsys.readouterr().out
    assert not os.path.exists(os.path.join(data, "review"))


def test_a_training_sheet_never_shows_an_evaluations_grades_and_both_are_kept(tmp_path):
    data = str(tmp_path)
    frames = _frames()
    u = frames["measures"]["cra"]["units"]["u000"]
    key = lambda stratum: [{"row_id": "1", "label_id": "cra", "unit_id": "u000",
                            "text_sha256": combined.text_sha(u["text"]),
                            "question_sha256": "q-cra", "stratum": stratum, "weight": "2"}]
    check_units.store_labels("cra", data, key("eval:committed"), {"1": (3, "fiber", "")}, "e")
    check_units.store_labels("cra", data, key("train:near"), {"1": (1, "none", "")}, "t")
    rows = list(csv.DictReader(open(os.path.join(data, "labels", "cra_units.csv"),
                                    encoding="utf-8")))
    assert sorted((r["sheet"], r["grade"]) for r in rows) == [("evaluation", "3"),
                                                             ("training", "1")]
    train = combined.prior_grades(data, frames, "train")["measures"]
    ev = combined.prior_grades(data, frames, "eval")["measures"]
    assert [r["grade"] for r in train.values()] == ["1"]
    assert [r["grade"] for r in ev.values()] == ["3"]


def test_an_answer_off_the_list_is_left_out_and_named():
    blocks = [{"block": "S1", "lines": [{"line": "S1.01", "kind": "measure"},
                                        {"line": "S1.02", "kind": "type"},
                                        {"line": "S1.03", "kind": "asset"},
                                        {"line": "S1.04", "kind": "other"}]}]
    answers = {"S1.01": "2?", "S1.02": "y", "S1.03": "3", "S1.04": "none"}
    assert combined.unreadable(blocks, answers) == ["S1.01", "S1.02"]


def test_a_seed_with_any_key_of_its_own_is_refused(tmp_path, monkeypatch):
    data = str(tmp_path)
    frames = _frames()
    monkeypatch.setattr(combined, "load_frames", lambda *a, **k: frames)
    monkeypatch.setattr(combined, "context", _context)
    os.makedirs(os.path.join(data, "review"))
    open(os.path.join(data, "review", "check_units_cra_s4.key.csv"), "w").close()
    with pytest.raises(SystemExit, match="seed 4 is taken"):
        combined.build(data, 4, "train")
