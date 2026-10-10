#!/usr/bin/env python3
"""How much the cleaned appraisal text depends on three settings.

Reads  data/raw/documents.csv, data/raw/pdf/*.pdf (paragraph test only),
       data/appraisal/{paragraphs.csv, text/}
Writes data/reports/sensitivity.md

1. Paragraph joining (src/pdf_layout.py LINE_GAP, LOOSE_GAP). A sample of
   documents is read again from the PDF at settings around the default, and
   each setting's model paragraphs are compared with the default's: how many
   paragraphs, and what share of the default's model text sits in paragraphs
   that no longer exist word for word. This re-reads PDFs and is the slow one.
2. Component tags (src/components.py Tagger). Every document is tagged with a
   component's tag closing at the section change (the default) and with it
   running on to the next component heading: how many model paragraphs gain
   or change a tag.
3. Sentences (src/sentences.py MIN_WORDS, ABBREV). Every model paragraph is
   split at the default, with the word minimum at 2 and 4, and without the
   abbreviation list: how many sentences, and how many paragraphs split
   differently.

Nothing the pipeline writes is changed; the report is the only output.

  python src/sensitivity.py              all three, 10 documents for the first
  python src/sensitivity.py --docs 20
  python src/sensitivity.py --skip-paragraphs
"""
import argparse, csv, hashlib, os, sys, time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where  # noqa: E402
import components  # noqa: E402
import pdf_layout  # noqa: E402
import sentences  # noqa: E402
from clean import MODEL_BLOCKS  # noqa: E402

GAPS = [(1.45, 3), (1.30, 3), (1.60, 3), (1.45, 2), (1.45, 4)]   # default first
WORD_MINIMUMS = [3, 2, 4]                                        # default first


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def sample_documents(data, n):
    """n appraisal documents, chosen by a hash of their id so the same store
    always gives the same sample: PADs and additional financings first, since
    they carry most of the model text."""
    docs = [d for d in _read(where(data, "documents"))
            if os.path.exists(where(data, "pdf", f"{d['doc_id']}.pdf"))]
    def order(d):
        return (d["doc_kind"] not in ("pad", "additional_financing"),
                hashlib.sha256(d["doc_id"].encode()).hexdigest())
    return [d["doc_id"] for d in sorted(docs, key=order)[:n]]


def _model_paragraphs(text, spans):
    return [text[a:b] for a, b, _p, _t, block, *_ in spans if block in MODEL_BLOCKS]


def paragraph_gaps(data, doc_ids, gaps=GAPS):
    """{(line_gap, loose_gap): (paragraphs, share of default model tokens in
    paragraphs that changed)} over the sample."""
    import pdfplumber
    keep = (pdf_layout.LINE_GAP, pdf_layout.LOOSE_GAP)
    paras = {g: [] for g in gaps}
    try:
        for did in doc_ids:
            with pdfplumber.open(where(data, "pdf", f"{did}.pdf")) as pdf:
                for g in gaps:
                    pdf_layout.LINE_GAP, pdf_layout.LOOSE_GAP = g
                    text, spans, _stats = pdf_layout.extract(pdf)
                    paras[g] += [(did, p) for p in _model_paragraphs(text, spans)]
    finally:
        pdf_layout.LINE_GAP, pdf_layout.LOOSE_GAP = keep
    base = paras[gaps[0]]
    base_tokens = sum(len(p.split()) for _, p in base) or 1
    out = {}
    for g in gaps:
        here = Counter(paras[g])
        lost = 0
        for key in base:
            if here[key]:
                here[key] -= 1
            else:
                lost += len(key[1].split())
        out[g] = (len(paras[g]), lost / base_tokens)
    return out


def _documents_text(data):
    rows = defaultdict(list)
    for r in _read(where(data, "appraisal", "paragraphs.csv")):
        rows[r["doc_id"]].append(r)
    for did in sorted(rows):
        path = where(data, "text", f"{did}.txt")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        yield did, sorted(rows[did], key=lambda r: int(r["ordinal"])), text


def component_closing(data):
    """Model paragraphs tagged with a component when tags close at the section
    change (default) and when they run on; and how many differ."""
    tagged = Counter()
    for did, rows, text in _documents_text(data):
        a, b = components.Tagger(), components.Tagger(close_at_section=False)
        for r in rows:
            body = text[int(r["char_start"]):int(r["char_end"])]
            ta = a.see(body, r["block"], r["section_path"])
            tb = b.see(body, r["block"], r["section_path"])
            if r["for_model"] != "true":
                continue
            tagged["paragraphs"] += 1
            tagged["tagged_default"] += bool(ta[0])
            tagged["tagged_run_on"] += bool(tb[0])
            tagged["differ"] += ta != tb
    return tagged


def sentence_rules(data, minimums=WORD_MINIMUMS):
    """{label: (sentences, paragraphs split differently from the default)}."""
    keep_min, keep_abbrev = sentences.MIN_WORDS, sentences.ABBREV
    texts = [text[int(r["char_start"]):int(r["char_end"])]
             for _did, rows, text in _documents_text(data) for r in rows
             if r["for_model"] == "true" and r["block"] in ("narrative", "annex")]
    variants = [(f"at least {m} words" + (" (default)" if i == 0 else ""), m, keep_abbrev)
                for i, m in enumerate(minimums)]
    variants.append(("no abbreviation list", minimums[0], set()))
    results, base = {}, None
    try:
        for label, m, abbrev in variants:
            sentences.MIN_WORDS, sentences.ABBREV = m, abbrev
            splits = [sentences.split(t) for t in texts]
            if base is None:
                base = splits
            results[label] = (sum(len(s) for s in splits),
                              sum(1 for x, y in zip(base, splits) if x != y))
    finally:
        sentences.MIN_WORDS, sentences.ABBREV = keep_min, keep_abbrev
    return results, len(texts)


def report(data, n_docs, skip_paragraphs=False):
    lines = ["# Sensitivity of the cleaned text to three settings", "",
             f"Run {time.strftime('%Y-%m-%d %H:%M')}. Nothing the pipeline writes was changed.", ""]
    if not skip_paragraphs:
        docs = sample_documents(data, n_docs)
        res = paragraph_gaps(data, docs)
        lines += ["## 1. Paragraph joining (pdf_layout.LINE_GAP, LOOSE_GAP)", "",
                  f"{len(docs)} documents read again from the PDF: {', '.join(docs)}.", "",
                  "| line gap / loose gap | model paragraphs | share of default model text in paragraphs that changed |",
                  "|---|---|---|"]
        for g, (n, share) in res.items():
            tag = " (default)" if g == GAPS[0] else ""
            lines.append(f"| {g[0]} / {g[1]}{tag} | {n:,} | {100 * share:.1f}% |")
        lines.append("")
    t = component_closing(data)
    lines += ["## 2. Component tags (closing at the section change)", "",
              f"Model paragraphs: {t['paragraphs']:,}. Under a component with tags closing at "
              f"the section change (default): {t['tagged_default']:,}; with tags running on "
              f"to the next component heading: {t['tagged_run_on']:,}; paragraphs whose tag "
              f"differs: {t['differ']:,}.", ""]
    res, n = sentence_rules(data)
    lines += ["## 3. Sentences (sentences.MIN_WORDS, ABBREV)", "",
              f"{n:,} narrative and annex model paragraphs.", "",
              "| rule | sentences | paragraphs split differently from the default |",
              "|---|---|---|"]
    for label, (s, d) in res.items():
        lines.append(f"| {label} | {s:,} | {d:,} |")
    lines.append("")
    out = where(data, "reports", "sensitivity.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--docs", type=int, default=10,
                    help="documents to re-read for the paragraph test")
    ap.add_argument("--skip-paragraphs", action="store_true",
                    help="skip the slow paragraph test (it re-reads PDFs)")
    args = ap.parse_args()
    print(f"wrote {report(args.data, args.docs, args.skip_paragraphs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
