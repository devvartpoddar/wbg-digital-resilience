"""The real stage entry points, as ./wbg_portfolio run calls them (cli.main), from
classify to publish, on the invented corpus with the fake decision and chat models:
band rule, digital rule and link strengths on, the scores in one schema and the
stored answers read from a store of another name (WBG_SCHEMA / WBG_STORE). No
model is called."""
import os
import uuid

import numpy
import pytest

from wbg_portfolio import api
from wbg_portfolio import cli
from wbg_portfolio import db
from wbg_portfolio import load_pg
from wbg_portfolio import panel
from wbg_portfolio import search

NEW_TABLES = ("commitment_package_links", "activities")
NEW_VIEWS = ("not_digital_findings", "commitment_package_links_in_context", "activity_summary")


@pytest.fixture
def v2(monkeypatch):
    """Scores, decision tables and views in schema t_<x>; stored answers in t_<x>_store
    (WBG_SCHEMA=t_<x> WBG_STORE=t_<x>_store, as WBG_SCHEMA=wbg_v2 WBG_STORE=wbg_store would)."""
    try:
        conn = db.connect()
    except Exception as exc:                                    # noqa: BLE001
        pytest.skip(f"no Postgres at {db.DSN}: {exc}")
    name = "t_" + uuid.uuid4().hex[:10]
    monkeypatch.setattr(db, "SCHEMA", name)
    monkeypatch.setattr(db, "STORE", name + "_store")
    monkeypatch.setenv("WBG_SCHEMA", name)
    monkeypatch.setenv("WBG_STORE", name + "_store")
    db.ensure(conn)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {name}")
    conn.commit()
    yield conn
    conn.rollback()
    conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    conn.execute(f"DROP SCHEMA IF EXISTS {name}_store CASCADE")
    conn.commit()
    conn.close()


def wide_corpus(corpus, fake_model, monkeypatch):
    """Three sentences committed to the measure (the third is not digital), unit 1 with no
    first-pass asset, a key that is no key, the spend cap variables restored afterwards."""
    fake_model.also_yes = ("fibre backbone", "road will be widened")
    fake_model.no_fibre = ("fibre backbone",)
    folder, _ = search.run_folder("cra", corpus)
    scores = numpy.load(os.path.join(folder, "scores.npy"))
    scores[:3, :] = 0.9
    numpy.save(os.path.join(folder, "scores.npy"), scores)
    monkeypatch.setattr(api, "key", lambda required=False: "k")
    for var in ("WBG_SPEND_CAP_USD", "WBG_SPEND_SINCE"):
        monkeypatch.setenv(var, "")
    return corpus


@pytest.fixture
def wide(corpus, fake_model, fake_chat, v2_panel, monkeypatch):
    return wide_corpus(corpus, fake_model, monkeypatch)


def run(corpus, *args):
    return cli.main(["--data", corpus, "run", *args])


def rows(conn, sql):
    return db.rows(conn, sql.replace("{S}", db.SCHEMA))


def test_a_full_run_from_classify_to_publish_fills_every_new_table_and_view(v2, wide, fake_model, fake_chat, capsys):
    corpus = wide
    default = fake_chat.reply
    fake_chat.reply = lambda p: ('{"assets": [{"asset": "fiber", "subtype": "backbone"}]}'
                                 if '"assets": [{"asset"' in p and "fibre backbone" in p else default(p))
    with load_pg.transaction(v2):
        load_pg.load(v2, corpus, log=lambda *_: None)
    assert run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "1") == 0
    out = capsys.readouterr().out
    for stage in ("classify", "measure", "link", "activities", "publish"):
        assert f"#### {stage}" in out
    for table in (*NEW_TABLES, "measure_findings", "unit_assets", "commitment_packages", "package_assets"):
        assert db.exists(v2, f"{db.SCHEMA}.{table}"), table
    for t in NEW_TABLES:
        assert rows(v2, f"SELECT count(*) FROM {{S}}.{t}")[0][0] > 0, t
    for v in NEW_VIEWS:
        assert rows(v2, f"SELECT count(*) FROM {{S}}.{v}")[0][0] > 0, v
    kinds = {k for (k,) in rows(v2, "SELECT DISTINCT kind FROM {S}.scores")}
    assert {"measure", "digital", "policy", "asset", "type", "package_asset", "package_measure",
            "activity"} <= kinds and "link" not in kinds
    # the second looks that the first pass left to the chat model
    assert {"asset_second", "asset_subtype"} <= kinds
    assert {r[0] for r in rows(v2, "SELECT DISTINCT asset_level FROM {S}.unit_assets")} >= {"unit", "second_look", "none"}
    # a second run asks nothing, of either model
    v2.commit()
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    assert run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "1") == 0
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat)


def test_a_dry_run_asks_nothing_prints_estimates_and_writes_nothing(v2, wide, fake_model, fake_chat, capsys):
    corpus = wide
    assert run(corpus, "--from", "classify", "--to", "publish", "--dry-run") == 0
    out = capsys.readouterr().out
    assert not fake_model and not fake_chat.calls
    assert "requests to make" in out and "about US$" in out
    # on a fresh schema the steps after the first cannot be estimated, and say so instead of a quiet 0
    for step, until in (("digital", "measure"), ("policy", "measure"), ("asset second look", "digital"),
                        ("asset kinds", "digital"), ("package asset second look", "link"),
                        ("package_measure", "link"), ("activities", "digital")):
        assert f"{step}: cannot estimate until {until} has run" in out, step
    for stage in ("classify", "measure", "link", "activities"):
        assert f"#### {stage}" in out
    assert "#### publish" in out and db.one(v2, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 0
    assert not db.exists(v2, f"{db.SCHEMA}.activities") and not db.exists(v2, f"{db.SCHEMA}.measure_findings")


def test_a_dry_run_after_a_run_still_asks_nothing(v2, wide, fake_model, fake_chat, capsys):
    corpus = wide
    with load_pg.transaction(v2):
        load_pg.load(v2, corpus, log=lambda *_: None)
    assert run(corpus, "--from", "classify", "--to", "activities", "--max-spend", "1") == 0
    v2.commit()
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    capsys.readouterr()
    assert run(corpus, "--from", "classify", "--to", "activities", "--dry-run") == 0
    out = capsys.readouterr().out
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat) and "0 requests to make" in out


def test_a_run_for_one_project_asks_for_that_project_only_and_honours_the_cap(v2, wide, fake_model, fake_chat,
                                                                         monkeypatch, capsys):
    corpus = wide
    monkeypatch.setenv("WBG_PUBLISH_SCOPED", "1")
    monkeypatch.setattr(cli, "_sel", lambda args: cli.Selection([{"project_id": "P1"}], "test"))
    with load_pg.transaction(v2):
        load_pg.load(v2, corpus, log=lambda *_: None)
    assert run(corpus, "--from", "classify", "--to", "publish", "--project", "P1", "--max-spend", "1") == 0
    v2.commit()
    assert rows(v2, "SELECT count(*) FROM {S}.activities")[0][0] > 0
    v2.commit()
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    assert n_chat > 0
    assert run(corpus, "--from", "classify", "--to", "publish", "--project", "P1", "--max-spend", "1") == 0
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat)


def test_the_spend_cap_stops_a_run_before_the_chat_model_is_asked(v2, wide, fake_model, fake_chat, capsys):
    corpus = wide
    with pytest.raises(SystemExit):
        run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "0.0000001")
    assert not fake_chat.calls


# ------------------------------------------------------------ running beside the live data

def snapshot(conn, schema):
    """Every relation and function of a schema with its oid (a dropped and recreated one has a
    new oid), a hash of its rows and its definition: what a write, a drop or a create would change."""
    out = {}
    for oid, name, kind in conn.execute(
            "SELECT c.oid, c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind IN ('r', 'v', 'm', 'p', 'f') ORDER BY 2", (schema,)).fetchall():
        rows = ""
        if kind in ("r", "m"):
            rows = conn.execute(f'SELECT md5(coalesce(string_agg(t::text, \'|\' ORDER BY t::text), \'\')), count(*) '
                                f'FROM {schema}."{name}" t').fetchone()
        view = conn.execute("SELECT pg_get_viewdef(%s::oid)", (oid,)).fetchone()[0] if kind in ("v", "m") else ""
        out[(kind, name)] = (oid, rows, view)
    for oid, name in conn.execute("SELECT p.oid, p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                                  "WHERE n.nspname = %s ORDER BY 2", (schema,)).fetchall():
        out[("f", name)] = (oid, "", "")
    return out


def test_a_run_into_another_schema_over_the_live_store_leaves_the_live_schema_untouched(
        corpus, fake_model, fake_chat, pipeline_on, monkeypatch, capsys):
    """The live data is schema A (its presentation tables and views) with its store A_store. A run
    with WBG_SCHEMA=B WBG_STORE=A_store reads A_store's answers and writes B alone: nothing in A is
    created, dropped or written, and A_store gains no answer and loses none."""
    try:
        conn = db.connect()
    except Exception as exc:                                    # noqa: BLE001
        pytest.skip(f"no Postgres at {db.DSN}: {exc}")
    a, b = "t_" + uuid.uuid4().hex[:10], "t_" + uuid.uuid4().hex[:10]
    store_a = a + "_store"
    try:
        wide_corpus(corpus, fake_model, monkeypatch)
        # the live run: schema A, store A_store
        monkeypatch.setattr(db, "SCHEMA", a)
        monkeypatch.setattr(db, "STORE", store_a)
        db.ensure(conn)
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {a}")
        conn.commit()
        with load_pg.transaction(conn):
            load_pg.load(conn, corpus, log=lambda *_: None)
        assert run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "1") == 0
        conn.commit()
        before = snapshot(conn, a)
        answers = conn.execute(f"SELECT md5(string_agg(t::text, '|' ORDER BY t::text)), count(*) "
                               f"FROM {store_a}.answers t").fetchone()
        spend = conn.execute(f"SELECT count(*) FROM {store_a}.spend").fetchone()[0]
        assert len(before) > 20 and answers[1] > 0
        n_model, n_chat = len(fake_model), len(fake_chat.calls)
        capsys.readouterr()
        # the new run: schema B over A's store
        monkeypatch.setattr(db, "SCHEMA", b)
        monkeypatch.setenv("WBG_SCHEMA", b)
        monkeypatch.setenv("WBG_STORE", store_a)
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {b}")
        conn.commit()
        with load_pg.transaction(conn):
            load_pg.load(conn, corpus, log=lambda *_: None)
        assert run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "1") == 0
        conn.commit()
        assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat)     # every answer was in A's store
        assert db.exists(conn, f"{b}.activities") and db.exists(conn, f"{b}.commitment_package_links")
        assert conn.execute(f"SELECT count(*) FROM {b}.measure_findings").fetchone()[0] > 0
        assert snapshot(conn, a) == before                                      # nothing created, dropped or written
        assert conn.execute(f"SELECT md5(string_agg(t::text, '|' ORDER BY t::text)), count(*) "
                            f"FROM {store_a}.answers t").fetchone() == answers
        # B's own store schema is never made, and the spend ledger of A_store only grows by the new run's steps
        assert not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (b + "_store",)).fetchone()
        assert conn.execute(f"SELECT count(*) FROM {store_a}.spend").fetchone()[0] >= spend
    finally:
        conn.rollback()
        for schema in (a, b, store_a):
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()
        conn.close()


@pytest.fixture
def wide_default(corpus, fake_model, fake_chat, monkeypatch):
    """The same corpus under the shipped control_panel.csv: every new switch off."""
    return wide_corpus(corpus, fake_model, monkeypatch)


OLD_KINDS = {"measure", "asset", "type", "pair", "link", "package_asset"}


def test_with_the_shipped_panel_a_full_run_behaves_as_before_the_second_looks(
        v2, wide_default, fake_model, fake_chat, capsys):
    """No chat call, no new kind of score, no activities row, and only the decision-model questions
    the earlier stages asked (the first pass, types, pairs, links); a second run asks nothing."""
    corpus = wide_default
    assert panel.require("band.on") is False and panel.require("policy.on") is False
    with load_pg.transaction(v2):
        load_pg.load(v2, corpus, log=lambda *_: None)
    assert run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "1") == 0
    assert fake_chat.calls == []
    kinds = {k for (k,) in rows(v2, "SELECT DISTINCT kind FROM {S}.scores")}
    assert kinds <= OLD_KINDS and {"measure", "link"} <= kinds
    assert rows(v2, "SELECT count(*) FROM {S}.activities")[0][0] == 0
    # every question the decision model got is a first-pass, type, pair or link question: none is
    # the context question, the digital flag or a package-measure question
    asked = {q["instructions"] for b in fake_model for q in b["questions"].values()}
    assert asked and not any("paragraph beside" in t or "digital sector" in t for t in asked)
    v2.commit()
    n = len(fake_model)
    assert run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "1") == 0
    assert len(fake_model) == n and fake_chat.calls == []
