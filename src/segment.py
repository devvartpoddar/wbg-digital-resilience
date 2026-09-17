#!/usr/bin/env python3
"""Stage 4: split prose paragraphs into clauses.

Reads  data/paragraphs.csv
       data/clean/{doc_id}.txt                (the cleaned text the offsets index)
Writes data/intermediate/prepared/clauses.csv     schema: docs/data-model.md
       data/intermediate/prepared/segment_manifest.json
       data/intermediate/prepared/segment_progress.json   (resume state)
       data/segment_report.txt

  python3 src/segment.py --dry-run        # counts, no parse
  python3 src/segment.py --limit 20       # smoke test
  python3 src/segment.py                  # the prose corpus

WHY A PARAGRAPH IS NOT A UNIT. One bullet routinely states five measures. A
single annex paragraph in the corpus (34338772:p00680) names, in one sentence,
five distinct techniques: adopting an international adaptation standard,
choosing between three transmission media, deploying hazard-resistant cable,
protecting the passive plant, and raising tower elevations. Attribution and
modality both need a unit small enough to hold one proposition, so the
paragraph is cut into clauses. The paragraph stays the unit for detection; the
clause is the unit for attribution and modality; the two are not reconciled
(methodology Stage 4).

THE BOUNDARY CONDITIONS are the ones named in methodology Stage 4 and the
values in business-rules A1.11: sentence, semicolon, coord_conj, subord_conj,
relative, verb_subtree. ClearNLP-style labels throughout - this model emits
`dobj` and `nsubjpass`, not `obj` and `nsubj:pass`, and a pattern written
against Universal Dependencies matches nothing and raises nothing.

WHAT IS NOT DONE. No noun-phrase splitting. A coordinated object noun phrase
still bundles: one verb governing a cable type and then a list of passive plant
is a single clause holding two measures. That is the known limit, it is
measured in the probe report, and `clause_measure` keys on clause_id +
measure_id, so it is multi-label by design. Splitting noun phrases would
inherit the parser's error rate for a marginal gain.

NO CLAUSE TEXT IS QUOTED ANYWHERE IN THIS FILE. No document text is committed
in this repository; a clause resolves from data/clean/ through its offsets.

IDEMPOTENT AND RESUMABLE. A clause_id embeds the splitter version, so a
splitter change cannot silently repoint existing clause labels at different
text. Each document's segmentation is keyed on a checksum of its paragraphs'
text_sha256 plus every version stamp; a document whose checksum is unchanged is
not re-parsed. The assembled table is checkpointed after every document, so an
interrupted run resumes rather than restarting. The final file is swapped in
atomically.

NO TEXT COLUMN. Offsets are into the parent paragraph, so a clause resolves
back to data/clean/{doc_id}.txt at read time. Nothing here is committed text.
"""
import argparse, csv, hashlib, json, os, subprocess, sys, time
from collections import Counter

import spacy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bumping either of these changes clause boundaries, which moves every clause
# label in the repository. SPLITTER_VERSION rides in clause_id for exactly that
# reason; PARSER_VERSION does not, so a parser change under a fixed splitter
# version is a bug - bump the splitter version too.
SPLITTER_VERSION = "split-1"

# The source of this module, hashed. SPLITTER_VERSION is the contract for
# "the boundaries moved" (business-rule 25), but a re-run that resumes is keyed
# on content checksums, and the code is content too: an edit to a boundary
# condition that forgets to bump the version would silently reuse the previous
# run's clauses. Folding the source in makes the resume honest either way.
SOURCE_SHA = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()

# src/clean.py does not stamp a clean_version on documents.csv or paragraphs.csv,
# so there is nothing to inherit. This names the first version of the cleaning
# rules so that business-rule 14 (clean_version identical across documents,
# paragraphs, clauses and rejected_units) has a value to check against once the
# cleaning stage starts writing one.
CLEAN_VERSION = "clean-1"

# The prose set the clause pool is drawn from. docs/business-rules.md A1.10
# names the column `block_type` with values prose/heading/bullet/table_cell/
# footnote; paragraphs.csv on disk carries `block` with values narrative/annex/
# heading/table/footnote/template:*. Both names are accepted below so this
# module keeps working when the schema and the table are reconciled.
PROSE_BLOCKS = ("narrative", "annex")

COLS = ["clause_id", "paragraph_id", "ordinal", "char_start", "char_end",
        "text_sha256", "clean_version", "split_rule", "splitter_version",
        "parser_version"]

# Which rule names a boundary when two conditions land on the same token. The
# more specific condition wins: a semicolon that is also followed by a
# coordinating conjunction is a semicolon boundary, and `sentence` is only used
# where no further split applied (business-rules A1.11 says so in as many words).
RULE_PRIORITY = {"sentence": 0, "verb_subtree": 1, "relative": 2,
                 "subord_conj": 3, "coord_conj": 4, "semicolon": 5}

RELATIVE_TAGS = ("WDT", "WP", "WP$", "WRB")
RELATIVE_DEPS = ("nsubj", "nsubjpass", "dobj", "pobj", "pcomp", "advmod",
                 "npadvmod", "attr", "acomp", "appos")
VERB_SUBTREE_DEPS = ("advcl", "xcomp", "ccomp", "acl", "relcl")
VERB_POS = ("VERB", "AUX")


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "nogit"


def load_parser(model, disable_ner=True):
    """The pinned parser. Returns (nlp, parser_version, settings)."""
    nlp = spacy.load(model)
    disabled = []
    if disable_ner and "ner" in nlp.pipe_names:
        # Named entities feed no boundary condition and the component is the
        # most expensive one. Disabling it changes no token, no tag and no
        # dependency, so the parse is unchanged.
        nlp.disable_pipe("ner")
        disabled.append("ner")
    version = f"spacy-{spacy.__version__}/{model}-{nlp.meta.get('version', '?')}"
    settings = "disable=" + (",".join(disabled) if disabled else "none")
    return nlp, version, settings


# ------------------------------------------------------------------ boundaries

def _next_conjunct(cc):
    """The conjunct a coordinating conjunction introduces, or None.

    `cc` attaches to the FIRST conjunct; the one it introduces is a `conj`
    child of the same head at a higher index.
    """
    later = [c for c in cc.head.conjuncts if c.i > cc.i]
    return min(later, key=lambda c: c.i) if later else None


def _is_verb_headed(token):
    return token.pos_ in VERB_POS or any(t.pos_ == "VERB" for t in token.subtree)


def find_boundaries(doc):
    """token index -> split_rule, for every boundary in this paragraph.

    Index i means "a clause starts at token i". Token 0 is never a boundary: it
    is the paragraph's own start, which business-rules A1.11 files under
    `sentence`.
    """
    found = {}

    def put(i, rule):
        if i <= 0 or i >= len(doc):
            return
        old = found.get(i)
        if old is None or RULE_PRIORITY[rule] > RULE_PRIORITY[old]:
            found[i] = rule

    for sent in doc.sents:
        put(sent.start, "sentence")

    # Semicolons first. A semicolon that is followed by the conjunction
    # continuing the list ("... x; and y ...") splits before the conjunction, so
    # "and" opens the clause it introduces instead of trailing the previous one.
    # Those positions are marked before the coordinating-conjunction rule runs,
    # which is what stops that rule from adding a second boundary immediately
    # after.
    for t in doc:
        if t.is_space or t.text != ";":
            continue
        j = t.i + 1
        while j < len(doc) and (doc[j].is_punct or doc[j].is_space):
            j += 1
        put(j, "semicolon")

    for t in doc:
        if t.is_space:
            continue

        if t.text == ";":
            continue

        if t.dep_ == "cc" and t.head.pos_ in VERB_POS:
            if t.i in found:
                continue                  # the semicolon already opened this clause
            conj = _next_conjunct(t)
            if conj is not None and _is_verb_headed(conj):
                # The boundary is the conjunction itself, not the conjunct, so
                # "and" opens the clause it introduces rather than trailing the
                # one before it.
                put(t.i, "coord_conj")
            continue

        if t.dep_ == "mark" and t.tag_ != "TO":
            if t.i > t.head.i:
                put(t.i, "subord_conj")
            else:
                # The subordinate clause leads ("Because X, Y"), so the boundary
                # is the token after it - past the comma that closes it.
                j = max(x.i for x in t.head.subtree) + 1
                while j < len(doc) and (doc[j].is_punct or doc[j].is_space):
                    j += 1
                put(j, "subord_conj")
            continue

        if t.tag_ in RELATIVE_TAGS and t.dep_ in RELATIVE_DEPS:
            put(t.i, "relative")
            continue

        if t.pos_ in VERB_POS and t.dep_ in VERB_SUBTREE_DEPS:
            # A clause already opened by a subordinating conjunction or a
            # relative pronoun is that rule's boundary. Firing here as well
            # would cut the clause again immediately after its own head - a
            # subordinate verb is an `advcl` and a relative verb a `relcl`, so
            # without this guard "the cable that carries the traffic" splits
            # into "the cable" / "that" / "carries the traffic".
            if t.dep_ == "relcl":
                continue
            if any(x.dep_ == "mark" and x.tag_ != "TO" for x in t.subtree):
                continue
            if any(x.tag_ in RELATIVE_TAGS and x.dep_ in RELATIVE_DEPS
                   for x in t.subtree):
                continue
            # An infinitival or participial clause heads its own subtree and
            # states its own proposition. Where a "to" marks it, the boundary
            # goes before the "to" so the clause reads as a unit.
            if t.i > 0 and doc[t.i - 1].tag_ == "TO" and doc[t.i - 1].dep_ == "aux":
                put(t.i - 1, "verb_subtree")
            else:
                put(t.i, "verb_subtree")
            continue

    return found


def _has_alnum(text):
    return any(ch.isalnum() for ch in text)


def _has_letter(text):
    return any(ch.isalpha() for ch in text)


def clause_spans(doc, boundaries):
    """[(char_start, char_end, rule)] relative to the paragraph, in order.

    A span is trimmed of leading and trailing tokens carrying no alphanumeric
    character, so a bullet glyph or a stranded comma never opens a clause, and
    a span with no letter anywhere in it is not a proposition and is dropped -
    a numbered list's "1." parses as its own token and would otherwise become a
    one-character clause. Offsets stay contiguous into the paragraph text, so
    every clause still resolves back to the corpus.
    """
    starts = sorted(boundaries)
    if not starts or starts[0] != 0:
        starts = [0] + starts
    spans = []
    for k, lo in enumerate(starts):
        hi = starts[k + 1] if k + 1 < len(starts) else len(doc)
        first, last = lo, hi
        while first < last and not _has_alnum(doc[first].text):
            first += 1
        while last > first and not _has_alnum(doc[last - 1].text):
            last -= 1
        if first >= last:
            continue                      # nothing but punctuation; not a unit
        if not any(_has_letter(doc[i].text) for i in range(first, last)):
            continue                      # a list marker or a bare number
        rule = "sentence" if k == 0 else boundaries[lo]
        spans.append((doc[first].idx, doc[last - 1].idx + len(doc[last - 1].text), rule))
    return spans


def segment_text(nlp, text):
    """The whole stage-4 split for one paragraph. Pure; no I/O."""
    doc = nlp(text)
    return clause_spans(doc, find_boundaries(doc))


def clause_id(paragraph_id, ordinal, splitter_version=SPLITTER_VERSION):
    return f"{paragraph_id}:c{ordinal:02d}:{splitter_version}"


# ------------------------------------------------------------------------ I/O

def read_paragraphs(path):
    """[(paragraph_id, doc_id, block, char_start, char_end, text_sha256)] in
    table order, plus the per-document grouping used to batch the parse."""
    rows, by_doc = [], {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            block = r.get("block_type") or r.get("block") or ""
            rec = (r["paragraph_id"], r["doc_id"], block,
                   int(r["char_start"]), int(r["char_end"]), r["text_sha256"])
            rows.append(rec)
            by_doc.setdefault(r["doc_id"], []).append(rec)
    return rows, by_doc


def read_clean_text(data, doc_id, cache):
    if doc_id not in cache:
        with open(os.path.join(data, "clean", f"{doc_id}.txt"), encoding="utf-8") as fh:
            cache[doc_id] = fh.read()
    return cache[doc_id]


def doc_checksum(paragraphs, parser_version, splitter_version, clean_version):
    """What a document's segmentation depends on. Unchanged -> not re-parsed."""
    h = hashlib.sha256()
    for pid, _did, _blk, _a, _b, sha in paragraphs:
        h.update(f"{pid}\x1f{sha}\x1e".encode("utf-8"))
    h.update(f"{parser_version}|{splitter_version}|{clean_version}".encode("utf-8"))
    h.update(SOURCE_SHA.encode("utf-8"))
    return h.hexdigest()


def build_rows(nlp, data, doc_id, paragraphs, cache, parser_version):
    """Rows for one document, in paragraph and clause order."""
    text_of = read_clean_text(data, doc_id, cache)
    rows = []
    texts = [text_of[a:b] for _pid, _did, _blk, a, b, _sha in paragraphs]
    for (pid, _did, _blk, _a, _b, _sha), para_text in zip(paragraphs, texts):
        for n, (cs, ce, rule) in enumerate(segment_text(nlp, para_text), start=1):
            rows.append({
                "clause_id": clause_id(pid, n),
                "paragraph_id": pid,
                "ordinal": n,
                "char_start": cs,
                "char_end": ce,
                "text_sha256": hashlib.sha256(
                    para_text[cs:ce].encode("utf-8")).hexdigest(),
                "clean_version": CLEAN_VERSION,
                "split_rule": rule,
                "splitter_version": SPLITTER_VERSION,
                "parser_version": parser_version,
            })
    return rows


def load_progress(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"docs": {}, "run_id": ""}


def load_checkpoint(path):
    """Rows from a previous, possibly interrupted, run, grouped by paragraph."""
    if not os.path.exists(path):
        return {}
    by_para = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            by_para.setdefault(r["paragraph_id"], []).append(r)
    return by_para


def write_rows(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--model", default="en_core_web_sm")
    ap.add_argument("--out-dir",
                    default=os.path.join(ROOT, "data", "intermediate", "prepared"))
    ap.add_argument("--limit", type=int, default=0, help="documents to process")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restart", action="store_true", help="ignore resume state")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    rows, by_doc = read_paragraphs(os.path.join(args.data, "paragraphs.csv"))
    prose = [r for r in rows if r[2] in PROSE_BLOCKS]
    order = [d for d in by_doc if any(r[2] in PROSE_BLOCKS for r in by_doc[d])]
    if args.limit:
        order = order[:args.limit]
    print(f"paragraphs {len(rows):,}   prose (narrative+annex) {len(prose):,}"
          f"   documents with prose {len({r[1] for r in prose}):,}"
          f"   processing {len(order)}")
    if args.dry_run:
        return 0

    nlp, parser_version, settings = load_parser(args.model)
    print(f"parser {parser_version}  settings {settings}")

    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, "clauses.csv")
    part_path = out_path + ".part"
    prog_path = os.path.join(args.out_dir, "segment_progress.json")
    man_path = os.path.join(args.out_dir, "segment_manifest.json")

    run_id = args.run_id or time.strftime("%Y%m%dT%H%M", time.gmtime()) + "-" + git_sha()
    if args.restart:
        progress = {"docs": {}, "run_id": run_id}
        done = {}
    else:
        progress = load_progress(prog_path)
        progress["run_id"] = run_id
        # The finished table is the reuse source; the .part file only exists
        # when a previous run was interrupted, and is what an interrupted run
        # resumes from.
        done = load_checkpoint(out_path if os.path.exists(out_path) else part_path)
    cache = {}

    assembled, n_reused, n_parsed = [], 0, 0
    for i, doc_id in enumerate(order, start=1):
        paras = [r for r in by_doc[doc_id] if r[2] in PROSE_BLOCKS]
        ck = doc_checksum(paras, parser_version, SPLITTER_VERSION, CLEAN_VERSION)
        prev = progress["docs"].get(doc_id)
        reuse = None
        if prev and prev.get("checksum") == ck and \
                all(p[0] in done for p in paras):
            reuse = [r for p in paras for r in done[p[0]]]
        if reuse is not None:
            assembled.extend(reuse)
            n_reused += 1
            continue
        new = build_rows(nlp, args.data, doc_id, paras, cache, parser_version)
        assembled.extend(new)
        n_parsed += 1
        progress["docs"][doc_id] = {"checksum": ck, "n_clauses": len(new),
                                    "run_id": run_id}
        write_rows(part_path, assembled)
        with open(prog_path, "w", encoding="utf-8") as pf:
            json.dump(progress, pf, indent=1, sort_keys=True)
        if i % 10 == 0 or i == len(order):
            print(f"  ...{i}/{len(order)} documents, {len(assembled):,} clauses",
                  flush=True)

    write_rows(out_path, assembled)
    if os.path.exists(part_path):
        os.remove(part_path)

    write_report(args.data, assembled, rows, prose, parser_version, settings,
                 run_id, n_reused, n_parsed, len(order))
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump({
            "stage": "segment",
            "input_table": "data/paragraphs.csv",
            "input_rows": len(rows),
            "prose_rows": len(prose),
            "documents_processed": len(order),
            "documents_reused": n_reused,
            "documents_parsed": n_parsed,
            "clauses": len(assembled),
            "parser_version": parser_version,
            "parser_settings": settings,
            "splitter_version": SPLITTER_VERSION,
            "clean_version": CLEAN_VERSION,
            "blocks": list(PROSE_BLOCKS),
            "source_sha256": SOURCE_SHA,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": run_id,
        }, fh, indent=2, sort_keys=True)
    print(f"wrote {out_path}  ({len(assembled):,} clauses, "
          f"{n_reused} documents reused, {n_parsed} parsed)")
    print(f"wrote {man_path}")
    print(f"wrote {os.path.join(args.data, 'segment_report.txt')}")
    return 0


def write_report(data, all_rows, rows, prose, parser_version, settings, run_id,
                 n_reused, n_parsed, n_docs):
    counts, rules, by_para = Counter(), Counter(), {}
    for r in all_rows:
        by_para[r["paragraph_id"]] = by_para.get(r["paragraph_id"], 0) + 1
        rules[r["split_rule"]] += 1
    for n in by_para.values():
        counts[n] += 1
    out = []
    out.append("stage 4 - clause segmentation")
    out.append("=" * 78)
    out.append(f"run_id            {run_id}")
    out.append(f"parser_version    {parser_version}")
    out.append(f"parser_settings   {settings}")
    out.append(f"splitter_version  {SPLITTER_VERSION}")
    out.append(f"clean_version     {CLEAN_VERSION}")
    out.append(f"blocks            {', '.join(PROSE_BLOCKS)}")
    out.append("")
    out.append(f"paragraphs in table                 {len(rows):>9,}")
    out.append(f"prose paragraphs                    {len(prose):>9,}")
    out.append(f"documents processed                 {n_docs:>9,}")
    out.append(f"  reused from an earlier run        {n_reused:>9,}")
    out.append(f"  parsed this run                   {n_parsed:>9,}")
    out.append(f"paragraphs yielding >=1 clause      {len(by_para):>9,}")
    out.append(f"clauses                             {len(all_rows):>9,}")
    out.append(f"clauses per segmented paragraph    "
               f"{len(all_rows) / max(len(by_para), 1):>9.2f}")
    out.append("")
    out.append("clause count per paragraph (paragraphs with that count)")
    out.append("-" * 78)
    total = sum(counts.values()) or 1
    run = 0
    for n in sorted(counts):
        run += counts[n]
        out.append(f"  {n:>3} clauses  {counts[n]:>8,}  {100.0 * counts[n] / total:>5.1f}%"
                   f"   cumulative {100.0 * run / total:>5.1f}%")
    out.append("")
    out.append("split_rule tally (the boundary that opened each clause)")
    out.append("-" * 78)
    for rule, n in rules.most_common():
        out.append(f"  {rule:<14}{n:>9,}  {100.0 * n / max(len(all_rows), 1):>5.1f}%")
    out.append("")
    out.append("`sentence` is the first clause of a sentence where no further")
    out.append("condition applied, so it is not a failure of splitting.")
    out.append("")
    out.append("NO TEXT IS WRITTEN HERE. A clause resolves back to")
    out.append("data/clean/{doc_id}.txt at paragraph.char_start + clause.char_start.")
    body = "\n".join(out)
    with open(os.path.join(data, "segment_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(body + "\n")


if __name__ == "__main__":
    sys.exit(main())
