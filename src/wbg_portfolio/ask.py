"""The one asker. Every decision puts its questions through ask(): what is
stored is read back, what is not is asked, batched, and stored under the
exact text and question sent (never under a run or a position).

  ask(conn, model, items, stage=..., label_set=...) -> ({item: {qid: answer}}, stats)

items = [(item, state, {qid: question})]. The store is Postgres
(db.STORE.answers, keyed on model, state_sha, question_sha). An answer the
old file store holds (data/classify/answers/<model>/<aa>/<sha>.json, keyed
sha256({model, state, question})) is read from there once and copied into
Postgres, so nothing already paid for is asked again.

Each answer is committed as it is stored, so nothing paid for is lost to a crash. Items with the same state and
question are asked once. A response with an incomplete answer keeps its complete ones (the cost is recorded once);
a request the provider refuses is split into single items, and an item refused alone is recorded in
<store>.refused and skipped (api.Refused: a bad key, no credit or a run with no answer at all still stop it).

Batches: items sharing a question set go together, up to ask.batch_items a
request and ask.batch_max_chars characters, ordered by the hash of their
state, so which items share a request depends on the items alone and not on
the order runs were made in (issue #80).
"""
import glob
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from wbg_portfolio import api
from wbg_portfolio import common
from wbg_portfolio import db
from wbg_portfolio import panel
from wbg_portfolio import questions as Q
from wbg_portfolio.paths import where

BATCH = int(os.environ.get("WBG_ASK_BATCH") or panel.require("ask.batch_items"))
BATCH_MAX_CHARS = int(panel.require("ask.batch_max_chars"))
MAX_REQUEST_CHARS = int(panel.require("ask.max_request_chars"))
WORKERS = 10
REFUSED = []                    # (stage, item, qid) the provider refused and the run skipped, this process
HOW = ("Items i01, i02, ... are each one record. Field 'q <id>' states a question in full. "
       "Each short question '<id> on i..' asks question <id> about that item alone, reading "
       "any field the question names ('sentence', 'paragraph', 'package', 'commitment') as "
       "that line of the item. The other items are separate.")


# ----------------------------------------------------------------- the store

def stored(conn, model, keys):
    """{(state_sha, question_sha): answer row} for the keys the store holds."""
    if not keys:
        return {}
    db.temp_keys(conn, "ask_keys", ["state_sha", "question_sha"], keys)
    got = db.rows(conn, f"SELECT a.state_sha, a.question_sha, a.answer, a.served FROM {db.STORE}.answers a "
                  f"JOIN ask_keys k USING (state_sha, question_sha) WHERE a.model = %s", (model,))
    return {(s, q): dict(a, served=m) for s, q, a, m in got}


def refused_stored(conn, model, keys):
    """{(state_sha, question_sha): reason} for the keys the provider refused earlier (and the run skipped)."""
    if not keys:
        return {}
    db.temp_keys(conn, "refused_keys", ["state_sha", "question_sha"], keys)
    return {(s, q): r for s, q, r in db.rows(
        conn, f"SELECT a.state_sha, a.question_sha, a.reason FROM {db.STORE}.refused a "
              f"JOIN refused_keys k USING (state_sha, question_sha) WHERE a.model = %s", (model,))}


def record_refused(conn, model, entries, states, qs, reason=""):
    """Remember what the provider refused alone (entries [(state_sha, question_sha)]), so a rerun does not
    ask it again and the report can list it. Delete its row from <store>.refused to ask it again."""
    with conn.transaction():
        for s_sha, q_sha in entries:
            conn.execute(f"INSERT INTO {db.STORE}.refused (model, state_sha, question_sha, reason) "
                         f"VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (model, s_sha, q_sha, reason[:300]))
        _insert_context(conn, states, qs)
    conn.commit()


def _legacy(data, model, state, question, aliases):
    """An answer from the old file store, under this model or an alias that
    the same snapshot served; None without one."""
    for m in (model, *aliases):
        slug = m.replace("/", "__").replace(":", "_")
        k = Q.sha({"model": m, "state": state, "question": question})
        path = where(data, "classify", "answers", slug, k[:2], k + ".json")
        try:
            with open(path, encoding="utf-8") as fh:
                e = json.load(fh)
        except (OSError, ValueError):
            continue
        if m == model or e.get("model") == model:
            return e
    return None


def _insert_context(conn, states, qs):
    """The states and questions an answer is about, once each."""
    for s_sha, state in states.items():
        conn.execute(f"INSERT INTO {db.STORE}.states (sha, state) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (s_sha, json.dumps(state, ensure_ascii=False)))
    for q_sha, q in qs.items():
        conn.execute(f"INSERT INTO {db.STORE}.questions (sha, question) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (q_sha, json.dumps(q, ensure_ascii=False)))


def _store(conn, model, rows, states, qs):
    """Write answers (and the states and questions they are about) once, and commit them at
    once: an answer is paid for, so a crash, a kill or the spend cap right after it loses nothing."""
    with conn.transaction():
        for s_sha, q_sha, answer, served, cost, call, at in rows:
            conn.execute(f"INSERT INTO {db.STORE}.answers (model, state_sha, question_sha, answer, "
                         f"served, cost_usd, call_sha, asked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                         f"ON CONFLICT DO NOTHING",
                         (model, s_sha, q_sha, json.dumps(answer, ensure_ascii=False), served, cost,
                          call, at))
        _insert_context(conn, states, qs)
    conn.commit()


def run_requests(conn, model, jobs, one, stats, *, stage, label_set, api_key, workers, log, tail=lambda: "",
                 lock=None):
    """The part every asker shares: check the cap against stats["estimate_usd"], run one(*job) for
    each job on a pool, count the requests that failed (api.Transport) in stats["failures"], stop
    at the cap, log a steady "n of total" line (common.Progress), and keep the ledger row (api.Spend)
    up to date as each request is paid. Sets stats calls, cost_usd and seconds. one(*job) returns
    what the request cost; `lock` is the lock one() holds while it writes to conn.

    A refusal (api.Refused) stops the run when it is about the whole request (a bad key, no credit, or
    a refusal that does not say which item it is about). A refusal about one item (exc.items) is
    recorded in stats["refused"] and skipped, but only when the run has had at least one answer from
    the model, which shows the key and the model work; with none (three such refusals so far, or the
    end of the run) the run stops with the refusal."""
    if not api_key:
        raise SystemExit(f"{stage}: no credential: set ${api.KEY_VAR}")
    t0 = time.time()
    progress = common.Progress(f"  {stage}: requests", len(jobs), log)
    with api.Spend(conn, stage, label_set, model, lock=lock) as spend:
        spend.check(stats["estimate_usd"], stats.get("estimate_basis", ""))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, *job) for job in jobs]
            counted = set()

            def stop():
                """No new request; the ones already in flight finish, are stored, and their cost
                reaches the ledger (recorded, not checked again: the cap already stops the step)."""
                pool.shutdown(wait=True, cancel_futures=True)
                for g in futures:
                    if g in counted or g.cancelled() or not g.done() or g.exception() is not None:
                        continue
                    counted.add(g)
                    spend.add(g.result(), check=False)
            skipped = []
            for n, f in enumerate(as_completed(futures), 1):
                counted.add(f)
                try:
                    spend.add(f.result())
                except api.Transport as exc:
                    stats["failures"].append(str(exc)[:200])
                except api.Refused as exc:
                    if not exc.fatal:
                        skipped.append(exc)
                        stats.setdefault("refused", []).extend(exc.items)
                        log(f"  refused, skipped: {str(exc)[:200]}")
                    if exc.fatal or (spend.calls == 0 and len(skipped) >= 3):
                        stop()
                        raise
                except api.CapReached:
                    stop()
                    raise
                progress.step(f"{len(stats['failures'])} failed{tail()}, US${spend.cost:.3f}")
        if skipped and not spend.calls:
            raise skipped[0]
        stats["calls"], stats["cost_usd"] = spend.calls, spend.cost
    stats["seconds"] = time.time() - t0


# ----------------------------------------------------------------- batches

def _spec(q):
    opts = [f"{i}: {t}" for i, t in enumerate(q["criteria"])]
    return q["instructions"] + "\nAnswers:\n" + "\n".join(opts)


def _item_question(q, qid, field):
    # The shortest stub the endpoint accepts: "type" is required, and a blank
    # instruction makes the model answer at random (tested 2026-10-24).
    return {"type": "score", "instructions": f"{qid} on {field}",
            "criteria": [str(i) for i in range(len(q["criteria"]))]}


def _item_text(state):
    return "\n".join(f"{k}: {v}" for k, v in state.items())


def body(model, state, qs):
    return {"model": model, "state": state, "questions": qs}


def batch_body(model, states, qs):
    """(body, [(item index, qid, batch qid)]) asking every question of each state."""
    shared = {"how": HOW, **{f"q {qid}": _spec(q) for qid, q in qs.items()}}
    bqs, members = {}, []
    for i, s in enumerate(states, 1):
        f = f"i{i:02d}"
        shared[f] = _item_text(s)
        for qid, q in qs.items():
            bqs[f"{f} {qid}"] = _item_question(q, qid, f)
            members.append((i - 1, qid, f"{f} {qid}"))
    return body(model, shared, bqs), members


def _size(b):
    return len(json.dumps(b, ensure_ascii=False))


def batches(model, items, size):
    """[(body, [(item index, qid, batch qid)])] for items [(state, qs)] that
    share one question set: as many a request as fit under BATCH_MAX_CHARS,
    at most `size`, in order. size 0 or 1: one item a request, its
    questions split over several requests when they pass MAX_REQUEST_CHARS."""
    out, k = [], 0
    if size <= 1:
        for i, (state, qs) in enumerate(items):
            for b in single(model, state, qs):
                out.append((b, [(i, q, q) for q in b["questions"]]))
        return out
    while k < len(items):
        qs = items[k][1]
        n = min(size, len(items) - k)
        while True:
            chunk = items[k:k + n]
            b, members = batch_body(model, [s for s, _ in chunk], qs)
            if n == 1 or _size(b) <= BATCH_MAX_CHARS:
                break
            n = max(1, n // 2)
        if n == 1 and _size(b) > BATCH_MAX_CHARS:
            out.extend((sb, [(k, q, q) for q in sb["questions"]]) for sb in single(model, *chunk[0]))
        else:
            out.append((b, [(k + i, qid, bq) for i, qid, bq in members]))
        k += n
    return out


def single(model, state, qs):
    """One item's questions in as few requests as fit under MAX_REQUEST_CHARS."""
    if _size(body(model, state, qs)) <= MAX_REQUEST_CHARS:
        return [body(model, state, qs)]
    out, batch = [], {}
    for qid, q in qs.items():
        if batch and _size(body(model, state, {**batch, qid: q})) > MAX_REQUEST_CHARS:
            out.append(body(model, state, batch))
            batch = {}
        batch[qid] = q
        if _size(body(model, state, batch)) > MAX_REQUEST_CHARS:
            raise SystemExit(f"ask: a state with one question is over {MAX_REQUEST_CHARS:,} "
                             f"characters; not truncated")
    out.append(body(model, state, batch))
    return out


def complete(answer, question):
    probs = (answer or {}).get("probabilities") or {}
    return isinstance(answer, dict) and answer.get("type") == "score" and \
        sorted(probs) == [str(i) for i in range(len(question["criteria"]))]


def split(response, b):
    """({batch qid: answer} for the complete answers, [batch qid] for the others) from one response;
    api.Transport when the response is not an object at all."""
    if not isinstance(response, dict):
        raise api.Transport(f"response is not an object: {str(response)[:200]}")
    answers = response.get("answers") or {}
    ok = {qid: answers[qid] for qid, q in b["questions"].items() if complete(answers.get(qid), q)}
    return ok, [qid for qid in b["questions"] if qid not in ok]


# ----------------------------------------------------------------- ask

def ask(conn, model, items, *, stage, label_set="", api_key="", dry_run=False, workers=WORKERS,
        log=print, data=None, aliases=Q.ALIASES, batch=None):
    """Answers for every item: {item: {qid: answer}} (an item with a missing
    answer is absent) and stats {stored, to_ask, estimate_usd, calls,
    cost_usd, failures, seconds}. Asks only what the store lacks; with
    dry_run nothing is asked and the answers returned are the stored ones."""
    batch = BATCH if batch is None else batch
    keyed = []                                  # (item, state, s_sha, {qid: (q, q_sha)})
    qsha = {}
    for item, state, qs in items:
        s_sha = Q.sha(state)
        keyed.append((item, state, s_sha, {qid: (q, qsha.setdefault(id(q), Q.sha(q)))
                                           for qid, q in qs.items()}))
    have = stored(conn, model, sorted({(s, q) for _, _, s, qs in keyed for _, q in qs.values()}))
    missing, imported = {}, []                  # missing: {s_sha: (state, {qid: question})}, once per state
    known = refused_stored(conn, model, sorted({(s, q) for _, _, s, qs in keyed for _, q in qs.values()
                                                if (s, q) not in have}))
    prior = set()                               # (s_sha, qid) the provider refused in an earlier run
    for item, state, s_sha, qs in keyed:
        for qid, (q, q_sha) in qs.items():
            if (s_sha, q_sha) in have:
                continue
            if (s_sha, q_sha) in known:
                prior.add((s_sha, qid))
                continue
            old = _legacy(data, model, state, q, aliases) if data else None
            if old:
                have[(s_sha, q_sha)] = dict(old["answer"], served=old.get("model", model))
                imported.append((s_sha, q_sha, old["answer"], old.get("model", model),
                                 old.get("cost_usd"), old.get("call"), old.get("asked_at")))
                continue
            missing.setdefault(s_sha, (state, {}))[1][qid] = q
    if imported:
        _store(conn, model, imported, {s: st for _, st, s, _ in keyed},
               {q_sha: q for _, _, _, qs in keyed for q, q_sha in qs.values()})
    # batches: states sharing a question set, ordered by the hash of the state. Two items with the
    # same state and question are one question (the answer is read back by key).
    groups = {}
    for s_sha, (state, qs) in sorted(missing.items()):
        groups.setdefault(Q.sha(sorted(Q.sha(q) for q in qs.values())), []).append((s_sha, state, qs))
    todo = []
    for _, members in sorted(groups.items()):
        for b, plan in batches(model, [(st, qs) for _, st, qs in members], batch):
            todo.append((b, [(members[i][0], members[i][2][qid], qid, bq) for i, qid, bq in plan]))
    chars = sum(_size(b) for b, _ in todo)
    stats = {"stored": len(have), "imported": len(imported), "to_ask": len(todo), "chars": chars,
             "estimate_usd": chars / 4 / 1e6 * Q.PRICE_PER_MTOK, "calls": 0, "cost_usd": 0.0,
             "failures": [], "refused": [], "seconds": 0.0}
    skipped_now = []
    if todo and not dry_run:
        lock = Lock()

        def send(b, plan):
            """One request. Its complete answers are stored and committed at once (and its cost counts
            once) whatever the rest of the response holds; an incomplete answer is a failure of that
            question alone, asked again next run."""
            response = api.post("/alpha/decisions", b, api_key)
            answers, bad = split(response, b)
            cost = float((response.get("usage") or {}).get("cost") or 0)
            served = response.get("model", "")
            at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            call = Q.sha(b)
            share = cost / max(len(plan), 1)
            rows, states, qs_ = [], {}, {}
            for s_sha, q, qid, bq in plan:
                if bq in answers:
                    q_sha = Q.sha(q)
                    rows.append((s_sha, q_sha, answers[bq], served, share, call, at))
                    states[s_sha] = missing[s_sha][0]
                    qs_[q_sha] = q
            with lock:
                if rows:
                    _store(conn, model, rows, states, qs_)
                    for s_sha, q_sha, a, *_ in rows:
                        have[(s_sha, q_sha)] = dict(a, served=served)
                if bad:
                    stats["failures"].append(f"{len(bad)} answer(s) of {len(plan)} incomplete: "
                                             f"{bad[0]!r} {str(response.get('answers', {}).get(bad[0]))[:120]}")
            return cost

        def one(b, plan):
            try:
                return send(b, plan)
            except api.Refused as exc:
                states_ = list(dict.fromkeys(p[0] for p in plan))
                if exc.hard:
                    raise
                if len(states_) < 2:                 # about this one state: the run decides whether to skip it
                    exc.items = [(p[0], p[2]) for p in plan]
                    raise
                return send_each(plan, exc)

        def send_each(plan, exc):
            """A batch was refused: ask each state alone. The ones the provider takes are stored; one it
            refuses alone is recorded as refused and skipped (when every one is refused the batch's refusal
            stands, and the run decides)."""
            cost, taken, refused, last = 0.0, 0, [], exc
            for s_sha in dict.fromkeys(p[0] for p in plan):
                mine = [p for p in plan if p[0] == s_sha]
                for sb in single(model, missing[s_sha][0], {p[2]: p[1] for p in mine}):
                    sub = [(sk, q, qid, qid) for sk, q, qid, _ in mine if qid in sb["questions"]]
                    try:
                        cost += send(sb, sub)
                        taken += 1
                    except api.Refused as e:
                        if e.hard:
                            raise
                        refused += [(p[0], p[2]) for p in sub]
                        last = e
            if not taken:
                last.items = refused
                raise last
            with lock:
                stats["refused"].extend(refused)
            log(f"  {len(refused)} question(s) refused, skipped: {str(last)[:160]}")
            return cost

        run_requests(conn, model, todo, one, stats, stage=stage, label_set=label_set, api_key=api_key,
                     workers=workers, log=log, lock=lock)
        # the run had answers from this model, so what was refused was refused for itself: remember it
        skipped_now = sorted(set(stats["refused"]))
        if skipped_now:
            record_refused(conn, model, [(s, Q.sha(missing[s][1][qid])) for s, qid in skipped_now],
                           {s: missing[s][0] for s, _ in skipped_now},
                           {Q.sha(missing[s][1][qid]): missing[s][1][qid] for s, qid in skipped_now},
                           "refused alone by the provider")
    stats["refused"] = sorted(set(stats["refused"]) | prior)
    by_state = {}
    for item, _, s_sha, qs in keyed:
        for qid in qs:
            by_state.setdefault((s_sha, qid), []).append(item)
    stats["refused"] = sorted({(item, qid) for k in stats["refused"] for item in by_state.get(k, ())},
                              key=str)
    REFUSED.extend((stage, item, qid) for item, qid in stats["refused"])
    out = {}
    for item, state, s_sha, qs in keyed:
        got = {qid: have.get((s_sha, q_sha)) for qid, (q, q_sha) in qs.items()}
        if all(v is not None for v in got.values()):
            out[item] = got
    return out, stats


def refused_report(log=print):
    """What the provider refused over this process, one line a stage; nothing when nothing was."""
    by = {}
    for stage, item, _ in REFUSED:
        by.setdefault(stage, set()).add(str(item))
    for stage, items_ in sorted(by.items()):
        log(f"refused by the provider and skipped, {stage}: {len(items_):,} item(s), "
            f"e.g. {', '.join(sorted(items_)[:5])}")
    return bool(by)


def refused_items(stats):
    """The items (not their questions) a run was refused for and skipped."""
    return {item for item, _ in stats.get("refused", ())}


def estimate_line(stage, stats):
    return (f"  {stage}: {stats['stored']:,} answers stored"
            + (f" ({stats['imported']:,} read from the old file store)" if stats.get("imported") else "")
            + f"; {stats['to_ask']:,} requests to make (about US${stats['estimate_usd']:.3f})"
            + (f"; {len(refused_items(stats)):,} item(s) refused by the provider and skipped"
               if stats.get("refused") else ""))
