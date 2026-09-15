"""Unit tests for the embedding run, on a synthetic corpus, with no network.

Two rules shape this file.

The first is the same one that governs tests/test_clean_rules.py: the paragraph
text below is invented for this test. Real document text is permitted in
exactly one committed place (inputs/labels/samples/), and tests/ is not it.

The second is specific to this stage. An autouse fixture replaces the requests
module inside src/embed.py with an object that raises on any attribute access,
so a test that reaches the network fails immediately and says so. Everything
here therefore runs against a stub embed_texts(). That is not a convenience -
it IS the assertion that the transport seam holds. When the asynchronous batch
endpoint is added for a larger cohort it replaces exactly one function, and
this suite is what proves nothing else was entangled with it.
"""
import csv
import hashlib
import json
import os
import sys

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import embed as E  # noqa: E402

DIM = 8
MODEL = "openai/text-embedding-3-large"
FAKE_KEY = "test-key-not-a-credential"

DOCS = {
    "D001": [
        "The towers were sited above the recorded flood line in every district.",
        "Backhaul follows the trunk road, which is itself raised in two places.",
        "Boilerplate that appears in more than one document.",
    ],
    "D002": [
        "Boilerplate that appears in more than one document.",
        "A separate operation extends the regulatory framework to landing stations.",
        "Redundant power is provided at each of the twelve aggregation points.",
    ],
}


# ----------------------------------------------------------------- the seam

class _NoNetwork:
    """Anything reaching for the network fails here rather than at a socket."""

    def __getattr__(self, name):
        raise AssertionError(
            f"src/embed.py touched requests.{name} during a test. Everything "
            f"outside embed_texts() must be transport-agnostic, or the batch "
            f"endpoint will not drop in as one function.")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(E, "requests", _NoNetwork())
    monkeypatch.setenv(E.KEY_VAR, FAKE_KEY)


# ------------------------------------------------------------- the fixtures

def vector_for(text, dim=DIM):
    """A deterministic unit vector per text, so a stored vector can be checked
    against the text it is supposed to describe."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vec = [0.0] * dim
    vec[digest[0] % dim] = 1.0
    return vec


def build_corpus(root, docs=DOCS):
    """Write clean files and paragraphs.csv exactly as src/clean.py would."""
    data = os.path.join(root, "data")
    os.makedirs(os.path.join(data, "clean"), exist_ok=True)
    rows = []
    for doc_id, paras in docs.items():
        text, offsets = "", []
        for para in paras:
            start = len(text)
            text += para
            offsets.append((start, len(text)))
            text += "\n\n"
        with open(os.path.join(data, "clean", f"{doc_id}.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        for i, ((a, b), para) in enumerate(zip(offsets, paras), 1):
            rows.append({
                "paragraph_id": f"{doc_id}:p{i:05d}", "doc_id": doc_id,
                "project_ids": "P000001", "ordinal": i, "section_path": "I.A",
                "section_title": "Background", "block": "narrative",
                "char_start": a, "char_end": b, "n_tokens": len(para.split()),
                "text_sha256": hashlib.sha256(para.encode("utf-8")).hexdigest(),
            })
    with open(os.path.join(data, "paragraphs.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "paragraph_id", "doc_id", "project_ids", "ordinal", "section_path",
            "section_title", "block", "char_start", "char_end", "n_tokens",
            "text_sha256"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return data


class Stub:
    """Stands in for embed_texts(). Records every call, so "was this text sent"
    is a question the tests can answer rather than assume."""

    def __init__(self, *, fail_on=(), wrong_dim_for=(), dim=DIM):
        self.calls, self.seen = [], []
        self.fail_on = set(fail_on)
        self.wrong_dim_for = set(wrong_dim_for)
        self.dim = dim

    def __call__(self, texts, *, model, api_key, base_url=None, **kw):
        assert api_key == FAKE_KEY, "the key must come from the environment"
        self.calls.append(list(texts))
        self.seen.extend(texts)
        hit = self.fail_on & set(texts)
        if hit:
            raise E.TransportError(f"stub refusing {len(hit)} text(s)")
        vectors = []
        for t in texts:
            vec = vector_for(t, self.dim)
            if t in self.wrong_dim_for:
                vec = vec[:-1]
            vectors.append(vec)
        return vectors, {"tokens": sum(len(t.split()) for t in texts),
                         "model": model,
                         "rate_limit": {"x-ratelimit-remaining-tokens": "999999"}}


def run(tmp_path, stub, monkeypatch, *extra):
    data = os.path.join(str(tmp_path), "data")
    meta = os.path.join(str(tmp_path), "meta")
    monkeypatch.setattr(E, "embed_texts", stub)
    monkeypatch.setattr(sys, "argv", [
        "embed.py", "--data", data, "--meta", meta, "--dim", str(DIM),
        "--model", MODEL, *extra])
    return E.main()


@pytest.fixture
def corpus(tmp_path):
    build_corpus(str(tmp_path))
    return tmp_path


def read_index(data):
    with open(os.path.join(data, "emb_index.csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# -------------------------------------------------------------------- basics

def test_dry_run_makes_no_request(corpus, monkeypatch):
    stub = Stub()
    assert run(corpus, stub, monkeypatch, "--dry-run") == 0
    assert stub.calls == [], "--dry-run must not call the transport"
    assert not os.path.exists(os.path.join(str(corpus), "data", "paragraph_emb.npy"))


def test_every_paragraph_gets_a_row(corpus, monkeypatch):
    data = os.path.join(str(corpus), "data")
    assert run(corpus, Stub(), monkeypatch) == 0
    index = read_index(data)
    assert len(index) == 6, "all six paragraphs should be embedded, boilerplate included"
    arr = numpy.load(os.path.join(data, "paragraph_emb.npy"), mmap_mode="r")
    assert arr.shape == (6, DIM)


def test_duplicate_text_is_embedded_once(corpus, monkeypatch):
    """The same boilerplate appears in both documents and must be paid for once,
    while still occupying a row in each paragraph's place."""
    stub = Stub()
    run(corpus, stub, monkeypatch)
    boiler = DOCS["D001"][2]
    assert stub.seen.count(boiler) == 1, "deduplication did not hold"
    assert len(stub.seen) == 5, "five unique texts across six paragraphs"


# --------------------------------------------------------------------- cache

def test_second_run_requests_nothing(corpus, monkeypatch):
    run(corpus, Stub(), monkeypatch)
    again = Stub()
    assert run(corpus, again, monkeypatch) == 0
    assert again.calls == [], "a cached corpus must cost nothing to re-run"


def test_cache_is_keyed_on_text_and_dimension(corpus, monkeypatch):
    data = os.path.join(str(corpus), "data")
    run(corpus, Stub(), monkeypatch)
    root = E.cache_root(data, MODEL, DIM)
    assert f".{DIM}" in os.path.basename(root), "dimension must be part of the cache key"
    sha = hashlib.sha256(DOCS["D001"][0].encode("utf-8")).hexdigest()
    path = E.cache_path(root, sha)
    assert os.path.exists(path), "vector not stored under the hash of its text"
    assert os.path.getsize(path) == DIM * 4
    assert os.path.basename(os.path.dirname(path)) == sha[:2], "shard on the first byte"


def test_a_torn_cache_file_is_refetched_not_trusted(corpus, monkeypatch):
    data = os.path.join(str(corpus), "data")
    run(corpus, Stub(), monkeypatch)
    sha = hashlib.sha256(DOCS["D001"][0].encode("utf-8")).hexdigest()
    path = E.cache_path(E.cache_root(data, MODEL, DIM), sha)
    with open(path, "wb") as fh:
        fh.write(b"\x00" * (DIM * 4 - 3))      # an interrupted write
    again = Stub()
    run(corpus, again, monkeypatch)
    assert DOCS["D001"][0] in again.seen, "a short cache file must not be read as data"
    assert os.path.getsize(path) == DIM * 4


def test_no_part_files_survive(corpus, monkeypatch):
    data = os.path.join(str(corpus), "data")
    run(corpus, Stub(), monkeypatch)
    leftover = [os.path.join(dirpath, f)
                for dirpath, _d, files in os.walk(data) for f in files
                if f.endswith(".part")]
    assert leftover == [], f"temporary files left behind: {leftover}"


# ------------------------------------------------------------------ failures

def test_a_failed_text_is_omitted_never_zeroed(corpus, monkeypatch):
    """The failure this guards against is a row of zeros sitting in the matrix
    looking like a vector. A paragraph with no vector has no row."""
    data = os.path.join(str(corpus), "data")
    bad = DOCS["D002"][1]
    stub = Stub(fail_on=[bad])
    assert run(corpus, stub, monkeypatch) == 1, "a failure must show in the exit code"

    index = read_index(data)
    assert len(index) == 5, "the failed paragraph should not be indexed"
    assert all(r["text_sha256"] != hashlib.sha256(bad.encode()).hexdigest() for r in index)

    root = E.cache_root(data, MODEL, DIM)
    assert not os.path.exists(E.cache_path(root, hashlib.sha256(bad.encode()).hexdigest()))

    arr = numpy.load(os.path.join(data, "paragraph_emb.npy"))
    norms = numpy.linalg.norm(arr, axis=1)
    assert (norms > 0.9).all(), "a zero row reached the matrix"


def test_one_bad_text_does_not_cost_the_rest_their_vectors(corpus, monkeypatch):
    """A chunk that fails is retried one text at a time, so the good ones in it
    are not collateral damage."""
    data = os.path.join(str(corpus), "data")
    bad = DOCS["D002"][1]
    monkeypatch.setattr(E, "CHUNK_ITEMS", 96)     # all five in one chunk
    stub = Stub(fail_on=[bad])
    run(corpus, stub, monkeypatch)
    assert len(stub.calls) > 1, "the failed chunk was not split"
    index = read_index(data)
    for doc, paras in DOCS.items():
        for i, para in enumerate(paras, 1):
            pid = f"{doc}:p{i:05d}"
            present = any(r["paragraph_id"] == pid for r in index)
            assert present == (para != bad), f"{pid} wrongly {'kept' if present else 'dropped'}"


def test_failures_are_named_in_the_report(corpus, monkeypatch):
    data = os.path.join(str(corpus), "data")
    run(corpus, Stub(fail_on=[DOCS["D002"][1]]), monkeypatch)
    report = open(os.path.join(data, "embed_report.txt"), encoding="utf-8").read()
    assert "failed after retries (1)" in report
    assert "paragraphs with no vector:  1" in report


def test_wrong_dimension_is_never_stored(corpus, monkeypatch):
    data = os.path.join(str(corpus), "data")
    bad = DOCS["D001"][1]
    run(corpus, Stub(wrong_dim_for=[bad]), monkeypatch)
    root = E.cache_root(data, MODEL, DIM)
    assert not os.path.exists(E.cache_path(root, hashlib.sha256(bad.encode()).hexdigest()))
    arr = numpy.load(os.path.join(data, "paragraph_emb.npy"))
    assert arr.shape == (5, DIM)


# ---------------------------------------------------------------- pack rules

@pytest.mark.parametrize("vec, why", [
    ([1.0] * (DIM - 1), "short vector"),
    ([1.0] * (DIM + 1), "long vector"),
    ([0.0] * DIM, "zero vector"),
    ([float("nan")] + [0.0] * (DIM - 1), "not a number"),
    ([float("inf")] + [0.0] * (DIM - 1), "infinity"),
    ([3.0] + [0.0] * (DIM - 1), "norm far from one"),
])
def test_pack_refuses(vec, why):
    with pytest.raises(E.TransportError):
        E.pack(vec, DIM)


def test_pack_accepts_a_unit_vector():
    blob = E.pack(vector_for("anything"), DIM)
    assert len(blob) == DIM * 4
    assert abs(float(numpy.linalg.norm(numpy.frombuffer(blob, dtype="<f4"))) - 1.0) < 1e-6


# ----------------------------------------------------------------- assembly

def test_row_order_matches_the_index(corpus, monkeypatch):
    """emb_index.csv is the only thing that says which row is which paragraph.
    If it drifts from the matrix, every downstream lookup is silently wrong."""
    data = os.path.join(str(corpus), "data")
    run(corpus, Stub(), monkeypatch)
    arr = numpy.load(os.path.join(data, "paragraph_emb.npy"))
    by_pid = {}
    for doc, paras in DOCS.items():
        for i, para in enumerate(paras, 1):
            by_pid[f"{doc}:p{i:05d}"] = para
    for row in read_index(data):
        expected = numpy.asarray(vector_for(by_pid[row["paragraph_id"]]), dtype="float32")
        assert numpy.allclose(arr[int(row["row"])], expected), \
            f"row {row['row']} does not hold {row['paragraph_id']}'s vector"


def test_index_rows_are_contiguous_from_zero(corpus, monkeypatch):
    data = os.path.join(str(corpus), "data")
    run(corpus, Stub(fail_on=[DOCS["D002"][1]]), monkeypatch)
    rows = [int(r["row"]) for r in read_index(data)]
    assert rows == list(range(len(rows))), "a dropped paragraph must not leave a gap"


# ----------------------------------------------------------------- provenance

def test_manifest_records_what_was_run(corpus, monkeypatch):
    run(corpus, Stub(), monkeypatch)
    manifest = json.load(open(os.path.join(str(corpus), "meta",
                                           "embedding_manifest.json"), encoding="utf-8"))
    assert manifest["model_requested"] == MODEL
    assert manifest["dimensions"] == DIM
    assert manifest["rows_embedded"] == 6
    assert manifest["unique_texts"] == 5
    assert manifest["tokens_reported"] > 0, "actual usage, not an estimate"
    assert manifest["provider_pin"] == {"order": ["openai"], "allow_fallbacks": False}
    assert manifest["rate_limit_headers"], "rate limits size the next cohort"
    assert FAKE_KEY not in json.dumps(manifest), "no credential in the manifest"


def test_manifest_counts_paragraphs_left_without_a_vector(corpus, monkeypatch):
    run(corpus, Stub(fail_on=[DOCS["D002"][1]]), monkeypatch)
    manifest = json.load(open(os.path.join(str(corpus), "meta",
                                           "embedding_manifest.json"), encoding="utf-8"))
    assert manifest["paragraphs_without_a_vector"] == 1
    assert manifest["rows_embedded"] == 5


# ------------------------------------------------------------------- guards

def test_a_changed_clean_file_aborts_the_run(corpus, monkeypatch):
    """paragraphs.csv stores offsets, not text. If the clean files are
    regenerated the offsets still parse and still yield something - just not
    what was audited. That has to stop the run, not colour the corpus."""
    path = os.path.join(str(corpus), "data", "clean", "D001.txt")
    body = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write("x" + body[1:])
    with pytest.raises(SystemExit) as exc:
        run(corpus, Stub(), monkeypatch)
    assert "recorded hash" in str(exc.value)


def test_missing_key_aborts_before_any_request(corpus, monkeypatch):
    monkeypatch.delenv(E.KEY_VAR, raising=False)
    stub = Stub()
    with pytest.raises(SystemExit) as exc:
        run(corpus, stub, monkeypatch)
    assert E.KEY_VAR in str(exc.value)
    assert stub.calls == []


def test_an_oversized_paragraph_aborts_rather_than_being_truncated(tmp_path, monkeypatch):
    build_corpus(str(tmp_path), {"D001": ["word " * 20_000]})
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, Stub(), monkeypatch)
    assert "ceiling" in str(exc.value)


def test_limit_writes_smoke_artifacts_and_leaves_the_real_ones(corpus, monkeypatch):
    data = os.path.join(str(corpus), "data")
    run(corpus, Stub(), monkeypatch)
    full = numpy.load(os.path.join(data, "paragraph_emb.npy")).shape
    run(corpus, Stub(), monkeypatch, "--limit", "2")
    assert numpy.load(os.path.join(data, "paragraph_emb.npy")).shape == full, \
        "a smoke run overwrote the full matrix"
    assert numpy.load(os.path.join(data, "smoke_paragraph_emb.npy")).shape == (2, DIM)
    assert os.path.exists(os.path.join(data, "smoke_embedding_manifest.json"))


# ------------------------------------------------------------------ chunking

def test_chunks_respect_both_caps(monkeypatch):
    monkeypatch.setattr(E, "CHUNK_ITEMS", 3)
    monkeypatch.setattr(E, "CHUNK_CHARS", 100)
    unique = {f"s{i}": "x" * 40 for i in range(7)}
    batches = list(E.chunked(list(unique), unique))
    assert all(len(b) <= 3 for b in batches), "item cap ignored"
    assert all(sum(len(unique[s]) for s in b) <= 100 or len(b) == 1
               for b in batches), "character cap ignored"
    assert [s for b in batches for s in b] == list(unique), "chunking lost or reordered texts"


# ----------------------------------------------- response parsing, no network

class FakeResponse:
    def __init__(self, payload, headers=None):
        self._payload, self.headers, self.text = payload, headers or {}, "-"

    def json(self):
        return self._payload


def _payload(vectors, model=MODEL, shuffle=False):
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    if shuffle:
        data = list(reversed(data))
    return {"data": data, "model": model, "usage": {"total_tokens": 42}}


def test_parse_orders_by_index_not_arrival():
    """The response carries an explicit index. Trusting arrival order would put
    the wrong vector under the right paragraph and nothing would look broken."""
    vectors = [vector_for("a"), vector_for("b"), vector_for("c")]
    got, _meta = E._parse(FakeResponse(_payload(vectors, shuffle=True)), 3, MODEL)
    assert got == vectors


def test_parse_rejects_a_short_response():
    with pytest.raises(E.TransportError):
        E._parse(FakeResponse(_payload([vector_for("a")])), 2, MODEL)


def test_parse_rejects_a_repeated_index():
    payload = _payload([vector_for("a"), vector_for("b")])
    payload["data"][1]["index"] = 0
    with pytest.raises(E.TransportError):
        E._parse(FakeResponse(payload), 2, MODEL)


def test_parse_aborts_when_a_different_model_was_served():
    """The provider pin failing is not a retryable error - it means the cache is
    about to mix vectors from two models, which no later check would catch."""
    with pytest.raises(SystemExit):
        E._parse(FakeResponse(_payload([vector_for("a")], model="cohere/embed-v3")),
                 1, MODEL)


def test_parse_accepts_the_same_model_reached_by_a_different_route():
    """OpenAI direct answers 'text-embedding-3-large'; OpenRouter answers
    'openai/text-embedding-3-large'. Same model, two spellings."""
    _got, meta = E._parse(
        FakeResponse(_payload([vector_for("a")], model="text-embedding-3-large")),
        1, MODEL)
    assert meta["model"] == "text-embedding-3-large"


def test_parse_captures_usage_and_rate_limit_headers():
    _got, meta = E._parse(
        FakeResponse(_payload([vector_for("a")]),
                     headers={"x-ratelimit-remaining-tokens": "12345",
                              "content-type": "application/json"}),
        1, MODEL)
    assert meta["tokens"] == 42
    assert meta["rate_limit"] == {"x-ratelimit-remaining-tokens": "12345"}


def test_a_partly_stored_chunk_is_not_paid_for_twice(corpus, monkeypatch):
    """A chunk whose vectors fail validation partway is retried one text at a
    time. The ones already validated and cached before the failure must not be
    sent again - that is money spent on vectors already on disk."""
    monkeypatch.setattr(E, "CHUNK_ITEMS", 96)          # one chunk for everything
    stub = Stub(wrong_dim_for=[DOCS["D002"][2]])       # fails late in the chunk
    run(corpus, stub, monkeypatch)
    resent = [t for t in set(stub.seen) if stub.seen.count(t) > 1]
    stored = [t for t in resent
              if t != DOCS["D002"][2]]
    assert not stored, f"already-cached texts were re-embedded: {stored}"


# ------------------------------------------------------------------ the key

def test_key_file_is_used_when_the_environment_is_empty(corpus, monkeypatch, tmp_path):
    monkeypatch.delenv(E.KEY_VAR, raising=False)
    path = os.path.join(str(tmp_path), "key")
    with open(path, "w") as fh:
        fh.write(FAKE_KEY + "\n")
    os.chmod(path, 0o600)
    stub = Stub()
    assert run(corpus, stub, monkeypatch, "--key-file", path) == 0
    assert stub.calls, "nothing was embedded"


def test_a_world_readable_key_file_is_refused(corpus, monkeypatch, tmp_path):
    """/srv/yggdrasil is a 0755 tree inside raven's read-only file jail. A
    credential left loose there is readable by every local account and through
    the MCP mount, so the run stops rather than using it."""
    monkeypatch.delenv(E.KEY_VAR, raising=False)
    path = os.path.join(str(tmp_path), "key")
    with open(path, "w") as fh:
        fh.write(FAKE_KEY)
    os.chmod(path, 0o644)
    stub = Stub()
    with pytest.raises(SystemExit) as exc:
        run(corpus, stub, monkeypatch, "--key-file", path)
    assert "0600" in str(exc.value)
    assert stub.calls == [], "the key was used before its mode was checked"


def test_the_credential_never_reaches_disk(corpus, monkeypatch):
    """Whatever else goes wrong, the key must not end up in an artifact. This
    walks everything the run wrote."""
    data = os.path.join(str(corpus), "data")
    run(corpus, Stub(), monkeypatch)
    for base in (data, os.path.join(str(corpus), "meta")):
        for dirpath, _d, files in os.walk(base):
            for name in files:
                if name.endswith(".f32") or name.endswith(".npy"):
                    continue
                body = open(os.path.join(dirpath, name), encoding="utf-8",
                            errors="ignore").read()
                assert FAKE_KEY not in body, f"credential written into {name}"
