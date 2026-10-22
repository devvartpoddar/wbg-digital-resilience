#!/usr/bin/env python3
"""The stages write Postgres themselves: a measure, a search, an asset map,
a type assignment or a package link is visible in Metabase when its stage
returns, not when someone runs a load with the right flags.

publish(data, sets=[...]) replaces, for the label sets named, their rows
in search_paragraphs, paragraph_grades and measure_findings (from each
set's current run folder), and the small whole tables the views join on
(labels, measure_types), keeping every view; with
links=True the two package-link tables too. src/load_pg.py remains the
full rebuild for the corpus tables (./wbg prepare runs it) and for a
fresh database.

The files under data/ stay the record (docs/methodology.md, section 6);
Postgres is where they are read.
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import load_pg  # noqa: E402

DEFAULT_DSN = load_pg.DEFAULT_DSN


def dsn():
    return os.environ.get("WBG_PG", DEFAULT_DSN)


def connect():
    import psycopg
    return psycopg.connect(dsn(), autocommit=True)


def publish(data, *, sets=None, projects=None, links=False, log=print, conn=None):
    """Write the named sets' current runs into Postgres. Returns the
    tables written, with their row counts. A run over named projects (a
    smoke test) is not written: its rows would replace each set's rows for
    the whole cohort, and Metabase would show one project until the next
    cohort run (2026-10-21, on a one-project test). $WBG_PUBLISH_SCOPED=1
    writes it anyway, for a database that holds only those projects."""
    import psycopg.errors as pg_errors
    if projects and os.environ.get("WBG_PUBLISH_SCOPED") != "1":
        log(f"  postgres: not written: a run over {len(projects)} named project(s) would "
            f"replace the cohort's rows (set WBG_PUBLISH_SCOPED=1 for a database of "
            f"those projects alone)")
        return {}
    t0 = time.time()
    own = conn is None
    conn = conn or connect()
    written = {}
    try:
        # One transaction for every table and the views rebuilt over them: a
        # dashboard sees the old state or the new one, never a measure gone
        # between two writes or a view missing while its table is replaced.
        # Rebuilding a view takes a lock a dashboard card holds while it
        # reads, so this waits LOCK_TIMEOUT and no longer: a publish stuck
        # behind a reader looks like a crash, and holds the reader up too.
        with conn.transaction():
            load_pg.lock_timeout(conn)
            _publish(conn, data, sets, projects, links, log, written)
    except pg_errors.LockNotAvailable:
        log(f"  postgres: no lock within {load_pg.LOCK_TIMEOUT}; nothing was "
            f"written. Who holds one:")
        load_pg.report_lock_wait(conn, log)
        raise SystemExit(1)
    finally:
        if own:
            conn.close()
    log("  postgres: " + ", ".join(f"{t} {n:,}" for t, n in written.items())
        + f" ({time.time() - t0:.0f}s)")
    return written


def _publish(conn, data, sets, projects, links, log, written):
    """The writes of publish(), inside its transaction."""
    load_pg.prepare(conn)
    sets = sorted(sets) if sets else None
    for table, cols, rows in (
            ("labels", ["label_set", "label_id", "name", "definition", "active", "version",
                        "folder"], load_pg.label_rows()),
            ("measure_types", ["label_set", "label_id", "type_id", "type_name", "definition",
                               "merged_from"], load_pg.type_rows(data)),
            *zip(("label_queries", "label_patterns", "label_scale"),
                 (load_pg.QUERY_COLS, load_pg.PATTERN_COLS, load_pg.SCALE_COLS),
                 load_pg.definition_rows())):
        n, _bad, made = load_pg.replace_rows(conn, table, cols, rows, "label_set", None,
                                             f"publish:{_now()}")
        written[table] = n
    for table, cols, finder in (
            ("search_paragraphs", load_pg.ROLLUP_COLS,
             lambda: load_pg.search_tables(data, sets)),
            ("paragraph_grades", load_pg.CLASSIFY_COLS,
             lambda: load_pg.classify_rows(data, projects, None, sets)),
            ("measure_findings", load_pg.MEASURE_COLS,
             lambda: load_pg.measure_rows(data, projects, sets))):
        sha, rows = finder()
        if sha is None:
            continue
        # Only the sets with a current run are replaced; a set named but
        # not found (being re-run, or run over another scope) keeps its
        # rows, and says so.
        keys = load_pg.found_sets(sha)
        missing = sorted(set(sets or []) - set(keys))
        if missing:
            log(f"  {table}: no current run for {', '.join(missing)}; their rows are kept")
        n, bad, made = load_pg.replace_rows(conn, table, cols, rows, "label_set", keys, sha)
        load_pg.purge_removed_sets(conn, table)
        written[table] = n
        if bad:
            log(f"  {table}: unparseable -> NULL: "
                + ", ".join(f"{k}={v}" for k, v in sorted(bad.items())))
    # The graded sheets' headline numbers (./wbg check --evaluate) are small
    # and change with every evaluation: written whole on every publish.
    qpath = os.path.join(data, "labels", "measure_quality.csv")
    if os.path.exists(qpath):
        written["measure_quality"] = load_pg.load_table(conn, data, "measure_quality",
                                                        "labels/measure_quality.csv")[0]
    # The paragraph counts read every stage's classification (src/prior.py):
    # rebuilt whole at every publish, so a set re-run moves every sentence's.
    sha, ctx = load_pg.context_rows(data, projects)
    if sha is not None:
        written["unit_paragraph_context"] = load_pg.replace_rows(
            conn, "unit_paragraph_context", load_pg.CONTEXT_COLS, ctx, "unit_id", None, sha)[0]
    # The commitment checks (src/commit_checks.py) span every measure: small,
    # written whole whenever a current run exists.
    sha, cpairs = load_pg.check_rows(data, projects)
    if sha is not None:
        written["unit_pair_checks"] = load_pg.replace_rows(
            conn, "unit_pair_checks", load_pg.CHECK_PAIR_COLS, cpairs, "unit_id", None, sha)[0]
    # Every sentence's asset, decided as the measure stage decides it.
    sha, ua = load_pg.unit_asset_rows(data, projects)
    if sha is not None:
        written["unit_assets"] = load_pg.replace_rows(
            conn, "unit_assets", load_pg.UNIT_ASSET_COLS, ua, "unit_id", None, sha)[0]
    # The record of judgement: grades, draws, layers, gates, runs, spend and
    # the answer store, each replaced when its sources changed.
    for table, cols, sha, rows, types in load_pg.judgement_tables(data):
        if sha is not None and load_pg._last_sha(conn, table) != sha:
            written[table] = load_pg.replace_rows(conn, table, cols, rows, "label_set", None,
                                                  sha, types=types)[0]
    sha, ans = load_pg.answer_rows(data)
    if sha is not None and load_pg._last_sha(conn, "answers") != sha:
        written["answers"] = load_pg.replace_rows(conn, "answers", load_pg.ANSWER_COLS, ans,
                                                  "answer_sha256", None, sha,
                                                  types={"answer": "jsonb"})[0]
    if links:
        sha, assets_, pairs_ = load_pg.link_rows(data, projects)
        if sha is not None:
            for table, cols, rows in (("package_assets", load_pg.ASSET_LINK_COLS, assets_),
                                      ("commitment_packages", load_pg.PAIR_LINK_COLS, pairs_)):
                n, _bad, made = load_pg.replace_rows(conn, table, cols, rows, "package_id",
                                                     None, sha)
                written[table] = n
    # Keys, comments, views and functions, every time: sql/views/ is
    # code, and a publish is when a changed view or function lands.
    load_pg.finish(conn, log=log)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def try_publish(data, log=print, **kw):
    """publish(), but a database that cannot be reached is reported, not
    fatal: the run folder is written either way and ./wbg publish catches
    up."""
    try:
        return publish(data, log=log, **kw)
    except Exception as exc:                          # noqa: BLE001
        log(f"  postgres: not written ({str(exc).splitlines()[0]}); run ./wbg publish")
        return None


def main():
    import argparse
    from paths import data_root
    import classify
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sets", nargs="*", help="label sets to write; none = every set with a run")
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--projects", default="", help="the scope the runs were made over")
    ap.add_argument("--links", action="store_true", help="also the package-link tables")
    args = ap.parse_args()
    publish(args.data, sets=args.sets or None, projects=classify.scope(args.projects),
            links=args.links)
    return 0


if __name__ == "__main__":
    sys.exit(main())
