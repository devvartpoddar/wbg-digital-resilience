#!/usr/bin/env python3
"""How much the cleaned appraisal text depends on three settings: paragraph joining
(pdf_layout LINE_GAP, LOOSE_GAP; re-reads a sample of PDFs), component tags
(components.Tagger) and sentence splitting (sentences MIN_WORDS, ABBREV).

Reads  data/raw/documents.csv, data/raw/pdf/*.pdf, data/appraisal/{paragraphs.csv,text/}
Writes data/reports/sensitivity.md (nothing the pipeline writes is changed)

  PYTHONPATH=src python -m wbg_portfolio.sensitivity [--docs 20] [--skip-paragraphs]
"""
import argparse, hashlib, os, sys
from collections import Counter, defaultdict

from wbg_portfolio.paths import data_root, where  # noqa: E402
from wbg_portfolio import components  # noqa: E402
from wbg_portfolio import pdf_layout  # noqa: E402
from wbg_portfolio import sentences  # noqa: E402
from wbg_portfolio.clean import MODEL_BLOCKS  # noqa: E402

GAPS = [(1.45, 3), (1.30, 3), (1.60, 3), (1.45, 2), (1.45, 4)]   # default first
WORD_MINIMUMS = [3, 2, 4]                                        # default first


from wbg_portfolio.common import read_csv as _read  # noqa: E402


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
             "Nothing the pipeline writes was changed.", ""]
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
