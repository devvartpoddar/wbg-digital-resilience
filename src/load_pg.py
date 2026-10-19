#!/usr/bin/env python3
"""Load the cleaned tables into Postgres, schema wbg, so they can be queried.

Reads  data/projects/projects.csv, data/raw/documents.csv,
       data/appraisal/{paragraphs,sentences,rejected,document_parts,
       components}.csv (+ data/appraisal/text/ for their text),
       data/procurement/{packages,superseded_packages,notices,awards}.csv
       the current search and classification runs, data/embeddings/,
       every label set (src/labels.py), data/releases/, sql/views/
       (the *_raw tables and the labels are not loaded: labels are read by
       evaluation alone, AGENTS.md rule 9)
Writes Postgres: wbg.<table> for each, paragraph_projects (one row per
       paragraph and project, so a paragraph joins its project), the results
       tables (search_best, search_paragraphs, classify_scores, candidates,
       sentence_vectors, taxonomy), releases and data_release, keys between
       them, the views and functions in sql/views/, and wbg.loads (one row
       per load, with the release it loaded); first it cuts a release
       (release.py)

The CSV files stay the source; these tables are a queryable copy of them, and
can be rebuilt from them at any time. Paragraph text, which the CSV leaves out
(it is an offset into data/appraisal/text/), is resolved here into a text column - the
database is on the box, and text never leaves the box.

Columns are typed for Metabase: dates as date, timestamps as timestamptz,
flags as boolean, counts as integer, amounts as numeric; a value that does not
parse is NULL and counted. Each table is replaced inside one transaction, so a
reader never sees half a table. A table whose source file has not changed
since its last load (same SHA-256) is skipped, so re-running is cheap.
--force reloads everything.

Connection: $WBG_PG, default postgresql:///work (the box's agents database,
peer authentication over the local socket - no password anywhere).

  python3 src/load_pg.py
  python3 src/load_pg.py --force
"""
import argparse, csv, glob, hashlib, os, re, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import labels  # noqa: E402

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
INTEGER = {"ordinal", "char_start", "char_end", "n_tokens", "n_chars", "page_from", "page_to",
           "raw_start", "raw_end", "approval_fy", "pdf_bytes", "page", "part", "glue_repairs",
           "rank", "queries_agreeing", "queries", "grade", "n_rows", "paragraphs", "packages",
           "notices", "awards", "sentences_embedded"}
NUMERIC = {"estimated_amount", "actual_amount", "total_amount", "supplier_amount",
           "cost_usd_m", "cost_before_usd_m", "commitment_usd_m", "score", "percentile",
           "best_score", "best_percentile", "expected", "confidence", "p0", "p1", "p2", "p3",
           "p4"}
BOOLEAN = {"for_model", "list_item", "is_rebid", "is_placeholder", "in_latest_plan",
           "in_projects_interface", "code_dirty", "active", "included"}
INDEXED = {"doc_id", "paragraph_id", "sentence_id", "project_id", "package_id", "notice_id",
           "contract_id", "unit_id", "borrower_ref_norm", "label_id", "label_set"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def col_type(name):
    """The column's Postgres type, from its name: every *_date and *_as_of
    column is a date, every *_at a timestamp, the flags boolean."""
    if name in INTEGER:
        return "integer"
    if name in NUMERIC:
        return "numeric"
    if name in BOOLEAN:
        return "boolean"
    if name.endswith(("_date", "_as_of")):
        return "date"
    if name.endswith("_at"):
        return "timestamptz"
    return "text"


def convert(name, value, bad):
    """'' -> NULL; typed values parsed, unparseable ones NULL and counted."""
    if value is None or value == "":
        return None
    kind = col_type(name)
    try:
        if kind == "integer":
            return int(value)
        if kind == "numeric":
            return float(value.replace(",", ""))
        if kind == "boolean":
            v = value.strip().lower()
            if v in ("true", "false"):
                return v == "true"
            raise ValueError(value)
        if kind == "date":
            if not _ISO_DATE.match(value):
                raise ValueError(value)
            return value
        if kind == "timestamptz":
            if not _ISO_STAMP.match(value):
                raise ValueError(value)
            return value
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


def load_rows(conn, table, cols, rows, source_sha, release_id="", types=None, after=()):
    """Replace wbg.<table> with rows, in one transaction. A table it replaces
    takes its dependent views and keys with it (CASCADE); finish() puts them
    back after every load. `after` is SQL run inside the same transaction
    once the rows are in (an index that must exist before the load counts
    as done)."""
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
        for sql in after:
            cur.execute(sql)
        cur.execute(f"INSERT INTO {SCHEMA}.loads (table_name, source_sha256, n_rows, "
                    f"loaded_at, release_id) VALUES (%s, %s, %s, %s, %s)",
                    (table, source_sha, n, _now(), release_id))
    return n, bad


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------ derived tables

def paragraph_projects(data):
    """(source sha, rows) for wbg.paragraph_projects: one row per paragraph
    and project, from the pipe-joined project_ids, so Metabase can follow a
    paragraph to its project with a key."""
    path = where(data, "appraisal", "paragraphs.csv")
    if not os.path.exists(path):
        return None, []

    def rows():
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                for p in r["project_ids"].split("|"):
                    if p:
                        yield {"paragraph_id": r["paragraph_id"], "project_id": p,
                               "doc_id": r["doc_id"]}
    return sha256_file(path) + ":projects", rows()


# ------------------------------------------------------------ results tables
#
# The search and the classifier write versioned runs; Postgres holds the run
# for each label set and the text as they stand now, one table each across
# label sets, so a reader filters by label_set rather than finding the right
# table. Label sets are every set in inputs/taxonomy/ and analysis/labels/
# (src/labels.py) that has a current run.


def search_tables(data):
    """(source sha, best rows, rollup rows) for wbg.search_best (per label
    and unit: the best cosine over the label's queries, the best percentile
    and the query that matched) and wbg.search_paragraphs (per label and
    paragraph: its best unit). The best rows stream, label by label; the
    rollup is collected as they pass. None without any current run."""
    import search
    folders = []
    for name in labels.label_sets():
        try:
            folder, _ = search.run_folder(name, data)
        except SystemExit:
            continue
        if os.path.exists(os.path.join(folder, "manifest.json")):
            folders.append((name, folder))
    if not folders:
        return None, None, None
    rollup = []

    def best():
        for name, folder in folders:
            run = os.path.basename(folder)
            for lid in search.run_labels(folder):
                rows = search.hits(folder, data, labels=[lid])
                for h in rows:
                    yield dict(h, label_set=name, run=run)
                rollup.extend(dict(r, label_set=name, run=run)
                              for r in search.paragraph_rollup(rows))
    return "|".join(os.path.basename(f) for _, f in folders), best(), rollup


def classify_rows(data):
    """(source sha, rows) for wbg.classify_scores from the current runs."""
    import classify
    rows, runs = [], []
    for name in labels.label_sets():
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
               "component_key", "copies", "score", "percentile", "best_query_id",
               "best_query", "queries_agreeing", "queries"]
ROLLUP_COLS = ["label_set", "run", "label_id", "paragraph_id", "doc_id", "project_ids",
               "component_number", "component_key", "best_score", "best_percentile",
               "best_unit_id", "best_query_id", "units_hit"]
CLASSIFY_COLS = ["label_set", "run", "paragraph_id", "doc_id", "project_ids",
                 "component_number", "label_id", "grade", "expected", "confidence",
                 "p0", "p1", "p2", "p3", "p4", "model_served"]
INTEGER.add("units_hit")
CANDIDATE_COLS = ["label_set", "run", "label_id", "unit_id", "kind", "paragraph_id", "doc_id",
                  "project_ids", "component_number", "subcomponent_number", "component_key",
                  "copies", "route", "rank", "score", "patterns", "types", "p_commitment",
                  "firmness", "priority", "read_order"]
NUMERIC.update({"p_commitment", "priority"})
INTEGER.add("read_order")


def candidate_rows(data):
    """(source sha, rows) for wbg.candidates from each set's current
    candidates (src/candidates.py), or (None, [])."""
    import candidates
    rows, runs = [], []
    for name in labels.label_sets():
        folder = candidates.current(name, data)
        if not folder:
            continue
        runs.append(os.path.basename(folder))
        with open(os.path.join(folder, "candidates.csv"), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append(dict(r, label_set=name, run=os.path.basename(folder)))
    return ("|".join(runs), rows) if rows else (None, [])


MEASURE_COLS = ["label_set", "run", "label_id", "unit_id", "kind", "paragraph_id", "doc_id",
                "project_ids", "component_number", "subcomponent_number", "component_key",
                "copies", "route", "round", "rank", "score", "patterns", "p_commitment",
                "firmness", "read_order", "asset_para", "asset_p_para", "asset_comp",
                "asset_p_comp", "asset_unit", "asset_p_unit", "assets_yes", "sent", "grade", "expected", "confidence", "p0", "p1", "p2", "p3", "p4",
                "model_served", "cost", "text"]
NUMERIC.update({"asset_p_para", "asset_p_comp", "asset_p_unit", "cost", "expected", "confidence",
                "p0", "p1", "p2", "p3", "p4", "score"})
INTEGER.update({"grade", "rank", "round"})
BOOLEAN.add("sent")


def measure_rows(data):
    """(source sha, rows) for wbg.measures: each analysis set's current
    measure run (src/measure.py, default context, no gate), or (None, [])."""
    import measure
    rows, runs = [], []
    for name in labels.label_sets():
        try:
            folder = measure.current_run(name, data)
        except SystemExit:
            continue
        if not folder:
            continue
        runs.append(os.path.basename(folder))
        with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append(dict(r, label_set=name, run=os.path.basename(folder)))
    return ("|".join(runs), rows) if rows else (None, [])


def vector_kind(conn, dim):
    """The pgvector column type for dim dimensions, or None without the
    extension: halfvec with an HNSW index from pgvector 0.7 (plain vector
    cannot be indexed past 2,000 dimensions), plain vector unindexed before."""
    ver = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                       ).fetchone()
    if not ver:
        return None
    half = tuple(int(x) for x in ver[0].split(".")[:2]) >= (0, 7)
    return f"{'halfvec' if half else 'vector'}({dim})"


def load_vectors(conn, data, release_id, force):
    """wbg.sentence_vectors: one row per search unit with its embedding, so a
    query can ask for the units nearest any other (pgvector). The index is
    built inside the load's transaction, so an interrupted build leaves no
    load recorded and the next run does it again."""
    npy = where(data, "embeddings", "sentence_emb.npy")
    idx = where(data, "embeddings", "sentence_index.csv")
    if not (os.path.exists(npy) and os.path.exists(idx)):
        return "skipped: no embeddings yet"
    sha = sha256_file(npy) + sha256_file(idx)
    if not force and _last_sha(conn, "sentence_vectors") == sha:
        return "unchanged"
    import numpy
    matrix = numpy.load(npy, mmap_mode="r")
    with open(idx, newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    if len(index) != matrix.shape[0]:
        raise SystemExit("load_pg: sentence_emb.npy and sentence_index.csv disagree on rows; "
                         "run ./wbg search again")
    kind = vector_kind(conn, matrix.shape[1])
    if kind is None:
        return "skipped: the vector extension is not installed in this database"

    def rows():
        for i, r in enumerate(index):
            vec = "[" + ",".join(f"{x:.6g}" for x in matrix[i]) + "]"
            yield {"unit_id": r["unit_id"], "paragraph_id": r["paragraph_id"],
                   "doc_id": r["doc_id"], "project_ids": r["project_ids"],
                   "text_sha256": r["text_sha256"], "embedding": vec}
    cols = ["unit_id", "paragraph_id", "doc_id", "project_ids", "text_sha256", "embedding"]
    half = kind.startswith("halfvec")
    after = [f"CREATE INDEX ON {SCHEMA}.sentence_vectors USING hnsw "
             f"(embedding halfvec_cosine_ops)"] if half else []
    n, _ = load_rows(conn, "sentence_vectors", cols, rows(), sha, release_id,
                     types={"embedding": kind}, after=after)
    return f"{n:>8} rows ({kind}{', HNSW index' if half else ', no index'})"


# ------------------------------------------------------- keys, views, release
#
# Metabase follows declared keys from one table to the next. The keys are
# declared NOT VALID: they describe the joins without the load failing over
# an orphan row, which the audits report instead.

PRIMARY = {"projects": ("project_id",), "documents": ("doc_id",),
           "paragraphs": ("paragraph_id",), "sentences": ("sentence_id",),
           "packages": ("package_id",), "notices": ("notice_id",), "awards": ("contract_id",),
           "sentence_vectors": ("unit_id",), "document_parts": ("doc_id", "part"),
           "paragraph_projects": ("paragraph_id", "project_id"),
           "search_paragraphs": ("label_set", "label_id", "paragraph_id"),
           "classify_scores": ("label_set", "label_id", "paragraph_id"),
           "candidates": ("label_set", "label_id", "unit_id"),
           "measures": ("label_set", "label_id", "unit_id"),
           "taxonomy": ("label_set", "label_id"), "releases": ("release_id",)}
FOREIGN = [("paragraphs", "doc_id", "documents"), ("sentences", "paragraph_id", "paragraphs"),
           ("sentences", "doc_id", "documents"), ("components", "doc_id", "documents"),
           ("document_parts", "doc_id", "documents"), ("packages", "project_id", "projects"),
           ("notices", "project_id", "projects"), ("awards", "project_id", "projects"),
           ("notices", "package_id", "packages"), ("awards", "package_id", "packages"),
           ("superseded_packages", "package_id", "packages"),
           ("paragraph_projects", "paragraph_id", "paragraphs"),
           ("paragraph_projects", "project_id", "projects"),
           ("classify_scores", "paragraph_id", "paragraphs"),
           ("candidates", "paragraph_id", "paragraphs"),
           ("measures", "paragraph_id", "paragraphs"),
           ("search_best", "paragraph_id", "paragraphs"),
           ("search_paragraphs", "paragraph_id", "paragraphs"),
           ("sentence_vectors", "paragraph_id", "paragraphs")]
SQL_DIR = os.path.join(ROOT, "sql")


def _exists(conn, table):
    return conn.execute("SELECT to_regclass(%s)", (f"{SCHEMA}.{table}",)).fetchone()[0]


def _last_sha(conn, table):
    last = conn.execute(f"SELECT source_sha256 FROM {SCHEMA}.loads WHERE table_name = %s "
                        "ORDER BY loaded_at DESC LIMIT 1", (table,)).fetchone()
    return last[0] if last and _exists(conn, table) else None


def _function_exists(conn, name):
    return bool(conn.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (name,)).fetchone())


def finish(conn, log=print):
    """Keys, views and functions, put back after every load (a replaced
    table drops them). A key that cannot be added (a duplicate id) and a
    view over a table not loaded yet are skipped and reported, never fatal."""
    for table, cols in PRIMARY.items():
        if not _exists(conn, table):
            continue
        has = conn.execute("SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass "
                           "AND contype = 'p'", (f"{SCHEMA}.{table}",)).fetchone()
        if not has:
            try:
                with conn.transaction():
                    conn.execute(f'ALTER TABLE {SCHEMA}.{table} ADD PRIMARY KEY ('
                                 + ", ".join(f'"{c}"' for c in cols) + ")")
            except Exception as exc:                  # noqa: BLE001
                log(f"  key {table}.{'+'.join(cols)}: not added ({str(exc).splitlines()[0]})")
    for table, col, ref in FOREIGN:
        if not (_exists(conn, table) and _exists(conn, ref)):
            continue
        name = f"fk_{table}_{col}"
        has = conn.execute("SELECT 1 FROM pg_constraint WHERE conname = %s AND "
                           "conrelid = %s::regclass", (name, f"{SCHEMA}.{table}")).fetchone()
        if not has:
            try:
                with conn.transaction():
                    conn.execute(f'ALTER TABLE {SCHEMA}.{table} ADD CONSTRAINT {name} '
                                 f'FOREIGN KEY ("{col}") REFERENCES {SCHEMA}.{ref} '
                                 f'("{PRIMARY[ref][0]}") NOT VALID')
            except Exception as exc:                  # noqa: BLE001
                log(f"  key {name}: not added ({str(exc).splitlines()[0]})")
    vec = None
    if _exists(conn, "sentence_vectors"):
        vec = conn.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE "
                           "attrelid = %s::regclass AND attname = 'embedding'",
                           (f"{SCHEMA}.sentence_vectors",)).fetchone()[0]
    for path in sorted(glob.glob(os.path.join(SQL_DIR, "views", "*.sql"))):
        with open(path, encoding="utf-8") as fh:
            sql = fh.read()
        if "openrouter(" in sql and not _function_exists(conn, "openrouter"):
            log(f"  {os.path.basename(path)}: skipped (no openrouter() function in this "
                f"database; it comes with the box's Metabase setup)")
            continue
        if "{VECTOR}" in sql:
            if not vec:
                log(f"  {os.path.basename(path)}: skipped (no sentence_vectors yet)")
                continue
            sql = sql.replace("{VECTOR}", vec)
        try:
            with conn.transaction():
                conn.execute(sql)
        except Exception as exc:                      # noqa: BLE001
            log(f"  {os.path.basename(path)}: skipped ({str(exc).splitlines()[0]})")


def taxonomy_rows():
    """Every label of every label set (src/labels.py), so a reader sees
    names and definitions next to label ids."""
    rows = []
    for name in labels.label_sets():
        for r in labels.load_set(name):
            rows.append({"label_set": name, "label_id": r["id"], "name": r["name"],
                         "definition": r["definition"], "active": r["active"],
                         "version": r["version"], "folder": r["folder"]})
    return rows


def load_release(conn, data, release_id):
    """wbg.releases: every release cut (data/releases/index.csv), and
    wbg.data_release: the one this load is, with when each kind of record
    was last fetched, so a card can say 'data as of'."""
    import release
    rows = release.releases(data)
    if rows:
        load_rows(conn, "releases", release.INDEX_COLS, rows,
                  sha256_file(where(data, "releases", "index.csv")), release_id)
    m = release.manifest(data)
    fetched = {k: v.get("latest", "") for k, v in m["fetched"].items()}
    load_rows(conn, "data_release",
              ["release_id", "commit", "code_dirty", "loaded_at", "appraisal_fetched_date",
               "procurement_fetched_date", "projects_fetched_date"],
              [{"release_id": release_id, "commit": m["code"]["commit"][:12],
                "code_dirty": str(m["code"]["dirty"]).lower(), "loaded_at": _now(),
                "appraisal_fetched_date": fetched.get("appraisal", ""),
                "procurement_fetched_date": fetched.get("procurement", ""),
                "projects_fetched_date": fetched.get("projects", "")}],
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

    def note(bad):
        return ("  unparseable -> NULL: " + ", ".join(f"{k}={v}" for k, v in sorted(bad.items()))
                if bad else "")

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
            print(f"  {table:<20} {n:>8} rows{note(bad)}")

        sha, rows = paragraph_projects(args.data)
        if sha is None:
            print(f"  {'paragraph_projects':<20} skipped: no paragraphs")
        elif not args.force and _last_sha(conn, "paragraph_projects") == sha:
            print(f"  {'paragraph_projects':<20} unchanged")
        else:
            n, _ = load_rows(conn, "paragraph_projects", ["paragraph_id", "project_id", "doc_id"],
                             rows, sha, release_id)
            print(f"  {'paragraph_projects':<20} {n:>8} rows")

        sha, best, rollup = search_tables(args.data)
        if sha is None:
            print(f"  {'search_best':<20} skipped: no current run")
        elif not args.force and _last_sha(conn, "search_best") == sha \
                and _last_sha(conn, "search_paragraphs") == sha:
            print(f"  {'search_best':<20} unchanged")
        else:
            n, _ = load_rows(conn, "search_best", SEARCH_COLS, best, sha, release_id,
                             after=[f"CREATE INDEX ON {SCHEMA}.search_best "
                                    f"(label_set, label_id, paragraph_id, score DESC)"])
            print(f"  {'search_best':<20} {n:>8} rows")
            n, _ = load_rows(conn, "search_paragraphs", ROLLUP_COLS, rollup, sha, release_id)
            print(f"  {'search_paragraphs':<20} {n:>8} rows")

        sha, rows = classify_rows(args.data)
        if sha is None:
            print(f"  {'classify_scores':<20} skipped: no current run")
        elif not args.force and _last_sha(conn, "classify_scores") == sha:
            print(f"  {'classify_scores':<20} unchanged")
        else:
            n, _ = load_rows(conn, "classify_scores", CLASSIFY_COLS, rows, sha, release_id)
            print(f"  {'classify_scores':<20} {n:>8} rows")

        sha, rows = candidate_rows(args.data)
        if sha is None:
            print(f"  {'candidates':<20} skipped: no current run")
        elif not args.force and _last_sha(conn, "candidates") == sha:
            print(f"  {'candidates':<20} unchanged")
        else:
            n, _ = load_rows(conn, "candidates", CANDIDATE_COLS, rows, sha, release_id)
            print(f"  {'candidates':<20} {n:>8} rows")

        sha, rows = measure_rows(args.data)
        if sha is None:
            print(f"  {'measures':<20} skipped: no current run")
        elif not args.force and _last_sha(conn, "measures") == sha:
            print(f"  {'measures':<20} unchanged")
        else:
            n, _ = load_rows(conn, "measures", MEASURE_COLS, rows, sha, release_id)
            print(f"  {'measures':<20} {n:>8} rows")

        tax = taxonomy_rows()
        tax_sha = hashlib.sha256(repr(tax).encode()).hexdigest()
        if not args.force and _last_sha(conn, "taxonomy") == tax_sha:
            print(f"  {'taxonomy':<20} unchanged")
        else:
            load_rows(conn, "taxonomy", ["label_set", "label_id", "name", "definition",
                                         "active", "version", "folder"], tax, tax_sha,
                      release_id)
            print(f"  {'taxonomy':<20} {len(tax):>8} rows")
        print(f"  {'sentence_vectors':<20} {load_vectors(conn, args.data, release_id, args.force)}")
        load_release(conn, args.data, release_id)
        finish(conn)
        print(f"  release {release_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
