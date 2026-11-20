"""The chat model's answers, stored beside the decision model's.

The second look of every step is one pinned chat model (control panel
second.model), asked one item a request and told to reply with a short JSON
object. Its answers live in the same table as the decision model's
(<store>.answers: model = the chat model, state_sha, question_sha), so a re-run
asks only what the store lacks.

  ask(conn, items, stage=..., label_set=...) -> ({item: answer}, stats)

items = [(item, state, question)]. A state is a dict of fields (what the model
is shown); a question is built by questions.chat_question() and says how to reply. An answer
is {"grade": int|None, "labels": [..]|None, "raw": the reply, "failed": bool}.
A reply that is not exactly the JSON asked for is never stored in answers
(which only gains rows): each failed attempt is one insert-only row of
<store>.chat_failures (model, state_sha, question_sha, raw text, at), counted
in stats["unparsed"] and left out of the returned answers. A question that has
failed second.max_attempts times is not asked again (stats["gave_up"], logged);
it is never read as a yes.

Replies (reply["form"]): grade (0 to reply["max"]), yesno (yes or no), choice
(one of reply["options"]), labels (any of reply["options"], at most
reply["max"], none allowed), assets (up to reply["max"] pairs of an asset and
a kind of it, none allowed; the ids are read as text and checked against the
files by the step, which discards and counts any it does not know), activities
(a list of labelled groups of sentence numbers; the step validates and
repairs them). temperature 0; the spend ledger and --max-spend
work as in src/wbg_portfolio/ask.py. Tests replace api.post (tests/conftest.py fake_chat).
"""
import json
import re
import time
from threading import Lock

from wbg_portfolio import api
from wbg_portfolio import ask as asker
from wbg_portfolio import db
from wbg_portfolio import panel
from wbg_portfolio import questions as Q

PATH = "/v1/chat/completions"


def model():
    """The pinned chat model (second.model)."""
    return str(panel.require("second.model"))


def max_tokens(q):
    """Most tokens a reply to this question may hold: second.max_tokens, or
    activities.max_tokens for the activities form (a list of labelled groups)."""
    return int(panel.require("activities.max_tokens" if q["reply"]["form"] == "activities"
                             else "second.max_tokens"))


def estimate(prompt_text, q):
    """US$ an item may cost: its prompt (characters / 4 tokens) at second.price_in_per_mtok plus a
    reply of up to max_tokens at second.price_out_per_mtok. An upper bound, so a cap is not passed
    by an estimate that is too low."""
    return (len(prompt_text) / 4 * float(panel.require("second.price_in_per_mtok"))
            + max_tokens(q) * float(panel.require("second.price_out_per_mtok"))) / 1e6


def cost_of(response, prompt_text, text, q):
    """What a response cost: usage.cost when it reports one, else its token counts at the panel's
    prices, else the estimate (never 0, so the cap cannot be bypassed by a missing field)."""
    usage = response.get("usage") or {}
    if usage.get("cost") is not None:
        return float(usage["cost"])
    p_in, p_out = float(panel.require("second.price_in_per_mtok")), float(panel.require("second.price_out_per_mtok"))
    t_in, t_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if t_in is None and t_out is None:
        return estimate(prompt_text, q)
    return ((t_in if t_in is not None else len(prompt_text) / 4) * p_in
            + (t_out if t_out is not None else len(text or "") / 4) * p_out) / 1e6


def reply_text(reply):
    f = reply["form"]
    if f == "grade":
        return f'{{"grade": <a whole number from 0 to {reply["max"]}>}}'
    if f == "yesno":
        return '{"answer": "yes"} or {"answer": "no"}'
    if f == "choice":
        return '{"answer": "<one of: ' + ", ".join(reply["options"]) + '>"}'
    if f == "assets":
        return ('{"assets": [{"asset": "<asset id from: ' + ", ".join(reply["options"]) + '>", "subtype": '
                '"<kind id of that asset>"}]} with at most ' + str(reply["max"]) + ' assets, or {"assets": []} when none applies')
    if f == "activities":
        return ('{"activities": [{"label": "<a short name for the activity>", "sentences": [<the numbers of '
                'its sentences>]}]}')
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
    if form == "assets":
        items = obj.get("assets")
        if not isinstance(items, list) or len(items) > reply["max"] or not all(
                isinstance(x, dict) and isinstance(x.get("asset"), str)
                and isinstance(x.get("subtype", ""), str) for x in items):
            raise ValueError(f"assets is not a list of at most {reply['max']} {{asset, subtype}} objects")
        return {"grade": None, "labels": [f"{x['asset'].strip()}/{x.get('subtype', '').strip()}" for x in items]}
    if form == "activities":
        acts = obj.get("activities")
        if not isinstance(acts, list) or not acts or not all(
                isinstance(x, dict) and isinstance(x.get("label"), str) and isinstance(x.get("sentences"), list)
                and all(isinstance(n, int) and not isinstance(n, bool) for n in x["sentences"]) for x in acts):
            raise ValueError("activities is not a list of {label, sentences} objects")
        return {"grade": None, "labels": None,
                "activities": [{"label": " ".join(x["label"].split()), "sentences": list(x["sentences"])}
                               for x in acts]}
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

def _store_failure(conn, mdl, s_sha, q_sha, raw, reason, state, q):
    """One attempt that could not be read: an insert-only row of chat_failures."""
    with conn.transaction():
        conn.execute(f"INSERT INTO {db.STORE}.chat_failures (model, state_sha, question_sha, raw, reason, at) "
                     f"VALUES (%s, %s, %s, %s, %s, now())", (mdl, s_sha, q_sha, raw, reason))
        asker._insert_context(conn, {s_sha: state}, {q_sha: q})


def failure_counts(conn, mdl, keys):
    """{(state_sha, question_sha): attempts that could not be read}."""
    if not keys:
        return {}
    db.temp_keys(conn, "chat_keys", ["state_sha", "question_sha"], keys)
    return {(s, q): n for s, q, n in db.rows(
        conn, f"SELECT f.state_sha, f.question_sha, count(*) FROM {db.STORE}.chat_failures f "
              f"JOIN chat_keys k USING (state_sha, question_sha) WHERE f.model = %s "
              f"GROUP BY f.state_sha, f.question_sha", (mdl,))}


def ask(conn, items, *, stage, label_set="", api_key="", dry_run=False, workers=asker.WORKERS,
        log=print, mdl=None):
    """Answers for every item: ({item: answer}, stats). Asks only what the
    store lacks (and what it holds as a failure); with dry_run nothing is
    asked. An item with no usable answer is absent. stats: stored, to_ask,
    estimate_usd, calls, cost_usd, failures (requests that failed), unparsed
    (replies that were not the JSON asked for), seconds."""
    mdl = mdl or model()
    keyed = [(item, state, Q.sha(state), q, Q.sha(q)) for item, state, q in items]
    keys = sorted({(s, qs) for _, _, s, _, qs in keyed})
    have = asker.stored(conn, mdl, keys)
    # an answer an older version stored as a failure is not an answer: it counts as one attempt
    legacy = {k for k, v in have.items() if v.get("failed")}
    attempts = failure_counts(conn, mdl, keys)
    limit = int(panel.require("second.max_attempts"))
    todo, gave_up = {}, set()
    for item, state, s_sha, q, q_sha in keyed:
        k = (s_sha, q_sha)
        got = have.get(k)
        if got is not None and k not in legacy:
            continue
        if attempts.get(k, 0) + (k in legacy) >= limit:
            gave_up.add(k)
        else:
            todo.setdefault(k, (state, q))
    sent = {k: prompt(state, q) for k, (state, q) in todo.items()}
    stats = {"stored": sum(1 for k, v in have.items() if k not in legacy), "to_ask": len(todo),
             "estimate_usd": sum(estimate(sent[k], todo[k][1]) for k in todo), "gave_up": len(gave_up),
             "calls": 0, "cost_usd": 0.0, "failures": [], "unparsed": 0, "seconds": 0.0}
    if todo and not dry_run:
        lock = Lock()

        def one(key):
            state, q = todo[key]
            body = {"model": mdl, "temperature": 0, "max_tokens": max_tokens(q),
                    "messages": [{"role": "user", "content": sent[key]}]}
            r = api.post(PATH, body, api_key, timeout=120)
            try:
                text = r["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise api.Transport(f"reply has no message: {str(r)[:200]}")
            cost = cost_of(r, sent[key], text, q)
            served = r.get("model", "")
            try:
                answer = dict(parse(text, q["reply"]), raw=text)
            except (ValueError, TypeError) as exc:
                with lock:
                    _store_failure(conn, mdl, key[0], key[1], text, str(exc)[:200], state, q)
                    stats["unparsed"] += 1
                return cost
            at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with lock:
                asker._store(conn, mdl, [(key[0], key[1], answer, served, cost, Q.sha(body), at)],
                             {key[0]: state}, {key[1]: q})
                have[key] = dict(answer, served=served)
                legacy.discard(key)
            return cost

        asker.run_requests(conn, mdl, [(k,) for k in sorted(todo)], one, stats, stage=stage,
                           label_set=label_set, api_key=api_key, workers=workers, log=log,
                           tail=lambda: f", {stats['unparsed']} unreadable")
    out = {}
    for item, _, s_sha, _, q_sha in keyed:
        a = have.get((s_sha, q_sha))
        if usable(a) and (s_sha, q_sha) not in legacy:
            out[item] = a
    return out, stats


def estimate_line(stage, stats):
    return (asker.estimate_line(stage, stats)
            + (f"; {stats['unparsed']} reply(ies) unreadable" if stats["unparsed"] else "")
            + (f"; gave up on {stats['gave_up']:,} question(s) unreadable second.max_attempts times "
               f"(not asked again, never a yes)" if stats.get("gave_up") else ""))


def asked(conn, items, stage, label_set, api_key, dry_run, workers, log):
    """ask() as a step runs it: (answers, ok): ok is False when a request failed (run again to resume);
    an unreadable reply is counted and the item left without an answer."""
    got, st = ask(conn, items, stage=stage, label_set=label_set, api_key=api_key, dry_run=dry_run,
                       workers=workers, log=log)
    log(estimate_line(stage, st))
    if dry_run:
        return got, False
    if st["failures"]:
        log(f"  {len(st['failures'])} request(s) failed; run again to resume.")
    return got, not st["failures"]
