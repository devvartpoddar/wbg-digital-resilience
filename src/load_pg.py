#!/usr/bin/env python3
"""Load the cleaned tables into Postgres, schema wbg, so they can be queried.

Reads  data/raw/documents.csv, data/appraisal/{paragraphs,sentences,rejected,
       components}.csv (+ data/appraisal/text/ for their text),
       data/procurement/*.csv
Writes Postgres: wbg.<table> for each, and wbg.loads (one row per load)

The CSV files stay the source; these tables are a queryable copy of them, and
can be rebuilt from them at any time. Paragraph text, which the CSV leaves out
(it is an offset into data/appraisal/text/), is resolved here into a text column - the
database is on the box, and text never leaves the box.

Each table is replaced inside one transaction, so a reader never sees half a
table. A table whose source file has not changed since its last load (same
SHA-256) is skipped, so re-running is cheap. --force reloads everything.

Connection: $WBG_PG, default postgresql:///work (the box's agents database,
peer authentication over the local socket - no password anywhere).

  python3 src/load_pg.py
  python3 src/load_pg.py --force
"""
import argparse, csv, hashlib, os, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402

SCHEMA = "wbg"
DEFAULT_DSN = "postgresql:///work"

# (table, path under data/). Everything is text unless typed below.
TABLES = [
    ("documents", "raw/documents.csv"),
    ("paragraphs", "appraisal/paragraphs.csv"),
    ("sentences", "appraisal/sentences.csv"),
    ("rejected", "appraisal/rejected.csv"),
    ("components", "appraisal/components.csv"),
    ("packages", "procurement/packages.csv"),
    ("superseded_packages", "procurement/superseded_packages.csv"),
    ("notices", "procurement/notices.csv"),
    ("awards", "procurement/awards.csv"),
]
INTEGER = {"ordinal", "char_start", "char_end", "n_tokens", "n_chars",
           "page_from", "page_to"}
NUMERIC = {"estimated_amount", "actual_amount", "total_amount", "supplier_amount"}
INDEXED = {"doc_id", "paragraph_id", "sentence_id", "project_id", "package_id", "notice_id",
           "contract_id", "unit_id", "borrower_ref_norm"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def col_type(name):
    if name in INTEGER:
        return "integer"
    if name in NUMERIC:
        return "numeric"
    return "text"


def convert(name, value, bad):
    """'' -> NULL; integers and amounts parsed, unparseable ones NULL and counted."""
    if value is None or value == "":
        return None
    kind = col_type(name)
    try:
        if kind == "integer":
            return int(value)
        if kind == "numeric":
            return float(value.replace(",", ""))
    except ValueError:
        bad[name] = bad.get(name, 0) + 1
        return None
    return value


TEXT_TABLES = {"paragraphs", "sentences"}


def rows_with_text(data, rows):
    """Paragraph or sentence rows plus the text their offsets point at."""
    cache = {}
    for r in rows:
        did = r["doc_id"]
        if did not in cache:
            with open(where(data, "text", f"{did}.txt"), encoding="utf-8") as fh:
                cache = {did: fh.read()}        # one document at a time
        r = dict(r)
        r["text"] = cache[did][int(r["char_start"]):int(r["char_end"])]
        yield r


def load_table(conn, data, table, rel):
    path = os.path.join(data, rel)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = reader if table not in TEXT_TABLES else rows_with_text(data, reader)
        if table in TEXT_TABLES:
            cols.append("text")
        bad = {}
        ident = f"{SCHEMA}.{table}"
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {ident}")
            cur.execute(f"CREATE TABLE {ident} ("
                        + ", ".join(f'"{c}" {col_type(c)}' for c in cols) + ")")
            n = 0
            col_list = ", ".join(f'"{c}"' for c in cols)
            with cur.copy(f"COPY {ident} ({col_list}) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row([convert(c, r.get(c), bad) for c in cols])
                    n += 1
            for c in cols:
                if c in INDEXED:
                    cur.execute(f'CREATE INDEX ON {ident} ("{c}")')
            cur.execute(f"INSERT INTO {SCHEMA}.loads VALUES (%s, %s, %s, %s)",
                        (table, sha256_file(path), n,
                         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
    return n, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--dsn", default=os.environ.get("WBG_PG", DEFAULT_DSN))
    ap.add_argument("--force", action="store_true", help="reload unchanged tables too")
    args = ap.parse_args()

    import psycopg
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {SCHEMA}.loads ("
                     "table_name text, source_sha256 text, n_rows integer, loaded_at text)")
        for table, rel in TABLES:
            path = os.path.join(args.data, rel)
            if not os.path.exists(path):
                print(f"  {table:<20} skipped: {rel} not found")
                continue
            last = conn.execute(
                f"SELECT source_sha256 FROM {SCHEMA}.loads WHERE table_name = %s "
                "ORDER BY loaded_at DESC LIMIT 1", (table,)).fetchone()
            exists = conn.execute("SELECT to_regclass(%s)", (f"{SCHEMA}.{table}",)).fetchone()[0]
            if (not args.force and last and exists
                    and last[0] == sha256_file(path)):
                print(f"  {table:<20} unchanged")
                continue
            n, bad = load_table(conn, args.data, table, rel)
            note = ("  unparseable -> NULL: "
                    + ", ".join(f"{k}={v}" for k, v in sorted(bad.items()))) if bad else ""
            print(f"  {table:<20} {n:>8} rows{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
