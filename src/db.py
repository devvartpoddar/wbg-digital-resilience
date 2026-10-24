"""Postgres: the record of every model answer, grade, fit and payment
(schema <wbg>_store, never dropped) and the tables and views Metabase reads
(schema wbg, rebuilt from the record and the corpus by src/decide.py).

  $WBG_PG       the connection string (default postgresql:///work)
  $WBG_SCHEMA   the presentation schema (default wbg); the store is <schema>_store
"""
import json
import os

import psycopg

DSN = os.environ.get("WBG_PG") or "postgresql:///work"
SCHEMA = os.environ.get("WBG_SCHEMA") or "wbg"
STORE = SCHEMA + "_store"

STORE_DDL = """
CREATE SCHEMA IF NOT EXISTS {s};
CREATE TABLE IF NOT EXISTS {s}.answers (
  model text NOT NULL, state_sha text NOT NULL, question_sha text NOT NULL,
  answer jsonb NOT NULL, served text, cost_usd numeric, call_sha text, asked_at timestamptz,
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


def connect(dsn=None, autocommit=False):
    return psycopg.connect(dsn or DSN, autocommit=autocommit)


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
