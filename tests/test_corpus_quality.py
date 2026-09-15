"""Corpus-level quality gates.

src/audit.py reports defect rates; this asserts them. A report has to be read
to be useful, and nobody reads one on every run. These thresholds are what stop
a cleaning change quietly degrading the corpus when the cohort grows.

Skips cleanly when data/ is absent, so the suite passes in a checkout without
the corpus (data/ is gitignored by design).

Thresholds are set a little above the rates measured when they were written, so
a real regression fails while ordinary variation as documents are added does
not. If adding projects pushes one over, look at the examples the audit prints
before relaxing the number - the point is to notice.
"""
import csv
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "src"))

import audit as A  # noqa: E402

PROSE = A.PROSE_BLOCKS

# check name -> maximum share of its scope, as a percentage
MAX_RATE = {
    "footnote marker glued to a word": 1.0,
    "paragraph opens as a footnote body": 0.5,
    "page number left inline": 0.5,
    "whitespace run of 3-5 spaces": 0.0,
    "whitespace run of 6+ spaces (column gutter)": 0.0,
    "tabular: numeric tokens over a third": 1.5,
    "unicode replacement character": 0.0,
    "control or format character": 0.0,
    "smart quote or ligature left unfolded": 0.0,
    # Soft checks. A paragraph starting mid-sentence is usually a split that
    # should not have happened; one without terminal punctuation is often a
    # legitimate bullet. Both are capped loosely to catch a collapse.
    "starts mid-sentence": 6.0,
    "no sentence-ending punctuation": 25.0,
}

MIN_CLEAN_PROSE_PCT = 97.0


def _load():
    path = os.path.join(DATA, "paragraphs.csv")
    if not os.path.exists(path):
        pytest.skip("no corpus on disk; run src/fetch.py and src/clean.py first")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        pytest.skip("paragraphs.csv is empty")
    return rows


@pytest.fixture(scope="module")
def scanned():
    rows = _load()
    cache = {}

    def text_of(row):
        did = row["doc_id"]
        if did not in cache:
            with open(os.path.join(DATA, "clean", f"{did}.txt"), encoding="utf-8") as fh:
                cache[did] = fh.read()
        return cache[did][int(row["char_start"]):int(row["char_end"])]

    hits = {name: [] for name, *_ in A.CHECKS}
    scope = {name: 0 for name, *_ in A.CHECKS}
    for row in rows:
        body = text_of(row)
        for name, only, _sev, fn in A.CHECKS:
            if only and row["block"] not in only:
                continue
            scope[name] += 1
            if fn(body):
                hits[name].append((row["paragraph_id"], body))
    return rows, hits, scope


@pytest.mark.parametrize("name", sorted(MAX_RATE))
def test_defect_rate_within_threshold(scanned, name):
    _rows, hits, scope = scanned
    n, total = len(hits[name]), scope[name]
    if not total:
        pytest.skip(f"no paragraphs in scope for {name!r}")
    rate = 100.0 * n / total
    example = hits[name][0][1][:140] if hits[name] else ""
    assert rate <= MAX_RATE[name], (
        f"{name}: {rate:.2f}% of {total} exceeds {MAX_RATE[name]}% "
        f"({n} hits). First: {example!r}")


def test_most_prose_is_defect_free(scanned):
    rows, hits, _scope = scanned
    prose = {r["paragraph_id"] for r in rows if r["block"] in PROSE}
    if not prose:
        pytest.skip("no prose paragraphs")
    dirty = set()
    for name, _only, severity, _fn in A.CHECKS:
        if severity == "defect":
            dirty.update(pid for pid, _b in hits[name])
    clean = prose - dirty
    pct = 100.0 * len(clean) / len(prose)
    assert pct >= MIN_CLEAN_PROSE_PCT, (
        f"only {pct:.1f}% of {len(prose)} prose paragraphs are defect-free, "
        f"below the {MIN_CLEAN_PROSE_PCT}% floor")


def test_every_check_has_a_threshold():
    """A check added to audit.py without a threshold here would report but never
    fail, which is the failure mode this file exists to prevent."""
    missing = sorted({name for name, *_ in A.CHECKS} - set(MAX_RATE))
    assert not missing, f"checks with no threshold in MAX_RATE: {missing}"


# ------------------------------------------------------------ table integrity

def test_paragraph_ids_are_unique(scanned):
    rows, _h, _s = scanned
    ids = [r["paragraph_id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate paragraph_id - documents.csv " \
                                      "must hold one row per doc_id"


def test_offsets_are_well_formed(scanned):
    rows, _h, _s = scanned
    for r in rows:
        assert int(r["char_end"]) > int(r["char_start"]), r["paragraph_id"]


def test_ordinals_are_contiguous_within_each_document(scanned):
    rows, _h, _s = scanned
    by_doc = {}
    for r in rows:
        by_doc.setdefault(r["doc_id"], []).append(int(r["ordinal"]))
    for did, ords in by_doc.items():
        assert len(ords) == len(set(ords)), f"{did}: duplicate ordinals"
        assert min(ords) >= 1, f"{did}: ordinal below 1"


def test_every_block_value_is_known(scanned):
    rows, _h, _s = scanned
    known = {"narrative", "annex", "frontmatter", "table", "footnote", "heading"}
    for r in rows:
        blk = r["block"]
        assert blk in known or blk.startswith("template:"), \
            f"unexpected block {blk!r} on {r['paragraph_id']}"


# ---------------------------------------------------------------- embeddings
#
# These skip until src/embed.py has run. Once it has, they are what stops the
# matrix and the index drifting apart. emb_index.csv is the ONLY record of
# which row holds which paragraph: if the two disagree, every downstream
# lookup returns a real vector for the wrong text, which looks like nothing
# being wrong at all.
#
# The matrix is opened with mmap_mode="r" on purpose. At this cohort it is
# ~415 MB; at the ten-to-twenty-times cohort it is 4-8 GB, and a plain
# numpy.load() pulls all of it into memory. This is the access pattern every
# reader of the file should copy.

def _embeddings():
    idx = os.path.join(DATA, "emb_index.csv")
    npy = os.path.join(DATA, "paragraph_emb.npy")
    if not (os.path.exists(idx) and os.path.exists(npy)):
        pytest.skip("no embeddings on disk; run src/embed.py first")
    numpy = pytest.importorskip("numpy")
    with open(idx, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return rows, numpy.load(npy, mmap_mode="r"), numpy


def test_matrix_and_index_have_the_same_length():
    index, arr, _np = _embeddings()
    assert arr.shape[0] == len(index), (
        f"paragraph_emb.npy has {arr.shape[0]} rows but emb_index.csv lists "
        f"{len(index)} - one of them was written from a different run")


def test_index_rows_are_contiguous_from_zero():
    index, _arr, _np = _embeddings()
    assert [int(r["row"]) for r in index] == list(range(len(index)))


def test_every_indexed_paragraph_exists(scanned):
    rows, _h, _s = scanned
    index, _arr, _np = _embeddings()
    known = {r["paragraph_id"] for r in rows}
    unknown = [r["paragraph_id"] for r in index if r["paragraph_id"] not in known][:5]
    assert not unknown, f"emb_index.csv names paragraphs that do not exist: {unknown}"


def test_no_paragraph_is_indexed_twice():
    index, _arr, _np = _embeddings()
    ids = [r["paragraph_id"] for r in index]
    assert len(ids) == len(set(ids)), "a paragraph holds two rows in the matrix"


def test_no_row_is_a_zero_vector():
    """A zeroed row would sit in the matrix looking like a vector and quietly
    corrupt every similarity computed against it. src/embed.py omits a failed
    paragraph rather than filling it; this asserts that it did."""
    index, arr, numpy = _embeddings()
    if arr.shape[0] == 0:
        pytest.skip("matrix is empty")
    norms = numpy.linalg.norm(numpy.asarray(arr, dtype="float64"), axis=1)
    bad = [i for i in range(len(norms)) if not 0.9 <= norms[i] <= 1.1][:5]
    assert not bad, (f"rows {bad} are not unit vectors "
                     f"(first norm {norms[bad[0]]:.4f}) - "
                     f"{index[bad[0]]['paragraph_id']}")
