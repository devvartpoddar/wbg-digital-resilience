"""A scratch measure: a question tried against the corpus in minutes, for a
few cents, without touching the label files or the dashboards' tables.

  ./wbg_portfolio try e_waste --definition "..." --phrase "..." --phrase "..." [--pattern '\\be-waste\\b']
  ./wbg_portfolio try e_waste --expand "e-waste from ICT equipment"      # one cheap generative call
                                                              # writes the definition, phrases
                                                              # and patterns for you to read
  ./wbg_portfolio try e_waste --max-spend 0.50                            # the funnel, capped
  ./wbg_portfolio try --list                                              # what has been tried

The files go to data/scratch/<slug>*.csv (the shape of analysis/labels/, outside
git). The search and the funnel are the measures' own (search.py, funnel.py),
so the sentences are embedded once, gated, pattern-matched, asked with the
graded question and scored; every answer is stored and reused. The scores land
in wbg.scores with kind "scratch", which the publish never reads: the
dashboards' commitments do not move. wbg.scratch_sets records the wording;
summary() prints what was found.

Graduation is a person's act: copy the three files into analysis/labels/ (a
pull request) and run ./wbg_portfolio add <slug>; the stored answers are reused.
"""
import csv
import json
import os
import re
import sys
import time

from wbg_portfolio import api
from wbg_portfolio import db
from wbg_portfolio import labels
from wbg_portfolio import questions as Q
from wbg_portfolio.paths import where

GEN_MODEL = "anthropic/claude-haiku-5.5"
EXPAND_PROMPT = """You are helping an analyst search World Bank project appraisal documents for one kind of commitment.

The analyst wants to find: {ask}

Write what the search needs. Rules:
- "name": at most six words, the thing itself (not "projects that...").
- "definition": 60 to 100 words, plain language, one paragraph: what counts as a commitment to it, what does not (name two close neighbours that are not it). Name the practice, never a particular technology brand or country.
- "phrases": 8 to 10 sentences a project document might actually print when it commits to this, varied in wording and formality, each 8 to 20 words.
- "patterns": 1 to 3 whole-word regular expressions (\\b at the edges, (?:x)? for optional parts) that match the plainest wording and little else; none that would match an unrelated common word.

Answer with JSON only, no prose: {{"name": "...", "definition": "...", "phrases": ["..."], "patterns": ["..."]}}
"""


def slug_of(text):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not s or s in ("assets", "scale"):
        raise SystemExit(f"try: {text!r} is not a usable name")
    return s[:40]


def expand(conn, ask, api_key, dry_run=False):
    """{name, definition, phrases, patterns} from one generative call, stored
    like any answer (the same ask gives the same files)."""
    from wbg_portfolio import checks
    body = {"model": GEN_MODEL, "temperature": 0,
            "messages": [{"role": "user", "content": EXPAND_PROMPT.format(ask=ask)}]}
    text = checks.generate(conn, body, api_key, dry_run)
    if text is None:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not d or not all(k in d for k in ("name", "definition", "phrases")):
        raise api.Transport(f"expansion is not the expected JSON: {text[:200]}")
    pats = []
    for pt in d.get("patterns") or []:
        try:
            re.compile(pt)
            pats.append(pt)
        except re.error:
            pass
    return {"name": " ".join(str(d["name"]).split()), "definition": " ".join(str(d["definition"]).split()),
            "phrases": [" ".join(str(x).split()) for x in d["phrases"] if str(x).strip()], "patterns": pats}


def write_files(data, slug, name, definition, phrases, patterns):
    """The three files of a set under data/scratch/, replacing earlier ones."""
    d = where(data, "scratch")
    os.makedirs(d, exist_ok=True)

    def put(path, cols, rows):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows(rows)
    put(os.path.join(d, f"{slug}.csv"), ["measure_id", "measure_name", "definition", "theme", "active", "version"],
        [[slug, name, definition, "scratch", "true", "1"]])
    put(os.path.join(d, f"{slug}_queries.csv"), ["measure_id", "query_id", "query", "active"],
        [[slug, f"q{i:02d}", ph, "true"] for i, ph in enumerate(phrases, 1)])
    ppath = os.path.join(d, f"{slug}_patterns.csv")
    if patterns:
        put(ppath, ["measure_id", "pattern_id", "pattern", "active"],
            [[slug, f"p{i:02d}", pt, "true"] for i, pt in enumerate(patterns, 1)])
    elif os.path.exists(ppath):
        os.remove(ppath)
    return d


def record(conn, slug, name, definition, phrases, patterns, asked_by, note=""):
    from wbg_portfolio import panel
    db.scores_table(conn)
    conn.execute(f"""INSERT INTO {db.SCHEMA}.scratch_sets
        (label_set, label_name, definition, phrases, patterns, asked_by, written_at, floor, note)
        VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s)
        ON CONFLICT (label_set) DO UPDATE SET label_name = EXCLUDED.label_name,
          definition = EXCLUDED.definition, phrases = EXCLUDED.phrases, patterns = EXCLUDED.patterns,
          asked_by = EXCLUDED.asked_by, written_at = now(), floor = EXCLUDED.floor, note = EXCLUDED.note""",
                 (slug, name, definition, phrases, patterns, asked_by, float(panel.require("floor.default")), note))
    conn.commit()


def listing(conn):
    db.scores_table(conn)
    return db.rows(conn, f"""SELECT s.label_set, s.label_name, s.written_at::date, s.asked_by,
        (SELECT count(*) FROM {db.SCHEMA}.scores c WHERE c.kind = 'scratch' AND c.label_set = s.label_set),
        (SELECT count(*) FROM {db.SCHEMA}.scores c WHERE c.kind = 'scratch' AND c.label_set = s.label_set
           AND c.score >= s.floor)
        FROM {db.SCHEMA}.scratch_sets s ORDER BY s.written_at DESC""")


def summary(conn, corpus, slug, floor, top=12):
    """Lines: committed sentences by project, then the strongest sentences."""
    rows = db.rows(conn, f"SELECT item_id, score, route FROM {db.SCHEMA}.scores WHERE kind = 'scratch' "
                         f"AND label_set = %s ORDER BY score DESC", (slug,))
    yes = [(u, float(s), r) for u, s, r in rows if float(s) >= floor]
    by_project = {}
    for u, s, r in yes:
        for pid in corpus.projects(u):
            by_project[pid] = by_project.get(pid, 0) + 1
    lines = [f"{slug}: {len(rows):,} sentences asked, {len(yes):,} at or above {floor:.2f} "
             f"in {len(by_project)} projects"]
    for pid, n in sorted(by_project.items(), key=lambda x: -x[1])[:top]:
        lines.append(f"  {pid}  {n}")
    if len(by_project) > top:
        lines.append(f"  ... and {len(by_project) - top} more projects")
    for u, s, r in yes[:top]:
        text = " ".join(corpus.texts.get(u, "").split())
        lines.append(f"  {s:.2f} {','.join(corpus.projects(u))} ({r}) {text[:160]}")
    return lines


def run(args, data):
    """The ./wbg_portfolio try command."""
    from wbg_portfolio import funnel
    from wbg_portfolio import search
    conn = db.connect()
    db.ensure(conn)
    if args.list:
        for r in listing(conn):
            print(f"{r[0]:<30} {r[1] or '':<40} {r[2]} {r[3] or '':<10} asked {r[4]:,} yes {r[5]:,}")
        return 0
    if not args.slug:
        raise SystemExit("try: a name is needed (./wbg_portfolio try e_waste ...), or --list")
    slug = slug_of(args.slug)
    key = "" if args.dry_run else api.key()
    if args.max_spend is not None:
        os.environ["WBG_SPEND_CAP_USD"] = f"{args.max_spend:.4f}"
        os.environ["WBG_SPEND_SINCE"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    name, definition, phrases, patterns = args.name or slug.replace("_", " "), args.definition, list(args.phrase or []), list(args.pattern or [])
    if args.expand:
        got = expand(conn, args.expand, key, dry_run=args.dry_run)
        if got is None:
            print("try: --dry-run: the expansion would make one generative call; nothing written")
            return 0
        name, definition = args.name or got["name"], definition or got["definition"]
        phrases, patterns = phrases or got["phrases"], patterns or got["patterns"]
        print(f"expanded: {name}\n  {definition}\n  phrases: " + " | ".join(phrases)
              + (f"\n  patterns: {' '.join(patterns)}" if patterns else ""), flush=True)
    exists = labels.is_scratch(slug)
    if not definition and not exists:
        raise SystemExit("try: give --definition (with --phrase ...) or --expand \"what to look for\"")
    if definition:
        if not phrases:
            raise SystemExit("try: a definition needs at least one --phrase (or use --expand)")
        if labels.is_scratch(slug) is False and any(os.path.exists(os.path.join(d, f"{slug}.csv"))
                                                      for d in labels.LABEL_DIRS):
            raise SystemExit(f"try: {slug} is a real label set; scratch names must differ")
        write_files(data, slug, name, definition, phrases, patterns)
        record(conn, slug, name, definition, phrases, patterns, os.environ.get("USER", ""), args.note or "")
    from wbg_portfolio import panel
    fl = float(panel.require("floor.default"))
    if search.search(slug, data, api_key=key, dry_run=args.dry_run, log=print) is None and not args.dry_run:
        return 1
    corpus = Q.Corpus(data)
    projects = sorted(args.projects) if args.projects else None
    out = funnel.run(conn, data, slug, corpus=corpus, api_key=key, dry_run=args.dry_run,
                     workers=args.workers, log=print, projects=projects)
    if out is None and not args.dry_run:
        print("try: a request failed; run again to resume")
        return 1
    if not args.dry_run:
        for line in summary(conn, corpus, slug, fl):
            print(line)
    return 0
