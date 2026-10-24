"""The one asker. Every decision puts its questions through ask(): what is
stored is read back, what is not is asked, batched, and stored under the
exact text and question sent (never under a run or a position).

  ask(conn, model, items, stage=..., label_set=...) -> ({item: {qid: answer}}, stats)

items = [(item, state, {qid: question})]. The store is Postgres
(db.STORE.answers, keyed on model, state_sha, question_sha). An answer the
old file store holds (data/classify/answers/<model>/<aa>/<sha>.json, keyed
sha256({model, state, question})) is read from there once and copied into
Postgres, so nothing already paid for is asked again.

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

import api
import db
import panel
import questions as Q
from paths import where

BATCH = int(os.environ.get("WBG_ASK_BATCH") or panel.get("ask.batch_items", 50))
BATCH_MAX_CHARS = int(panel.get("ask.batch_max_chars", 80_000))
MAX_REQUEST_CHARS = int(panel.get("ask.max_request_chars", 30_000))
WORKERS = 10
HOW = ("Several items follow, each in its own field ('item 01', 'item 02', ...), and each "
       "question is set out in full in its own field ('question <id>'). Each short question "
       "below asks one of those questions about one item: answer it for that item alone, "
       "reading any field the question names (such as 'sentence', 'paragraph', 'package' or "
       "'commitment') as that line of the item. The other items are separate.")


# ----------------------------------------------------------------- the store

def stored(conn, model, keys):
    """{(state_sha, question_sha): answer row} for the keys the store holds."""
    if not keys:
        return {}
    db.temp_keys(conn, "ask_keys", ["state_sha", "question_sha"], keys)
    got = db.rows(conn, f"SELECT a.state_sha, a.question_sha, a.answer, a.served FROM {db.STORE}.answers a "
                  f"JOIN ask_keys k USING (state_sha, question_sha) WHERE a.model = %s", (model,))
    return {(s, q): dict(a, served=m) for s, q, a, m in got}


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


def _store(conn, model, rows, states, qs):
    """Write answers (and the states and questions they are about) once."""
    with conn.transaction():
        for s_sha, q_sha, answer, served, cost, call, at in rows:
            conn.execute(f"INSERT INTO {db.STORE}.answers (model, state_sha, question_sha, answer, "
                         f"served, cost_usd, call_sha, asked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                         f"ON CONFLICT DO NOTHING",
                         (model, s_sha, q_sha, json.dumps(answer, ensure_ascii=False), served, cost,
                          call, at))
        for s_sha, state in states.items():
            conn.execute(f"INSERT INTO {db.STORE}.states (sha, state) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (s_sha, json.dumps(state, ensure_ascii=False)))
        for q_sha, q in qs.items():
            conn.execute(f"INSERT INTO {db.STORE}.questions (sha, question) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (q_sha, json.dumps(q, ensure_ascii=False)))


# ----------------------------------------------------------------- batches

def _spec(q):
    opts = [f"{i}: {t}" for i, t in enumerate(q["criteria"])]
    return q["instructions"] + "\nAnswers:\n" + "\n".join(opts)


def _item_question(q, qid, field):
    return {"type": "score", "instructions": f"Question '{qid}' about {field}.",
            "criteria": [str(i) for i in range(len(q["criteria"]))]}


def _item_text(state):
    return "\n".join(f"{k}: {v}" for k, v in state.items())


def body(model, state, qs):
    return {"model": model, "state": state, "questions": qs}


def batch_body(model, states, qs):
    """(body, [(item index, qid, batch qid)]) asking every question of each state."""
    shared = {"how to answer": HOW, **{f"question {qid}": _spec(q) for qid, q in qs.items()}}
    bqs, members = {}, []
    for i, s in enumerate(states, 1):
        f = f"item {i:02d}"
        shared[f] = _item_text(s)
        for qid, q in qs.items():
            bqs[f"{f} | {qid}"] = _item_question(q, qid, f)
            members.append((i - 1, qid, f"{f} | {qid}"))
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
    """{batch qid: answer} from one response, or api.Transport."""
    if not isinstance(response, dict):
        raise api.Transport(f"response is not an object: {str(response)[:200]}")
    answers = response.get("answers") or {}
    for qid, q in b["questions"].items():
        if not complete(answers.get(qid), q):
            raise api.Transport(f"answer for {qid!r} is incomplete: {str(answers.get(qid))[:200]}")
    return answers


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
    missing, imported = {}, []
    for item, state, s_sha, qs in keyed:
        for qid, (q, q_sha) in qs.items():
            if (s_sha, q_sha) in have:
                continue
            old = _legacy(data, model, state, q, aliases) if data else None
            if old:
                have[(s_sha, q_sha)] = dict(old["answer"], served=old.get("model", model))
                imported.append((s_sha, q_sha, old["answer"], old.get("model", model),
                                 old.get("cost_usd"), old.get("call"), old.get("asked_at")))
                continue
            missing.setdefault((item, s_sha), (state, {}))[1][qid] = q
    if imported:
        _store(conn, model, imported, {s: st for _, st, s, _ in keyed},
               {q_sha: q for _, _, _, qs in keyed for q, q_sha in qs.values()})
    # batches: items sharing a question set, ordered by the hash of their state
    groups = {}
    for (item, s_sha), (state, qs) in sorted(missing.items(), key=lambda x: x[0][1]):
        groups.setdefault(Q.sha(sorted(Q.sha(q) for q in qs.values())), []).append(((item, s_sha), state, qs))
    todo = []
    for _, members in sorted(groups.items()):
        for b, plan in batches(model, [(st, qs) for _, st, qs in members], batch):
            todo.append((b, [(members[i][0], members[i][2][qid], qid, bq) for i, qid, bq in plan]))
    chars = sum(_size(b) for b, _ in todo)
    stats = {"stored": len(have), "imported": len(imported), "to_ask": len(todo), "chars": chars,
             "estimate_usd": chars / 4 / 1e6 * Q.PRICE_PER_MTOK, "calls": 0, "cost_usd": 0.0,
             "failures": [], "seconds": 0.0}
    if todo and not dry_run:
        if not api_key:
            raise SystemExit(f"{stage}: no credential: set ${api.KEY_VAR}")
        lock = Lock()
        t0 = time.time()
        with api.Spend(conn, stage, label_set, model) as spend:
            spend.check(stats["estimate_usd"])

            def one(b, plan):
                response = api.post("/alpha/decisions", b, api_key)
                answers = split(response, b)
                cost = float((response.get("usage") or {}).get("cost") or 0)
                served = response.get("model", "")
                at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                call = Q.sha(b)
                share = cost / max(len(plan), 1)
                rows, states, qs_ = [], {}, {}
                for (item, s_sha), q, qid, bq in plan:
                    q_sha = Q.sha(q)
                    rows.append((s_sha, q_sha, answers[bq], served, share, call, at))
                    states[s_sha] = missing[(item, s_sha)][0]
                    qs_[q_sha] = q
                with lock:
                    _store(conn, model, rows, states, qs_)
                    for s_sha, q_sha, a, *_ in rows:
                        have[(s_sha, q_sha)] = dict(a, served=served)
                return cost

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(one, b, plan): b for b, plan in todo}
                for n, f in enumerate(as_completed(futures), 1):
                    try:
                        spend.add(f.result())
                    except api.Transport as exc:
                        stats["failures"].append(str(exc)[:200])
                    except (api.Refused, api.CapReached):
                        pool.shutdown(wait=True, cancel_futures=True)
                        raise
                    if n % 500 == 0 or n == len(todo):
                        log(f"  {n:,}/{len(todo):,} asked, {len(stats['failures'])} failed, "
                            f"US${spend.cost:.3f}")
            stats["calls"], stats["cost_usd"] = spend.calls, spend.cost
        stats["seconds"] = time.time() - t0
    out = {}
    for item, state, s_sha, qs in keyed:
        got = {qid: have.get((s_sha, q_sha)) for qid, (q, q_sha) in qs.items()}
        if all(v is not None for v in got.values()):
            out[item] = got
    return out, stats


def estimate_line(stage, stats):
    return (f"  {stage}: {stats['stored']:,} answers stored"
            + (f" ({stats['imported']:,} read from the old file store)" if stats["imported"] else "")
            + f"; {stats['to_ask']:,} requests to make (about US${stats['estimate_usd']:.3f})")
