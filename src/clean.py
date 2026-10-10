#!/usr/bin/env python3
"""Turn appraisal documents into clean, addressable paragraphs and sentences.

Reads  data/raw/pdf/{doc_id}.pdf   the PDF, read by layout (src/pdf_layout.py)
       data/raw/documents.csv
Writes data/raw/pdf_text/{doc_id}.txt            the PDF's text as read, before cleaning
       data/appraisal/text/{doc_id}.txt          the cleaned reference text
       data/appraisal/cache/{doc_id}.layout.json the cached PDF read, keyed on checksums
       data/appraisal/paragraphs.csv  one row per paragraph, offsets into the clean file
       data/appraisal/sentences.csv   one row per sentence, offsets into the same file
       data/appraisal/rejected.csv    every unit dropped, with a reason
       data/appraisal/document_parts.csv     which project each stretch of a document belongs to
       data/reports/clean.txt

Paragraph text is not stored in the table. To read a paragraph, slice its clean
file with char_start:char_end; to see where it came from, slice the PDF text
with raw_start:raw_end.

Only the PDF is read. A document whose PDF is missing, unreadable or a scan
(no text layer) is not cleaned, and the report lists it.
"""
import argparse, csv, hashlib, os, re, sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
from text_rules import TOKEN, ROMAN, annex_match  # noqa: E402
from keys import project_parts, project_at  # noqa: E402

# Which project each stretch of a document belongs to (keys.project_parts):
# one part holding the interface's listing, or a combined document split at
# its data sheets.
DOC_PART_COLS = ["doc_id", "part", "char_start", "char_end", "project_ids", "assigned_by",
                 "listed_project_ids"]
PARA_COLS = ["paragraph_id", "doc_id", "project_ids", "ordinal", "section_path",
             "section_title", "block", "char_start", "char_end", "n_tokens",
             "text_sha256", "page_from", "page_to", "parser",
             "for_model", "list_item", "lead_in_id", "table_id", "raw_start", "raw_end",
             "component_number", "subcomponent_number", "component_mentions"]
REJ_COLS = ["unit_id", "doc_id", "reason", "n_chars"]


# A PDF whose text layer holds fewer characters than this is a scan, not a
# document that can be read by layout.
PDF_MIN_CHARS = 2000
# Footnotes are mostly references ('ITU. 2021. Global Cybersecurity Index
# 2020.'), which a sentence splitter can only cut into nonsense.
SENTENCE_BLOCKS = {"narrative", "annex"}
# What the embedding and the decision model are given: the body prose and
# its footnotes. Front matter and tables stay in the tables for reading, but
# are not cleaned to the standard prose is and add little the prose does not
# say; component names and costs are read from the front matter separately
# (src/components.py), straight from the PDF.
MODEL_BLOCKS = {"narrative", "annex", "footnote"}


def citation_only(text, block):
    """A footnote that is a reference and a link, and nothing else: 'World
    Bank. 2021. Climate Risk Profile. [link]'. It is a bibliography entry, so it
    is kept for reading and left out of what the model is given."""
    if block != "footnote" or "[link]" not in text:
        return False
    return len(re.findall(r"[^\W\d_]{2,}", text.replace("[link]", ""))) < 15


def write_if_changed(path, text):
    """Write a file only when its content differs, so a re-run touches nothing."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(path + ".part", path)


SENT_COLS = ["sentence_id", "paragraph_id", "doc_id", "ordinal", "char_start",
             "char_end", "n_tokens", "text_sha256"]


def parser_version():
    """The PDF reader's identity: pdfplumber's version plus a checksum of the
    code that shapes its output - pdf_layout.py and the text rules it uses - so
    any change to those rules invalidates the cache and is recorded on every
    row, while an edit elsewhere does not cost an hour of re-reading."""
    import pdfplumber
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for name in ("pdf_layout.py", "text_rules.py"):
        with open(os.path.join(here, name), "rb") as fh:
            h.update(fh.read())
    return f"pdf-layout-{h.hexdigest()[:10]}/pdfplumber-{pdfplumber.__version__}"


def _layout_job(args):
    """Worker: read one PDF. Runs in a separate process."""
    doc_id, path = args
    import pdfplumber
    import pdf_layout
    try:
        raw = []
        with pdfplumber.open(path) as pdf:
            text, spans, stats = pdf_layout.extract(pdf, raw)
        return doc_id, text, [list(s) for s in spans], dict(stats), raw[0], ""
    except Exception as exc:                       # a broken PDF is a fallback, not a crash
        return doc_id, "", [], {}, "", f"{type(exc).__name__}: {exc}"


def layout_cached(data, docs, workers):
    """{doc_id: (text, spans, stats, error, raw)} for every document with a PDF;
    raw is the PDF's text as read, before cleaning.

    Each result is cached in data/appraisal/cache/{doc_id}.layout.json, keyed on the PDF's
    SHA-256 and the parser version, so a re-run reads the cache and only a
    changed PDF or a changed parser costs a re-read."""
    import json
    from concurrent.futures import ProcessPoolExecutor
    version = parser_version()
    out, todo = {}, []
    for doc in docs:
        pdf = where(data, "pdf", f"{doc['doc_id']}.pdf")
        if not os.path.exists(pdf):
            continue
        sha = doc.get("pdf_sha256") or hashlib.sha256(open(pdf, "rb").read()).hexdigest()
        cache = where(data, "cache", f"{doc['doc_id']}.layout.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as fh:
                c = json.load(fh)
            if c.get("pdf_sha256") == sha and c.get("parser") == version:
                out[doc["doc_id"]] = (c["text"], c["spans"], c["stats"], c.get("error", ""),
                                      c.get("raw", ""))
                continue
        todo.append((doc["doc_id"], pdf, sha, cache))
    if todo:
        print(f"  reading {len(todo)} PDFs with {workers} workers "
              f"({len(out)} cached)", flush=True)
        meta = {d: (sha, cache) for d, _, sha, cache in todo}
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for n, (did, text, spans, stats, raw, err) in enumerate(
                    ex.map(_layout_job, [(d, p) for d, p, _, _ in todo]), 1):
                sha, cache = meta[did]
                tmp = cache + ".part"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"pdf_sha256": sha, "parser": version, "text": text,
                               "spans": spans, "stats": stats, "error": err,
                               "raw": raw}, fh)
                os.replace(tmp, cache)
                out[did] = (text, spans, stats, err, raw)
                if n % 10 == 0:
                    print(f"    ...{n}/{len(todo)} PDFs read", flush=True)
    return out


def previous_hashes(path):
    """paragraph_id -> (text_sha256, parser) from the last run, if there was one."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["paragraph_id"]: (r["text_sha256"], r.get("parser", ""))
                for r in csv.DictReader(fh)}


def main():
    import sentences
    import components
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-tokens", type=int, default=5)
    ap.add_argument("--workers", type=int, default=3,
                    help="PDF reading processes; the box has four cores")
    args = ap.parse_args()

    clean_dir = where(args.data, "text")
    for d in (clean_dir, where(args.data, "cache"), where(args.data, "reports"),
              where(args.data, "pdf_text")):
        os.makedirs(d, exist_ok=True)

    with open(where(args.data, "documents"), newline="", encoding="utf-8") as fh:
        docs = list(csv.DictReader(fh))
    if args.limit:
        docs = docs[:args.limit]

    layouts = layout_cached(args.data, docs, args.workers)
    version = parser_version()
    paras_path = where(args.data, "appraisal", "paragraphs.csv")
    before = previous_hashes(paras_path)

    paras, rejected, sents, stats = [], [], [], Counter()
    doc_parts = []
    per_doc, not_cleaned = [], []

    for n, doc in enumerate(docs, 1):
        did = doc["doc_id"]
        lay = layouts.get(did)
        if lay is None:
            not_cleaned.append((did, "no PDF"))
            continue
        text, spans, lstats, err, raw = lay
        if err:
            not_cleaned.append((did, f"unreadable: {err}"))
            continue
        if len(re.sub(r"\s", "", raw)) < PDF_MIN_CHARS:
            not_cleaned.append((did, "no text layer (a scan)"))
            continue
        for k, v in lstats.items():
            stats[k] += v
        clean = text
        write_if_changed(where(args.data, "pdf_text", f"{did}.txt"), raw)
        write_if_changed(os.path.join(clean_dir, f"{did}.txt"), clean)

        # Which project each stretch of the document belongs to: a combined
        # appraisal document is split at its data sheets (keys.py).
        parts = project_parts(clean, doc["project_ids"])
        for n, (start, end, owners, how) in enumerate(parts, 1):
            doc_parts.append({"doc_id": did, "part": n, "char_start": start,
                              "char_end": end, "project_ids": owners, "assigned_by": how,
                              "listed_project_ids": doc["project_ids"]})
        kept, doc_paras = 0, []
        tagger = components.Tagger()
        for ordinal, (a, b, path, title, block, p0, p1, meta) in enumerate(spans, 1):
            body = clean[a:b]
            pid = f"{did}:p{ordinal:05d}"
            comp, sub = tagger.see(body, block, path)
            ntok = len(TOKEN.findall(body))
            if ntok < args.min_tokens and block != "heading" \
                    and not (ROMAN.match(body) or annex_match(body)):
                rejected.append({"unit_id": pid, "doc_id": did,
                                 "reason": "too_short", "n_chars": len(body)})
                stats["too_short"] += 1
                continue
            src = meta.get("src") or ["", ""]
            doc_paras.append({
                "paragraph_id": pid, "doc_id": did,
                "project_ids": project_at(parts, a), "ordinal": ordinal,
                "section_path": path, "section_title": title[:120], "block": block,
                "char_start": a, "char_end": b, "n_tokens": ntok,
                "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "page_from": p0, "page_to": p1, "parser": version,
                "for_model": str(block in MODEL_BLOCKS
                                 and not citation_only(body, block)).lower(),
                "list_item": str(bool(meta.get("list_item"))).lower(),
                "lead_in_id": (f"{did}:p{meta['lead_in'] + 1:05d}"
                               if "lead_in" in meta else ""),
                "table_id": f"{did}:t{meta['table']:03d}" if "table" in meta else "",
                "raw_start": src[0], "raw_end": src[1],
                "component_number": comp, "subcomponent_number": sub,
                "component_mentions": "|".join(components.mentions(body)),
            })
            kept += 1
            if block in SENTENCE_BLOCKS:
                for k, (sa, sb) in enumerate(sentences.split(body), 1):
                    st = body[sa:sb]
                    sents.append({
                        "sentence_id": f"{pid}:s{k:03d}", "paragraph_id": pid,
                        "doc_id": did, "ordinal": k,
                        "char_start": a + sa, "char_end": a + sb,
                        "n_tokens": len(TOKEN.findall(st)),
                        "text_sha256": hashlib.sha256(st.encode("utf-8")).hexdigest(),
                    })
        # A list's introducing sentence may itself have been dropped as too
        # short; then the list has no lead-in to point at.
        ids = {p["paragraph_id"] for p in doc_paras}
        for p in doc_paras:
            if p["lead_in_id"] and p["lead_in_id"] not in ids:
                p["lead_in_id"] = ""
                stats["lead_in_dropped"] += 1
        paras.extend(doc_paras)
        # Retention: how much of the PDF's text survives cleaning. Headers,
        # page numbers, contents lines and footnote markers are a few per
        # cent; anything much lower wants looking at.
        rn = len(re.sub(r"\s", "", raw))
        cn = len(re.sub(r"\s", "", clean))
        per_doc.append((did, doc["doc_kind"], len(spans), kept,
                        sum(1 for s in spans if s[4] == "narrative"),
                        100.0 * cn / rn if rn else 0.0))
        if n % 25 == 0:
            print(f"  ...{n}/{len(docs)} documents, {len(paras)} paragraphs", flush=True)

    # A shared regional document serves several projects. If it ever lands in
    # documents.csv once per project again, every one of its paragraphs is
    # emitted several times under the same identifier - which is silent, and
    # poisons any count or join downstream. Fail loudly instead.
    dupes = [k for k, v in Counter(p["paragraph_id"] for p in paras).items() if v > 1]
    if dupes:
        raise SystemExit(
            f"{len(dupes)} duplicate paragraph_id values, e.g. {dupes[:3]}. "
            "documents.csv must hold one row per doc_id, with project_ids pipe-delimited.")

    # The re-parse check. Identifiers are positional, so a paragraph whose text
    # changed under the SAME parser means the read is not reproducible, and
    # every label keyed to that position would point at different words. That
    # stops the run. Under a changed parser, a changed text is expected and is
    # counted, so its effect on labels is visible.
    unstable, changed, gone = [], 0, 0
    now = {p["paragraph_id"]: p for p in paras}
    for pid, (sha, parser) in before.items():
        p = now.get(pid)
        if p is None:
            gone += 1 if not args.limit else 0
        elif p["text_sha256"] != sha:
            if parser == p["parser"]:
                unstable.append(pid)
            else:
                changed += 1
    if unstable:
        raise SystemExit(
            f"{len(unstable)} paragraphs changed text with the parser unchanged, "
            f"e.g. {unstable[:3]}: the PDF read is not reproducible. Nothing written.")

    doc_parts.sort(key=lambda r: (r["doc_id"], r["part"]))
    for name, rows, cols in (("paragraphs.csv", paras, PARA_COLS),
                             ("rejected.csv", rejected, REJ_COLS),
                             ("sentences.csv", sents, SENT_COLS),
                             ("document_parts.csv", doc_parts, DOC_PART_COLS)):
        path = where(args.data, "appraisal", name)
        with open(path + ".part", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        os.replace(path + ".part", path)

    blocks = Counter(p["block"] for p in paras)
    sections = Counter(p["section_path"] for p in paras)
    with open(where(args.data, "reports", "clean.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"parser: {version}\n")
        fh.write(f"documents cleaned: {len(per_doc)}\n")
        fh.write(f"documents not cleaned: {len(not_cleaned)}\n")
        for d, why in not_cleaned:
            fh.write(f"  {d}  {why}\n")
        fh.write(f"paragraphs kept:   {len(paras)}\n")
        fh.write(f"  given to the model (for_model): "
                 f"{sum(p['for_model'] == 'true' for p in paras)}\n")
        fh.write(f"paragraphs dropped:{len(rejected)}\n")
        fh.write(f"sentences:         {len(sents)}\n")
        fh.write(f"since the last run: {changed} paragraphs changed text under a new "
                 f"parser, {gone} no longer exist\n\n")
        fh.write("counters:\n")
        for k in sorted(stats):
            fh.write(f"  {k}: {stats[k]}\n")
        fh.write("\nby block:\n")
        for k, v in blocks.most_common():
            fh.write(f"  {v:>7}  {k}\n")
        fh.write("\ntop section paths:\n")
        for k, v in sections.most_common(30):
            fh.write(f"  {v:>7}  {k or '(none)'}\n")
        fh.write("\ndocuments with no narrative paragraphs:\n")
        for d, kind, total, kept, narr, pct in per_doc:
            if narr == 0:
                fh.write(f"  {d}  kind={kind}  spans={total} kept={kept}\n")
        low = sorted((pct, d, kind) for d, kind, t, kp, nr, pct in per_doc)
        fh.write("\ntext retention against the PDF's own text, lowest 15:\n")
        for pct, d, kind in low[:15]:
            fh.write(f"  {pct:5.1f}%  {d}  {kind}\n")
        if low:
            fh.write(f"  median {sorted(p for p, *_ in low)[len(low)//2]:.1f}%\n")

    print(f"\n{len(paras)} paragraphs, {len(sents)} sentences from {len(per_doc)} documents "
          f"({len(rejected)} dropped; {len(not_cleaned)} documents not cleaned)")
    print("  blocks: " + "  ".join(f"{k}={v}" for k, v in blocks.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
