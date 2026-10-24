#!/usr/bin/env python3
"""The corpus tables into Postgres, and the views over everything.

The CSV tables under data/ (projects, documents, paragraphs with their text,
components, packages, notices, awards), the derived search_units,
paragraph_projects and search_paragraphs, the sentence vectors and the
release row are loaded here, each replaced whole inside one transaction and
skipped when its source checksum is unchanged. Columns are renamed and typed
from src/schema.py, which also holds the comments Metabase shows. The
decision tables are written by src/decide.py; this module creates them empty
when missing so every view builds, and runs sql/views/*.sql in order, each
file fatal when it fails.

  python3 src/load_pg.py [--force]
"""
import argparse
import contextlib
import csv
import glob
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
from common import file_sha256  # noqa: E402
import db  # noqa: E402
import labels  # noqa: E402
import schema as S  # noqa: E402

LOCK_TIMEOUT = "2min"
# (table, path under data/)
CORPUS = [("projects", "projects/projects.csv"), ("documents", "raw/documents.csv"),
          ("paragraphs", "appraisal/paragraphs.csv"), ("components", "appraisal/components.csv"),
          ("packages", "procurement/packages.csv"), ("notices", "procurement/notices.csv"),
          ("awards", "procurement/awards.csv")]
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")

# The decision tables (src/decide.py), by their CSV column names.
FINDING_COLS = ["label_set", "label_id", "unit_id", "kind", "paragraph_id", "doc_id", "project_ids",
                "component_number", "subcomponent_number", "component_key", "copies", "route", "round",
                "score", "patterns", "firmness", "asset_unit", "asset_p_unit", "assets_yes", "asset_link",
                "asset_link_level", "asset_link_p", "asset_links", "template_projects", "template_share",
                "sent", "committed", "confidence", "p0", "p1", "p2", "p3", "p_committed_calibrated",
                "types_yes", "best_type", "best_p", "model_served", "text"]
GRADE_COLS = ["label_set", "unit_id", "paragraph_id", "doc_id", "project_ids", "component_number",
              "label_id", "asked", "route", "round", "best_cosine", "confidence", "p0", "p1", "p2", "p3",
              "p_committed_calibrated", "model_served"]
UNIT_ASSET_COLS = ["unit_id", "asset_rank", "paragraph_id", "project_ids", "asset_id", "probability",
                   "asset_level", "own_best_asset_id", "own_best_probability"]
PAIR_COLS = ["unit_id", "set_a", "set_b", "decision", "score_a", "score_b", "p_keep_a", "p_keep_b",
             "model_served"]
PACKAGE_ASSET_COLS = ["package_id", "project_id", "label_id", "cosine", "cosine_rank", "probability",
                      "p_calibrated", "model_served"]
LINK_COLS = ["label_set", "label_id", "unit_id", "package_id", "project_id", "shared_asset_ids", "route",
             "measure_rank", "measure_cosine", "probability", "p_calibrated", "model_served"]
LABEL_COLS = ["label_set", "label_id", "name", "definition", "active", "version", "folder"]
TYPE_COLS = ["label_set", "label_id", "type_id", "type_name", "definition", "merged_from"]
QUERY_COLS = ["label_set", "label_id", "query_id", "query", "active", "version", "file_sha256"]
PATTERN_COLS = ["label_set", "label_id", "pattern_id", "pattern", "active", "version", "file_sha256"]
SCALE_COLS = ["label_set", "label_id", "grade", "description", "version", "file_sha256"]
QUALITY_COLS = ["label_set", "version", "question_sha256", "sheet", "graded_units", "precision",
                "recall", "evaluated_at", "report_path"]
DECISION_TABLES = {"measure_findings": FINDING_COLS, "paragraph_grades": GRADE_COLS,
                   "unit_assets": UNIT_ASSET_COLS, "unit_pair_checks": PAIR_COLS,
                   "package_assets": PACKAGE_ASSET_COLS, "commitment_packages": LINK_COLS,
                   "labels": LABEL_COLS, "measure_types": TYPE_COLS, "label_queries": QUERY_COLS,
                   "label_patterns": PATTERN_COLS, "label_scale": SCALE_COLS,
                   "measure_quality": QUALITY_COLS}
ROLLUP_COLS = ["label_set", "run", "label_id", "paragraph_id", "doc_id", "project_ids",
               "component_number", "component_key", "best_score", "best_percentile",
               "best_unit_id", "best_query_id", "units_hit"]
SEARCH_UNIT_COLS = ["unit_id", "kind", "paragraph_id", "doc_id", "project_ids", "block",
                    "section_path", "component_number", "subcomponent_number", "component_key",
                    "char_start", "char_end", "n_tokens", "text_sha256", "lead_in",
                    "left_out_reason", "copy_of_unit_id", "template_projects", "template_share",
                    "text"]


def loads():
    return f"{db.SCHEMA}.loads"


def ident(table):
    return f"{db.SCHEMA}.{table}"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- locks

def report_lock_wait(conn, log=print):
    """Who held the database when a lock timed out: the sessions blocking
    this backend, else every other session with an open transaction."""
    rows = conn.execute(
        "SELECT a.pid, a.usename, coalesce(a.state, 'n/a'), "
        "       coalesce(now() - a.xact_start, now() - a.query_start), left(a.query, 200) "
        "FROM pg_stat_activity a WHERE a.datname = current_database() AND a.pid <> pg_backend_pid() "
        "  AND (a.pid = ANY (pg_blocking_pids(pg_backend_pid())) OR a.xact_start IS NOT NULL) "
        "ORDER BY a.xact_start NULLS LAST, a.pid").fetchall()
    if not rows:
        log("  no other session holds an open transaction")
    for pid, who, state, age, query in rows:
        log(f"  pid {pid} ({who}, {state}, open {age}): " + " ".join((query or "").split()))


@contextlib.contextmanager
def transaction(conn):
    """One transaction with LOCK_TIMEOUT: a load behind a reader never hangs
    silently; it rolls back, says who holds the lock and exits non-zero."""
    import psycopg.errors as pg_errors
    try:
        with conn.transaction():
            conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            yield conn
    except pg_errors.LockNotAvailable:
        print(f"load: no lock within {LOCK_TIMEOUT}; nothing was written. Who holds one:")
        report_lock_wait(conn)
        raise SystemExit(1)


# ---------------------------------------------------------------- rows in

def convert(name, value, bad, kind):
    """'' -> NULL; typed values parsed, unparseable ones NULL and counted; a
    pipe-joined list becomes an array. name is the CSV name."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        value = str(value).lower() if isinstance(value, bool) else str(value)
    try:
        if kind == "integer":
            return int(float(value))
        if kind == "numeric":
            return float(value.replace(",", ""))
        if kind == "boolean":
            v = value.strip().lower()
            if v not in ("true", "false"):
                raise ValueError(value)
            return v == "true"
        if kind == "date" and not _ISO_DATE.match(value) or \
                kind == "timestamptz" and not _ISO_STAMP.match(value):
            raise ValueError(value)
        if kind == "text[]":
            return [x for x in value.split("|") if x]
    except ValueError:
        bad[name] = bad.get(name, 0) + 1
        return None
    return value


def create_table(conn, table, cols, types=None):
    """CREATE (dropping what was there) with the schema's names and types.
    Returns the column names."""
    types = types or {}
    names = [S.rename(table, c) for c in cols]
    kinds = [types.get(c) or S.col_type(n) for c, n in zip(cols, names)]
    conn.execute(f"DROP TABLE IF EXISTS {ident(table)} CASCADE")
    conn.execute(f"CREATE TABLE {ident(table)} (" + ", ".join(f'"{n}" {k}' for n, k in zip(names, kinds)) + ")")
    for nm in names:
        if nm in S.INDEXED:
            conn.execute(f'CREATE INDEX ON {ident(table)} ("{nm}")')
    return names, kinds


def load_rows(conn, table, cols, rows, source_sha, types=None, after=()):
    """Replace the table with rows (CSV-named dicts), in the caller's
    transaction. Returns (rows written, {column: unparseable count})."""
    types = types or {}
    names, kinds = create_table(conn, table, cols, types)
    bad, n = {}, 0
    with conn.cursor() as cur, cur.copy(
            f"COPY {ident(table)} ({', '.join(chr(34) + c + chr(34) for c in names)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r.get(c) if c in types else convert(c, r.get(c), bad, k)
                          for c, k in zip(cols, kinds)])
            n += 1
    for sql in after:
        conn.execute(sql)
    conn.execute(f"INSERT INTO {loads()} (table_name, source_sha256, row_count, loaded_at) "
                 f"VALUES (%s, %s, %s, %s)", (table, source_sha, n, _now()))
    return n, bad


def last_sha(conn, table):
    row = conn.execute(f"SELECT source_sha256 FROM {loads()} WHERE table_name = %s "
                       "ORDER BY loaded_at DESC LIMIT 1", (table,)).fetchone()
    return row[0] if row and (not row[0] or db.exists(conn, ident(table)) or table == "views") else None


def prepare(conn):
    """The schema, the loads table, and every decision table created empty
    where missing, so the views always build."""
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {db.SCHEMA}")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {loads()} (table_name text, source_sha256 text, "
                 f"row_count integer, loaded_at timestamptz)")
    for table, cols in DECISION_TABLES.items():
        if not db.exists(conn, ident(table)):
            create_table(conn, table, cols)
    db.scores_table(conn)


# ---------------------------------------------------------------- the corpus

def _text_rows(data, rows):
    cache = {}
    for r in rows:
        did = r["doc_id"]
        if did not in cache:
            with open(where(data, "text", f"{did}.txt"), encoding="utf-8") as fh:
                cache = {did: fh.read()}
        yield dict(r, text=cache[did][int(r["char_start"]):int(r["char_end"])])


def _component_rows(rows):
    from keys import name_key
    for r in rows:
        yield dict(r, component_id=":".join([r["doc_id"], r.get("project_ids", ""), r["source"], r["level"],
                                             r["number"], r.get("name_key") or name_key(r["name"])]))


def load_corpus_table(conn, data, table, rel):
    path = os.path.join(data, rel)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = reader
        if table == "paragraphs":
            rows, cols = _text_rows(data, reader), cols + ["text"]
        elif table == "components":
            rows, cols = _component_rows(reader), ["component_id"] + cols
        return load_rows(conn, table, cols, rows, file_sha256(path))


def paragraph_projects(data):
    path = where(data, "appraisal", "paragraphs.csv")
    if not os.path.exists(path):
        return None, []

    def rows():
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                for p in (r.get("project_ids") or "").split("|"):
                    if p:
                        yield {"paragraph_id": r["paragraph_id"], "project_id": p, "doc_id": r["doc_id"]}
    return file_sha256(path) + ":projects", rows()


def search_unit_rows(data):
    paras, sents = where(data, "appraisal", "paragraphs.csv"), where(data, "appraisal", "sentences.csv")
    if not (os.path.exists(paras) and os.path.exists(sents)):
        return None, []
    import units

    def rows():
        texts = {}
        for u in units.search_units(data):
            d = u["doc_id"]
            if d not in texts:
                with open(where(data, "text", f"{d}.txt"), encoding="utf-8") as fh:
                    texts = {d: fh.read()}
            t = texts[d]
            yield {**{c: u.get(c, "") for c in SEARCH_UNIT_COLS},
                   "lead_in": t[int(u["lead_in_start"]):int(u["lead_in_end"])] if u["lead_in_start"] else "",
                   "left_out_reason": u["left_out"], "copy_of_unit_id": u["copy_of"],
                   "text": t[int(u["char_start"]):int(u["char_end"])]}
    code = file_sha256(os.path.join(os.path.dirname(os.path.abspath(__file__)), "units.py"))
    return file_sha256(paras) + file_sha256(sents) + code[:12] + ",".join(SEARCH_UNIT_COLS), rows()


def search_rollup(data):
    """(key, rows) of search_paragraphs: per label and paragraph, the best
    unit of every set's current search run."""
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
        return None, []

    def rows():
        for name, folder in folders:
            for lid in search.run_labels(folder):
                for r in search.paragraph_rollup(search.hits(folder, data, labels=[lid])):
                    yield dict(r, label_set=name, run=os.path.basename(folder))
    return "|".join(os.path.basename(f) for _, f in folders), rows()


def label_rows():
    return [{"label_set": name, "label_id": r["id"], "name": r["name"], "definition": r["definition"],
             "active": r["active"], "version": r["version"], "folder": r["folder"]}
            for name in labels.label_sets() for r in labels.load_set(name)]


def type_rows(data):
    """Every type shown for every set: a merged type folded into its target,
    display_name as the name; the OTH types proposed by the gap step after."""
    out = []
    for name in labels.label_sets():
        seen = set()
        for key, t in labels.type_resolution(name)[1].items():
            seen.add((key[0], t["type_id"]))
            out.append({"label_set": name, "label_id": key[0], "type_id": t["type_id"],
                        "type_name": t["type_name"], "definition": t["definition"],
                        "merged_from": "|".join(t["merged_from"])})
        path = where(data, "types", f"{name}_oth_types.csv")
        if os.path.exists(path):
            for t in labels.rows(path):
                if (t["label_id"], t["type_id"]) not in seen:
                    out.append({"label_set": name, "label_id": t["label_id"], "type_id": t["type_id"],
                                "type_name": t["type_name"], "definition": t["definition"], "merged_from": ""})
    return out


def definition_rows():
    """(queries, patterns, scale): every set's phrases, patterns and grade
    scale, with the file's checksum, so a page shows the wording a run used."""
    import questions
    queries, patterns, scale = [], [], []
    for name in labels.label_sets():
        d = labels.folder(name)
        lab = {r["id"]: r for r in labels.load_set(name)}
        for suffix, out, cols in (("_queries", queries, ("query_id", "query")),
                                  ("_patterns", patterns, ("pattern_id", "pattern"))):
            path = os.path.join(d, f"{name}{suffix}.csv")
            if not os.path.exists(path):
                continue
            sha = file_sha256(path)
            for r in labels.rows(path):
                lid = r[next(c for c in r if c.endswith("_id") and c not in cols)].strip()
                out.append({"label_set": name, "label_id": lid, cols[0]: r[cols[0]].strip(),
                            cols[1]: r[cols[1]], "active": str(labels.is_active(r)).lower(),
                            "version": (lab.get(lid) or {}).get("version", ""), "file_sha256": sha})
        path = os.path.join(d, f"{name}_scale.csv")
        if not os.path.exists(path):
            path = os.path.join(questions.ROOT, "analysis", "labels", "scale.csv")
        if os.path.exists(path):
            sha = file_sha256(path)
            grades = sorted(labels.rows(path), key=lambda r: int(r["grade"]))
            for lid, r in lab.items():
                for g in grades:
                    scale.append({"label_set": name, "label_id": lid, "grade": g["grade"],
                                  "description": " ".join(g["description"].split()).replace("{name}", r["name"]),
                                  "version": r["version"], "file_sha256": sha})
    return queries, patterns, scale


def load_vectors(conn, data, force):
    """sentence_vectors with pgvector, or a note why not."""
    npy, idx = where(data, "embeddings", "sentence_emb.npy"), where(data, "embeddings", "sentence_index.csv")
    if not (os.path.exists(npy) and os.path.exists(idx)):
        return "skipped: no embeddings yet"
    sha = file_sha256(npy) + file_sha256(idx)
    if not force and last_sha(conn, "sentence_vectors") == sha:
        return "unchanged"
    ver = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    if not ver:
        return "skipped: the vector extension is not installed in this database"
    import numpy
    matrix = numpy.load(npy, mmap_mode="r")
    with open(idx, newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    if len(index) != matrix.shape[0]:
        raise SystemExit("load_pg: sentence_emb.npy and sentence_index.csv disagree on rows")
    half = tuple(int(x) for x in ver[0].split(".")[:2]) >= (0, 7)
    kind = f"{'halfvec' if half else 'vector'}({matrix.shape[1]})"
    rows = ({"unit_id": r["unit_id"], "paragraph_id": r["paragraph_id"], "doc_id": r["doc_id"],
             "project_ids": r["project_ids"], "text_sha256": r["text_sha256"],
             "embedding": "[" + ",".join(f"{x:.6g}" for x in matrix[i]) + "]"} for i, r in enumerate(index))
    after = [f"CREATE INDEX ON {ident('sentence_vectors')} USING hnsw (embedding halfvec_cosine_ops)"] if half else []
    n, _ = load_rows(conn, "sentence_vectors", ["unit_id", "paragraph_id", "doc_id", "project_ids",
                                                 "text_sha256", "embedding"], rows, sha,
                     types={"embedding": kind}, after=after)
    return f"{n:>8} rows ({kind})"


# ---------------------------------------------------------------- the views

def views_key(conn=None):
    """A hash of what finish() builds the views from: the view files, the
    panel values they read, and the schema's comments."""
    import panel
    h = hashlib.sha256()
    for f in sorted(glob.glob(os.path.join(ROOT, "sql", "views", "*.sql"))):
        with open(f, "rb") as fh:
            h.update(os.path.basename(f).encode() + b"\0" + fh.read())
    h.update(repr(sorted(panel.settings().items())).encode())
    h.update(file_sha256(S.__file__).encode())
    return h.hexdigest()[:24]


def _lit(text):
    return "'" + text.replace("'", "''") + "'"


def finish(conn, log=print):
    """Keys, comments, then every view file in order (each fatal when it
    fails: a view missing silently is worse than a load that stops)."""
    import panel
    for table, spec in S.TABLES.items():
        if not db.exists(conn, ident(table)):
            continue
        present = {r[0] for r in conn.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped",
            (ident(table),))}
        if spec["key"] and all(c in present for c in spec["key"]) and not conn.execute(
                "SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'p'",
                (ident(table),)).fetchone():
            try:
                with conn.transaction():
                    conn.execute(f'ALTER TABLE {ident(table)} ADD PRIMARY KEY ('
                                 + ", ".join(f'"{c}"' for c in spec["key"]) + ")")
            except Exception as exc:                                  # noqa: BLE001
                log(f"  key {table}: not added ({str(exc).splitlines()[0]})")
        conn.execute(f"COMMENT ON TABLE {ident(table)} IS {_lit(spec['comment'])}")
        for col, text in spec["columns"].items():
            if col in present:
                conn.execute(f'COMMENT ON COLUMN {ident(table)}."{col}" IS {_lit(text)}')
    vec = None
    if db.exists(conn, ident("sentence_vectors")):
        vec = conn.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE "
                           "attrelid = %s::regclass AND attname = 'embedding'",
                           (ident("sentence_vectors"),)).fetchone()[0]
    for path in sorted(glob.glob(os.path.join(ROOT, "sql", "views", "*.sql"))):
        with open(path, encoding="utf-8") as fh:
            sql = fh.read()
        if "openrouter(" in sql and not conn.execute(
                "SELECT 1 FROM pg_proc WHERE proname = 'openrouter'").fetchone():
            log(f"  {os.path.basename(path)}: skipped (no openrouter() function here)")
            continue
        if "sentence_vectors" in sql or "similar_units(" in sql or "search_phrase(" in sql:
            if not vec:
                log(f"  {os.path.basename(path)}: skipped (no sentence_vectors yet)")
                continue
            sql = sql.replace("{VECTOR}", vec)
        sql = re.sub(r"\{\{panel:([a-z0-9_.]+)\}\}", lambda m: panel.sql_value(m.group(1)), sql)
        import embed
        sql = sql.replace("{{embed_model}}", embed.DEFAULT_MODEL)
        if db.SCHEMA != "wbg":
            sql = re.sub(r"\bwbg\.", db.SCHEMA + ".", sql).replace("'wbg'", f"'{db.SCHEMA}'")
        try:
            conn.execute(sql)
        except Exception as exc:                                      # noqa: BLE001
            raise SystemExit(f"{os.path.basename(path)}: {str(exc).splitlines()[0]}")
    conn.execute(f"INSERT INTO {loads()} (table_name, source_sha256, row_count, loaded_at) VALUES "
                 f"('views', %s, 0, %s)", (views_key(), _now()))


# ---------------------------------------------------------------- main

def load(conn, data, *, force=False, log=print):
    """The corpus tables, the derived tables, the vectors, the release row and
    the views, in one transaction."""
    import release
    t_all = time.time()

    def write(table, sha, cols, rows, **kw):
        t0 = time.time()
        if sha is None:
            log(f"  {table:<20} skipped: no source")
        elif not force and last_sha(conn, table) == sha:
            log(f"  {table:<20} unchanged")
        else:
            n, bad = load_rows(conn, table, cols, rows, sha, **kw)
            note = ("  unparseable -> NULL: " + ", ".join(f"{k}={v}" for k, v in sorted(bad.items()))) if bad else ""
            log(f"  {table:<20} {n:>8} rows{note}  ({time.time() - t0:.0f}s)")
    release_id = release.cut(data)
    with transaction(conn):
        prepare(conn)
        for table, rel in CORPUS:
            path = os.path.join(data, rel)
            if not os.path.exists(path):
                log(f"  {table:<20} skipped: {rel} not found")
            elif not force and last_sha(conn, table) == file_sha256(path):
                log(f"  {table:<20} unchanged")
            else:
                t0 = time.time()
                n, bad = load_corpus_table(conn, data, table, rel)
                log(f"  {table:<20} {n:>8} rows  ({time.time() - t0:.0f}s)")
        sha, rows = paragraph_projects(data)
        write("paragraph_projects", sha, ["paragraph_id", "project_id", "doc_id"], rows)
        sha, rows = search_unit_rows(data)
        write("search_units", sha, SEARCH_UNIT_COLS, rows)
        sha, rows = search_rollup(data)
        write("search_paragraphs", sha, ROLLUP_COLS, rows)
        log(f"  {'sentence_vectors':<20} {load_vectors(conn, data, force)}")
        m = release.manifest(data)
        fetched = {k: v.get("latest", "") for k, v in m["fetched"].items()}
        load_rows(conn, "data_release", ["release_id", "commit", "code_dirty", "loaded_at",
                                         "appraisal_fetched_date", "procurement_fetched_date",
                                         "projects_fetched_date"],
                  [{"release_id": release_id, "commit": m["code"]["commit"][:12],
                    "code_dirty": str(m["code"]["dirty"]).lower(), "loaded_at": _now(),
                    "appraisal_fetched_date": fetched.get("appraisal", ""),
                    "procurement_fetched_date": fetched.get("procurement", ""),
                    "projects_fetched_date": fetched.get("projects", "")}], release_id)
        finish(conn, log=log)
    log(f"  release {release_id}; load took {time.time() - t_all:.0f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--force", action="store_true", help="reload unchanged tables too")
    args = ap.parse_args()
    with db.connect() as conn:
        load(conn, args.data, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
