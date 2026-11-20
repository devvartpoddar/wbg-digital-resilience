"""The chat model's answers, stored beside the decision model's.

The second look of every step is one pinned chat model (control panel
second.model), asked one item a request and told to reply with a short JSON
object. Its answers live in the same table as the decision model's
(<store>.answers: model = the chat model, state_sha, question_sha), so a re-run
asks only what the store lacks.

  ask(conn, items, stage=..., label_set=...) -> ({item: answer}, stats)

items = [(item, state, question)]. A state is a dict of fields (what the model
is shown); a question is built by question() and says how to reply. An answer
is {"grade": int|None, "labels": [..]|None, "raw": the reply, "failed": bool}.
A reply that is not exactly the JSON asked for is stored as a failure (grade
and labels None), counted in stats["unparsed"], left out of the returned
answers, and asked again at the next run. It is never read as a yes.

Replies (reply["form"]): grade (0 to reply["max"]), yesno (yes or no), choice
(one of reply["options"]), labels (any of reply["options"], at most
reply["max"], none allowed). temperature 0; the spend ledger and --max-spend
work as in src/wbg_portfolio/ask.py. Tests replace api.post (tests/conftest.py fake_chat).
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from wbg_portfolio import api
from wbg_portfolio import ask as asker
from wbg_portfolio import db
from wbg_portfolio import panel
from wbg_portfolio import questions as Q

PATH = "/v1/chat/completions"
PRICE_PER_MTOK = 0.30     # US$ per million prompt tokens, replies included: for estimates only
REPLY_TOKENS = 40
FORMS = ("grade", "yesno", "choice", "labels")


def model():
    """The pinned chat model (second.model)."""
    return str(panel.require("second.model"))


# ----------------------------------------------------------------- questions

def question(instructions, form, options=None, maximum=None):
    """A chat question: what to judge, and how to reply."""
    if form not in FORMS:
        raise ValueError(f"chat: unknown reply form {form!r}")
    reply = {"form": form}
    if options is not None:
        reply["options"] = list(options)
    if maximum is not None:
        reply["max"] = int(maximum)
    return {"type": "chat", "instructions": instructions, "reply": reply}


def grade_question(lab, scale):
    """The 0-n grade of a measure on the shared scale (the measure's second look)."""
    grades = "\n".join(f"{i}: {t.replace('{name}', lab['name'])}" for i, t in enumerate(scale))
    return question(
        f"Grade how the sentence in the 'sentence' field, from a World Bank project appraisal "
        f"document, relates to {lab['name']}. {lab['name']} means: {lab['definition']}\nGrades:\n{grades}",
        "grade", maximum=len(scale) - 1)


def reply_text(reply):
    f = reply["form"]
    if f == "grade":
        return f'{{"grade": <a whole number from 0 to {reply["max"]}>}}'
    if f == "yesno":
        return '{"answer": "yes"} or {"answer": "no"}'
    if f == "choice":
        return '{"answer": "<one of: ' + ", ".join(reply["options"]) + '>"}'
    return ('{"labels": [<ids from: ' + ", ".join(reply["options"]) + f'; at most {reply["max"]}; '
            'an empty list when none applies>]}')


def prompt(state, q):
    """The one message sent: the question, the item's fields, the reply format."""
    fields = "\n\n".join(f"[{k}]\n{v}" for k, v in state.items())
    return (f"{q['instructions']}\n\n{fields}\n\nReply with only a JSON object, nothing else: "
            f"{reply_text(q['reply'])}")


# ------------------------------------------------------------------- parsing

def parse(text, reply):
    """The answer {grade, labels} of a reply, or ValueError. Exactly one JSON
    object (a code fence around it is allowed) with the key the form asks for
    and a valid value; anything else is a failure."""
    t = (text or "").strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    obj = json.loads(m.group(1) if m else t)
    if not isinstance(obj, dict):
        raise ValueError("not a JSON object")
    form = reply["form"]
    if form == "grade":
        g = obj.get("grade")
        if isinstance(g, bool) or not isinstance(g, int) or not 0 <= g <= reply["max"]:
            raise ValueError(f"grade {g!r} is not a whole number from 0 to {reply['max']}")
        return {"grade": g, "labels": None}
    if form in ("yesno", "choice"):
        a = obj.get("answer")
        allowed = ["yes", "no"] if form == "yesno" else reply["options"]
        if not isinstance(a, str) or a.strip().lower() not in allowed:
            raise ValueError(f"answer {a!r} is not one of {allowed}")
        a = a.strip().lower()
        return {"grade": (1 if a == "yes" else 0) if form == "yesno" else None, "labels": [a]}
    labs = obj.get("labels")
    if not isinstance(labs, list) or not all(isinstance(x, str) for x in labs):
        raise ValueError("labels is not a list of text")
    labs = [x.strip() for x in labs]
    if any(x not in reply["options"] for x in labs) or len(set(labs)) != len(labs) \
            or len(labs) > reply["max"]:
        raise ValueError(f"labels {labs!r} are not at most {reply['max']} of {reply['options']}")
    return {"grade": None, "labels": labs}


def usable(answer):
    """False for an answer the reply could not be read into (never a yes)."""
    return bool(answer) and not answer.get("failed")


# ---------------------------------------------------------------- the asker

def _store(conn, mdl, rows, states, qs):
    """Write answers once; a stored failure is replaced by a later reading."""
    with conn.transaction():
        for s_sha, q_sha, answer, served, cost, call, at in rows:
            conn.execute(f"INSERT INTO {db.STORE}.answers (model, state_sha, question_sha, answer, served, "
                         f"cost_usd, call_sha, asked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                         f"ON CONFLICT (model, state_sha, question_sha) DO UPDATE SET answer = EXCLUDED.answer, "
                         f"served = EXCLUDED.served, cost_usd = EXCLUDED.cost_usd, call_sha = EXCLUDED.call_sha, "
                         f"asked_at = EXCLUDED.asked_at WHERE {db.STORE}.answers.answer->>'failed' = 'true'",
                         (mdl, s_sha, q_sha, json.dumps(answer, ensure_ascii=False), served, cost, call, at))
        for s_sha, state in states.items():
            conn.execute(f"INSERT INTO {db.STORE}.states (sha, state) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (s_sha, json.dumps(state, ensure_ascii=False)))
        for q_sha, q in qs.items():
            conn.execute(f"INSERT INTO {db.STORE}.questions (sha, question) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (q_sha, json.dumps(q, ensure_ascii=False)))


def ask(conn, items, *, stage, label_set="", api_key="", dry_run=False, workers=asker.WORKERS,
        log=print, mdl=None):
    """Answers for every item: ({item: answer}, stats). Asks only what the
    store lacks (and what it holds as a failure); with dry_run nothing is
    asked. An item with no usable answer is absent. stats: stored, to_ask,
    estimate_usd, calls, cost_usd, failures (requests that failed), unparsed
    (replies that were not the JSON asked for), seconds."""
    mdl = mdl or model()
    keyed = [(item, state, Q.sha(state), q, Q.sha(q)) for item, state, q in items]
    have = asker.stored(conn, mdl, sorted({(s, qs) for _, _, s, _, qs in keyed}))
    todo = {}
    for item, state, s_sha, q, q_sha in keyed:
        got = have.get((s_sha, q_sha))
        if got is None or got.get("failed"):
            todo.setdefault((s_sha, q_sha), (state, q))
    sent = {k: prompt(state, q) for k, (state, q) in todo.items()}
    chars = sum(len(p) for p in sent.values())
    stats = {"stored": sum(1 for v in have.values() if not v.get("failed")), "to_ask": len(todo),
             "estimate_usd": (chars / 4 + REPLY_TOKENS * len(todo)) / 1e6 * PRICE_PER_MTOK,
             "calls": 0, "cost_usd": 0.0, "failures": [], "unparsed": 0, "seconds": 0.0}
    if todo and not dry_run:
        if not api_key:
            raise SystemExit(f"{stage}: no credential: set ${api.KEY_VAR}")
        lock, t0 = Lock(), time.time()
        with api.Spend(conn, stage, label_set, mdl) as spend:
            spend.check(stats["estimate_usd"])

            def one(key):
                state, q = todo[key]
                body = {"model": mdl, "temperature": 0,
                        "messages": [{"role": "user", "content": sent[key]}]}
                r = api.post(PATH, body, api_key, timeout=120)
                try:
                    text = r["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    raise api.Transport(f"reply has no message: {str(r)[:200]}")
                cost = float((r.get("usage") or {}).get("cost") or 0)
                served = r.get("model", "")
                try:
                    answer = dict(parse(text, q["reply"]), raw=text)
                except (ValueError, TypeError) as exc:
                    answer = {"grade": None, "labels": None, "raw": text, "failed": True,
                              "reason": str(exc)[:200]}
                at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                with lock:
                    _store(conn, mdl, [(key[0], key[1], answer, served, cost, Q.sha(body), at)],
                           {key[0]: state}, {key[1]: q})
                    have[key] = dict(answer, served=served)
                    stats["unparsed"] += bool(answer.get("failed"))
                return cost

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(one, k) for k in sorted(todo)]
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
                            f"{stats['unparsed']} unreadable, US${spend.cost:.3f}")
            stats["calls"], stats["cost_usd"] = spend.calls, spend.cost
        stats["seconds"] = time.time() - t0
    out = {}
    for item, _, s_sha, _, q_sha in keyed:
        a = have.get((s_sha, q_sha))
        if usable(a):
            out[item] = a
    return out, stats


def estimate_line(stage, stats):
    return (f"  {stage}: {stats['stored']:,} answers stored; {stats['to_ask']:,} requests to make "
            f"(about US${stats['estimate_usd']:.3f})"
            + (f"; {stats['unparsed']} reply(ies) unreadable, asked again next run" if stats["unparsed"] else ""))
