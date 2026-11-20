"""Activities: the distinct things a project commits to do for one measure.

A project often says one activity several times (in the appraisal document, a
later financing paper, a different section). For every project x measure with
two or more committed, digital sentences, the chat model is shown the whole
group, numbered, and names the distinct activities: each a short label and the
numbers of its sentences. A group of one sentence is one activity, with no call.

  groups     the project x measure groups, from the committed, digital sentences
  parts      what is shown at once: the whole group, or (over
             activities.max_sentences) one part per document, appraisal
             document first; each part is merged on its own, never cut finer
  repair     every sentence must be in exactly one activity: one the reply drops
             becomes an activity of its own, one it repeats stays where it was
             first placed, a number outside the group is ignored; each repair
             is counted in the log
  run        asks what the store lacks (kind activity); item_id is
             project|measure|sha of the part's sorted unit ids, so a changed group asks again
  rows       the `activities` table of a publish: activity_id = md5(project |
             measure | the activity's sorted unit ids), one row per sentence

A group is read from stored answers at every publish; one with no answer yet is
left out and counted, never filled in. The model's reading, not a person's.
"""
import hashlib
import os

from wbg_portfolio import ask
from wbg_portfolio import checks
from wbg_portfolio import chat
from wbg_portfolio import decide
from wbg_portfolio import panel
from wbg_portfolio import questions as Q
from wbg_portfolio.paths import where

KIND = "activity"
LABEL_ID = "activity"
APPRAISAL = "pad"


def max_sentences():
    return int(panel.require("activities.max_sentences"))


def document_kinds(data):
    """{doc_id: doc_kind} of the fetched documents."""
    from wbg_portfolio import labels
    path = where(data, "documents")
    return {r["doc_id"]: r.get("doc_kind", "") for r in labels.rows(path)} if os.path.exists(path) else {}


def groups(conn, corpus, names, projects=None):
    """[(project_id, label_set, [unit_id in reading order])] for every project x
    measure with a committed, digital sentence (a group of one is one
    activity, with no call; see run and rows)."""
    out = []
    for name in names:
        by_project = {}
        for u in decide.committed_digital_units(conn, corpus, [name]):
            for pid in corpus.projects(u):
                if not projects or pid in projects:
                    by_project.setdefault(pid, []).append(u)
        for pid in sorted(by_project):
            out.append((pid, name, sorted(by_project[pid], key=corpus.row_of.get)))
    return out


def parts(units, corpus, kinds, cap):
    """[[unit_id]] shown at once. The whole group when it has at most `cap`
    sentences; else one part per document, appraisal documents first, then
    the others by kind and document id. A part is never cut finer, so one
    document's part may be over the cap."""
    if len(units) <= cap:
        return [units]
    by_doc = {}
    for u in units:
        by_doc.setdefault(corpus.row(u)["doc_id"], []).append(u)
    order = sorted(by_doc, key=lambda d: (kinds.get(d, "") != APPRAISAL, kinds.get(d, ""), d))
    return [by_doc[d] for d in order]


def item_id(project_id, name, units):
    return f"{project_id}|{name}|{Q.sha(sorted(units))[:16]}"


def number_label(text, words=8):
    """A label for a sentence with no better one: its first words."""
    w = " ".join(text.split()).split()
    return " ".join(w[:words]) + (" ..." if len(w) > words else "")


def state(corpus, kinds, units):
    """What the chat model is shown: the numbered sentences, each with its
    section, block, component and the kind of document it is from."""
    lines = []
    for i, u in enumerate(units, 1):
        r = corpus.row(u)
        para = corpus.paras.get(r["paragraph_id"], {})
        comp = corpus.component(u)
        meta = "; ".join(x for x in (f"document: {kinds.get(r['doc_id'], '') or 'unknown'} {r['doc_id']}",
                                     f"section: {para.get('section_title', '')}" if para.get("section_title") else "",
                                     f"block: {para.get('block', '')}" if para.get("block") else "",
                                     f"component: {comp}" if comp else "") if x)
        lines.append(f"{i}. [{meta}]\n{corpus.texts[u]}")
    return {"sentences": "\n\n".join(lines)}


def repair(acts, n):
    """(activities [{label, numbers}], counts) so that each of the sentences 1..n
    is in exactly one activity. counts: missing (put in an activity of their
    own), repeated (kept where first placed), unknown (a number outside 1..n,
    ignored), empty (an activity left with no sentence, dropped). A label of
    '' means none was given."""
    seen, out = set(), []
    counts = {"missing": 0, "repeated": 0, "unknown": 0, "empty": 0}
    for a in acts:
        nums = []
        for k in a["sentences"]:
            if not 1 <= k <= n:
                counts["unknown"] += 1
            elif k in seen:
                counts["repeated"] += 1
            else:
                seen.add(k)
                nums.append(k)
        if nums:
            out.append({"label": a["label"], "numbers": sorted(nums)})
        else:
            counts["empty"] += 1
    for k in range(1, n + 1):
        if k not in seen:
            counts["missing"] += 1
            out.append({"label": "", "numbers": [k]})
    return out, counts


def _stored(conn):
    """{(label_set, item_id): answer} of the activity scores."""
    return {(lset, item): answer for lset, _, item, _, answer, *_ in decide.scores(conn, KIND)}


def _answer(units, got):
    """The stored form of a part's answer, repaired: activities as
    {label, unit_ids}, and the repair counts."""
    acts, counts = repair(got["activities"], len(units))
    return {"activities": [{"label": a["label"], "unit_ids": [units[k - 1] for k in a["numbers"]]}
                           for a in acts], "repair": counts}


def run(conn, data, corpus, names, *, api_key="", dry_run=False, workers=ask.WORKERS, log=print,
        projects=None, **_):
    """The activities of every project x measure group: the chat model asked
    once per part of a group of two or more sentences (what the store lacks).
    A part of one sentence is one activity and needs no call or row (rows()
    makes it). Writes kind activity (label_set the measure, label activity,
    item project|measure|sha). Returns the number of parts written, or None on
    a dry run or a failed request. Nothing is asked while activities.on is false."""
    if not decide.activities_on():
        return 0
    if checks.cannot_estimate(conn, dry_run, log, "activities", ("measure", "measure"), digital=True):
        return None
    kinds, cap = document_kinds(data), max_sentences()
    todo, one = [], 0
    for pid, name, units in groups(conn, corpus, names, projects):
        for part in parts(units, corpus, kinds, cap):
            if len(part) > 1:
                todo.append((pid, name, tuple(part)))
            else:
                one += 1
    qs = {n: Q.activity_question(Q.load(n)[0][0]) for n in names}
    log(f"activities: {len(todo):,} group(s) of two or more sentences to read, {one:,} of one (no call)")
    got, ok = chat.asked(conn, [(k, state(corpus, kinds, list(k[2])), qs[k[1]]) for k in todo],
                           "activities", "activities", api_key, dry_run, workers, log)
    if dry_run:
        return None
    counts, rows_by_set = {"missing": 0, "repeated": 0, "unknown": 0, "empty": 0}, {n: [] for n in names}
    for key in todo:
        if key not in got:
            continue                                            # unreadable or failed: asked again next run
        pid, name, part = key
        a = _answer(list(part), got[key])
        for k, v in a["repair"].items():
            counts[k] += v
        rows_by_set[name].append(checks._row(KIND, name, LABEL_ID, item_id(pid, name, part),
                                            state(corpus, kinds, list(part)), qs[name],
                                            dict(got[key], **a), score=len(a["activities"])))
    for name in names:
        checks.write_scoped(conn, KIND, name, rows_by_set[name], projects or None,
                            "split_part(item_id, '|', 1) = ANY(%s)")
    n = sum(len(r) for r in rows_by_set.values())
    log(f"  {n:,} group(s) written; repaired {counts['missing']} dropped sentence(s), {counts['repeated']} "
        f"repeated, {counts['unknown']} unknown sentence number(s), {counts['empty']} empty activit(ies)")
    return n if ok else None


def rows(conn, data, corpus, names, log=print):
    """The `activities` table's rows from the stored answers and the groups as
    they stand now: one row per sentence of each activity. A group with no
    stored answer yet is left out and counted in the log."""
    if not decide.activities_on():
        return []
    kinds, cap = document_kinds(data), max_sentences()
    stored, out, waiting = _stored(conn), [], 0
    for pid, name, units in groups(conn, corpus, names):
        for part in parts(units, corpus, kinds, cap):
            if len(part) == 1:
                acts = [{"label": number_label(corpus.texts[part[0]]), "unit_ids": [part[0]]}]
            else:
                a = stored.get((name, item_id(pid, name, part)))
                if a is None:
                    waiting += 1
                    continue
                acts = [{"label": x["label"] or number_label(corpus.texts[x["unit_ids"][0]]),
                         "unit_ids": x["unit_ids"]} for x in a["activities"]]
            for act in acts:
                ids = sorted(act["unit_ids"])
                aid = hashlib.md5(f"{pid}|{name}|{'|'.join(ids)}".encode()).hexdigest()
                for u in ids:
                    out.append({"activity_id": aid, "project_id": pid, "label_set": name,
                                "activity_label": act["label"], "unit_id": u, "n_sentences": len(ids)})
    if waiting:
        log(f"  {waiting:,} group(s) of sentences have no activities yet (run the activities stage)")
    return out
