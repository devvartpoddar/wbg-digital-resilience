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
in stats["unparsed"] and left out of the returned answers. An empty reply (HTTP 200, no content) is not such an
attempt: it is a provider hiccup, asked again up to EMPTY_TRIES times in the run and, if it stays empty, a failed
request (stats["failures"]) that never counts toward the limit below. A request the provider refuses for this
item alone (HTTP 4xx, once the run has had answers) is recorded in <store>.refused and skipped (stats["refused"]). A question that has
failed second.max_attempts times is not asked again (stats["gave_up"], logged);
it is never read as a yes.

Replies (reply["form"]): grade (0 to reply["max"]), yesno (yes or no), choice
(one of reply["options"]), labels (any of reply["options"], at most
reply["max"], none allowed, read without regard to case, a repeated id read once; an id not among the
options is unreadable), fields (named answers, each a grade, one of a list or any of a list: the evaluation's
graders, whose question is a template with a system line, questions.grader_question). temperature 0; the spend ledger and --max-spend
work as in src/wbg_portfolio/ask.py. Tests replace api.post (tests/conftest.py fake_chat).

Reasoning is off (second.reasoning false: the request body carries "reasoning": {"enabled": false}).
Measured 2026-11-20 on the production wording: with reasoning on and second.max_tokens 300 the model
spent its tokens on hidden reasoning and 61 per cent of grade replies came back empty or as prose
(asking again did not help), while with reasoning off the same replies were readable and cost about a
quarter as much. The request body is part of how an
answer is produced, not of the question: it is not in the question dict and not in the question hash.
A reply may carry prose around one JSON object; that object is read (parse), but a reply with no
object, with more than one, or with an invalid one is a failure, and a yes is never read out of prose.
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
EMPTY_TRIES = 3          # requests an empty reply (HTTP 200, no content) is tried in one run


def model():
    """The pinned chat model (second.model)."""
    return str(panel.require("second.model"))


def max_tokens(q, request=None):
    """Most tokens a reply to this question may hold: second.max_tokens; `request` (a step's
    own request, {"max_tokens", "reasoning"}) overrides it."""
    if request:
        return int(request["max_tokens"])
    return int(panel.require("second.max_tokens"))


def reasoning(q, request=None):
    """Whether the chat model may reason before it answers this question: second.reasoning (read
    through panel.require); `request` overrides it."""
    if request:
        return bool(request["reasoning"])
    return bool(panel.require("second.reasoning"))


def request_body(mdl, q, content, request=None):
    """The request sent for one question. "reasoning": {"enabled": false} only when reasoning is off;
    a question with a "system" text sends it as the system message."""
    messages = ([{"role": "system", "content": q["system"]}] if q.get("system") else []) + \
        [{"role": "user", "content": content}]
    body = {"model": mdl, "temperature": 0, "max_tokens": max_tokens(q, request), "messages": messages}
    if not reasoning(q, request):
        body["reasoning"] = {"enabled": False}
    return body


def estimate(prompt_text, q, request=None):
    """US$ an item may cost: its prompt (characters / 4 tokens) at second.price_in_per_mtok plus a
    reply of up to max_tokens at second.price_out_per_mtok. An upper bound, so a cap is not passed
    by an estimate that is too low."""
    return (len(prompt_text) / 4 * float(panel.require("second.price_in_per_mtok"))
            + max_tokens(q, request) * float(panel.require("second.price_out_per_mtok"))) / 1e6


def cost_of(response, prompt_text, text, q, request=None):
    """What a response cost: usage.cost when it reports one, else its token counts at the panel's
    prices, else the estimate (never 0, so the cap cannot be bypassed by a missing field)."""
    usage = response.get("usage") or {}
    if usage.get("cost") is not None:
        return float(usage["cost"])
    p_in, p_out = float(panel.require("second.price_in_per_mtok")), float(panel.require("second.price_out_per_mtok"))
    t_in, t_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if t_in is None and t_out is None:
        return estimate(prompt_text, q, request)
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
    return ('{"labels": [<ids from: ' + ", ".join(reply["options"]) + f'; at most {reply["max"]}; '
            'an empty list when none applies>]}')


def prompt(state, q):
    """The one message sent: the question, the item's fields, the reply format. A question with a
    "template" (the evaluation's graders, questions.grader_question) is that text with the item's
    fields put in its {places}; it says how to reply itself."""
    if q.get("template"):
        return q["template"].format_map(state)
    fields = "\n\n".join(f"[{k}]\n{v}" for k, v in state.items())
    return (f"{q['instructions']}\n\n{fields}\n\nReply with only a JSON object, nothing else: "
            f"{reply_text(q['reply'])}")


# ------------------------------------------------------------------- parsing

def _the_object(text):
    """The one top-level JSON object in a reply: the whole reply, or an object with prose before or
    after it (a code fence is prose). ValueError when there is none or more than one."""
    t = (text or "").strip()
    dec, found, i = json.JSONDecoder(), [], 0
    while True:
        i = t.find("{", i)
        if i < 0:
            break
        try:
            obj, end = dec.raw_decode(t, i)
        except ValueError:
            i += 1
            continue
        found.append(obj)
        i = end
    if not found:
        raise ValueError("no JSON object in the reply")
    if len(found) > 1:
        raise ValueError("more than one JSON object in the reply")
    return found[0]


def parse(text, reply):
    """The answer {grade, labels} of a reply, or ValueError. Exactly one top-level JSON object
    (prose or a code fence around it is allowed) with the key the form asks for and a valid value;
    no object, several objects or an invalid one is a failure."""
    obj = _the_object(text)
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
    if form == "fields":
        return {"grade": None, "labels": None, **_fields(obj, reply["fields"])}
    labs = obj.get("labels")
    if not isinstance(labs, list) or not all(isinstance(x, str) for x in labs):
        raise ValueError("labels is not a list of text")
    # ids are read as the choice form reads its answer: without regard to case; an id given twice is
    # one id (a repeat is not unreadable)
    known = {o.lower(): o for o in reply["options"]}
    labs = list(dict.fromkeys(known.get(x.strip().lower(), x.strip()) for x in labs))
    if any(x not in reply["options"] for x in labs) or len(labs) > reply["max"]:
        raise ValueError(f"labels {labs!r} are not at most {reply['max']} of {reply['options']}")
    return {"grade": None, "labels": labs}


def _fields(obj, specs):
    """The named answers of a "fields" reply: {"fields": {name: value}}, and "grade" when a field is
    named grade. A spec is {"max": n} (a whole number 0 to n), {"options": [..]} (one of them) or
    {"options": [..], "many": true} (any of them, none allowed). Every field must be there."""
    out = {}
    for name, spec in specs:
        v = obj.get(name)
        if "max" in spec:
            if isinstance(v, str) and v.strip().isdigit():
                v = int(v.strip())
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= spec["max"]:
                raise ValueError(f"{name} {v!r} is not a whole number from 0 to {spec['max']}")
        elif spec.get("many"):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError(f"{name} is not a list of text")
            v = sorted({x.strip().lower() for x in v} - {"none"})
            if any(x not in spec["options"] for x in v):
                raise ValueError(f"{name} {v!r} are not among {spec['options']}")
        else:
            if not isinstance(v, str) or v.strip().lower() not in spec["options"]:
                raise ValueError(f"{name} {v!r} is not one of {spec['options']}")
            v = v.strip().lower()
        out[name] = v
    return {"fields": out, **({"grade": out["grade"]} if "grade" in out else {})}


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
    conn.commit()


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
        log=print, mdl=None, request=None, price=None):
    """Answers for every item: ({item: answer}, stats). Asks only what the
    store lacks (and what it holds as a failure); with dry_run nothing is
    asked. An item with no usable answer is absent. stats: stored, to_ask,
    estimate_usd, calls, cost_usd, failures (requests that failed), unparsed
    (replies that were not the JSON asked for), seconds. `request` is a step's own
    {"max_tokens", "reasoning"} (the evaluation's graders); `price` a measured US$ per item, used
    for the estimate in place of the upper bound."""
    mdl = mdl or model()
    keyed = [(item, state, Q.sha(state), q, Q.sha(q)) for item, state, q in items]
    keys = sorted({(s, qs) for _, _, s, _, qs in keyed})
    have = asker.stored(conn, mdl, keys)
    # an answer an older version stored as a failure is not an answer: it counts as one attempt
    legacy = {k for k, v in have.items() if v.get("failed")}
    attempts = failure_counts(conn, mdl, keys)
    limit = int(panel.require("second.max_attempts"))
    todo, gave_up = {}, set()
    known = asker.refused_stored(conn, mdl, [k for k in keys if k not in have or k in legacy])
    for item, state, s_sha, q, q_sha in keyed:
        k = (s_sha, q_sha)
        got = have.get(k)
        if got is not None and k not in legacy:
            continue
        if k in known:                                  # refused by the provider in an earlier run
            continue
        if attempts.get(k, 0) + (k in legacy) >= limit:
            gave_up.add(k)
        else:
            todo.setdefault(k, (state, q))
    sent = {k: prompt(state, q) for k, (state, q) in todo.items()}
    # the estimate the cap checks: a measured price per item where there is one (the step's own
    # average in the ledger, else the model's; the graders pass theirs), else the upper bound
    if price is not None:
        basis = f"measured: US${price:.6f} an item"
    elif todo:
        price, basis = api.measured_cost(conn, stage, mdl)
    else:
        basis = "nothing to ask"
    stats = {"stored": sum(1 for k, v in have.items() if k not in legacy), "to_ask": len(todo),
             "estimate_usd": (price * len(todo) if price is not None
                              else sum(estimate(sent[k], todo[k][1], request) for k in todo)),
             "estimate_basis": "estimate " + basis,
             "gave_up": len(gave_up),
             "calls": 0, "cost_usd": 0.0, "failures": [], "refused": [], "unparsed": 0, "seconds": 0.0}
    if todo and not dry_run:
        lock = Lock()

        def one(key):
            state, q = todo[key]
            body = request_body(mdl, q, sent[key], request)
            cost = 0.0
            for _ in range(EMPTY_TRIES):
                try:
                    r = api.post(PATH, body, api_key, timeout=120)
                except api.Refused as exc:
                    exc.items = [key]
                    raise
                try:
                    text = r["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    raise api.Transport(f"reply has no message: {str(r)[:200]}")
                cost += cost_of(r, sent[key], text, q, request)
                if isinstance(text, str) and text.strip():
                    break
            else:
                # an empty reply (HTTP 200, no content) is a provider hiccup, not an unreadable
                # answer: asked again here, and if it stays empty it is a failed request (not an
                # attempt that counts toward second.max_attempts); the next run asks again
                with lock:
                    stats["failures"].append(f"empty reply {EMPTY_TRIES} times in a row: {str(r)[:120]}")
                return cost
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
                           tail=lambda: f", {stats['unparsed']} unreadable", lock=lock)
    skipped = set(stats["refused"]) | set(known)
    if stats["refused"] and not dry_run:        # the run had answers from this model: it was refused for itself
        asker.record_refused(conn, mdl, sorted(set(stats["refused"])),
                             {k[0]: todo[k][0] for k in stats["refused"]},
                             {k[1]: todo[k][1] for k in stats["refused"]}, "refused alone by the provider")
    stats["refused"] = [item for item, _, s_sha, _, q_sha in keyed if (s_sha, q_sha) in skipped]
    asker.REFUSED.extend((stage, item, "") for item in stats["refused"])
    out = {}
    for item, _, s_sha, _, q_sha in keyed:
        a = have.get((s_sha, q_sha))
        if usable(a) and (s_sha, q_sha) not in legacy:
            out[item] = a
    return out, stats


def estimate_line(stage, stats):
    return (asker.estimate_line(stage, stats)
            + (f" ({stats['estimate_basis']})" if stats.get("to_ask") and stats.get("estimate_basis") else "")
            + (f"; {stats['unparsed']} reply(ies) unreadable" if stats["unparsed"] else "")
            + (f"; {len(stats['refused']):,} item(s) refused by the provider and skipped" if stats["refused"] else "")
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
