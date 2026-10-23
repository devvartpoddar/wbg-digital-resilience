#!/usr/bin/env python3
"""The store of decision-model answers, one per (model, state, question).

A request to the decisions endpoint carries one state and any number of
questions, and the price is the tokens of both. The questions are most of
it: nine asset definitions with their grade criteria are 2,600 tokens
against 180 for an average paragraph. Storing the answer per question, not
per request, means a changed definition re-asks that one question (about
470 tokens a paragraph) and not all nine (about 2,800): the difference
between US$0.35 and US$2.15 for one edit on the 70-project corpus.

  key(model, state, question)    SHA-256 over the model, the state and the
                                 question as sent (not its id, which the
                                 model never sees)
  ask(model, requests, ...)      requests = [(state, {qid: question})]. For
                                 each state, the questions whose answer is
                                 not stored go in one call; the response is
                                 split and each answer stored under its key
                                 with the call's cost shared equally. Returns
                                 [{qid: answer}] in request order, assembled
                                 from the store.

Stored under data/classify/answers/<model>/<aa>/<sha>.json:
  {"answer": {...}, "model": "<snapshot served>", "cost_usd": share,
   "asked_at": ..., "call": <SHA-256 of the whole request>}

An answer is stored only if complete for its type: a score answer with a
probability for every criterion, a yes/no (noul) answer with its number.

The JSON files are the record. A rebuild reads them through a cache,
data/cache/answers-<model>.sqlite (sha, the file's text), filled the first
time a file is read: one query per thousand answers instead of opening each
file. The cache only ever holds a file's own bytes, and can be thrown away.
"""
import argparse, glob, hashlib, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import panel  # noqa: E402


def _slug(model):
    return model.replace("/", "__").replace(":", "_")


_DBS = {}


def _db(root_):
    """The read cache for a model's store (see the module docstring), or None
    where it cannot be opened."""
    if root_ in _DBS:
        return _DBS[root_]
    import sqlite3
    try:
        cache = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(root_))),
                             "cache", f"answers-{os.path.basename(root_)}.sqlite")
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        con = sqlite3.connect(cache, timeout=60, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS a (sha TEXT PRIMARY KEY, body TEXT NOT NULL)")
    except Exception:                                  # noqa: BLE001 - a cache, never needed
        con = None
    _DBS[root_] = con
    return con


def read_many(root_, shas, bodies=True):
    """{sha: entry (or True without bodies)} of the stored answers among
    shas: from the cache, then the files for what it lacks (put in the cache
    as they are read)."""
    shas = list(dict.fromkeys(shas))
    out, con = {}, _db(root_)
    if con is not None:
        col = "sha, body" if bodies else "sha"
        for i in range(0, len(shas), 900):
            part = shas[i:i + 900]
            q = f"SELECT {col} FROM a WHERE sha IN ({','.join('?' * len(part))})"
            for row in con.execute(q, part):
                out[row[0]] = json.loads(row[1]) if bodies else True
    fresh = []
    for sha in shas:
        if sha in out:
            continue
        path = _path(root_, sha)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            entry = json.loads(text)
        except (OSError, ValueError):
            continue
        out[sha] = entry if bodies else True
        fresh.append((sha, text))
    if con is not None and fresh:
        try:
            with con:
                con.executemany("INSERT OR IGNORE INTO a (sha, body) VALUES (?, ?)", fresh)
        except Exception:                              # noqa: BLE001 - a cache, never needed
            pass
    return out


def root(data, model):
    return where(data, "classify", "answers", _slug(model))


def key(model, state, question):
    return hashlib.sha256(json.dumps({"model": model, "state": state, "question": question},
                                     sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


_PREFIX = {}


def keys_of(model, state, qs):
    """{qid: key(model, state, q)} for one state, byte for byte, serialising
    the state once and each question once per process (keys over the asset
    map serialised 443,740 questions of about a kilobyte each)."""
    tail = (json.dumps(state, sort_keys=True, ensure_ascii=False) + "}").encode("utf-8")
    out = {}
    for qid, q in qs.items():
        hit = _PREFIX.get((model, id(q)))
        if hit is None or hit[0] is not q:
            pre = ('{"model": ' + json.dumps(model, ensure_ascii=False) + ', "question": '
                   + json.dumps(q, sort_keys=True, ensure_ascii=False) + ', "state": ')
            hit = (q, hashlib.sha256(pre.encode("utf-8")))
            _PREFIX[(model, id(q))] = hit
        h = hit[1].copy()
        h.update(tail)
        out[qid] = h.hexdigest()
    return out


def _path(root_, sha):
    return os.path.join(root_, sha[:2], sha + ".json")


def read(root_, sha):
    try:
        with open(_path(root_, sha), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write(root_, sha, entry):
    path = _path(root_, sha)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump(entry, fh, ensure_ascii=False, sort_keys=True)
    os.replace(path + ".part", path)


def complete(answer, question):
    """Whether an answer is whole for its question's type."""
    if not isinstance(answer, dict):
        return False
    if question["type"] == "score":
        probs = answer.get("probabilities") or {}
        return answer.get("type") == "score" and \
            sorted(probs) == [str(i) for i in range(len(question["criteria"]))]
    if question["type"] == "noul":
        return answer.get("type") == "noul" and isinstance(answer.get("noul"), (int, float))
    if question["type"] == "choice":
        probs = answer.get("probabilities") or {}
        return answer.get("type") == "choice" and set(probs) == set(question["criteria"])
    return False


def split(response, questions, call_sha):
    """{qid: entry} from one response, or a TransportError naming what is
    missing. The call's cost is shared equally between its questions."""
    import classify
    if not isinstance(response, dict):
        raise classify.TransportError(f"response is not an object: {str(response)[:200]}")
    answers = response.get("answers") or {}
    cost = float((response.get("usage") or {}).get("cost") or 0) / max(len(questions), 1)
    out = {}
    for qid, q in questions.items():
        a = answers.get(qid)
        if not complete(a, q):
            raise classify.TransportError(f"answer for {qid!r} is incomplete: {str(a)[:200]}")
        out[qid] = {"answer": a, "model": response.get("model", ""), "cost_usd": cost,
                    "asked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "call": call_sha}
    return out


# Items are asked many to a request: each distinct question set out once in
# the state, each item (a sentence, a paragraph, a package, a pair) in its
# own field, and one short question per item and question. A request costs a
# fixed overhead of about 300 tokens plus its state and questions, and the
# questions are most of it, so batching cuts the cost: ten a request cost
# about a quarter of one a request (2026-10-21, 477 graded units, US$0.0053
# against US$0.0196, the same accuracy within the noise).
#
# An item's first answer depends on its neighbours (2026-10-22, 120
# sentences, the climate risk assessment question): one a request asked
# twice agreed exactly (0 of 120 crossed 0.5), while batches moved an answer
# by 0.11 on average (10 to 12 of 120 crossed 0.5) and said yes more often.
# Against a person's grades (131 sentences of two measures) batches of ten
# and of 62 to 66 ranked as well as one a request or better (AUC 0.90 and
# 0.97 against 0.88 and 0.96). Every answer is stored under its own item and
# question, so a re-run reads it back whatever its neighbours were; only
# first answers move. Batches above 120 items were never measured, so
# ask.batch_items stays inside what was (control panel; $WBG_ASK_BATCH
# overrides it, 0 asks one item a request).
BATCH = int(os.environ.get("WBG_ASK_BATCH") or panel.get("ask.batch_items", 50))
# A batch may pass the 30,000-character line above which the endpoint reads
# in its long-context mode (classify.MAX_REQUEST_CHARS): on 60 paragraphs x
# 13 asset questions, batches of ten agreed with one a request as often at
# 80,000 characters as at 30,000 (96 per cent the same grade, mean shift in
# P(top grade) 0.016), and cost 61 per cent less instead of 24.
BATCH_MAX_CHARS = int(panel.get("ask.batch_max_chars", 80_000))


def spec(question):
    """A question written out in full, for the shared part of a batch."""
    c = question["criteria"]
    if question["type"] == "score":
        opts = [f"{i}: {t}" for i, t in enumerate(c)]
    elif question["type"] == "noul":
        opts = [f"true: {c.get('true', 'yes')}", f"false: {c.get('false', 'no')}"]
    else:
        opts = [f"{k}: {v}" for k, v in c.items()]
    return question["instructions"] + "\nAnswers:\n" + "\n".join(opts)


HOW = ("Several items follow, each in its own field ('item 01', 'item 02', ...), and each "
       "question is set out in full in its own field ('question <id>'). Each short question "
       "below asks one of those questions about one item: answer it for that item alone, "
       "reading any field the question names (such as 'sentence', 'paragraph', 'package' or "
       "'commitment') as that line of the item. The other items are separate.")


def item_question(question, qid, field):
    """The short question one item of a batch is asked: the full question is
    in the state, so its options keep their keys and only a short label."""
    c = question["criteria"]
    if question["type"] == "score":
        # The grade numbers alone: each grade is written out once, in the
        # full question in the state. On 615 sentences x 13 assets the
        # four-word previews were 44 per cent of the characters and changed
        # nothing (top asset agreeing with Dev 0.66 against 0.65, mean average
        # precision 0.604 against 0.603; measures 0.882 against 0.878), 2026-10-22.
        crit = [str(i) for i in range(len(c))]
    elif question["type"] == "noul":
        crit = {"true": "Yes", "false": "No"}
    else:
        crit = {k: k.replace("_", " ") for k in c}
    return {"type": question["type"],
            "instructions": f"Question '{qid}' about {field}.",
            "criteria": crit}


def item_text(state):
    """An item's state as one text field: one 'name: value' line a field."""
    return "\n".join(f"{k}: {v}" for k, v in state.items())


def batch_body(states, questions, model):
    """(body, [(item index, qid, batch qid)]) asking every question of each state."""
    import classify
    shared = {"how to answer": HOW, **{f"question {qid}": spec(q) for qid, q in questions.items()}}
    qs, members = {}, []
    for i, s in enumerate(states, 1):
        f = f"item {i:02d}"
        shared[f] = item_text(s)
        for qid, q in questions.items():
            bq = f"{f} | {qid}"
            qs[bq] = item_question(q, qid, f)
            members.append((i - 1, qid, bq))
    return classify.request_body(shared, qs, model), members


def _item_chars(state, questions, i):
    """About what one more item adds to a batched body, in characters."""
    f = f"item {i:02d}"
    one = {f: item_text(state)}
    one.update({f"{f} | {qid}": item_question(q, qid, f) for qid, q in questions.items()})
    return len(json.dumps(one, ensure_ascii=False))


def batches_for(items, model, size):
    """Batched bodies for [(state, {qid: question})] items that share one
    question set: as many a request as fit under BATCH_MAX_CHARS, at most
    `size`, in the items' order. Returns [(body, [(item, qid, batch qid)])]."""
    import classify
    out, k = [], 0
    while k < len(items):
        qs = items[k][1]
        base = len(json.dumps(batch_body([], qs, model)[0], ensure_ascii=False))
        n, used = 0, base
        while k + n < len(items) and n < size:
            add = _item_chars(items[k + n][0], qs, n + 1)
            if n and used + add > BATCH_MAX_CHARS:
                break
            used += add
            n += 1
        n = max(n, 1)
        while True:
            chunk = items[k:k + n]
            body, members = batch_body([s for s, _ in chunk], chunk[0][1], model)
            if n == 1 or len(json.dumps(body, ensure_ascii=False)) <= BATCH_MAX_CHARS:
                break
            n = max(1, n // 2)
        if n == 1 and len(json.dumps(body, ensure_ascii=False)) > BATCH_MAX_CHARS:
            out.extend((b, [(chunk[0], q, q) for q in b["questions"]])
                       for b in bodies_for(chunk[0][0], chunk[0][1], model))
        else:
            out.append((body, [(chunk[i], qid, bq) for i, qid, bq in members]))
        k += n
    return out


def bodies_for(state, questions, model):
    """The request bodies that ask `questions` of `state`, each under
    classify.MAX_REQUEST_CHARS: one body when they fit, else the questions
    in file order split over as many bodies as needed (the store is per
    question, so a split changes nothing downstream). A state too long for
    even one question is refused, never truncated."""
    import classify
    if not questions:
        return []
    size = lambda qs: len(json.dumps(classify.request_body(state, qs, model), ensure_ascii=False))
    if size(questions) <= classify.MAX_REQUEST_CHARS:
        return [classify.request_body(state, questions, model)]
    out, batch = [], {}
    for qid, q in questions.items():
        if batch and size({**batch, qid: q}) > classify.MAX_REQUEST_CHARS:
            out.append(classify.request_body(state, batch, model))
            batch = {}
        batch[qid] = q
        if size(batch) > classify.MAX_REQUEST_CHARS:
            raise SystemExit(f"answers: a state with one question is over "
                             f"{classify.MAX_REQUEST_CHARS:,} characters; not truncated")
    out.append(classify.request_body(state, batch, model))
    return out


# A hard cap on what this process may spend on the decision model, in US$,
# from $WBG_SPEND_CAP_USD (unset: no cap). Set by the dashboard's measure
# runner (src/measure_requests.py); a run that would pass it stops before
# asking, or as soon as it is reached, and resumes from the store later.
_spent_here = [0.0]


def spend_cap(data=None):
    """US$ this process may still spend: $WBG_SPEND_CAP_USD, less what the
    ledger records since $WBG_SPEND_SINCE (set by ./wbg run --max-spend, so
    the cap covers every stage of a run), less what this process has spent
    and not yet recorded. None without a cap."""
    try:
        cap = float(os.environ["WBG_SPEND_CAP_USD"])
    except (KeyError, ValueError):
        return None
    since = os.environ.get("WBG_SPEND_SINCE")
    if since and data:
        import csv
        path = where(data, "reports", "spend.csv")
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as fh:
                cap -= sum(float(r["cost_usd"] or 0) for r in csv.DictReader(fh)
                           if r["at"] >= since)
    return cap


class CapReached(SystemExit):
    """The process's spend cap would be or was passed."""


def ask(model, requests, data, *, api_key="", log=print, workers=None, dry_run=False,
        aliases=(), batch=None, read_answers=True, tags=None):
    """Answers for every request, asking only what is not stored.
    Returns (answers, stats): answers = [{qid: entry}] per request (None for
    a request with a missing answer when nothing could be asked), stats =
    {"to_ask": calls planned, "chars": request characters, "calls", "cost_usd",
    "failures": [(call sha, error)], "seconds", "stored_items": requests with
    every answer stored}. With read_answers False (an estimate) nothing stored
    is opened: whether an answer is stored is a stat of its file, and
    answers is [None] * len(requests). tags (one per request) split the
    calls, cost and failures by tag in stats["by_tag"]: several label sets
    asked in one call (src/measure.py, measure_many) keep their own ledger.
    A request asked for several tags counts under the first."""
    import classify
    root_ = root(data, model)
    keys = [keys_of(model, state, qs) for state, qs in requests]
    todo = {}
    # every stored answer is looked up once per call, all together (the
    # cache, then the files): the alias check, the missing check and the
    # answers returned all read through this
    memo = read_many(root_, [s for ks in keys for s in ks.values()], bodies=read_answers)

    def stored(sha):
        return memo.get(sha)
    # Answers asked under an alias of the model (the moving name the asset
    # map once used) count when the snapshot that served them is this model:
    # they are copied under this model's key, never asked again.
    for alias in aliases:
        aroot = root(data, alias)
        for (state, qs), ks in zip(requests, keys):
            for qid, q in qs.items():
                if stored(ks[qid]) is None:
                    old = read(aroot, key(alias, state, q))
                    if old and old.get("model") == model:
                        write(root_, ks[qid], dict(old, migrated_from=alias))
                        memo.update(read_many(root_, [ks[qid]], bodies=read_answers))
    batch = BATCH if batch is None else batch
    # what each body answers: [(state, qid, the body's own qid)]
    plan = {}
    groups, seen = {}, set()
    complete_ = 0
    tag_of, item_tag = {}, {}
    tags = list(tags) if tags is not None else [None] * len(requests)
    for (state, qs), ks, tag in zip(requests, keys, tags):
        missing = {qid: q for qid, q in qs.items() if stored(ks[qid]) is None}
        if not missing:
            complete_ += 1
            continue
        if batch > 1:
            k = json.dumps([state, missing], sort_keys=True, ensure_ascii=False)
            if k not in seen:                                  # the same item asks once
                seen.add(k)
                item_tag[k] = tag
                groups.setdefault(json.dumps(missing, sort_keys=True), []).append((state, missing))
            continue
        for body in bodies_for(state, missing, model):
            sha = classify.request_sha(body)
            todo.setdefault(sha, body)                         # the same request asks once
            tag_of.setdefault(sha, tag)
            plan.setdefault(sha, [(state, body["questions"][q], q) for q in body["questions"]])
    # Items sharing a question set go together, ordered by their state (a
    # sentence's section, component and text), so a batch holds neighbours
    # from one part of one document where it can.
    for _, items in sorted(groups.items()):
        items.sort(key=lambda it: json.dumps(it[0], sort_keys=True, ensure_ascii=False))
        for body, members in batches_for(items, model, batch):
            sha = classify.request_sha(body)
            todo.setdefault(sha, body)
            first = members[0][0]
            tag_of.setdefault(sha, item_tag.get(json.dumps(list(first), sort_keys=True,
                                                           ensure_ascii=False)))
            plan.setdefault(sha, [(it[0], it[1][qid], bq) for it, qid, bq in members])
    chars = sum(len(json.dumps(b, ensure_ascii=False)) for b in todo.values())
    stats = {"to_ask": len(todo), "chars": chars, "calls": 0, "cost_usd": 0.0, "failures": [],
             "seconds": 0.0, "stored_items": complete_,
             "estimate_usd": chars / 4 / 1e6 * classify.PRICE_PER_MTOK, "by_tag": {}}
    for t in dict.fromkeys(tags):
        stats["by_tag"][t] = {"to_ask": 0, "chars": 0, "calls": 0, "cost_usd": 0.0,
                              "failures": []}
    for sha, body in todo.items():
        b = stats["by_tag"][tag_of.get(sha)]
        b["to_ask"] += 1
        b["chars"] += len(json.dumps(body, ensure_ascii=False))
    for b in stats["by_tag"].values():
        b["estimate_usd"] = b["chars"] / 4 / 1e6 * classify.PRICE_PER_MTOK
    cap = spend_cap(data)
    if todo and not dry_run and cap is not None and \
            _spent_here[0] + stats["estimate_usd"] > cap:
        raise CapReached(f"answers: spend cap US${cap:.2f} would be passed (spent "
                         f"US${_spent_here[0]:.3f}, this step about "
                         f"US${stats['estimate_usd']:.3f}); nothing asked")
    if todo and not dry_run:
        if not api_key:
            raise SystemExit("answers: no credential: set $WBG_OPENROUTER_KEY")
        t0 = time.time()

        def one(sha, body):
            response = classify.decide(body, api_key=api_key)
            entries = split(response, body["questions"], sha)
            # each answer goes under its own item and its own question (the
            # question itself, not its id: one sentence asked the "pair"
            # question of several measure pairs has one id and several questions)
            for state, q, bq in plan[sha]:
                entry = dict(entries[bq], batch=bq) if "how to answer" in body["state"] \
                    else entries[bq]
                write(root_, key(model, state, q), entry)
            return float((response.get("usage") or {}).get("cost") or 0)

        with ThreadPoolExecutor(max_workers=workers or classify.WORKERS) as pool:
            futures = {pool.submit(one, sha, body): sha for sha, body in todo.items()}
            for n, f in enumerate(as_completed(futures), 1):
                try:
                    paid = f.result()
                    stats["cost_usd"] += paid
                    stats["calls"] += 1
                    b = stats["by_tag"][tag_of.get(futures[f])]
                    b["cost_usd"] += paid
                    b["calls"] += 1
                    _spent_here[0] += paid
                    if cap is not None and _spent_here[0] > cap:
                        pool.shutdown(wait=True, cancel_futures=True)
                        raise CapReached(f"answers: spend cap US${cap:.2f} reached after "
                                         f"{stats['calls']:,} calls; the answers so far are "
                                         f"stored")
                except classify.TransportError as exc:
                    stats["failures"].append((futures[f], str(exc)[:200]))
                    stats["by_tag"][tag_of.get(futures[f])]["failures"].append(
                        (futures[f], str(exc)[:200]))
                except classify.Refused as exc:
                    pool.shutdown(wait=True, cancel_futures=True)
                    raise SystemExit(f"answers: refused, stopping ({stats['calls']:,} stored so "
                                     f"far; run again to resume): {exc}")
                if n % 500 == 0 or n == len(todo):
                    log(f"  {n:,}/{len(todo):,} asked, {stats['calls']:,} stored, "
                        f"{len(stats['failures'])} failed, US${stats['cost_usd']:.3f}")
        stats["seconds"] = time.time() - t0
    if not read_answers:
        return [None] * len(keys), stats
    # what was missing may have been asked since: read those again
    memo.update(read_many(root_, [s for ks in keys for s in ks.values() if s not in memo]))
    answers = []
    for ks in keys:
        got = {qid: memo.get(sha) for qid, sha in ks.items()}
        answers.append(got if all(got.values()) else None)
    return answers, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    args = ap.parse_args()
    n = sum(1 for _ in glob.glob(where(args.data, "classify", "answers", "*", "*", "*.json")))
    print(f"{n:,} answers stored under {where(args.data, 'classify', 'answers')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
