"""The real stage entry points, as ./wbg_portfolio run calls them (cli.main), from
classify to publish, on the invented corpus with the fake decision and chat models:
the scores in one schema and the stored answers read from a store of another name
(WBG_SCHEMA / WBG_STORE). No model is called."""
import os
import uuid

import numpy
import pytest

from wbg_portfolio import api
from wbg_portfolio import cli
from wbg_portfolio import db
from wbg_portfolio import load_pg
from wbg_portfolio import search

TABLES = ("commitment_package_links", "commitments", "commitment_members")
VIEWS = ("not_digital_findings", "commitment_package_links_in_context", "commitment_summary", "commitment_units")


@pytest.fixture
def schema(monkeypatch):
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
    fake_model.strong_too = ("consulting on roads",)       # a package with no asset that names the measure
    folder, _ = search.run_folder("cra", corpus)
    scores = numpy.load(os.path.join(folder, "scores.npy"))
    scores[:3, :] = 0.9
    numpy.save(os.path.join(folder, "scores.npy"), scores)
    monkeypatch.setattr(api, "key", lambda required=False: "k")
    for var in ("WBG_SPEND_CAP_USD", "WBG_SPEND_SINCE"):
        monkeypatch.setenv(var, "")
    return corpus


@pytest.fixture
def wide(corpus, fake_model, fake_chat, monkeypatch):
    return wide_corpus(corpus, fake_model, monkeypatch)


def run(corpus, *args):
    return cli.main(["--data", corpus, "run", "--fresh-store", *args])      # the test store starts empty


def rows(conn, sql):
    return db.rows(conn, sql.replace("{S}", db.SCHEMA))


def test_a_full_run_from_classify_to_publish_fills_every_new_table_and_view(schema, wide, fake_model, fake_chat, capsys):
    corpus = wide
    default = fake_chat.reply
    fake_chat.reply = lambda p: ('{"labels": ["fiber"]}'
                                 if "Which digital asset" in p and "fibre backbone" in p else default(p))
    with load_pg.transaction(schema):
        load_pg.load(schema, corpus, log=lambda *_: None)
    assert run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "1") == 0
    out = capsys.readouterr().out
    for stage in ("classify", "measure", "link", "publish"):
        assert f"#### {stage}" in out
    for table in (*TABLES, "measure_findings", "unit_assets", "package_assets"):
        assert db.exists(schema, f"{db.SCHEMA}.{table}"), table
    for t in TABLES:
        assert rows(schema, f"SELECT count(*) FROM {{S}}.{t}")[0][0] > 0, t
    for v in VIEWS:
        assert rows(schema, f"SELECT count(*) FROM {{S}}.{v}")[0][0] > 0, v
    kinds = {k for (k,) in rows(schema, "SELECT DISTINCT kind FROM {S}.scores")}
    assert {"measure", "digital", "policy", "asset", "type", "package_asset", "package_measure",
            "package_asset_subtype"} <= kinds
    # every link has a level; the commitments are made by the rule and carry their best link level
    assert {r[0] for r in rows(schema, "SELECT DISTINCT level FROM {S}.commitment_package_links")} <= {
        "strong", "type_and_component", "type", "component", "asset"}
    assert rows(schema, "SELECT count(*) FROM {S}.commitment_package_links WHERE level IS NULL")[0][0] == 0
    keys = {r[0] for r in rows(schema, "SELECT DISTINCT commitment_key FROM {S}.commitments")}
    assert keys and keys <= {"asset_type", "type", "paragraph"}
    assert rows(schema, "SELECT count(*) FROM {S}.commitments WHERE best_link_level = 'strong'")[0][0] > 0
    assert rows(schema, "SELECT count(*) FROM {S}.commitment_members")[0][0] >= \
        rows(schema, "SELECT count(*) FROM {S}.commitments")[0][0]
    # the digital question is graded on the four-grade scale; a package that has an asset has its type
    assert rows(schema, "SELECT count(*) FROM {S}.scores WHERE kind = 'digital' "
                        "AND jsonb_typeof(answer->'probabilities'->'3') IS NOT NULL")[0][0] > 0
    assert rows(schema, "SELECT count(*) FROM {S}.package_assets WHERE has_asset AND subtype_id <> ''")[0][0] > 0
    # a package with no asset is strongly linked when it names the measure
    assert rows(schema, "SELECT count(*) FROM {S}.package_assets WHERE package_id = 'P1-2' AND has_asset")[0][0] == 0
    assert rows(schema, "SELECT count(*) FROM {S}.commitment_package_links WHERE package_id = 'P1-2' "
                        "AND level = 'strong'")[0][0] > 0
    # every package is asked the strong-link question, with an asset or not
    assert {p for (p,) in rows(schema, "SELECT DISTINCT item_id FROM {S}.scores WHERE kind = 'package_measure'")} == \
        {"P1-1", "P1-2"}
    # the second looks that the first pass left to the chat model
    assert {"asset_second", "asset_subtype"} <= kinds
    assert {r[0] for r in rows(schema, "SELECT DISTINCT asset_level FROM {S}.unit_assets")} >= {"unit", "second_look", "none"}
    # a second run asks nothing, of either model
    schema.commit()
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    assert run(corpus, "--from", "classify", "--to", "publish", "--max-spend", "1") == 0
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat)


def test_a_dry_run_asks_nothing_prints_estimates_and_writes_nothing(schema, wide, fake_model, fake_chat, capsys):
    corpus = wide
    assert run(corpus, "--from", "classify", "--to", "publish", "--dry-run") == 0
    out = capsys.readouterr().out
    assert not fake_model and not fake_chat.calls
    assert "requests to make" in out and "about US$" in out
    # on a fresh schema the steps after the first cannot be estimated, and say so instead of a quiet 0
    for step, until in (("digital", "measure"), ("policy", "measure"), ("asset second look", "digital"),
                        ("asset types", "digital"), ("package asset second look", "link"),
                        ("package asset types", "link"), ("package_measure", "link")):
        assert f"{step}: cannot estimate until {until} has run" in out, step
    for stage in ("classify", "measure", "link"):
        assert f"#### {stage}" in out
    assert "#### publish" in out and db.one(schema, f"SELECT count(*) FROM {db.STORE}.answers")[0] == 0
    assert not db.exists(schema, f"{db.SCHEMA}.commitments") and not db.exists(schema, f"{db.SCHEMA}.measure_findings")


def test_a_dry_run_after_a_run_still_asks_nothing(schema, wide, fake_model, fake_chat, capsys):
    corpus = wide
    with load_pg.transaction(schema):
        load_pg.load(schema, corpus, log=lambda *_: None)
    assert run(corpus, "--from", "classify", "--to", "link", "--max-spend", "1") == 0
    schema.commit()
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    capsys.readouterr()
    assert run(corpus, "--from", "classify", "--to", "link", "--dry-run") == 0
    out = capsys.readouterr().out
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat) and "0 requests to make" in out


def test_a_run_for_one_project_asks_for_that_project_only_and_honours_the_cap(schema, wide, fake_model, fake_chat,
                                                                         monkeypatch, capsys):
    corpus = wide
    monkeypatch.setenv("WBG_PUBLISH_SCOPED", "1")
    monkeypatch.setattr(cli, "_sel", lambda args: cli.Selection([{"project_id": "P1"}], "test"))
    with load_pg.transaction(schema):
        load_pg.load(schema, corpus, log=lambda *_: None)
    assert run(corpus, "--from", "classify", "--to", "publish", "--project", "P1", "--max-spend", "1") == 0
    schema.commit()
    assert rows(schema, "SELECT count(*) FROM {S}.commitments")[0][0] > 0
    schema.commit()
    n_model, n_chat = len(fake_model), len(fake_chat.calls)
    assert n_chat > 0
    assert run(corpus, "--from", "classify", "--to", "publish", "--project", "P1", "--max-spend", "1") == 0
    assert (len(fake_model), len(fake_chat.calls)) == (n_model, n_chat)


def test_the_spend_cap_stops_a_run_before_the_chat_model_is_asked(schema, wide, fake_model, fake_chat, capsys):
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
        corpus, fake_model, fake_chat, monkeypatch, capsys):
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
        assert db.exists(conn, f"{b}.commitments") and db.exists(conn, f"{b}.commitment_package_links")
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


# ------------------------------------------------------------ killed and started again

def published(conn, schema):
    """Every table of a schema as sorted rows of text, without the columns that hold a time: what
    a reader of the published schema sees."""
    out = {}
    for (name,) in conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                "WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY 1", (schema,)).fetchall():
        cols = [c for (c,) in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
            "AND data_type NOT LIKE 'timestamp%%' AND data_type <> 'date' ORDER BY ordinal_position",
            (schema, name)).fetchall()]
        sel = ", ".join(f'"{c}"' for c in cols)
        out[name] = sorted(str(r) for r in conn.execute(f'SELECT {sel} FROM {schema}."{name}"').fetchall())
    return out


class Killer:
    """Stands between the run and the fake models: counts the requests that were answered and
    which stage asked them, and at request `at` the process 'dies': that request and every one
    after it raise KeyboardInterrupt, as a kill would stop them, until the run is started again."""

    def __init__(self, inner):
        self.inner, self.at, self.dead, self.answered, self.stage = inner, None, False, [], None
        self.conns = []

    def __call__(self, path, body, key, **kw):
        if self.dead or (self.at is not None and len(self.answered) + 1 >= self.at):
            self.dead = True
            raise KeyboardInterrupt("killed")
        got = self.inner(path, body, key, **kw)
        self.answered.append(self.stage)
        return got

    def restart(self):
        """The process died: Postgres rolls back and closes what its connections held. Then the
        same command is started again."""
        for c in self.conns:
            c.close()
        self.conns.clear()
        self.at, self.dead = None, False


@pytest.fixture
def killable(wide, fake_model, fake_chat, monkeypatch):
    """The wide corpus with a Killer in front of both fake models, told the stage by the run's
    '#### <stage>' line."""
    import builtins
    k = Killer(api.post)
    monkeypatch.setattr(api, "post", k)
    connect = db.connect

    def tracked(*a, **kw):
        c = connect(*a, **kw)
        k.conns.append(c)
        return c
    monkeypatch.setattr(db, "connect", tracked)

    def say(*a, **kw):
        if a and isinstance(a[0], str) and a[0].startswith("#### "):
            k.stage = a[0][5:]
        builtins.print(*a, **kw)
    monkeypatch.setattr(cli, "print", say, raising=False)
    return wide, k


def _fresh(conn, monkeypatch, corpus):
    name = "t_" + uuid.uuid4().hex[:10]
    monkeypatch.setattr(db, "SCHEMA", name)
    monkeypatch.setattr(db, "STORE", name + "_store")
    monkeypatch.setenv("WBG_SCHEMA", name)
    monkeypatch.setenv("WBG_STORE", name + "_store")
    db.ensure(conn)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {name}")
    conn.commit()
    with load_pg.transaction(conn):
        load_pg.load(conn, corpus, log=lambda *_: None)
    conn.commit()
    return name


def test_a_run_killed_in_any_stage_and_started_again_asks_nothing_twice_and_publishes_the_same(
        schema, killable, monkeypatch, capsys):
    """The real ./wbg_portfolio run from classify to publish, killed before each of its requests in
    turn (so in every paid stage and every step of it) and in the middle of the publish, then started
    again with the same command: the second start finishes, the two starts together make exactly the
    requests the uninterrupted run made (nothing paid twice), and the published schema is the same as
    the uninterrupted run's."""
    corpus, k = killable
    conn = schema
    args = ("--from", "classify", "--to", "publish", "--max-spend", "1")
    made = []
    try:
        whole = _fresh(conn, monkeypatch, corpus)
        made.append(whole)
        assert run(corpus, *args) == 0
        conn.commit()
        expected, asked = published(conn, whole), list(k.answered)
        assert {"classify", "measure", "link"} <= set(asked)
        for at in range(1, len(asked) + 1):                    # before each request in turn
            stage = asked[at - 1]
            name = _fresh(conn, monkeypatch, corpus)
            made.append(name)
            k.answered.clear()
            k.at = at
            with pytest.raises(KeyboardInterrupt):
                run(corpus, *args)
            conn.rollback()
            assert len(k.answered) == at - 1
            k.restart()
            assert run(corpus, *args) == 0, stage
            conn.commit()
            assert sorted(k.answered, key=str) == sorted(asked, key=str), \
                f"killed before request {at} ({stage}): asked again or asked otherwise"
            assert published(conn, name) == expected, f"killed before request {at} ({stage}): published differently"
            conn.execute(f"DROP SCHEMA {name} CASCADE")
            conn.execute(f"DROP SCHEMA {name}_store CASCADE")
            conn.commit()
            made.remove(name)
        # killed in the publish, part way through writing its tables
        name = _fresh(conn, monkeypatch, corpus)
        made.append(name)
        assert run(corpus, *args[:-2], "--to", "link", *args[-2:]) == 0
        conn.commit()
        real, calls = load_pg.load_rows, []

        def dies_on_the_third_table(*a, **kw):
            calls.append(1)
            if len(calls) == 3:
                raise KeyboardInterrupt("killed")
            return real(*a, **kw)
        monkeypatch.setattr(load_pg, "load_rows", dies_on_the_third_table)
        with pytest.raises(KeyboardInterrupt):
            run(corpus, *args)
        conn.rollback()
        k.restart()
        monkeypatch.setattr(load_pg, "load_rows", real)
        k.answered.clear()
        assert run(corpus, *args) == 0 and k.answered == []
        conn.commit()
        assert published(conn, name) == expected, "killed in publish: published differently"
    finally:
        conn.rollback()
        for name in made:
            conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
            conn.execute(f"DROP SCHEMA IF EXISTS {name}_store CASCADE")
        conn.commit()
