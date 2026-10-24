#!/usr/bin/env python3
"""The pipeline's command line and the one place its stages are ordered.

    ./wbg run [--from S] [--to S] [--only S] [--measures a b] [--max-spend US$] [--dry-run]
        fetch > prepare > embed > search > classify > measure > link > publish (> audit)
    ./wbg add <set>...        a measure from its files to Postgres (run --from search --to measure)
    ./wbg publish             every decision read again from the scores, then the views
    ./wbg check draw|fill|evaluate|spotcheck|import   the grading cycle (src/grading.py)
    ./wbg list|prepare|summary [filters]              the cohort, the corpus, where each project stands
    ./wbg status | release | audit | sensitivity | requests

Projects are selected from inputs/config/cohort.csv (included rows), kept by
people and only read here. The corpus stages (fetch, prepare) skip what is up
to date by fingerprint (data/reports/stages.json); the model stages ask only
what the answer store lacks (src/ask.py). Every threshold is a line of the
control panel; no option here moves a number.
"""
import argparse, csv, datetime, glob, hashlib, json, os, re, subprocess, sys
from collections import Counter, defaultdict

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
from paths import data_root, where  # noqa: E402

COHORT = os.path.join(ROOT, "inputs", "config", "cohort.csv")
UPCOMING = ("Pending", "Pending Implementation", "Under Review", "Planned")


# ------------------------------------------------------------------ selection

class Selection:
    """A set of cohort projects, with a name for its outputs."""

    def __init__(self, rows, name):
        self.rows = rows
        self.name = name
        self.ids = [r["project_id"] for r in rows]
        self._ids = set(self.ids)

    def __len__(self):
        return len(self.ids)

    def __contains__(self, pid):
        return pid in self._ids

    def __repr__(self):
        return f"Selection({self.name!r}, {len(self)} projects)"


def cohort(path=COHORT):
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["included"].strip().lower() == "true"]


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


# Columns a selection row takes from data/projects/projects.csv. Region,
# country and approval year come from the cohort, and from the projects
# interface only where the cohort is blank.
PROJECT_COLS = ("project_name", "status", "closing_date", "practice", "practice_code",
                "managing_unit", "unit_code")
FALLBACK = ("region", "country_code", "approval_fy")


def _projects(data):
    path = where(data or data_root(), "projects")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["project_id"]: r for r in csv.DictReader(fh)}


def _has(values, *fields):
    """Does any value occur, without regard to case, in any of the fields?"""
    text = " | ".join(f.lower() for f in fields)
    return any(v in text for v in values)


def select(project=None, country=None, region=None, fy=None, practice=None, unit=None,
           cohort_set=None, path=COHORT, data=None):
    """Cohort projects matching every filter given; all of them when none is.

    cohort_set  the cohort's own set ('digital': operations whose object is a
                digital asset; 'digital_contributor': operations where a
                digital asset contributes to another sector's object)
    project   project ids, a list or comma-separated ('P171528,P176932')
    country   country codes ('KE', '3W')
    region    region names, or part of one, without regard to case ('western')
    fy        approval fiscal years ('2024')
    practice  Global Practice name or code, or part of one ('digital', 'transport')
    unit      managing unit or its code, or part of one ('IDD04', 'AFR EAST')

    Region, country and year are the cohort's, or the projects interface's
    where the cohort records none. Practice and unit come from the projects
    table (src/projects.py), so they need a prepared store. A project with no
    value for a filter is not matched by it."""
    rows = [dict(r) for r in cohort(path)]
    info = _projects(data)
    for r in rows:
        p = (info or {}).get(r["project_id"], {})
        for col in FALLBACK:
            r[col] = r.get(col) or p.get(col, "")
        for col in PROJECT_COLS:
            r[col] = p.get(col, "")
    given = {"project": project, "country": country, "region": region, "fy": fy,
             "practice": practice, "unit": unit, "set": cohort_set}
    blank = [k for k, v in given.items() if v is not None and not _as_list(v)]
    if blank:
        raise SystemExit(f"empty filter: --{', --'.join(blank)}")
    projects = [p.upper() for p in _as_list(project)]
    countries = [c.upper() for c in _as_list(country)]
    regions = [r.lower() for r in _as_list(region)]
    years = [re.sub(r"(?i)^fy\s*", "", y) for y in _as_list(fy)]
    years = [f"20{y}" if len(y) == 2 and y.isdigit() else y for y in years]
    pracs, units = [x.lower() for x in _as_list(practice)], [x.lower() for x in _as_list(unit)]
    sets = [x.lower() for x in _as_list(cohort_set)]
    known_sets = {r.get("cohort_set", "").lower() for r in rows}
    if sets and not set(sets) <= known_sets:
        raise SystemExit(f"no such cohort set: {', '.join(sorted(set(sets) - known_sets))} "
                         f"(the cohort has {', '.join(sorted(known_sets))})")
    if (pracs or units) and info is None:
        raise SystemExit("no projects table yet: run ./wbg prepare first")
    unknown = sorted(set(projects) - {r["project_id"] for r in rows})
    if unknown:
        raise SystemExit(f"not in the cohort (or not included): {', '.join(unknown)}")
    out = [r for r in rows
           if (not projects or r["project_id"] in projects)
           and (not countries or any(c in countries for c in r["country_code"].upper().split("|")))
           and (not regions or _has(regions, r["region"]))
           and (not years or r["approval_fy"] in years)
           and (not sets or r.get("cohort_set", "").lower() in sets)
           and (not pracs or _has(pracs, r["practice"], r["practice_code"]))
           and (not units or _has(units, r["managing_unit"], r["unit_code"]))]
    # Each value carries its filter's name, so '--practice digital' and
    # '--unit digital' do not share a folder; a long name keeps a hash of
    # the whole.
    parts = [*[f"project-{x}" for x in projects], *[f"country-{x}" for x in countries],
             *[f"region-{x}" for x in regions], *[f"fy{y}" for y in years],
             *[f"practice-{x}" for x in pracs], *[f"unit-{x}" for x in units],
             *[f"set-{x}" for x in sets]]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", "-".join(parts)).strip("-").lower() or "all"
    if len(name) > 80:
        name = name[:71].rstrip("-") + "-" + hashlib.sha256(name.encode()).hexdigest()[:8]
    return Selection(out, name)


# ------------------------------------------------------------------ preparing

# Each stage after fetching: the script, the code it depends on, and the files
# it reads. It runs when any of those changed since it last ran.
STAGES = [
    ("projects", "projects.py", ["projects.py", "fetch.py"],
     [("projects_json", "*.json"), ("documents",), ("cohort",)],
     [("projects",)]),
    ("clean", "clean.py",
     ["clean.py", "pdf_layout.py", "text_rules.py", "components.py", "sentences.py",
      "keys.py"],
     [("documents",), ("pdf", "*.pdf")],
     [("appraisal", "paragraphs.csv"), ("appraisal", "sentences.csv"),
      ("appraisal", "document_parts.csv")]),
    ("components", "components.py", ["components.py", "pdf_layout.py", "text_rules.py",
                                     "keys.py"],
     [("documents",), ("appraisal", "paragraphs.csv")],
     [("appraisal", "components.csv")]),
    ("clean_procurement", "clean_procurement.py",
     ["clean_procurement.py", "glue.py", "text_rules.py", "plan_table.py", "links.py",
      "keys.py", "procurement_vocab.json"],
     [("procurement", "packages_raw.csv"), ("procurement", "notices_raw.csv"),
      ("procurement", "awards_raw.csv"), ("appraisal", "components.csv"),
      ("appraisal", "paragraphs.csv")],
     [("procurement", "packages.csv"), ("procurement", "notices.csv"),
      ("procurement", "awards.csv"), ("procurement", "superseded_packages.csv")]),
]


def _library_versions():
    """The PDF reader's version: an upgrade changes how text is read, so
    every stage after it must run again (AGENTS.md rule 7)."""
    try:
        from importlib.metadata import version
        return f"pdfplumber={version('pdfplumber')}"
    except Exception:
        return "pdfplumber=unknown"


def _parallel(jobs, names, commands, data):
    """Run commands (name -> [[script, *args], ...]) for several names at
    once, each name's commands in order in its own process. Prints each
    name's output when it finishes. Returns the names that failed."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    env = dict(os.environ, WBG_DATA=data)

    def one(name):
        t0, log = time.time(), []
        for script, *extra in commands(name):
            cmd = [_python(), os.path.join(SRC, script), *extra]
            done = subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True)
            log.append(f"== {script} {' '.join(extra)}\n{done.stdout.rstrip()}")
            if done.returncode:
                log.append(done.stderr.rstrip())
                return name, time.time() - t0, False, "\n".join(log)
        return name, time.time() - t0, True, "\n".join(log)

    failed = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for name, secs, ok, log in pool.map(one, names):
            print(log, flush=True)
            print(f"-- {name}: {'done' if ok else 'FAILED'} in {secs:.0f}s", flush=True)
            if not ok:
                failed.append(name)
    return failed


def _python():
    box = "/opt/yggdrasil/venvs/work/bin/python"
    return os.environ.get("WBG_PY") or (box if os.path.exists(box) else sys.executable)


def _run(script, *args, data, tail=0):
    """Run one stage. tail=N shows only the last N lines of its output (the
    audits print their whole report; it is in data/reports/ anyway)."""
    env = dict(os.environ, WBG_DATA=data)
    cmd = [_python(), os.path.join(SRC, script), *args]
    print(f"== {script} {' '.join(args)}".rstrip(), flush=True)
    if not tail:
        subprocess.run(cmd, check=True, env=env, cwd=ROOT)
        return
    done = subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True)
    print("\n".join(done.stdout.rstrip().splitlines()[-tail:]), flush=True)
    if done.returncode:
        sys.stderr.write(done.stderr)
        raise subprocess.CalledProcessError(done.returncode, cmd)

def _fingerprint(data, code, inputs):
    """SHA-256 over the stage's code and the bytes of its input files. A PDF
    matched by a pattern is fingerprinted by name and size, which is enough:
    its content checksum is in documents.csv, also an input. Any other file
    matched by a pattern is read whole."""
    h = hashlib.sha256()
    h.update(_library_versions().encode())
    for name in code:
        with open(os.path.join(SRC, name), "rb") as fh:
            h.update(name.encode() + fh.read())
    for spec in inputs:
        # ("cohort",) is the hand-kept cohort file, outside the data folder.
        path = COHORT if spec == ("cohort",) else where(data, *spec)
        label = "cohort" if spec == ("cohort",) else os.path.relpath(path, data)
        if "*" in path:
            for p in sorted(glob.glob(path)):
                h.update(f"{os.path.basename(p)}:{os.path.getsize(p)}".encode())
                if not p.endswith(".pdf"):
                    with open(p, "rb") as fh:
                        h.update(fh.read())
        elif os.path.exists(path):
            # The path relative to the data folder: moving the folder is not
            # a change to any stage's input.
            h.update(label.encode())
            with open(path, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
        else:
            h.update(f"{label}:missing".encode())
    return h.hexdigest()


def _outputs(data, outputs):
    """SHA-256 over a stage's output files, or None if any is missing: a
    stage whose output was removed or replaced is not up to date, whatever
    its inputs say."""
    h = hashlib.sha256()
    for spec in outputs:
        path = where(data, *spec)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    return h.hexdigest()


def _state_path(data):
    return where(data, "reports", "stages.json")


def _load_state(data):
    """What each stage last ran on. A store that predates this file starts from
    what it already holds: a project with documents is counted as fetched, so
    the first prepare does not fetch the whole cohort again."""
    try:
        with open(_state_path(data), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        pass
    seen = {"appraisal": set(), "procurement": set(), "projects": set()}
    for r in _read(where(data, "documents")):
        seen["appraisal"].update(p for p in r["project_ids"].split("|") if p)
    for name in ("packages_raw.csv", "notices_raw.csv", "awards_raw.csv"):
        seen["procurement"].update(r["project_id"] for r in
                                   _read(where(data, "procurement", name)))
    seen["projects"].update(os.path.basename(p)[:-5] for p in
                            glob.glob(where(data, "projects_json", "P*.json")))
    stamp = "before stages.json"
    return {"fetched": {k: {p: stamp for p in sorted(v)} for k, v in seen.items()}}


def _fetch_status(data, kind, asked):
    """(ok, failed) from the fetch's status file; everything asked counts as
    ok when the fetcher wrote none."""
    try:
        with open(where(data, "fetch_status", f"{kind}.json"), encoding="utf-8") as fh:
            s = json.load(fh)
    except (OSError, ValueError):
        return list(asked), []
    ok = set(s.get("ok", []))
    return [p for p in asked if p in ok], [p for p in asked if p not in ok]


def _save_state(data, state):
    path = _state_path(data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(path + ".part", path)


def stale_stages(data=None):
    """The stages whose code, inputs or outputs differ from when they last
    ran: what ./wbg prepare would run now."""
    data = data or data_root()
    recorded = _load_state(data).get("stages", {})
    out = []
    for name, _script, code, inputs, outputs in STAGES:
        rec = recorded.get(name)
        if isinstance(rec, str):
            rec = {"in": rec, "out": None}
        out_now = _outputs(data, outputs)
        if not rec or rec["in"] != _fingerprint(data, code, inputs) or out_now is None \
                or rec["out"] not in (None, out_now):
            out.append(name)
    return out


PROJECT_REFRESH_DAYS = 7


def _record_due(pid, fetched_on, today, data, _status={}):
    """Whether a project's record is old enough to fetch again: fetched
    PROJECT_REFRESH_DAYS ago or more (or before dates were recorded), and the
    project not Closed."""
    if not _status.get(data):
        _status[data] = {r["project_id"]: r.get("status", "")
                         for r in _read(where(data, "projects"))}
    if _status[data].get(pid) == "Closed":
        return False
    try:
        age = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(fetched_on)).days
    except ValueError:
        return True
    return age >= PROJECT_REFRESH_DAYS



def prepare(sel=None, update=False, data=None, fetch=True, refresh_projects=False):
    """Bring the store up to date for a selection.

    2. Fetching: each selected project not fetched before - or every selected
       project with update=True - is fetched (appraisal documents, then
       procurement plans, notices and awards).
    3. Every later stage runs over the whole store, from its caches, only if
       its code or inputs changed since it last ran.

    Returns the names of the stages that ran."""
    data = data or data_root()
    sel = sel if sel is not None else select()
    state = _load_state(data)
    ran = []
    if fetch:
        # UTC, like every other timestamp the pipeline records.
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        for kind, script in (("appraisal", "fetch.py"), ("procurement", "fetch_procurement.py"),
                             ("projects", "fetch_projects.py")):
            done = state.setdefault("fetched", {}).setdefault(kind, {})
            # Project records (status, closing date, commitment) change under a
            # restructuring or an extension: an open project's record is
            # fetched again once it is PROJECT_REFRESH_DAYS old; a closed
            # one's is not. Documents and procurement are fetched once, and
            # again with --update.
            todo = [p for p in sel.ids if update or p not in done
                    or (kind == "projects" and (refresh_projects
                                                or _record_due(p, done[p], today, data)))]
            if not todo:
                continue
            args = ["--projects", ",".join(todo)]
            if update and kind == "procurement":
                args.append("--refresh-records")      # notices and awards change
            _run(script, *args, data=data)
            # Only what the fetch says it fully read counts as fetched; a
            # project whose request failed is tried again next time.
            ok, _failed = _fetch_status(data, kind, todo)
            for p in ok:
                done[p] = today
            _save_state(data, state)
            ran.append(script)
    for name, script, code, inputs, outputs in STAGES:
        fp = _fingerprint(data, code, inputs)
        rec = state.get("stages", {}).get(name)
        if isinstance(rec, str):                 # a state file from before outputs
            rec = {"in": rec, "out": None}
        out = _outputs(data, outputs)
        if rec and rec["in"] == fp and out is not None and rec["out"] in (None, out):
            if rec["out"] is None:
                state["stages"][name] = {"in": fp, "out": out}
                _save_state(data, state)
            continue
        _run(script, data=data)
        state.setdefault("stages", {})[name] = {"in": _fingerprint(data, code, inputs),
                                                "out": _outputs(data, outputs)}
        _save_state(data, state)
        ran.append(name)
    if not ran:
        print("nothing to do: every stage is up to date", flush=True)
        return ran
    # The corpus changed: record it and put it where it is read, in one go.
    _run("release.py", data=data)
    _run("load_pg.py", data=data)
    return ran


# ------------------------------------------------------------------ reading

from common import read_csv as _read  # noqa: E402



def _in(sel, ids):
    want = set(sel.ids)
    return any(p in want for p in (ids or "").split("|"))


def tables(sel=None, data=None, text=False):
    """The cleaned tables, cut to the selection, as lists of rows (dicts).

    projects, documents, paragraphs, sentences, components, packages,
    superseded_packages, notices, awards. With text=True each paragraph also carries its text (the
    tables themselves store offsets, not text)."""
    data = data or data_root()
    sel = sel if sel is not None else select()
    # A document belongs to the projects its parts belong to
    # (document_parts.csv): its listing, or, for a combined appraisal
    # document, the operations its data sheets name.
    owners = defaultdict(list)
    for r in _read(where(data, "appraisal", "document_parts.csv")):
        for p in r["project_ids"].split("|"):
            if p not in owners[r["doc_id"]]:
                owners[r["doc_id"]].append(p)
    docs = [dict(r, listed_project_ids=r["project_ids"],
                 project_ids="|".join(sorted(owners[r["doc_id"]])) if owners.get(r["doc_id"])
                 else r["project_ids"])
            for r in _read(where(data, "documents"))]
    out = {
        "projects": [r for r in _read(where(data, "projects")) if r["project_id"] in sel],
        "documents": [r for r in docs if _in(sel, r["project_ids"])],
        "paragraphs": [r for r in _read(where(data, "appraisal", "paragraphs.csv"))
                       if _in(sel, r["project_ids"])],
        "components": [r for r in _read(where(data, "appraisal", "components.csv"))
                       if _in(sel, r["project_ids"])],
    }
    # By paragraph, not document: a shared document's other parts belong to
    # other projects.
    paras = {r["paragraph_id"] for r in out["paragraphs"]}
    out["sentences"] = [r for r in _read(where(data, "appraisal", "sentences.csv"))
                        if r["paragraph_id"] in paras]
    for name in ("packages", "superseded_packages", "notices", "awards"):
        out[name] = [r for r in _read(where(data, "procurement", f"{name}.csv"))
                     if r["project_id"] in sel]
    if text:
        cache = {}
        for r in out["paragraphs"]:
            d = r["doc_id"]
            if d not in cache:
                with open(where(data, "text", f"{d}.txt"), encoding="utf-8") as fh:
                    cache[d] = fh.read()
            r["text"] = cache[d][int(r["char_start"]):int(r["char_end"])]
    return out


def _amount(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def summary(sel=None, data=None, today=None, t=None):
    """One row per selected project: what the store holds and where it stands.

    Documents and the latest appraisal-side disclosure; components and their
    cost; the latest procurement plan; packages by STEP status; what is still
    to come (Pending, Pending Implementation, Under Review, Planned) with its
    estimated value, how many are planned for a date already past, and the
    next planned date; notices, and those still open
    (deadline today or later); signed contracts and their value. Dates are
    ISO; amounts are US dollars as the plans and the awards interface give
    them. A project with no procurement plan has blank plan columns."""
    data = data or data_root()
    sel = sel if sel is not None else select()
    today = today or datetime.date.today().isoformat()
    t = t or tables(sel, data)
    by = defaultdict(lambda: defaultdict(list))
    for name in ("documents", "components"):
        for r in t[name]:
            for p in r["project_ids"].split("|"):
                by[p][name].append(r)
    for name in ("packages", "notices", "awards"):
        for r in t[name]:
            by[r["project_id"]][name].append(r)
    rows = []
    for c in sel.rows:
        p = c["project_id"]
        g = by[p]
        docs = g["documents"]
        kinds = Counter(d["doc_kind"] for d in docs)
        comps = _latest_components(g["components"])
        pk = g["packages"]
        statuses = Counter(r["status"] for r in pk)
        up = [r for r in pk if r["status"] in UPCOMING]
        next_dates = sorted(r["planned_date"] for r in up if r["planned_date"] >= today)
        open_n = [n for n in g["notices"] if n["deadline_date"] and n["deadline_date"] >= today]
        rows.append({
            "project_id": p, "project_name": c.get("project_name", ""),
            "country_code": c["country_code"], "region": c["region"],
            "approval_fy": c["approval_fy"], "status": c.get("status", ""),
            "closing_date": c.get("closing_date", ""), "practice": c.get("practice", ""),
            "managing_unit": c.get("managing_unit", ""),
            "documents": len(docs),
            "documents_by_kind": "; ".join(f"{k} {v}" for k, v in sorted(kinds.items())),
            "latest_document_date": max((d["disclosure_date"] for d in docs), default=""),
            "components": "; ".join(f"{n}. {name}" + (f" (US${cost}m)" if cost else "")
                                    for n, name, cost in comps),
            "components_cost_usd_m": f"{sum(_amount(x[2]) for x in comps):.2f}" if comps else "",
            "latest_plan_date": max((r["plan_disclosure_date"] for r in pk), default=""),
            "packages": len(pk),
            "packages_by_status": "; ".join(f"{k} {v}" for k, v in statuses.most_common()),
            "upcoming_packages": len(up),
            # Of those, how many the project's latest plan no longer lists:
            # their status is from an older plan (packages.in_latest_plan).
            "upcoming_not_in_latest_plan": sum(1 for r in up
                                               if r.get("in_latest_plan") == "false"),
            "upcoming_estimated_usd": f"{sum(_amount(r['estimated_amount']) for r in up):.2f}",
            # Still to come, but planned for a date already past: the plan has
            # not been updated, or the package is late. Either is worth a question.
            "upcoming_planned_date_passed": sum(1 for r in up if r["planned_date"]
                                                and r["planned_date"] < today),
            "next_planned_date": next_dates[0] if next_dates else "",
            "notices": len(g["notices"]),
            "open_notices": len(open_n),
            "latest_notice_date": max((n["publication_date"] for n in g["notices"]), default=""),
            "contracts_signed": len(g["awards"]),
            "contracts_value_usd": f"{sum(_amount(a['total_amount']) for a in g['awards']):.2f}",
            "latest_contract_date": max((a["signed_date"] for a in g["awards"]), default=""),
        })
    return rows


def _latest_components(rows):
    """(number, name, cost) of top-level components, from the most recent
    document that lists them; the data sheet's list where it has one."""
    tops = [r for r in rows if r["level"] == "component" and r["number"]]
    if not tops:
        return []
    latest = max(r["disclosure_date"] for r in tops)
    pick = [r for r in tops if r["disclosure_date"] == latest]
    pref = [r for r in pick if r["source"] in ("datasheet", "restructuring")] or pick
    seen, out = set(), []
    for r in sorted(pref, key=lambda r: [int(x) for x in r["number"].split(".") if x.isdigit()]):
        if r["number"] not in seen:
            seen.add(r["number"])
            out.append((r["number"], r["name"], r["cost_usd_m"]))
    return out


def upcoming(sel=None, data=None, t=None):
    """Packages still to come, one row each, soonest planned date first, with
    the notices linked to each (links.py): how many, and the latest one's
    publication date and deadline."""
    t = t or tables(sel, data)
    cols = ("project_id", "package_id", "borrower_ref", "description_clean", "category",
            "method", "method_name", "market_approach", "component_number", "component",
            "status", "status_source", "status_as_of", "planned_date", "revised_date",
            "estimated_amount", "currency", "amount_source", "plan_disclosure_date",
            "in_latest_plan")
    notices = defaultdict(list)
    for n in t["notices"]:
        if n.get("package_id"):
            notices[n["package_id"]].append(n)
    rows = []
    for r in t["packages"]:
        if r["status"] not in UPCOMING:
            continue
        row = {k: r.get(k, "") for k in cols}
        mine = sorted(notices[r["package_id"]], key=lambda n: n["publication_date"])
        row["notices"] = len(mine)
        row["latest_notice_date"] = mine[-1]["publication_date"] if mine else ""
        row["latest_notice_deadline"] = mine[-1]["deadline_date"] if mine else ""
        rows.append(row)
    return sorted(rows, key=lambda r: (r["planned_date"] or "9999", r["project_id"],
                                       r["package_id"]))


def open_notices(sel=None, data=None, today=None, t=None):
    """Notices whose deadline is today or later, soonest deadline first."""
    today = today or datetime.date.today().isoformat()
    t = t or tables(sel, data)
    cols = ("project_id", "notice_id", "notice_type", "publication_date", "deadline_date",
            "borrower_ref", "description_clean", "category", "method", "method_name",
            "package_id", "package_link")
    pk = {r["package_id"]: r for r in t["packages"]}
    rows = []
    for n in t["notices"]:
        if not (n["deadline_date"] and n["deadline_date"] >= today):
            continue
        row = {k: n.get(k, "") for k in cols}
        p = pk.get(n.get("package_id") or "", {})
        row["package_status"] = p.get("status", "")
        row["package_estimated_amount"] = p.get("estimated_amount", "")
        rows.append(row)

# ------------------------------------------------------------------ command line

def _filters(ap):
    ap.add_argument("--project", action="append", help="project id (repeat or comma-separate)")
    ap.add_argument("--set", action="append", dest="cohort_set",
                    help="the cohort's set: digital or digital_contributor")
    ap.add_argument("--country", action="append", help="country code as in the cohort")
    ap.add_argument("--region", action="append", help="region name, or part of one")
    ap.add_argument("--fy", action="append", help="approval fiscal year")
    ap.add_argument("--practice", action="append",
                    help="Global Practice name or code, or part of one ('digital')")
    ap.add_argument("--unit", action="append",
                    help="managing unit or its code, or part of one ('IDD04')")


def _scope(args):
    """['--projects', 'P1,P2'] when a selection filter was given, else []."""
    if not any(getattr(args, k, None) for k in
               ("project", "cohort_set", "country", "region", "fy", "practice", "unit")):
        return []
    return ["--projects", ",".join(_sel(args).ids)]


def _sel(args):
    return select(project=args.project and ",".join(args.project),
                  country=args.country and ",".join(args.country),
                  region=args.region and ",".join(args.region),
                  fy=args.fy and ",".join(args.fy),
                  cohort_set=args.cohort_set and ",".join(args.cohort_set),
                  practice=args.practice and ",".join(args.practice),
                  unit=args.unit and ",".join(args.unit))


def _print_rows(rows, cols):
    if not rows:
        print("(none)")
        return
    width = {c: min(40, max(len(c), *(len(str(r.get(c, ""))) for r in rows))) for c in cols}
    print("  ".join(c.ljust(width[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, ""))[:40].ljust(width[c]) for c in cols))



# ------------------------------------------------------------------ the run

RUN_STAGES = ("fetch", "prepare", "embed", "search", "classify", "measure", "link", "publish", "audit")


def stages_between(start=None, stop=None, only=None):
    if only:
        return [only]
    i = RUN_STAGES.index(start) if start else 0
    j = RUN_STAGES.index(stop) if stop else RUN_STAGES.index("publish")
    if j < i:
        raise SystemExit(f"run: --to {stop} comes before --from {start}")
    return list(RUN_STAGES[i:j + 1])


def run(args, data):
    """The pipeline, or any stretch of it. The model stages share one
    process, one Postgres connection and one reading of the corpus; a
    failed step stops the run, and a run again resumes from the store."""
    import time
    import api
    import db
    import labels as labels_mod
    import questions as Q
    t_all = time.time()
    stages = stages_between(args.from_stage, args.to_stage, args.only)
    if args.dry_run:
        stages = [s for s in stages if s not in ("fetch", "prepare")]
    if getattr(args, "max_spend", None) is not None:
        os.environ["WBG_SPEND_CAP_USD"] = f"{args.max_spend:.4f}"
        os.environ["WBG_SPEND_SINCE"] = datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sets = args.measures or [n for n in labels_mod.label_sets() if n != Q.ASSETS]
    projects = sorted(_sel(args).ids) if _scope(args) else None
    dry = ["--dry-run"] if args.dry_run else []
    key = "" if args.dry_run else api.key()
    kw = dict(api_key=key, dry_run=args.dry_run, workers=args.workers, projects=projects)
    conn = corpus = None
    timings = []
    for stage in stages:
        t0 = time.time()
        print(f"#### {stage}", flush=True)
        ok = True
        if stage == "fetch":
            prepare(_sel(args), update=args.update, data=data, fetch=True,
                    refresh_projects=args.refresh_projects)
        elif stage == "prepare":
            prepare(_sel(args), data=data, fetch=False)
        elif stage == "embed":
            _run("embed.py", *dry, "--context", "none", data=data)
        elif stage == "search":
            ok = not _parallel(args.jobs, [Q.ASSETS, *sets], lambda n: [["search.py", n, *dry]], data)
        elif stage == "audit":
            _run("audit.py", data=data, tail=2)
        else:
            if conn is None:
                conn = db.connect()
                db.ensure(conn)
                corpus = Q.Corpus(data)
            import checks
            import decide
            import funnel
            if stage == "classify":
                ok = funnel.run(conn, data, Q.ASSETS, corpus=corpus, **kw) is not None or args.dry_run
            elif stage == "measure":
                for name in sets:
                    if funnel.run(conn, data, name, corpus=corpus, **kw) is None and not args.dry_run:
                        ok = False
                        continue
                    checks.propose(conn, data, corpus, name, api_key=key, dry_run=args.dry_run)
                    if checks.types(conn, data, corpus, name, **{k: v for k, v in kw.items()
                                                                  if k != "projects"}) is None \
                            and not args.dry_run:
                        ok = False
                if ok:
                    ok = checks.pairs(conn, data, corpus, sets, **{k: v for k, v in kw.items()
                                                                     if k != "projects"}) is not None \
                        or args.dry_run
            elif stage == "link":
                ok = checks.links(conn, data, corpus, sets, **kw) is not None or args.dry_run
            elif stage == "publish" and not args.dry_run:
                if projects and os.environ.get("WBG_PUBLISH_SCOPED") != "1":
                    print("  postgres: not written: a run over named projects would replace the "
                          "cohort's rows (set WBG_PUBLISH_SCOPED=1 for a scratch database)")
                else:
                    decide.publish(conn, data, corpus=corpus)
        timings.append((stage, time.time() - t0))
        if not ok:
            print(f"run: {stage} failed; stopped. Run again to resume.")
            return 1
    print("run: " + ", ".join(f"{s} {t:.0f}s" for s, t in timings)
          + f"; everything {time.time() - t_all:.0f}s", flush=True)
    return 0


def check(args, data):
    import db
    import grading
    import questions as Q
    conn = db.connect()
    db.ensure(conn)
    if args.what == "import":
        return 0 if grading.import_legacy(conn, data) >= 0 else 1
    if args.what == "fill":
        grading.fill(conn, data, args.seed, args.file, grader=args.grader or "")
        return 0
    if args.what == "spotcheck":
        out = args.out or where(data, "review", f"spotcheck_s{args.seed}.xlsx")
        print(f"wrote {grading.spotcheck(data, args.seed, args.file, out)}")
        return 0
    corpus = Q.Corpus(data)
    if args.what == "draw":
        grading.draw(conn, data, corpus, args.seed, design=args.design, dry_run=args.dry_run)
    else:
        grading.evaluate(conn, data, corpus, grid=args.grid)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wbg", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=None, help="data folder (default: the main checkout's data/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in (("list", "show the projects a selection covers"),
                       ("prepare", "fetch what is missing and rebuild what changed"),
                       ("summary", "where each selected project stands")):
        p = sub.add_parser(name, help=text)
        _filters(p)
        if name == "prepare":
            p.add_argument("--update", action="store_true", help="fetch the selected projects again")
    p = sub.add_parser("run", help="the pipeline, or any stretch of it")
    p.add_argument("--from", dest="from_stage", choices=RUN_STAGES, default=None)
    p.add_argument("--to", dest="to_stage", choices=RUN_STAGES, default=None)
    p.add_argument("--only", choices=RUN_STAGES, default=None)
    p.add_argument("--measures", nargs="*", default=None, help="the sets (default: every set)")
    p.add_argument("--jobs", type=int, default=3, help="search sets side by side")
    p.add_argument("--workers", type=int, default=10, help="model requests in flight")
    p.add_argument("--update", action="store_true", help="fetch the selected projects again")
    p.add_argument("--refresh-projects", action="store_true", help="fetch every project record again")
    p.add_argument("--dry-run", action="store_true", help="estimates only; nothing asked or written")
    p.add_argument("--max-spend", type=float, default=None, help="US$ the run may spend on models")
    _filters(p)
    p = sub.add_parser("add", help="a measure from its files to Postgres (run --from search --to measure)")
    p.add_argument("label_set", nargs="+")
    p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-spend", type=float, default=None)
    _filters(p)
    sub.add_parser("publish", help="every decision read again from the scores, then the views")
    p = sub.add_parser("check", help="the grading cycle (src/grading.py)")
    p.add_argument("what", choices=("draw", "fill", "evaluate", "spotcheck", "import"))
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--design", choices=("train", "eval", "elusion"), default="train")
    p.add_argument("--file", help="fill: the JSONL of answers; spotcheck: the fills to check")
    p.add_argument("--grader", help="fill: who or what graded (recorded on every grade)")
    p.add_argument("--out", help="spotcheck: the workbook to write")
    p.add_argument("--grid", action="store_true", help="evaluate: also rank the layer settings (slow)")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("requests", help="run the measures asked for from the dashboard")
    p.add_argument("--once", action="store_true")
    p.add_argument("--schema", action="store_true")
    sub.add_parser("audit", help="the parse and procurement audits (reports in data/reports/)")
    p = sub.add_parser("sensitivity", help="how much the cleaned text depends on its settings")
    p.add_argument("--docs", type=int, default=10)
    sub.add_parser("status", help="which code and which data release the store is at")
    sub.add_parser("release", help="record the store's current data as a release")
    args = ap.parse_args(argv)
    data = args.data or data_root()
    if args.cmd == "run":
        return run(args, data)
    if args.cmd == "add":
        args.from_stage, args.to_stage, args.only = "search", "measure", None
        args.measures, args.update, args.refresh_projects = args.label_set, False, False
        return run(args, data)
    if args.cmd == "publish":
        import db
        import decide
        with db.connect() as conn:
            db.ensure(conn)
            decide.publish(conn, data)
        return 0
    if args.cmd == "check":
        return check(args, data)
    if args.cmd == "status":
        import release
        print("\n".join(release.status(data)))
        return 0
    if args.cmd == "release":
        _run("release.py", data=data)
        return 0
    if args.cmd == "requests":
        _run("measure_requests.py", *(["--once"] if args.once else []),
             *(["--schema"] if args.schema else []), data=data)
        return 0
    if args.cmd == "audit":
        _run("audit.py", data=data, tail=2)
        return 0
    if args.cmd == "sensitivity":
        _run("sensitivity.py", "--docs", str(args.docs), data=data)
        return 0
    sel = _sel(args)
    if args.cmd == "list":
        _print_rows(sel.rows, ["project_id", "country_code", "region", "approval_fy", "status",
                               "closing_date", "practice", "unit_code"])
        print(f"\n{len(sel)} projects")
    elif args.cmd == "prepare":
        prepare(sel, update=args.update, data=data)
    elif args.cmd == "summary":
        _print_rows(summary(sel, data), ["project_id", "country_code", "status", "closing_date",
                                         "latest_plan_date", "packages", "upcoming_packages",
                                         "upcoming_not_in_latest_plan", "upcoming_estimated_usd",
                                         "upcoming_planned_date_passed", "next_planned_date",
                                         "open_notices", "contracts_signed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
