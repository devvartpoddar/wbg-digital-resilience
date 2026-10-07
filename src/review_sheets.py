#!/usr/bin/env python3
"""Write two plain spreadsheets for reading the cleaning by eye.

Reads  data/paragraphs.csv, data/clean/{doc_id}.txt, data/raw/{doc_id}.txt
       data/intermediate/procurement/packages.csv
Writes data/review/paragraphs_s{seed}.xlsx    100 paragraphs: cleaned, split into
                                              sentences, and the raw text beside them
       data/review/packages_s{seed}.xlsx      100 packages, raw next to cleaned

One sheet each, one row per unit, and two empty columns - ok, note - for the
person reading. The files live in data/, so they are never committed.

A file that already exists is left alone: it may hold someone's notes. Pass a
new --seed for a fresh draw, or --force to overwrite.

  python3 src/review_sheets.py
  python3 src/review_sheets.py --n 200 --seed 2

The raw column for a paragraph is found, not stored. The cleaner keeps no map
from cleaned offsets back to the raw file, so this matches the paragraph's
first and last letters against the raw text with everything but letters thrown
away - which survives rejoined hyphens, collapsed whitespace and stripped
footnote numbers. What lies between those two points in the raw file is shown
as it is, including any running header the cleaner removed from the middle.
That is the point: the reader sees what was taken out. A paragraph that cannot
be matched says so, and that is itself worth a look.
"""
import argparse, csv, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root                    # noqa: E402
from clean import CHAR_MAP, decode_raw         # noqa: E402
from review import draw                        # noqa: E402
import sentences                               # noqa: E402

CELL_MAX = 32_000          # Excel refuses a cell over 32,767 characters
ANCHOR = 20                # letters matched at each end of a paragraph; short,
                           # so a header cut into the paragraph rarely splits one

PARA_COLS = [("paragraph_id", 18), ("project_ids", 12), ("page_from", 6),
             ("section_path", 12), ("block", 12), ("n_tokens", 8),
             ("cleaned", 70), ("sentences", 70), ("raw", 70), ("ok", 6), ("note", 30)]
PKG_COLS = [("package_id", 18), ("project_id", 10), ("plan_version", 10),
            ("category", 12), ("borrower_ref", 18), ("description", 45),
            ("description_clean", 45), ("lot", 6), ("phase", 6), ("is_rebid", 7),
            ("is_placeholder", 8), ("method", 10), ("status_raw", 14), ("status", 12),
            ("estimated_amount", 12), ("actual_amount", 12), ("currency", 7),
            ("planned_date", 11), ("carried_from", 14), ("ok", 6), ("note", 30)]


def letters(text):
    """Lowercased letters only, with each one's offset in the original text."""
    out, idx = [], []
    for i, ch in enumerate(text):
        for c in CHAR_MAP.get(ch, ch):
            if c.isalpha():
                out.append(c.lower())
                idx.append(i)
    return "".join(out), idx


def locate_raw(clean_para, raw, raw_letters):
    """The stretch of the raw text this cleaned paragraph came from, or None."""
    norm, idx = raw_letters
    para, _ = letters(clean_para)
    if not para:
        return None
    head = para[:ANCHOR]
    limit = 3 * len(para) + 400
    start = norm.find(head)
    while start >= 0:                      # a short head can repeat: take the
        end = -1                           # first one whose tail follows close
        for k in (ANCHOR, 12, 8):          # behind; shorten the tail in case a
            tail = para[-k:]               # page header was cut into it
            end = norm.find(tail, start + (len(para) - k) // 2)
            if 0 <= end - start <= limit:
                break
            end = -1
        if end >= 0:
            break
        start = norm.find(head, start + 1)
    if start < 0:
        return None
    a, b = idx[start], idx[end + len(tail) - 1] + 1
    # Widen to whole lines: the match is on letters, so a leading number or a
    # trailing figure would otherwise be cut off - and those are what go wrong.
    a = raw.rfind("\n", 0, a) + 1
    b = raw.find("\n", b)
    return raw[a:b if b >= 0 else len(raw)]


def cell_text(value):
    """Excel refuses control characters. A form feed is a page break in the
    raw file, which the reader wants to see, so it gets a visible marker."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    text = str(value if value is not None else "").replace("\f", "\n[page break]\n")
    return ILLEGAL_CHARACTERS_RE.sub("", text)[:CELL_MAX]


def write_sheet(path, cols, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "review"
    ws.append([c for c, _ in cols])
    for r in rows:
        ws.append([cell_text(r.get(c)) for c, _ in cols])
    for i, (_, width) in enumerate(cols, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = width
    for cell in ws[1]:
        cell.font = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrap
    ws.freeze_panes = "A2"
    wb.save(path)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def paragraph_rows(data, n, seed):
    paras = read_csv(os.path.join(data, "paragraphs.csv"))
    picked = draw(paras, n, seed)
    clean_cache, raw_cache = {}, {}
    out, missed = [], 0
    for r in sorted(picked, key=lambda r: r["paragraph_id"]):
        did = r["doc_id"]
        if did not in clean_cache:
            with open(os.path.join(data, "clean", f"{did}.txt"), encoding="utf-8") as fh:
                clean_cache[did] = fh.read()
            raw_path = os.path.join(data, "raw", f"{did}.txt")
            raw = ""
            if os.path.exists(raw_path):
                with open(raw_path, "rb") as fh:
                    raw, _ = decode_raw(fh.read())
            raw_cache[did] = (raw, letters(raw))
        body = clean_cache[did][int(r["char_start"]):int(r["char_end"])]
        raw, raw_letters = raw_cache[did]
        found = locate_raw(body, raw, raw_letters)
        if found is None and r["block"] == "table":
            # A table row read from the PDF joins its cells with " | "; the text
            # rendition flattens the same table its own way, so the two rarely
            # line up. Not a miss - the page number says where to look.
            found = "(table row: compare with the PDF page)"
        elif found is None:
            missed += 1
        cuts = sentences.split(body)
        out.append(dict(r, cleaned=body,
                        sentences="\n".join(f"[{k}] {body[a:b]}" for k, (a, b)
                                             in enumerate(cuts, 1)),
                        raw=found if found is not None else "(not located in the raw file)"))
    return out, missed


def package_rows(data, n, seed):
    path = os.path.join(data, "intermediate", "procurement", "packages.csv")
    if not os.path.exists(path):
        return None
    picked = draw(read_csv(path), n, seed, key="project_id")
    return sorted(picked, key=lambda r: (r["project_id"], r["package_id"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=data_root())
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()

    out_dir = os.path.join(args.data, "review")
    os.makedirs(out_dir, exist_ok=True)
    jobs = [("paragraphs", PARA_COLS), ("packages", PKG_COLS)]
    for name, cols in jobs:
        dest = os.path.join(out_dir, f"{name}_s{args.seed}.xlsx")
        if os.path.exists(dest) and not args.force:
            print(f"kept {dest} (exists; may hold notes - use --seed or --force)")
            continue
        if name == "paragraphs":
            rows, missed = paragraph_rows(args.data, args.n, args.seed)
            extra = f", {missed} not located in raw"
        else:
            rows, extra = package_rows(args.data, args.n, args.seed), ""
            if rows is None:
                print("skipped packages: run the procurement stages first")
                continue
        write_sheet(dest, cols, rows)
        print(f"wrote {dest}: {len(rows)} rows{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
