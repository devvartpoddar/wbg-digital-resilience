#!/usr/bin/env python3
"""Load the cleaned tables and the model runs into Postgres for Metabase.

Reads  data/projects/projects.csv, data/raw/documents.csv,
       data/appraisal/{paragraphs,sentences,components}.csv (+ data/appraisal/
       text/ for their text), data/procurement/{packages,notices,awards}.csv,
       the current search, asset-map and measure runs, data/embeddings/,
       every label set (src/labels.py), data/releases/, sql/views/ (the
       *_raw tables and a person's grades are not loaded)
Writes one schema, wbg: projects, documents, paragraphs, search_units,
       components, packages, notices, awards, labels, search_paragraphs,
       paragraph_grades, measure_findings, sentence_vectors, and the
       bookkeeping tables paragraph_projects, data_release and loads, with
       keys, comments and the views in sql/views/. First it cuts a release
       (release.py). A table nothing in sql/ reads (sentences, the rejected
       units, document parts, superseded package versions, the per-unit
       search scores, the candidates, the release index) is not loaded: the
       CSV under data/ is its record, and measure_findings holds every
       candidate the measure stage read.

The CSV files stay the source and keep their names; src/schema.py says what
each column is called and typed in Postgres and what its comment says, and
the loader applies that on the way in. Paragraph text, which the CSV leaves
out (it is an offset into data/appraisal/text/), is resolved here; the
database is on the box, and text never leaves the box.

Each table is replaced inside one transaction, so a reader never sees half a
table. A table whose source has not changed since its last load (same
SHA-256) is skipped; --force reloads everything. Tables an earlier layout
made under other names in wbg are dropped on the first load with this one.

Connection: $WBG_PG, default postgresql:///work (the box's agents database,
peer authentication over the local socket - no password anywhere).

  python3 src/load_pg.py
  python3 src/load_pg.py --force
"""
import argparse, contextlib, time, csv, glob, hashlib, os, re, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import labels  # noqa: E402
import schema as S  # noqa: E402

DEFAULT_DSN = "postgresql:///work"
PUBLIC, INTERNAL = S.PUBLIC, S.INTERNAL
LOADS = f"{INTERNAL}.loads"

# A load replaces tables and rebuilds every view, which needs locks the
# dashboards hold while a card is being read. Without a limit a load waits
# forever and looks like a crash; with one it says who is in the way.
# src/store.py uses the same value for a publish.
LOCK_TIMEOUT = "2min"


def lock_timeout(conn):
    """Refuse to wait longer than LOCK_TIMEOUT for a lock in this
    transaction: a wait that long is a stuck reader, not a slow one."""
    conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")


def blocking_sessions(conn):
    """[(pid, who, state, how long, query)] of the sessions in the way when a
    lock timed out: the ones pg_blocking_pids names for this backend (empty
    once the wait itself has ended) and, failing that, every other session on
    the database with an open transaction, oldest first. A role that cannot
    see another role's query reads '<insufficient privilege>' there, which is
    the answer: the block is outside this role (Metabase reads as
    metabase_reader)."""
    return conn.execute(
        "SELECT a.pid, a.usename, coalesce(a.state, 'n/a'), "
        "       coalesce(now() - a.xact_start, now() - a.query_start), "
        "       left(a.query, 200) "
        "FROM pg_stat_activity a "
        "WHERE a.datname = current_database() AND a.pid <> pg_backend_pid() "
        "  AND (a.pid = ANY (pg_blocking_pids(pg_backend_pid())) "
        "       OR a.xact_start IS NOT NULL) "
        "ORDER BY a.xact_start NULLS LAST, a.pid").fetchall()


def report_lock_wait(conn, log=print):
    """Say who held the database when a load's or a publish's lock timed out.
    Called after the failed transaction has rolled back, so the wait itself is
    over and the sessions are listed by their open transaction."""
    rows = blocking_sessions(conn)
    if not rows:
        log("  no other session holds an open transaction")
        return
    for pid, who, state, age, query in rows:
        log(f"  pid {pid} ({who}, {state}, open {age}): "
            + " ".join((query or "").split()))


@contextlib.contextmanager
def load_transaction(dsn):
    """The load's one transaction, with LOCK_TIMEOUT set on it. A lock that
    times out rolls the whole load back (nothing is written), prints who held
    it and exits non-zero: a load behind a reader must never hang silently."""
    import psycopg
    import psycopg.errors as pg_errors
    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            with conn.transaction():
                lock_timeout(conn)
                yield conn
        except pg_errors.LockNotAvailable:
            print(f"load: no lock within {LOCK_TIMEOUT}; nothing was written. "
                  f"Who holds one:")
            report_lock_wait(conn)
            raise SystemExit(1)


# (table, path under data/)
TABLES = [
    ("projects", "projects/projects.csv"),
    ("documents", "raw/documents.csv"),
    ("paragraphs", "appraisal/paragraphs.csv"),
    ("components", "appraisal/components.csv"),
    ("packages", "procurement/packages.csv"),
    ("notices", "procurement/notices.csv"),
    ("awards", "procurement/awards.csv"),
    ("measure_quality", "labels/measure_quality.csv"),
]
# Names earlier layouts used in wbg, and tables no longer loaded; dropped
# once so nothing stale lingers. The earlier wbg_internal schema is folded
# into wbg by prepare().
OLD_NAMES = ["taxonomy", "classify_scores", "measures", "releases", "rejected",
             "superseded_packages", "search_best", "sentences", "document_parts", "candidates",
             "label_groups"]
OLD_VIEWS = ["v_packages", "v_paragraphs", "v_notices", "v_awards", "v_classified_paragraphs",
             "v_search_paragraphs"]
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


from common import file_sha256 as sha256_file  # noqa: E402


def col_type(csv_name):
    """The Postgres type a CSV column gets (src/schema.py), by its CSV name."""
    return S.col_type(S.RENAME.get(csv_name, csv_name))


def convert(name, value, bad, kind=None):
    """'' -> NULL; typed values parsed, unparseable ones NULL and counted;
    a pipe-joined list becomes a Postgres array. `name` is the CSV name."""
    if value is None or value == "":
        return None
    kind = kind or col_type(name)
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
        if kind == "text[]":
            return [x for x in value.split("|") if x]
    except ValueError:
        bad[name] = bad.get(name, 0) + 1
        return None
    return value


TEXT_TABLES = {"paragraphs"}


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


def component_rows(path):
    """Components with a surrogate key: a name can recur, so the row is
    identified by where and how it was read."""
    from keys import name_key
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            r = dict(r)
            # A combined programme document lists the same component once per
            # country part, so the part's projects are in the key.
            r["component_id"] = ":".join([r["doc_id"], r.get("project_ids", ""), r["source"],
                                          r["level"], r["number"],
                                          r.get("name_key") or name_key(r["name"])])
            yield r


def load_table(conn, data, table, rel, release_id=""):
    path = os.path.join(data, rel)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        if table in TEXT_TABLES:
            rows = rows_with_text(data, reader)
            cols.append("text")
        elif table == "components":
            rows = component_rows(path)
            cols = ["component_id"] + cols
        else:
            rows = reader
        return load_rows(conn, table, cols, rows, sha256_file(path), release_id)


def load_rows(conn, table, cols, rows, source_sha, release_id="", types=None, after=()):
    """Replace <schema>.<table> with rows, in one transaction, renaming and
    typing the columns from src/schema.py. A table it replaces takes its
    dependent views and keys with it (CASCADE); finish() puts them back
    after every load. `after` is SQL run inside the same transaction once
    the rows are in."""
    types = types or {}
    bad = {}
    names = [S.rename(table, c) for c in cols]
    kinds = [types.get(c) or S.col_type(n) for c, n in zip(cols, names)]
    ident = S.qualified(table) if table in S.TABLES else f"{PUBLIC}.{table}"
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {ident} CASCADE")
        cur.execute(f"CREATE TABLE {ident} ("
                    + ", ".join(f'"{n}" {k}' for n, k in zip(names, kinds)) + ")")
        n = 0
        col_list = ", ".join(f'"{c}"' for c in names)
        with cur.copy(f"COPY {ident} ({col_list}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([r.get(c) if c in types else convert(c, r.get(c), bad, k)
                              for c, nm, k in zip(cols, names, kinds)])
                n += 1
        for nm in names:
            if nm in S.INDEXED:
                cur.execute(f'CREATE INDEX ON {ident} ("{nm}")')
        for sql in after:
            cur.execute(sql)
        cur.execute(f"INSERT INTO {LOADS} (table_name, source_sha256, row_count, loaded_at, "
                    f"release_id) VALUES (%s, %s, %s, %s, %s)",
                    (table, source_sha, n, _now(), release_id))
    return n, bad


def replace_rows(conn, table, cols, rows, key_col, key_values, source_sha, release_id="",
                 types=None):
    """Replace the rows of <schema>.<table> whose key_col is in key_values
    (every row when key_values is None) with rows, keeping the table and
    the views on it: the way a stage writes its own set into Postgres
    without a full load. The table is created when missing, and
    recreated when its columns no longer match (that drops its views;
    finish() puts them back). Returns (rows written, unparseable)."""
    types = types or {}
    bad = {}
    names = [S.rename(table, c) for c in cols]
    kinds = [types.get(c) or S.col_type(n) for c, n in zip(cols, names)]
    ident = S.qualified(table) if table in S.TABLES else f"{PUBLIC}.{table}"
    created = False
    with conn.transaction(), conn.cursor() as cur:
        if not _exists(conn, table) or _columns(conn, ident) != set(names):
            # The columns changed (new code): the table is made again, and
            # the rows of every key this call does not replace are carried
            # into it (the columns both shapes share), so a set whose run is
            # not found now keeps what it had rather than vanishing.
            keep = None
            if _exists(conn, table) and key_values is not None:
                keep = sorted(_columns(conn, ident) & set(names))
                cur.execute(f"ALTER TABLE {ident} RENAME TO {table}__old")
            else:
                cur.execute(f"DROP TABLE IF EXISTS {ident} CASCADE")
            cur.execute(f"CREATE TABLE {ident} ("
                        + ", ".join(f'"{n}" {k}' for n, k in zip(names, kinds)) + ")")
            if keep:
                kc = ", ".join(f'"{c}"' for c in keep)
                cur.execute(f'INSERT INTO {ident} ({kc}) SELECT {kc} FROM {PUBLIC}.{table}__old '
                            f'WHERE NOT ("{S.rename(table, key_col)}" = ANY(%s))',
                            (list(key_values),))
            if keep is not None:
                cur.execute(f"DROP TABLE {PUBLIC}.{table}__old CASCADE")
            for nm in names:
                if nm in S.INDEXED:
                    cur.execute(f'CREATE INDEX ON {ident} ("{nm}")')
            created = True
        if key_values is None:
            cur.execute(f"DELETE FROM {ident}")
        else:
            cur.execute(f'DELETE FROM {ident} WHERE "{S.rename(table, key_col)}" = ANY(%s)',
                        (list(key_values),))
        n = 0
        col_list = ", ".join(f'"{c}"' for c in names)
        with cur.copy(f"COPY {ident} ({col_list}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([r.get(c) if c in types else convert(c, r.get(c), bad, k)
                              for c, nm, k in zip(cols, names, kinds)])
                n += 1
        label = table if key_values is None else f"{table}:{','.join(sorted(key_values))}"
        cur.execute(f"INSERT INTO {LOADS} (table_name, source_sha256, row_count, loaded_at, "
                    f"release_id) VALUES (%s, %s, %s, %s, %s)",
                    (label, source_sha, n, _now(), release_id))
    return n, bad, created


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------ derived tables

def paragraph_projects(data):
    """(source sha, rows): one row per paragraph and project, from the
    pipe-joined project_ids, so a paragraph joins its project by key."""
    path = where(data, "appraisal", "paragraphs.csv")
    if not os.path.exists(path):
        return None, []

    def rows():
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                for p in (r.get("project_ids") or "").split("|"):
                    if p:
                        yield {"paragraph_id": r["paragraph_id"], "project_id": p,
                               "doc_id": r["doc_id"]}
    return sha256_file(path) + ":projects", rows()


SEARCH_UNIT_COLS = ["unit_id", "kind", "paragraph_id", "doc_id", "project_ids", "block",
                    "section_path", "component_number", "subcomponent_number", "component_key",
                    "char_start", "char_end", "n_tokens", "text_sha256", "lead_in",
                    "left_out_reason", "copy_of_unit_id", "template_projects", "template_share",
                    "text"]


def search_unit_rows(data):
    """(source sha, rows): every search unit (src/units.py) with its text and
    lead-in, the parent of every unit_id in the results tables."""
    paras = where(data, "appraisal", "paragraphs.csv")
    sents = where(data, "appraisal", "sentences.csv")
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
                   "lead_in": t[int(u["lead_in_start"]):int(u["lead_in_end"])]
                   if u["lead_in_start"] else "",
                   "left_out_reason": u["left_out"], "copy_of_unit_id": u["copy_of"],
                   "text": t[int(u["char_start"]):int(u["char_end"])]}
    # The rows are computed (src/units.py), so its code and the column list
    # are part of the source: a new column or rule reloads the table.
    code = sha256_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "units.py"))
    return sha256_file(paras) + sha256_file(sents) + code[:12] + ",".join(SEARCH_UNIT_COLS), rows()


# ------------------------------------------------------------ results tables
#
# The search, the asset map and the measure write versioned
# runs; Postgres holds the run for each label set as it stands now, one
# table each across label sets, so a reader filters by label_set.

def search_tables(data, sets=None):
    """(source sha, rollup rows): per label and paragraph its best unit
    (search_paragraphs). None without any current run. The per-unit scores
    stay in the run folder; measure_findings carries each candidate's."""
    import search
    folders = []
    for name in labels.label_sets():
        if sets and name not in sets:
            continue
        try:
            folder, _ = search.run_folder(name, data)
        except SystemExit:
            continue
        if os.path.exists(os.path.join(folder, "manifest.json")):
            folders.append((name, folder))
    if not folders:
        return None, None
    # A generator: the rows are read only when the table is loaded, and the
    # sha alone says whether it will be.
    rollup = (dict(r, label_set=name, run=os.path.basename(folder))
              for name, folder in folders for r in run_rollup(folder, data))
    return "|".join(os.path.basename(f) for _, f in folders), rollup


def run_rollup(folder, data):
    """A search run's paragraph rollup, computed once and kept in the run
    folder as paragraph_hits.csv: a run never changes, and re-ranking
    150,000 units for every label set on every load was most of the
    load's time."""
    import search
    path = os.path.join(folder, "paragraph_hits.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    out = []
    for lid in search.run_labels(folder):
        out.extend(search.paragraph_rollup(search.hits(folder, data, labels=[lid])))
    cols = [c for c in ROLLUP_COLS if c not in ("label_set", "run")]
    try:
        with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            w.writerows(out)
        os.replace(path + ".part", path)
    except OSError:
        pass
    return out


ROLLUP_COLS = ["label_set", "run", "label_id", "paragraph_id", "doc_id", "project_ids",
               "component_number", "component_key", "best_score", "best_percentile",
               "best_unit_id", "best_query_id", "units_hit"]
CLASSIFY_COLS = ["label_set", "run", "unit_id", "paragraph_id", "doc_id", "project_ids",
                 "component_number", "label_id", "asked", "best_cosine", "grade", "expected",
                 "confidence", "p0", "p1", "p2", "p3", "p4", "model_served"]
MEASURE_COLS = ["label_set", "run", "label_id", "unit_id", "kind", "paragraph_id", "doc_id",
                "project_ids", "component_number", "subcomponent_number", "component_key",
                "copies", "route", "round", "rank", "score", "patterns",
                "firmness", "asset_para", "asset_p_para", "asset_comp",
                "asset_p_comp", "asset_unit", "asset_p_unit", "assets_yes", "asset_link",
                "asset_link_level", "asset_link_p", "kinds_yes", "kind_best", "kind_p",
                "conditional_p", "actors_yes", "actor_best", "actor_p",
                "template_projects", "template_share", "sent", "committed",
                "grade", "expected", "confidence", "p0", "p1", "p2", "p3", "p4",
                "p_committed_calibrated", "types_yes", "best_type", "best_p", "model_served",
                "cost", "text"]


def found_sets(source_sha):
    """The label sets a run-table source (the run folder names joined with
    "|") holds a current run for: the keys a write may replace. A set
    whose current run is not found (being re-run, or made over another
    scope) is not among them, and its rows are left as they were."""
    names = set(labels.label_sets())
    return sorted({r.rsplit("-", 1)[0] for r in (source_sha or "").split("|") if "-" in r}
                  & names)


def purge_removed_sets(conn, table):
    """Delete the rows of label sets that no longer exist in inputs/ or
    analysis/labels/: the one way a set's rows leave a run table."""
    if _exists(conn, table):
        conn.execute(f"DELETE FROM {S.qualified(table)} WHERE label_set <> ALL(%s)",
                     (list(labels.label_sets()),))


def _run_rows(data, finder, file, sets=None):
    rows, runs = [], []
    for name in labels.label_sets():
        if sets and name not in sets:
            continue
        try:
            folder = finder(name, data)
        except SystemExit:
            continue
        if not folder:
            continue
        runs.append(os.path.basename(folder))
        with open(os.path.join(folder, file), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append(dict(r, label_set=name, run=os.path.basename(folder)))
    return ("|".join(runs), rows) if rows else (None, [])


def classify_rows(data, projects=None, min_cosine=None, sets=None):
    """The current asset map for the scope. With min_cosine None, the gate
    is the one the last run was made with (classify's marker); the flag
    still names one explicitly."""
    import classify
    sha, rows = _run_rows(data, lambda n, d: classify.current_run(
        n, d, projects=projects, min_cosine=min_cosine), "scores.csv", sets)
    for r in rows:              # a paragraph map (or one made before unit_id) keys on the paragraph
        r["unit_id"] = r.get("unit_id") or r["paragraph_id"]
    return sha, rows


def measure_rows(data, projects=None, sets=None):
    """The current measure run per set (or the sets named), with the
    current type assignments (src/kinds.py) joined on as types_yes,
    best_type and best_p where a set has an approved list and an
    assignment run; blank otherwise."""
    import kinds
    import measure
    sha, rows = _run_rows(data, lambda n, d: measure.current_run(n, d, projects=projects),
                          "units.csv", sets)
    if sha is None:
        return sha, rows
    assigned, runs = {}, []
    for name in labels.label_sets():
        if sets and name not in sets:
            continue
        folder = kinds.current_assign(name, data, projects=projects)
        if not folder:
            continue
        runs.append(os.path.basename(folder))
        with open(os.path.join(folder, "assignments.csv"), newline="", encoding="utf-8") as fh:
            for a in csv.DictReader(fh):
                assigned[(name, a["label_id"], a["unit_id"])] = a
    for r in rows:
        a = assigned.get((r["label_set"], r["label_id"], r["unit_id"]), {})
        r.update({"types_yes": a.get("types_yes", ""), "best_type": a.get("best_type", ""),
                  "best_p": a.get("best_p", "")})
    mapping = {name: labels.type_resolution(name)[0] for name in labels.label_sets()}
    apply_type_resolution(rows, mapping)
    merges = hashlib.sha256(repr(sorted((n, sorted(m.items())) for n, m in mapping.items()))
                            .encode()).hexdigest()[:12]
    return sha + "|" + "|".join(runs) + "|" + merges, rows


ASSET_LINK_COLS = ["package_id", "project_id", "label_id", "cosine", "cosine_rank", "probability",
                   "model_served", "cost"]
PAIR_LINK_COLS = ["label_set", "run", "label_id", "unit_id", "package_id", "project_id",
                  "shared_asset_ids", "route", "measure_rank",
                  "measure_cosine", "probability", "model_served", "cost"]


CHECK_PAIR_COLS = ["unit_id", "set_a", "set_b", "decision", "probability", "p_only_a",
                   "p_only_b", "p_both", "p_neither", "model_served", "cost"]
CHECK_SCOPE_COLS = ["unit_id", "scope", "probability", "p_rule", "p_investment",
                    "p_not_commitment", "model_served", "cost"]


def check_rows(data, projects=None):
    """(sha, pair rows, scope rows) of the current commitment checks
    (src/commit_checks.py), or (None, [], []) when there are none."""
    import commit_checks
    folder = commit_checks.current_run(data, projects=projects)
    if not folder:
        return None, [], []
    out = []
    for file in ("pairs.csv", "scope.csv"):
        with open(os.path.join(folder, file), newline="", encoding="utf-8") as fh:
            out.append(list(csv.DictReader(fh)))
    return os.path.basename(folder), out[0], out[1]


def link_rows(data, projects=None):
    """(sha, package-asset rows, commitment-package rows) of the current
    link run (src/link_packages.py), or (None, [], []) when there is none."""
    import link_packages
    folder = link_packages.current_run(data, projects=projects)
    if not folder:
        return None, [], []
    out = []
    for file in ("package_assets.csv", "commitment_packages.csv"):
        with open(os.path.join(folder, file), newline="", encoding="utf-8") as fh:
            out.append(list(csv.DictReader(fh)))
    return os.path.basename(folder), out[0], out[1]


def apply_type_resolution(rows, mapping):
    """Show each unit's types as the label file says now: a type merged into
    another is replaced by it (duplicates dropped, order kept) in types_yes
    and best_type. mapping: {label_set: {(label_id, type_id): shown id}}."""
    for r in rows:
        m = mapping.get(r["label_set"]) or {}
        if not m:
            continue
        lid = r["label_id"]
        shown = []
        for t in (r.get("types_yes") or "").split("|"):
            if not t:
                continue
            t = m.get((lid, t), t)
            if t not in shown:
                shown.append(t)
        r["types_yes"] = "|".join(shown)
        if r.get("best_type"):
            r["best_type"] = m.get((lid, r["best_type"]), r["best_type"])
    return rows


def type_rows(data=None):
    """Every type shown for every set with a list (analysis/labels/<set>_types.csv):
    a merged type is folded into its target, a display_name is the name. The
    OTH types the gap step proposed (data/types/<set>_oth_types.csv, src/kinds.py)
    follow, named with their OTH. prefix."""
    out = []
    for name in labels.label_sets():
        seen = set()
        for key, t in labels.type_resolution(name)[1].items():
            seen.add((key[0], t["type_id"]))
            out.append({"label_set": name, "label_id": key[0], "type_id": t["type_id"],
                        "type_name": t["type_name"], "definition": t["definition"],
                        "merged_from": "|".join(t["merged_from"])})
        path = where(data or data_root(), "types", f"{name}_oth_types.csv")
        if os.path.exists(path):
            for t in labels.rows(path):
                if (t["label_id"], t["type_id"]) not in seen:
                    out.append({"label_set": name, "label_id": t["label_id"],
                                "type_id": t["type_id"], "type_name": t["type_name"],
                                "definition": t["definition"], "merged_from": ""})
    return out


def label_rows():
    """Every label of every label set (src/labels.py)."""
    rows = []
    for name in labels.label_sets():
        for r in labels.load_set(name):
            rows.append({"label_set": name, "label_id": r["id"], "name": r["name"],
                         "definition": r["definition"], "active": r["active"],
                         "version": r["version"], "folder": r["folder"]})
    return rows


QUERY_COLS = ["label_set", "label_id", "query_id", "query", "active", "version", "file_sha256"]
PATTERN_COLS = ["label_set", "label_id", "pattern_id", "pattern", "active", "version",
                "file_sha256"]
SCALE_COLS = ["label_set", "label_id", "grade", "description", "version", "file_sha256"]


def definition_rows():
    """(queries, patterns, scale): a copy of every set's phrase, pattern and
    grade-scale files, every row active or not, each with the file's SHA-256
    and the label's version, so a page can show the wording a run used. The
    scale is one row per label and grade with {name} filled in."""
    queries, patterns, scale = [], [], []
    for name in labels.label_sets():
        d = labels.folder(name)
        lab = {r["id"]: r for r in labels.load_set(name)}

        def version(lid):
            return (lab.get(lid) or {}).get("version", "")

        for suffix, out, cols in (("_queries", queries, ("query_id", "query")),
                                  ("_patterns", patterns, ("pattern_id", "pattern"))):
            path = os.path.join(d, f"{name}{suffix}.csv")
            if not os.path.exists(path):
                continue
            sha = sha256_file(path)
            for r in labels.rows(path):
                lid = r[next(c for c in r if c.endswith("_id") and c not in cols)].strip()
                out.append({"label_set": name, "label_id": lid, cols[0]: r[cols[0]].strip(),
                            cols[1]: r[cols[1]], "active": str(labels.is_active(r)).lower(),
                            "version": version(lid), "file_sha256": sha})
        path = os.path.join(d, f"{name}_scale.csv")
        if os.path.exists(path):
            sha = sha256_file(path)
            grades = sorted(labels.rows(path), key=lambda r: int(r["grade"]))
            for lid, r in lab.items():
                for g in grades:
                    scale.append({"label_set": name, "label_id": lid, "grade": g["grade"],
                                  "description": " ".join(g["description"].split())
                                  .replace("{name}", r["name"]),
                                  "version": r["version"], "file_sha256": sha})
    return queries, patterns, scale


def vector_kind(conn, dim):
    """The pgvector column type for dim dimensions, or None without the
    extension: halfvec with an HNSW index from pgvector 0.7, plain vector
    unindexed before."""
    ver = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                       ).fetchone()
    if not ver:
        return None
    half = tuple(int(x) for x in ver[0].split(".")[:2]) >= (0, 7)
    return f"{'halfvec' if half else 'vector'}({dim})"


def load_vectors(conn, data, release_id, force):
    """wbg.sentence_vectors: one row per search unit with its
    embedding (pgvector). The index is built inside the load's transaction."""
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
    after = [f"CREATE INDEX ON {INTERNAL}.sentence_vectors USING hnsw "
             f"(embedding halfvec_cosine_ops)"] if half else []
    n, _ = load_rows(conn, "sentence_vectors", cols, rows(), sha, release_id,
                     types={"embedding": kind}, after=after)
    return f"{n:>8} rows ({kind}{', HNSW index' if half else ', no index'})"


# ------------------------------------------------------- keys, views, release

def _lit(text):
    """An SQL string literal (COMMENT ON takes no parameters)."""
    return "'" + text.replace("'", "''") + "'"


def _exists(conn, table):
    ident = S.qualified(table) if table in S.TABLES else f"{PUBLIC}.{table}"
    return conn.execute("SELECT to_regclass(%s)", (ident,)).fetchone()[0]


def _last_sha(conn, table):
    last = conn.execute(f"SELECT source_sha256 FROM {LOADS} WHERE table_name = %s "
                        "ORDER BY loaded_at DESC LIMIT 1", (table,)).fetchone()
    return last[0] if last and _exists(conn, table) else None


def _function_exists(conn, name):
    return bool(conn.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (name,)).fetchone())


def _columns(conn, ident):
    return {r[0] for r in conn.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 "
        "AND NOT attisdropped", (ident,))}


def finish(conn, log=print):
    """Keys, comments, views and functions, put back after every load (a
    replaced table drops them). A key that cannot be added (a duplicate id)
    and a view over a table not loaded yet are skipped and reported."""
    for table, spec in S.TABLES.items():
        if not _exists(conn, table):
            continue
        ident = S.qualified(table)
        present = _columns(conn, ident)
        if spec["key"] and all(c in present for c in spec["key"]):
            has = conn.execute("SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass "
                               "AND contype = 'p'", (ident,)).fetchone()
            if not has:
                try:
                    with conn.transaction():
                        conn.execute(f'ALTER TABLE {ident} ADD PRIMARY KEY ('
                                     + ", ".join(f'"{c}"' for c in spec["key"]) + ")")
                except Exception as exc:                  # noqa: BLE001
                    log(f"  key {table}.{'+'.join(spec['key'])}: not added "
                        f"({str(exc).splitlines()[0]})")
        for col, ref, refcol in spec["foreign"]:
            if col not in present or not _exists(conn, ref):
                continue
            name = f"fk_{table}_{col}"
            has = conn.execute("SELECT 1 FROM pg_constraint WHERE conname = %s AND "
                               "conrelid = %s::regclass", (name, ident)).fetchone()
            if not has:
                try:
                    with conn.transaction():
                        conn.execute(f'ALTER TABLE {ident} ADD CONSTRAINT {name} FOREIGN KEY '
                                     f'("{col}") REFERENCES {S.qualified(ref)} ("{refcol}") '
                                     f'NOT VALID')
                except Exception as exc:                  # noqa: BLE001
                    log(f"  key {name}: not added ({str(exc).splitlines()[0]})")
        with conn.transaction():
            conn.execute(f"COMMENT ON TABLE {ident} IS {_lit(spec['comment'])}")
            for col, text in spec["columns"].items():
                if col in present:
                    conn.execute(f'COMMENT ON COLUMN {ident}."{col}" IS {_lit(text)}')
    vec = None
    if _exists(conn, "sentence_vectors"):
        vec = conn.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE "
                           "attrelid = %s::regclass AND attname = 'embedding'",
                           (f"{INTERNAL}.sentence_vectors",)).fetchone()[0]
    for path in sorted(glob.glob(os.path.join(ROOT, "sql", "views", "*.sql"))):
        with open(path, encoding="utf-8") as fh:
            sql = fh.read()
        if "openrouter(" in sql and not _function_exists(conn, "openrouter"):
            log(f"  {os.path.basename(path)}: skipped (no openrouter() function in this "
                f"database; it comes with the box's Metabase setup)")
            continue
        if "{{panel:" in sql:          # a control-panel setting (inputs/config/control_panel.csv)
            import re
            import panel
            sql = re.sub(r"\{\{panel:([a-z0-9_.]+)\}\}", lambda m: panel.sql_value(m.group(1)), sql)
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


def load_release(conn, data, release_id):
    """wbg.data_release: the one release this load is, with when each kind
    of record was last fetched (data/releases/index.csv lists them all)."""
    import release
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


OLD_SCHEMA = "wbg_internal"   # an earlier layout's bookkeeping schema, folded into wbg


def prepare(conn):
    """The schema, the loads table, and the removal of what earlier layouts
    left behind (one-time): tables under other names in wbg, and the
    separate bookkeeping schema, whose load history is carried over."""
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {PUBLIC}")
    # A loads table of an earlier shape (row_count or release_id missing,
    # e.g. the first layout's n_rows) is started afresh: the skip-hints are
    # stale once the columns or types change.
    if _exists(conn, "loads") and not {"row_count", "release_id"} <= _columns(conn, LOADS):
        conn.execute(f"DROP TABLE {LOADS} CASCADE")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {LOADS} (table_name text, source_sha256 text, "
                 f"row_count integer, loaded_at timestamptz, release_id text)")
    conn.execute(f"COMMENT ON TABLE {LOADS} IS {_lit(S.TABLES['loads']['comment'])}")
    if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (OLD_SCHEMA,)).fetchone():
        if conn.execute("SELECT to_regclass(%s)", (f"{OLD_SCHEMA}.loads",)).fetchone()[0]:
            conn.execute(f"INSERT INTO {LOADS} SELECT table_name, source_sha256, row_count, "
                         f"loaded_at, release_id FROM {OLD_SCHEMA}.loads")
        conn.execute(f"DROP SCHEMA {OLD_SCHEMA} CASCADE")
    for v in OLD_VIEWS:
        conn.execute(f"DROP VIEW IF EXISTS {PUBLIC}.{v} CASCADE")
    for t in OLD_NAMES:
        conn.execute(f"DROP TABLE IF EXISTS {PUBLIC}.{t} CASCADE")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--dsn", default=os.environ.get("WBG_PG", DEFAULT_DSN))
    ap.add_argument("--force", action="store_true", help="reload unchanged tables too")
    ap.add_argument("--projects", default="",
                    help="comma-separated project ids: load the model runs made over this "
                         "scope (the cohort set they were run with)")
    ap.add_argument("--set", action="append", dest="cohort_set",
                    help="the cohort set the model runs were made over (digital, ...)")
    ap.add_argument("--min-cosine", type=float, default=None,
                    help="the asset map's cosine gate, to find the classify run made at "
                         "that gate (default: the classify stage's default)")
    args = ap.parse_args()
    import classify
    projects = classify.scope(args.projects)
    if args.cohort_set:
        from wbg import select
        projects = sorted(select(cohort_set=",".join(args.cohort_set), data=args.data).ids)

    import release
    release_id = release.cut(args.data)

    def note(bad):
        return ("  unparseable -> NULL: " + ", ".join(f"{k}={v}" for k, v in sorted(bad.items()))
                if bad else "")

    t_start = time.time()

    def took(t0):
        return f"  ({time.time() - t0:.0f}s)"

    def derived(conn, table, sha, cols, rows, after=()):
        t0 = time.time()
        if sha is None:
            print(f"  {table:<20} skipped: no source")
        elif not args.force and _last_sha(conn, table) == sha:
            print(f"  {table:<20} unchanged{took(t0)}")
        else:
            n, bad = load_rows(conn, table, cols, rows, sha, release_id, after=after)
            print(f"  {table:<20} {n:>8} rows{note(bad)}{took(t0)}")

    # One transaction for the whole load, views included: a reader (a
    # Metabase card) sees the database as it was or as it is now, never a
    # table replaced and its views not yet put back. Readers of a replaced
    # table wait for the commit instead - and this load waits no longer than
    # LOCK_TIMEOUT for them (load_transaction).
    with load_transaction(args.dsn) as conn:
        prepare(conn)
        for table, rel in TABLES:
            path = os.path.join(args.data, rel)
            if not os.path.exists(path):
                print(f"  {table:<20} skipped: {rel} not found")
                continue
            if not args.force and _last_sha(conn, table) == sha256_file(path):
                print(f"  {table:<20} unchanged")
                continue
            t0 = time.time()
            n, bad = load_table(conn, args.data, table, rel, release_id)
            print(f"  {table:<20} {n:>8} rows{note(bad)}{took(t0)}")

        sha, rows = paragraph_projects(args.data)
        derived(conn, "paragraph_projects", sha, ["paragraph_id", "project_id", "doc_id"], rows)
        sha, rows = search_unit_rows(args.data)
        derived(conn, "search_units", sha, SEARCH_UNIT_COLS, rows)

        # The run tables are replaced set by set: a set whose current run is
        # not found keeps the rows it had (found_sets).
        for table, cols, (sha, rows) in (
                ("search_paragraphs", ROLLUP_COLS, search_tables(args.data)),
                ("paragraph_grades", CLASSIFY_COLS,
                 classify_rows(args.data, projects, args.min_cosine)),
                ("measure_findings", MEASURE_COLS, measure_rows(args.data, projects))):
            t0 = time.time()
            if sha is None:
                print(f"  {table:<20} skipped: no current run")
                continue
            if not args.force and _last_sha(conn, table) == sha:
                print(f"  {table:<20} unchanged{took(t0)}")
                continue
            n, bad, _ = replace_rows(conn, table, cols, rows, "label_set", found_sets(sha), sha,
                                     release_id)
            conn.execute(f"INSERT INTO {LOADS} (table_name, source_sha256, row_count, loaded_at, "
                         f"release_id) VALUES (%s, %s, %s, %s, %s)",
                         (table, sha, n, _now(), release_id))
            purge_removed_sets(conn, table)
            print(f"  {table:<20} {n:>8} rows{note(bad)}{took(t0)}")

        sha, assets_, pairs_ = link_rows(args.data, projects)
        derived(conn, "package_assets", sha, ASSET_LINK_COLS, assets_)
        derived(conn, "commitment_packages", sha, PAIR_LINK_COLS, pairs_)
        sha, cpairs, cscope = check_rows(args.data, projects)
        derived(conn, "unit_pair_checks", sha, CHECK_PAIR_COLS, cpairs)
        derived(conn, "unit_scope", sha, CHECK_SCOPE_COLS, cscope)

        lab = label_rows()
        derived(conn, "labels", hashlib.sha256(repr(lab).encode()).hexdigest(),
                ["label_set", "label_id", "name", "definition", "active", "version", "folder"],
                lab)
        typ = type_rows(args.data)
        derived(conn, "measure_types", hashlib.sha256(repr(typ).encode()).hexdigest(),
                ["label_set", "label_id", "type_id", "type_name", "definition", "merged_from"],
                typ)
        for table, cols, rows_ in zip(("label_queries", "label_patterns", "label_scale"),
                                      (QUERY_COLS, PATTERN_COLS, SCALE_COLS), definition_rows()):
            derived(conn, table, hashlib.sha256(repr(rows_).encode()).hexdigest(), cols, rows_)
        t0 = time.time()
        print(f"  {'sentence_vectors':<20} {load_vectors(conn, args.data, release_id, args.force)}"
              f"{took(t0)}")
        load_release(conn, args.data, release_id)
        t0 = time.time()
        finish(conn)
        print(f"  keys, comments, views{took(t0)}")
        print(f"  release {release_id}; load took {time.time() - t_start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
