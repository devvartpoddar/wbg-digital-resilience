"""Postgres: the record of every model answer, grade, fit and payment
(schema <wbg>_store, never dropped) and the tables and views the app reads
(schema wbg, rebuilt from the record and the corpus by src/wbg_portfolio/decide.py).

  $WBG_PG       the connection string (default postgresql:///work)
  $WBG_SCHEMA   the presentation schema (default wbg)
  $WBG_STORE    the store schema (default <schema>_store). A run with WBG_SCHEMA=wbg_v2
                WBG_STORE=wbg_store reuses every stored answer and writes its scores,
                decision tables and views into wbg_v2 alone.
"""
import json
import os

import psycopg

DSN = os.environ.get("WBG_PG") or "postgresql:///work"
SCHEMA = os.environ.get("WBG_SCHEMA") or "wbg"
STORE = os.environ.get("WBG_STORE") or SCHEMA + "_store"

STORE_DDL = """
CREATE SCHEMA IF NOT EXISTS {s};
CREATE TABLE IF NOT EXISTS {s}.answers (
  model text NOT NULL, state_sha text NOT NULL, question_sha text NOT NULL,
  answer jsonb NOT NULL, served text, cost_usd numeric, call_sha text, asked_at timestamptz,
  PRIMARY KEY (model, state_sha, question_sha));
CREATE TABLE IF NOT EXISTS {s}.chat_failures (
  model text NOT NULL, state_sha text NOT NULL, question_sha text NOT NULL,
  raw text, reason text, at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS chat_failures_key ON {s}.chat_failures (model, state_sha, question_sha);
CREATE TABLE IF NOT EXISTS {s}.refused (
  model text NOT NULL, state_sha text NOT NULL, question_sha text NOT NULL,
  reason text, at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (model, state_sha, question_sha));
CREATE TABLE IF NOT EXISTS {s}.states (sha text PRIMARY KEY, state jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS {s}.questions (sha text PRIMARY KEY, question jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS {s}.grades (
  kind text NOT NULL, label_set text NOT NULL, label_id text NOT NULL, item_sha text NOT NULL,
  design text NOT NULL, grade text NOT NULL, grader text, source text, question_sha text,
  stratum text, weight numeric, note text, graded_at timestamptz,
  PRIMARY KEY (kind, label_set, label_id, item_sha, design));
CREATE TABLE IF NOT EXISTS {s}.fits (
  kind text NOT NULL, label_set text NOT NULL, label_id text NOT NULL,
  question_sha text, spec jsonb NOT NULL, fitted_at timestamptz,
  PRIMARY KEY (kind, label_set, label_id));
CREATE TABLE IF NOT EXISTS {s}.spend (
  at timestamptz NOT NULL, stage text, label_set text, model text, calls integer,
  cost_usd numeric, seconds numeric);
COMMENT ON SCHEMA {s} IS 'The record: every decision-model answer (keyed on the exact text and question sent), every grade a person or an agent gave, every fitted layer and gate, and every payment. Never dropped; the wbg schema is rebuilt from it.';
"""


SCORES_DDL = """CREATE TABLE IF NOT EXISTS {s}.scores (
  kind text NOT NULL, label_set text NOT NULL, label_id text NOT NULL, item_id text NOT NULL,
  state_sha text, question_sha text, score numeric, answer jsonb, served text, route text,
  round integer, cosine numeric, rank integer, PRIMARY KEY (kind, label_set, label_id, item_id));
CREATE INDEX IF NOT EXISTS scores_item ON {s}.scores (item_id);
CREATE TABLE IF NOT EXISTS {s}.funnel_state (
  kind text NOT NULL, label_set text NOT NULL, fingerprint text NOT NULL, finished_at timestamptz,
  PRIMARY KEY (kind, label_set));
CREATE TABLE IF NOT EXISTS {s}.funnel_rounds (
  kind text NOT NULL, label_set text NOT NULL, run text NOT NULL, item_id text NOT NULL,
  label_id text NOT NULL, round integer, route text, cosine numeric,
  PRIMARY KEY (kind, label_set, run, item_id, label_id));
CREATE TABLE IF NOT EXISTS {s}.scratch_sets (
  label_set text PRIMARY KEY, label_name text, definition text, phrases text[], patterns text[],
  asked_by text, written_at timestamptz, floor numeric, note text);"""


def scores_table(conn):
    """The derived scores table (with the funnel's up-to-date record and the
    scratch sets' record), created where missing; returns the scores table's name."""
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    conn.execute(SCORES_DDL.format(s=SCHEMA))
    return f"{SCHEMA}.scores"


def connect(dsn=None, autocommit=False):
    return psycopg.connect(dsn or DSN, autocommit=autocommit)


def check_store(conn, fresh=False, log=print):
    """At the start of a paid stage: say which store the answers are read from and written to, and how many
    it holds (per model), and refuse (SystemExit, a plain message) to go on when it is empty while the scores
    schema and the store are not the default pair (wbg, wbg_store): that is a new WBG_SCHEMA without WBG_STORE,
    which would pay again for everything the real store already holds. fresh=True (--fresh-store) says an
    empty store is meant. Returns the number of answers."""
    rows_ = conn.execute(f"SELECT model, count(*) FROM {STORE}.answers GROUP BY model ORDER BY model").fetchall()
    total = sum(n for _, n in rows_)
    conn.commit()
    log(f"store {STORE} (scores in {SCHEMA}): {total:,} answers"
        + (" (" + ", ".join(f"{m} {n:,}" for m, n in rows_) + ")" if rows_ else ""))
    if total == 0 and not fresh and (SCHEMA, STORE) != ("wbg", "wbg_store"):
        raise SystemExit(f"the store {STORE} holds no answers, and the scores go to {SCHEMA}: every question "
                         f"would be asked and paid for again. Did you mean WBG_STORE=wbg_store? If this really "
                         f"is a new store, say so with --fresh-store.")
    return total


def ensure(conn):
    """The store schema and its tables (idempotent)."""
    conn.execute(STORE_DDL.format(s=STORE))
    conn.commit()


def execute(conn, sql, params=None):
    return conn.execute(sql, params)


def one(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()


def rows(conn, sql, params=None):
    return conn.execute(sql, params).fetchall()


def copy(conn, table, cols, rows_):
    """COPY rows (sequences, or dicts keyed by cols) into table. Returns the
    count. A dict or list value goes in as JSON text."""
    n = 0
    with conn.cursor() as cur, cur.copy(
            f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows_:
            vals = [r.get(c) for c in cols] if isinstance(r, dict) else list(r)
            cp.write_row([json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                          for v in vals])
            n += 1
    return n


def replace(conn, table, cols, rows_, where="", params=()):
    """Delete the rows `where` matches (every row by default) and COPY rows_
    in, in the caller's transaction. Returns the count written."""
    conn.execute(f"DELETE FROM {table}" + (f" WHERE {where}" if where else ""), params)
    return copy(conn, table, cols, rows_)


def exists(conn, table):
    return one(conn, "SELECT to_regclass(%s)", (table,))[0] is not None


def temp_keys(conn, name, cols, keys):
    """A temporary table of key tuples to join against, for lookups of many
    keys at once."""
    conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.execute(f"CREATE TEMP TABLE {name} ({', '.join(c + ' text' for c in cols)}) ON COMMIT DROP")
    copy(conn, name, cols, keys)
    return name
