"""Grading (src/grading.py): self-contained tasks, a strict fill reader,
grades keyed on text and label, the evaluation that fits and reports."""
import json

import pytest

import db
import grading
import questions as Q
from test_decide import run_all


def test_tasks_are_self_contained_and_one_per_decision(pg, corpus, fake_model, tmp_path):
    c = run_all(pg, corpus)
    tasks = grading.draw(pg, corpus, c, 7, log=lambda *_: None)
    assert tasks and all({"task_id", "kind", "question", "allowed", "weight", "chance", "design"} <= set(t) for t in tasks)
    m = next(t for t in tasks if t["kind"] == "measure")
    assert m["definition"] == "an assessment of hazards" and "Hazard assessment" in m["question"]
    assert m["scale"][3].startswith("3 = ") and m["allowed"] == grading.GRADES
    assert m["sentence"] and "»" in m["paragraph"] and m["section"] == "Project Components"
    u0 = c.index[0]["unit_id"]
    types = [t for t in tasks if t["kind"] == "type" and t["unit_id"] == u0]
    assert {t["label_id"] for t in types} == {"site", "route"} and types[0]["parent"]["label_set"] == "cra"
    assert types[0]["allowed"] == grading.YES_NO and types[0]["definition"]
    other = [t for t in tasks if t["kind"] == "other"]
    assert other and "none" in other[0]["allowed"]
    assert any(t["kind"] == "package_asset" and t["package"] for t in tasks)
    link = next(t for t in tasks if t["kind"] == "link")
    assert link["commitment"] and link["package"]
    with pytest.raises(SystemExit):
        grading.draw(pg, corpus, c, 7, log=lambda *_: None)          # a seed already drawn


def test_the_draw_is_repeatable(pg, corpus, fake_model, tmp_path):
    c = run_all(pg, corpus)
    a = grading.draw(pg, corpus, c, 3, log=lambda *_: None, dry_run=True)
    b = grading.draw(pg, corpus, c, 3, log=lambda *_: None, dry_run=True)
    assert a == b and not grading.tasks_path(corpus, 3).endswith("x") 


def fill_all(tasks, answer):
    return [{"task_id": t["task_id"], "grade": answer(t), "rationale": "test"} for t in tasks]


def write_fills(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return str(path)


def test_fills_are_checked_strictly_and_stored_by_text_and_label(pg, corpus, fake_model, tmp_path):
    c = run_all(pg, corpus)
    tasks = grading.draw(pg, corpus, c, 7, log=lambda *_: None)
    u0 = c.index[0]["unit_id"]

    def answer(t):
        if t["kind"] == "other":
            return "none"
        if t["kind"] in ("measure", "asset"):
            return "3" if t.get("unit_id") == u0 and t["label_id"] in ("cra", "towers") else "0"
        return "yes" if t.get("unit_id") == u0 or t.get("package_id") == "P1-1" else "no"
    bad = fill_all(tasks, answer)
    bad[0]["grade"] = "3.0"
    bad.append({"task_id": "S9999.01", "grade": "1"})
    with pytest.raises(SystemExit):
        grading.fill(pg, corpus, 7, write_fills(tmp_path / "bad.jsonl", bad), log=lambda *_: None)
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.grades")[0] == 0
    good = fill_all(tasks, answer)
    n = grading.fill(pg, corpus, 7, write_fills(tmp_path / "good.jsonl", good), grader="agent:test", log=lambda *_: None)
    assert n > 0
    got = {(k, ls, lid, i): g for k, ls, lid, i, g in db.rows(
        pg, f"SELECT kind, label_set, label_id, item_sha, grade FROM {db.STORE}.grades")}
    assert got[("measure", "cra", "cra", grading.sha(c.texts[u0]))] == "3"
    assert got[("type", "cra", "site", grading.sha(c.texts[u0]))] == "yes"
    assert all(v == "agent:test" for (v,) in db.rows(pg, f"SELECT DISTINCT grader FROM {db.STORE}.grades"))
    # a second fill of the same text replaces the grade
    again = fill_all(tasks, lambda t: "none" if t["kind"] == "other" else ("1" if t["kind"] in ("measure", "asset") else "no"))
    grading.fill(pg, corpus, 7, write_fills(tmp_path / "again.jsonl", again), log=lambda *_: None)
    assert db.one(pg, f"SELECT grade FROM {db.STORE}.grades WHERE kind = 'measure' AND item_sha = %s",
                  (grading.sha(c.texts[u0]),))[0] == "1"


def test_evaluate_reports_and_writes_fits_from_training_grades_only(pg, corpus, fake_model, tmp_path, monkeypatch):
    c = run_all(pg, corpus)
    tasks = grading.draw(pg, corpus, c, 7, log=lambda *_: None)
    u0 = c.index[0]["unit_id"]
    rows = fill_all(tasks, lambda t: "none" if t["kind"] == "other" else
                    ("3" if t.get("unit_id") == u0 and t["label_id"] in ("cra", "towers") else "0")
                    if t["kind"] in ("measure", "asset") else ("yes" if t.get("unit_id") == u0 else "no"))
    grading.fill(pg, corpus, 7, write_fills(tmp_path / "f.jsonl", rows), log=lambda *_: None)
    import posterior
    monkeypatch.setattr(posterior, "settings", lambda: dict(sharpness=20, centroid=True, ridge=1.0, min_graded=2,
                                                            min_each=1, near_copy=2.0, pool=True, folds=2,
                                                            fit_weight_power=0.0))
    out = grading.evaluate(pg, corpus, c, log=lambda *_: None)
    text = open(out).read()
    assert "## measure" in text and "Raw score:" in text and "Skip rule:" in text
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.fits WHERE kind = 'gate'")[0] >= 0
    q = db.rows(pg, f"SELECT label_set, sheet, graded_units FROM {db.SCHEMA}.measure_quality")
    assert q and q[0][0] == "cra" and q[0][1] == "training"
    # evaluation grades never fit: mark every grade eval, refit, no layer
    pg.execute(f"UPDATE {db.STORE}.grades SET design = 'eval'")
    pg.commit()
    grading.evaluate(pg, corpus, c, log=lambda *_: None)
    assert db.one(pg, f"SELECT count(*) FROM {db.STORE}.fits WHERE kind = 'layer'")[0] == 0


def test_legacy_file_grades_are_imported_once(pg, corpus, tmp_path):
    import os
    c = Q.Corpus(corpus)
    u0 = c.index[0]["unit_id"]
    lab = os.path.join(corpus, "labels")
    os.makedirs(lab)
    with open(os.path.join(lab, "cra_units.csv"), "w") as fh:
        fh.write("label_set,label_id,unit_id,text_sha256,question_sha256,grade,asset,type,source,graded_on,stratum,weight,unsure,sheet\n")
        fh.write(f"cra,cra,{u0},{grading.sha(c.texts[u0])},q,3,towers,site,s7,2026-10-20,train:near,4.0,,training\n")
        fh.write(f"cra,cra,{c.index[1]['unit_id']},{grading.sha(c.texts[c.index[1]['unit_id']])},q,0,,,s7,2026-10-20,eval:not_committed,2.0,,evaluation\n")
    n = grading.import_legacy(pg, corpus, log=lambda *_: None)
    got = db.rows(pg, f"SELECT kind, label_id, grade, design, weight FROM {db.STORE}.grades ORDER BY kind, label_id")
    assert ("measure", "cra", "3", "train", 4) in [(k, l, g, d, int(w)) for k, l, g, d, w in got]
    assert ("type", "site", "yes", "train", 4) in [(k, l, g, d, int(w)) for k, l, g, d, w in got]
    assert ("type", "route", "no", "train", 4) in [(k, l, g, d, int(w)) for k, l, g, d, w in got]
    assert any(d == "eval" for *_, d, _w in got) and n == len(got)
