"""Activities (src/wbg_portfolio/activities.py): the chat model groups a project x measure's
committed, digital sentences into distinct activities; the reply is validated and repaired,
the ids are deterministic. The fake decision and chat models only."""
import hashlib
import json

import pytest

from helpers import QUIET, count, first_pass
from wbg_portfolio import activities
from wbg_portfolio import chat
from wbg_portfolio import checks
from wbg_portfolio import db
from wbg_portfolio import decide
from wbg_portfolio import funnel
from wbg_portfolio import load_pg
from wbg_portfolio import panel
from wbg_portfolio import second

NAMES = ["cra"]


def ready(pg, corpus, units=(0, 1)):
    c = first_pass(pg, corpus, 0.95, units=units)
    funnel.run(pg, corpus, "assets", corpus=c, api_key="k", **QUIET)
    second.digital(pg, corpus, c, NAMES, api_key="k", **QUIET)
    return c


def reply(*acts):
    return lambda p: json.dumps({"activities": [{"label": l, "sentences": s} for l, s in acts]})


# ------------------------------------------------------------------ the reply

def test_the_activities_reply_is_read_only_in_its_shape():
    r = {"form": "activities"}
    got = chat.parse('{"activities": [{"label": " Build  towers ", "sentences": [1, 2]}]}', r)
    assert got["activities"] == [{"label": "Build towers", "sentences": [1, 2]}]
    for bad in ('{"activities": []}', '{"activities": [{"label": "a", "sentences": ["1"]}]}',
                '{"activities": [{"label": "a"}]}', '{"activities": [{"label": "a", "sentences": [true]}]}',
                "Activity one: sentences 1 and 2", '{"groups": []}'):
        with pytest.raises(ValueError):
            chat.parse(bad, r)


def test_a_reply_that_drops_or_repeats_a_sentence_is_repaired():
    acts = [{"label": "A", "sentences": [1, 2]}, {"label": "B", "sentences": [2, 3, 9]}, {"label": "C", "sentences": [9]}]
    out, counts = activities.repair(acts, 5)
    assert out == [{"label": "A", "numbers": [1, 2]}, {"label": "B", "numbers": [3]},
                   {"label": "", "numbers": [4]}, {"label": "", "numbers": [5]}]
    assert counts == {"missing": 2, "repeated": 1, "unknown": 2, "empty": 1}
    flat = [n for a in out for n in a["numbers"]]
    assert sorted(flat) == [1, 2, 3, 4, 5]                               # every sentence exactly once
    assert activities.repair([{"label": "A", "sentences": [1, 2]}], 2) == (
        [{"label": "A", "numbers": [1, 2]}], {"missing": 0, "repeated": 0, "unknown": 0, "empty": 0})


# ------------------------------------------------------------------ the groups

class Stub:
    def __init__(self, docs):
        self.docs = docs
        self.row_of = {u: i for i, u in enumerate(docs)}

    def row(self, u):
        return {"doc_id": self.docs[u]}


def test_a_group_over_the_cap_is_split_by_document_appraisal_first_and_never_cut_finer():
    c = Stub({"u1": "AF", "u2": "PAD", "u3": "AF", "u4": "PAD", "u5": "PAD"})
    kinds = {"PAD": "pad", "AF": "additional_financing"}
    assert activities.parts(["u1", "u2"], c, kinds, 2) == [["u1", "u2"]]
    assert activities.parts(["u1", "u2", "u3", "u4", "u5"], c, kinds, 3) == [["u2", "u4", "u5"], ["u1", "u3"]]
    assert activities.parts(["u2", "u4", "u5"], c, kinds, 1) == [["u2", "u4", "u5"]]    # one document: whole


def test_the_ids_are_deterministic():
    assert activities.item_id("P1", "cra", ["b", "a"]) == activities.item_id("P1", "cra", ("a", "b"))
    assert activities.item_id("P1", "cra", ["a", "b"]) != activities.item_id("P1", "cra", ["a", "c"])
    assert activities.item_id("P1", "cra", ["a"]).startswith("P1|cra|")


# ------------------------------------------------------------------ the step

def test_a_group_is_shown_numbered_with_its_places_and_asked_once(pg, corpus, fake_chat, pipeline_on):
    c = ready(pg, corpus)
    logged = []
    assert activities.run(pg, corpus, c, NAMES, api_key="k", log=logged.append) == 1
    assert len(fake_chat.calls) == 1
    p = fake_chat.prompts()[0]
    assert "1. [document: pad D; section: Project Components; block: narrative; component: 1 Connectivity]" in p
    assert "2. [document:" in p and "hazard assessment of tower sites" in p and "fibre backbone" in p
    assert "Hazard assessment (an assessment of hazards)" in p
    row = db.one(pg, f"SELECT item_id, label_id, answer FROM {db.SCHEMA}.scores WHERE kind = 'activity'")
    assert row[0].startswith("P1|cra|") and row[1] == "activity"
    assert [a["unit_ids"] for a in row[2]["activities"]] == [[c.index[0]["unit_id"], c.index[1]["unit_id"]]]
    activities.run(pg, corpus, c, NAMES, api_key="k", **QUIET)
    assert len(fake_chat.calls) == 1                                     # stored: a rerun asks nothing


def test_a_dropped_and_a_repeated_sentence_are_repaired_and_counted_in_the_log(pg, corpus, fake_chat, pipeline_on):
    c = ready(pg, corpus)
    u0, u1 = c.index[0]["unit_id"], c.index[1]["unit_id"]
    fake_chat.reply = reply(("Assess hazards", [1]))                     # drops sentence 2
    logged = []
    activities.run(pg, corpus, c, NAMES, api_key="k", log=logged.append)
    assert any("repaired 1 dropped sentence(s), 0 repeated" in line for line in logged)
    acts = db.one(pg, f"SELECT answer FROM {db.SCHEMA}.scores WHERE kind = 'activity'")[0]["activities"]
    assert [a["unit_ids"] for a in acts] == [[u0], [u1]]
    pg.execute(f"DELETE FROM {db.SCHEMA}.scores WHERE kind = 'activity'")
    pg.execute(f"DELETE FROM {db.STORE}.answers WHERE model = 'anthropic/claude-haiku-5.5'")
    pg.commit()
    fake_chat.reply = reply(("First", [1, 2]), ("Second", [2]))          # repeats sentence 2
    logged.clear()
    activities.run(pg, corpus, c, NAMES, api_key="k", log=logged.append)
    assert any("0 dropped sentence(s), 1 repeated, 0 unknown sentence number(s), 1 empty" in line for line in logged)
    acts = db.one(pg, f"SELECT answer FROM {db.SCHEMA}.scores WHERE kind = 'activity'")[0]["activities"]
    assert [(a["label"], a["unit_ids"]) for a in acts] == [("First", [u0, u1])]


def test_a_group_of_one_is_one_activity_without_a_call_and_a_changed_group_asks_again(pg, corpus, fake_chat, pipeline_on):
    c = ready(pg, corpus, units=(0,))
    assert activities.run(pg, corpus, c, NAMES, api_key="k", **QUIET) == 0
    assert not fake_chat.calls and count(pg, "activity") == 0
    assert [(r["n_sentences"], r["activity_label"]) for r in activities.rows(pg, corpus, c, NAMES)] == [
        (1, "The project will finance a hazard assessment of ...")]
    # a second sentence is committed: the group changed, so it is asked
    c = ready(pg, corpus, units=(0, 1))
    assert activities.run(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1 and len(fake_chat.calls) == 1


def test_a_dry_run_asks_nothing_and_a_failed_request_is_resumed(pg, corpus, fake_chat, pipeline_on, monkeypatch):
    c = ready(pg, corpus)
    assert activities.run(pg, corpus, c, NAMES, dry_run=True, **QUIET) is None and not fake_chat.calls
    assert count(pg, "activity") == 0
    from wbg_portfolio import api
    inner = api.post

    def down(path, b, key, **kw):
        raise api.Transport("HTTP 503")
    monkeypatch.setattr(api, "post", down)
    assert activities.run(pg, corpus, c, NAMES, api_key="k", **QUIET) is None
    monkeypatch.setattr(api, "post", inner)
    assert activities.run(pg, corpus, c, NAMES, api_key="k", **QUIET) == 1


def test_the_scope_replaces_only_that_projects_groups(pg, corpus, fake_chat, pipeline_on):
    c = ready(pg, corpus)
    assert activities.run(pg, corpus, c, NAMES, api_key="k", projects=["P9"], **QUIET) == 0
    assert count(pg, "activity") == 0 and not fake_chat.calls


def test_the_table_and_the_summary(pg, corpus, fake_chat, pipeline_on):
    c = ready(pg, corpus)
    u0, u1 = c.index[0]["unit_id"], c.index[1]["unit_id"]
    checks.types(pg, corpus, c, "cra", api_key="k", **QUIET)
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=lambda *_: None)
    logged = []
    decide.publish(pg, corpus, corpus=c, log=logged.append)
    assert any("no activities yet" in line for line in logged)           # not asked yet: absent, not invented
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.activities")[0] == 0
    activities.run(pg, corpus, c, NAMES, api_key="k", **QUIET)
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    got = db.rows(pg, f"SELECT activity_id, project_id, label_set, unit_id, n_sentences FROM {db.SCHEMA}.activities "
                      f"ORDER BY unit_id")
    aid = hashlib.md5(f"P1|cra|{'|'.join(sorted([u0, u1]))}".encode()).hexdigest()
    assert got == sorted([(aid, "P1", "cra", u0, 2), (aid, "P1", "cra", u1, 2)], key=lambda r: r[3])
    s = db.one(pg, f"SELECT activity_id, sentences, asset_ids, instruments, type_ids, link_strengths "
                   f"FROM {db.SCHEMA}.activity_summary")
    assert s[0] == aid and s[1] == 2 and s[2] == ["fiber", "towers"] and s[3] is None and s[4] == ["site"]
    # a second publish gives byte-identical ids
    decide.publish(pg, corpus, corpus=c, log=lambda *_: None)
    assert db.rows(pg, f"SELECT activity_id FROM {db.SCHEMA}.activities ORDER BY unit_id")[0][0] == aid
