#!/usr/bin/env python3
"""Load the cleaned tables into Postgres, schema wbg, so they can be queried.

Reads  data/projects/projects.csv, data/raw/documents.csv,
       data/appraisal/{paragraphs,sentences,rejected,document_parts,
       components}.csv (+ data/appraisal/text/ for their text),
       data/procurement/{packages,superseded_packages,notices,awards}.csv
       the current search and classification runs, data/embeddings/,
       inputs/taxonomy/, data/releases/, sql/views/
       (the *_raw tables and the labels are not loaded: labels are read by
       evaluation alone, AGENTS.md rule 9)
Writes Postgres: wbg.<table> for each, the results tables (search_best,
       classify_scores, sentence_vectors, taxonomy), releases and data_release,
       keys between them, the views in sql/views/, and wbg.loads (one row per
       load, with the release it loaded); first it cuts a release (release.py)

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
import argparse, csv, glob, hashlib, os, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402

SCHEMA = "wbg"
DEFAULT_DSN = "postgresql:///work"

# (table, path under data/). Everything is text unless typed below.
TABLES = [
    ("projects", "projects/projects.csv"),
    ("documents", "raw/documents.csv"),
    ("paragraphs", "appraisal/paragraphs.csv"),
    ("sentences", "appraisal/sentences.csv"),
    ("rejected", "appraisal/rejected.csv"),
    ("document_parts", "appraisal/document_parts.csv"),
    ("components", "appraisal/components.csv"),
    ("packages", "procurement/packages.csv"),
    ("superseded_packages", "procurement/superseded_packages.csv"),
    ("notices", "procurement/notices.csv"),
    ("awards", "procurement/awards.csv"),
]
INTEGER = {"ordinal", "char_start", "char_end", "n_tokens", "n_chars",
           "page_from", "page_to", "raw_start", "raw_end", "approval_fy"}
NUMERIC = {"estimated_amount", "actual_amount", "total_amount", "supplier_amount",
           "cost_usd_m", "cost_before_usd_m", "commitment_usd_m"}
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


def load_table(conn, data, table, rel, release_id=""):
    path = os.path.join(data, rel)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = reader if table not in TEXT_TABLES else rows_with_text(data, reader)
        if table in TEXT_TABLES:
            cols.append("text")
        return load_rows(conn, table, cols, rows, sha256_file(path), release_id)


def load_rows(conn, table, cols, rows, source_sha, release_id="", types=None):
    """Replace wbg.<table> with rows, in one transaction. A table it replaces
    takes its dependent views and keys with it (CASCADE); finish() puts them
    back after every load."""
    types = types or {}
    bad = {}
    ident = f"{SCHEMA}.{table}"
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {ident} CASCADE")
        cur.execute(f"CREATE TABLE {ident} ("
                    + ", ".join(f'"{c}" {types.get(c) or col_type(c)}' for c in cols) + ")")
        n = 0
        col_list = ", ".join(f'"{c}"' for c in cols)
        with cur.copy(f"COPY {ident} ({col_list}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([r.get(c) if c in types else convert(c, r.get(c), bad)
                              for c in cols])
                n += 1
        for c in cols:
            if c in INDEXED:
                cur.execute(f'CREATE INDEX ON {ident} ("{c}")')
        cur.execute(f"INSERT INTO {SCHEMA}.loads (table_name, source_sha256, n_rows, "
                    f"loaded_at, release_id) VALUES (%s, %s, %s, %s, %s)",
                    (table, source_sha, n,
                     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), release_id))
    return n, bad


# ------------------------------------------------------------ results tables
#
# The search and the classifier write versioned runs; Postgres holds the run
# for the label set and text as they stand now, one table each across label
# sets, so a reader filters by label_set rather than finding the right table.

LABEL_SETS = ("assets", "measures")


def search_rows(data):
    """(source sha, rows) for wbg.search_best: per label set, label and unit,
    the best cosine over the label's queries, the best percentile and the
    query that matched. None without a current search run."""
    import search
    rows, shas = [], []
    for name in LABEL_SETS:
        try:
            folder, _ = search.run_folder(name, data)
        except SystemExit:
            continue
        if not os.path.exists(os.path.join(folder, "manifest.json")):
            continue
        shas.append(os.path.basename(folder))
        for h in search.hits(folder, data):
            rows.append(dict(h, label_set=name, run=os.path.basename(folder)))
    return ("|".join(shas), rows) if rows else (None, [])


def classify_rows(data):
    """(source sha, rows) for wbg.classify_scores from the current runs."""
    import classify
    rows, runs = [], []
    for name in LABEL_SETS:
        try:
            folder = classify.current_run(name, data)
        except SystemExit:
            continue
        if not folder:
            continue
        runs.append(os.path.basename(folder))
        with open(os.path.join(folder, "scores.csv"), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append(dict(r, label_set=name, run=os.path.basename(folder)))
    return ("|".join(runs), rows) if rows else (None, [])


SEARCH_COLS = ["label_set", "run", "label_id", "rank", "unit_id", "kind", "paragraph_id",
               "doc_id", "project_ids", "component_number", "subcomponent_number",
               "component_key", "score", "percentile", "best_query_id", "best_query",
               "queries_agreeing", "queries"]
CLASSIFY_COLS = ["label_set", "run", "paragraph_id", "doc_id", "project_ids",
                 "component_number", "label_id", "grade", "expected", "confidence",
                 "p0", "p1", "p2", "p3", "p4", "model_served"]
NUMERIC.update({"score", "percentile", "expected", "confidence", "p0", "p1", "p2", "p3",
                "p4"})
INTEGER.update({"rank", "queries_agreeing", "queries", "grade"})
INDEXED.update({"label_id", "label_set"})


def load_vectors(conn, data, release_id, force):
    """wbg.sentence_vectors: one row per search unit with its embedding, so a
    query can ask for the units nearest any other (pgvector). halfvec with an
    HNSW index where pgvector is 0.7 or later (3,072 dimensions is over plain
    vector's indexable limit); plain vector, unindexed, before that."""
    npy = where(data, "embeddings", "sentence_emb.npy")
    idx = where(data, "embeddings", "sentence_index.csv")
    if not (os.path.exists(npy) and os.path.exists(idx)):
        return "skipped: no embeddings yet"
    ver = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                       ).fetchone()
    if not ver:
        return "skipped: the vector extension is not installed in this database"
    sha = sha256_file(npy) + sha256_file(idx)
    if not force and _last_sha(conn, "sentence_vectors") == sha:
        return "unchanged"
    import numpy
    matrix = numpy.load(npy, mmap_mode="r")
    with open(idx, newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    half = tuple(int(x) for x in ver[0].split(".")[:2]) >= (0, 7)
    kind = f"{'halfvec' if half else 'vector'}({matrix.shape[1]})"

    def rows():
        for i, r in enumerate(index):
            vec = "[" + ",".join(f"{x:.6g}" for x in matrix[i]) + "]"
            yield {"unit_id": r["unit_id"], "paragraph_id": r["paragraph_id"],
                   "doc_id": r["doc_id"], "project_ids": r["project_ids"],
                   "text_sha256": r["text_sha256"], "embedding": vec}
    cols = ["unit_id", "paragraph_id", "doc_id", "project_ids", "text_sha256", "embedding"]
    n, _ = load_rows(conn, "sentence_vectors", cols, rows(), sha, release_id,
                     types={"embedding": kind})
    if half:
        conn.execute(f"CREATE INDEX ON {SCHEMA}.sentence_vectors USING hnsw "
                     f"(embedding halfvec_cosine_ops)")
    return f"{n:>8} rows ({kind}{', HNSW index' if half else ', no index'})"


# ------------------------------------------------------- keys, views, release
#
# Metabase follows declared keys from one table to the next. The keys are
# declared NOT VALID: they describe the joins without the load failing over
# an orphan row, which the audits report instead.

PRIMARY = {"projects": "project_id", "documents": "doc_id", "paragraphs": "paragraph_id",
           "sentences": "sentence_id", "packages": "package_id", "notices": "notice_id",
           "awards": "contract_id", "sentence_vectors": "unit_id"}
FOREIGN = [("paragraphs", "doc_id", "documents"), ("sentences", "paragraph_id", "paragraphs"),
           ("sentences", "doc_id", "documents"), ("components", "doc_id", "documents"),
           ("document_parts", "doc_id", "documents"), ("packages", "project_id", "projects"),
           ("notices", "project_id", "projects"), ("awards", "project_id", "projects"),
           ("notices", "package_id", "packages"), ("awards", "package_id", "packages"),
           ("superseded_packages", "package_id", "packages"),
           ("classify_scores", "paragraph_id", "paragraphs"),
           ("search_best", "paragraph_id", "paragraphs"),
           ("sentence_vectors", "paragraph_id", "paragraphs")]
SQL_DIR = os.path.join(ROOT, "sql")


def _exists(conn, table):
    return conn.execute("SELECT to_regclass(%s)", (f"{SCHEMA}.{table}",)).fetchone()[0]


def _last_sha(conn, table):
    last = conn.execute(f"SELECT source_sha256 FROM {SCHEMA}.loads WHERE table_name = %s "
                        "ORDER BY loaded_at DESC LIMIT 1", (table,)).fetchone()
    return last[0] if last and _exists(conn, table) else None


def finish(conn, log=print):
    """Keys and views, put back after every load (a replaced table drops
    them). A view over a table not loaded yet is skipped and reported."""
    for table, col in PRIMARY.items():
        if not _exists(conn, table):
            continue
        has = conn.execute("SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass "
                           "AND contype = 'p'", (f"{SCHEMA}.{table}",)).fetchone()
        if not has:
            try:
                with conn.transaction():
                    conn.execute(f'ALTER TABLE {SCHEMA}.{table} ADD PRIMARY KEY ("{col}")')
            except Exception as exc:                  # noqa: BLE001
                log(f"  key {table}.{col}: not added ({str(exc).splitlines()[0]})")
    for table, col, ref in FOREIGN:
        if not (_exists(conn, table) and _exists(conn, ref)):
            continue
        name = f"fk_{table}_{col}"
        has = conn.execute("SELECT 1 FROM pg_constraint WHERE conname = %s AND "
                           "conrelid = %s::regclass", (name, f"{SCHEMA}.{table}")).fetchone()
        if not has:
            with conn.transaction():
                conn.execute(f'ALTER TABLE {SCHEMA}.{table} ADD CONSTRAINT {name} '
                             f'FOREIGN KEY ("{col}") REFERENCES {SCHEMA}.{ref} '
                             f'("{PRIMARY[ref]}") NOT VALID')
    for path in sorted(glob.glob(os.path.join(SQL_DIR, "views", "*.sql"))):
        with open(path, encoding="utf-8") as fh:
            sql = fh.read()
        try:
            with conn.transaction():
                conn.execute(sql)
        except Exception as exc:                      # noqa: BLE001
            log(f"  view {os.path.basename(path)}: skipped ({str(exc).splitlines()[0]})")


def taxonomy_rows():
    """Every label of every label set in inputs/taxonomy/, so a reader sees
    names and definitions next to label ids."""
    rows = []
    for path in sorted(glob.glob(os.path.join(ROOT, "inputs", "taxonomy", "*.csv"))):
        name = os.path.basename(path)[:-4]
        if name.endswith(("_queries", "_scale")):
            continue
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                id_col = next(c for c in r if c.endswith("_id"))
                name_col = next(c for c in r if c.endswith("_name"))
                rows.append({"label_set": name, "label_id": r[id_col], "name": r[name_col],
                             "definition": r.get("definition", ""),
                             "active": r.get("active", ""), "version": r.get("version", "")})
    return rows


def load_release(conn, data, release_id):
    """wbg.releases: every release cut (data/releases/index.csv), and
    wbg.data_release: the one this load is."""
    import release
    rows = release.releases(data)
    if rows:
        load_rows(conn, "releases", release.INDEX_COLS, rows,
                  sha256_file(where(data, "releases", "index.csv")), release_id)
    m = release.manifest(data)
    load_rows(conn, "data_release", ["release_id", "commit", "code_dirty", "loaded_at",
                                     "fetched"],
              [{"release_id": release_id, "commit": m["code"]["commit"][:12],
                "code_dirty": str(m["code"]["dirty"]).lower(),
                "loaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fetched": "; ".join(f"{k} {v['latest']}" for k, v in m["fetched"].items())}],
              release_id, release_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--dsn", default=os.environ.get("WBG_PG", DEFAULT_DSN))
    ap.add_argument("--force", action="store_true", help="reload unchanged tables too")
    args = ap.parse_args()

    import psycopg
    import release
    release_id = release.cut(args.data)
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {SCHEMA}.loads ("
                     "table_name text, source_sha256 text, n_rows integer, loaded_at text)")
        conn.execute(f"ALTER TABLE {SCHEMA}.loads ADD COLUMN IF NOT EXISTS release_id text")
        for table, rel in TABLES:
            path = os.path.join(args.data, rel)
            if not os.path.exists(path):
                print(f"  {table:<20} skipped: {rel} not found")
                continue
            if not args.force and _last_sha(conn, table) == sha256_file(path):
                print(f"  {table:<20} unchanged")
                continue
            n, bad = load_table(conn, args.data, table, rel, release_id)
            note = ("  unparseable -> NULL: "
                    + ", ".join(f"{k}={v}" for k, v in sorted(bad.items()))) if bad else ""
            print(f"  {table:<20} {n:>8} rows{note}")
        for table, cols, make in (("search_best", SEARCH_COLS, search_rows),
                                  ("classify_scores", CLASSIFY_COLS, classify_rows)):
            sha, rows = make(args.data)
            if sha is None:
                print(f"  {table:<20} skipped: no current run")
            elif not args.force and _last_sha(conn, table) == sha:
                print(f"  {table:<20} unchanged")
            else:
                n, _ = load_rows(conn, table, cols, rows, sha, release_id)
                print(f"  {table:<20} {n:>8} rows")
        tax = taxonomy_rows()
        load_rows(conn, "taxonomy", ["label_set", "label_id", "name", "definition", "active",
                                     "version"], tax,
                  hashlib.sha256(repr(tax).encode()).hexdigest(), release_id)
        print(f"  {'taxonomy':<20} {len(tax):>8} rows")
        print(f"  {'sentence_vectors':<20} {load_vectors(conn, args.data, release_id, args.force)}")
        load_release(conn, args.data, release_id)
        finish(conn)
        print(f"  release {release_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
