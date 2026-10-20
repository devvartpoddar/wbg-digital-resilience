#!/usr/bin/env python3
"""Measures asked for from the dashboard: a queue in Postgres, run one at a
time under a hard spend cap.

The dashboard's form inserts a row into wbg_requests.measure_requests (a
separate schema, so no load of wbg ever touches it; the role that may write
there is set up on the box, not here). This script takes the oldest queued
row, checks it, writes the measure's files to data/requested_labels/ (a
third label folder that src/labels.py reads; never analysis/labels/, which
people keep in git), and runs the same stages as ./wbg add: the search, the
funnel, the type proposals and their assignment, then the Postgres write.
It records the stage and the spend as each finishes, and never retries a
request that failed: a person looks at it and queues it again.

The cap is per request (REQUEST_CAP_USD) and per day across requests
(DAILY_CAP_USD); the stages enforce it themselves through
$WBG_SPEND_CAP_USD (src/answers.py), so a request that would pass it stops
with what is stored, as status 'stopped'.

  python3 src/measure_requests.py            run the queue until it is empty
  python3 src/measure_requests.py --once     one request
  python3 src/measure_requests.py --schema   create the schema and table only

Reads  wbg_requests.measure_requests; analysis/labels/backup_power_scale.csv
       (the grade wording, with the measure's name put in)
Writes data/requested_labels/<set>.csv, <set>_queries.csv, <set>_scale.csv,
       <set>_patterns.csv; the request row's status, stage, spend and result
"""
import argparse, csv, datetime, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import labels  # noqa: E402

SCHEMA = "wbg_requests"
TABLE = f"{SCHEMA}.measure_requests"
REQUEST_CAP_USD = 1.0       # the most one request may spend on models
DAILY_CAP_USD = 3.0         # the most all requests together may spend in a UTC day
NAME_RULE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
SCALE_TEMPLATE = os.path.join(labels.ROOT, "analysis", "labels", "backup_power_scale.csv")
STAGES = (("search", ["search.py", "{set}"]),
          ("measure", ["measure.py", "{set}", "--no-publish"]),
          ("types", ["kinds.py", "{set}"]),
          ("assign", ["kinds.py", "{set}", "--assign", "--no-publish"]),
          ("publish", ["store.py", "{set}"]))

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {TABLE} (
  request_id bigserial PRIMARY KEY,
  label_set text NOT NULL,
  measure_name text NOT NULL,
  definition text NOT NULL,
  phrases text NOT NULL,
  patterns text,
  cohort_set text,
  requested_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'done', 'failed', 'stopped')),
  stage text,
  spend_usd numeric NOT NULL DEFAULT 0,
  committed_found integer,
  error text,
  finished_at timestamptz);
COMMENT ON TABLE {TABLE} IS 'Measures asked for from the dashboard''s New measure page: a person fills label_set (a short id), measure_name, definition (name the measure, never the digital asset), phrases (one per line) and optionally patterns (one per line) and cohort_set; src/measure_requests.py runs them one at a time under a US$1 cap each and fills status, stage, spend_usd, committed_found and error. A failed request is not retried on its own.';
"""


def connect():
    import store
    return store.connect()


def ensure_schema(conn):
    conn.execute(DDL)


def check(req, existing):
    """The reason a request cannot run, or ''."""
    name = req["label_set"] or ""
    if not NAME_RULE.match(name):
        return f"label_set {name!r} must match {NAME_RULE.pattern}"
    if name in existing:
        return f"a label set named {name!r} exists already"
    if len((req["definition"] or "").split()) < 8:
        return "the definition needs a sentence or more"
    phrases = [p.strip() for p in (req["phrases"] or "").splitlines() if p.strip()]
    if len(phrases) < 3:
        return "at least three search phrases, one per line"
    for p in (req["patterns"] or "").splitlines():
        if p.strip():
            try:
                re.compile(p.strip())
            except re.error as exc:
                return f"pattern {p.strip()!r} does not compile: {exc}"
    return ""


def write_files(req, folder):
    """The measure's four files, as analysis/labels/ keeps them."""
    name = req["label_set"]
    os.makedirs(folder, exist_ok=True)

    def put(file, cols, rows):
        path = os.path.join(folder, file)
        with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        os.replace(path + ".part", path)
    put(f"{name}.csv", ["measure_id", "measure_name", "definition", "active", "version", "note"],
        [{"measure_id": name, "measure_name": " ".join(req["measure_name"].split()),
          "definition": " ".join(req["definition"].split()), "active": "true", "version": "v1",
          "note": f"Requested from the dashboard (request {req['request_id']})."}])
    phrases = [p.strip() for p in req["phrases"].splitlines() if p.strip()]
    put(f"{name}_queries.csv", ["measure_id", "query_id", "query", "active"],
        [{"measure_id": name, "query_id": f"q{i}", "query": p, "active": "true"}
         for i, p in enumerate(phrases, 1)])
    with open(SCALE_TEMPLATE, newline="", encoding="utf-8") as fh:
        scale = list(csv.DictReader(fh))
    put(f"{name}_scale.csv", ["grade", "description"], scale)
    pats = [p.strip() for p in (req["patterns"] or "").splitlines() if p.strip()]
    if pats:
        put(f"{name}_patterns.csv", ["measure_id", "pattern_id", "pattern", "active"],
            [{"measure_id": name, "pattern_id": f"p{i}", "pattern": p, "active": "true"}
             for i, p in enumerate(pats, 1)])


def spent(data, name, since):
    """US$ the spend ledger records for this set since a moment."""
    import classify
    path = where(data, "reports", "spend.csv")
    total = 0.0
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["label_set"] == name and r["at"] >= since:
                    total += float(r["cost_usd"] or 0)
    return total


def committed(data, name, projects):
    import measure
    folder = measure.current_run(name, data, projects=projects)
    if not folder:
        return None
    with open(os.path.join(folder, "units.csv"), newline="", encoding="utf-8") as fh:
        return sum(1 for u in csv.DictReader(fh) if u["sent"] == "true" and u["grade"] == "3")


def run_one(conn, data, log=print, runner=subprocess.run):
    """Take and run the oldest queued request. Returns its final status, or
    None when the queue is empty or today's cap is spent."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    spent_today = float(conn.execute(
        f"SELECT coalesce(sum(spend_usd), 0) FROM {TABLE} WHERE requested_at::date = %s "
        f"OR finished_at::date = %s", (today, today)).fetchone()[0])
    left = min(REQUEST_CAP_USD, DAILY_CAP_USD - spent_today)
    if left <= 0.01:
        log(f"requests: today's cap of US${DAILY_CAP_USD:.2f} is spent; the queue waits")
        return None
    with conn.transaction():
        row = conn.execute(f"SELECT request_id, label_set, measure_name, definition, phrases, "
                           f"patterns, cohort_set FROM {TABLE} WHERE status = 'queued' "
                           f"ORDER BY requested_at, request_id LIMIT 1 FOR UPDATE SKIP LOCKED"
                           ).fetchone()
        if not row:
            return None
        req = dict(zip(["request_id", "label_set", "measure_name", "definition", "phrases",
                        "patterns", "cohort_set"], row))
        conn.execute(f"UPDATE {TABLE} SET status = 'running', stage = 'check' "
                     f"WHERE request_id = %s", (req["request_id"],))

    def finish(status, error=None, found=None, cost=0.0):
        conn.execute(f"UPDATE {TABLE} SET status = %s, error = %s, committed_found = %s, "
                     f"spend_usd = %s, finished_at = now() WHERE request_id = %s",
                     (status, error, found, round(cost, 4), req["request_id"]))
        log(f"request {req['request_id']} ({req['label_set']}): {status}"
            + (f" - {error}" if error else ""))
        return status

    why = check(req, set(labels.label_sets()))
    if why:
        return finish("failed", why)
    name = req["label_set"]
    write_files(req, labels.requested_dir())
    scope = []
    projects = None
    if req["cohort_set"]:
        from wbg import select
        projects = sorted(select(cohort_set=req["cohort_set"], data=data).ids)
        scope = ["--projects", ",".join(projects)]
    since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    py = sys.executable
    for stage, cmd in STAGES:
        cost = spent(data, name, since)
        conn.execute(f"UPDATE {TABLE} SET stage = %s, spend_usd = %s WHERE request_id = %s",
                     (stage, round(cost, 4), req["request_id"]))
        env = dict(os.environ, WBG_DATA=data,
                   WBG_SPEND_CAP_USD=f"{max(left - cost, 0):.4f}")
        args = [a.format(set=name) for a in cmd] + (scope if stage != "search" else [])
        done = runner([py, os.path.join(os.path.dirname(os.path.abspath(__file__)), args[0]),
                       *args[1:]], env=env, capture_output=True, text=True)
        if done.returncode:
            tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
            cost = spent(data, name, since)
            if any("spend cap" in t for t in tail):
                return finish("stopped", f"{stage}: " + " | ".join(tail), cost=cost)
            return finish("failed", f"{stage}: " + " | ".join(tail), cost=cost)
    return finish("done", found=committed(data, name, projects), cost=spent(data, name, since))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--once", action="store_true", help="one request, then stop")
    ap.add_argument("--schema", action="store_true", help="create the schema and table only")
    args = ap.parse_args()
    with connect() as conn:
        ensure_schema(conn)
        if args.schema:
            return 0
        while True:
            status = run_one(conn, args.data)
            if status is None or args.once:
                return 0
            time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
