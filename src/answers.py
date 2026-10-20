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
"""
import argparse, glob, hashlib, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402


def _slug(model):
    return model.replace("/", "__").replace(":", "_")


def root(data, model):
    return where(data, "classify", "answers", _slug(model))


def key(model, state, question):
    return hashlib.sha256(json.dumps({"model": model, "state": state, "question": question},
                                     sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


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


def spend_cap():
    try:
        return float(os.environ["WBG_SPEND_CAP_USD"])
    except (KeyError, ValueError):
        return None


class CapReached(SystemExit):
    """The process's spend cap would be or was passed."""


def ask(model, requests, data, *, api_key="", log=print, workers=None, dry_run=False):
    """Answers for every request, asking only what is not stored.
    Returns (answers, stats): answers = [{qid: entry}] per request (None for
    a request with a missing answer when nothing could be asked), stats =
    {"to_ask": calls planned, "chars": request characters, "calls", "cost_usd",
    "failures": [(call sha, error)], "seconds"}."""
    import classify
    root_ = root(data, model)
    keys = [{qid: key(model, state, q) for qid, q in qs.items()} for state, qs in requests]
    todo = {}
    for (state, qs), ks in zip(requests, keys):
        missing = {qid: q for qid, q in qs.items() if read(root_, ks[qid]) is None}
        for body in bodies_for(state, missing, model):
            todo.setdefault(classify.request_sha(body), body)   # the same request asks once
    chars = sum(len(json.dumps(b, ensure_ascii=False)) for b in todo.values())
    stats = {"to_ask": len(todo), "chars": chars, "calls": 0, "cost_usd": 0.0, "failures": [],
             "seconds": 0.0,
             "estimate_usd": chars / 4 / 1e6 * classify.PRICE_PER_MTOK}
    cap = spend_cap()
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
            for qid, entry in entries.items():
                write(root_, key(model, body["state"], body["questions"][qid]), entry)
            return float((response.get("usage") or {}).get("cost") or 0)

        with ThreadPoolExecutor(max_workers=workers or classify.WORKERS) as pool:
            futures = {pool.submit(one, sha, body): sha for sha, body in todo.items()}
            for n, f in enumerate(as_completed(futures), 1):
                try:
                    paid = f.result()
                    stats["cost_usd"] += paid
                    stats["calls"] += 1
                    _spent_here[0] += paid
                    if cap is not None and _spent_here[0] > cap:
                        pool.shutdown(wait=True, cancel_futures=True)
                        raise CapReached(f"answers: spend cap US${cap:.2f} reached after "
                                         f"{stats['calls']:,} calls; the answers so far are "
                                         f"stored")
                except classify.TransportError as exc:
                    stats["failures"].append((futures[f], str(exc)[:200]))
                except classify.Refused as exc:
                    pool.shutdown(wait=True, cancel_futures=True)
                    raise SystemExit(f"answers: refused, stopping ({stats['calls']:,} stored so "
                                     f"far; run again to resume): {exc}")
                if n % 500 == 0 or n == len(todo):
                    log(f"  {n:,}/{len(todo):,} asked, {stats['calls']:,} stored, "
                        f"{len(stats['failures'])} failed, US${stats['cost_usd']:.3f}")
        stats["seconds"] = time.time() - t0
    answers = []
    for ks in keys:
        got = {qid: read(root_, sha) for qid, sha in ks.items()}
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
