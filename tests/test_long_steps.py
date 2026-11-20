"""What a long step needs to run unattended (common.Progress, common.limited,
common.write_atomic), the stages that use them, and the spend guard a restarted run relies on.
Everything is invented; no model is called."""
import json
import os
import time

import pytest

from wbg_portfolio import api
from wbg_portfolio import cli
from wbg_portfolio import common


@pytest.fixture(autouse=True)
def no_skips_left(monkeypatch):
    monkeypatch.setattr(common, "SKIPPED", [])


# ------------------------------------------------------------ the steady line

def test_progress_says_n_of_total_on_the_first_item_then_once_a_minute_and_on_the_last():
    now = [0.0]
    lines = []
    p = common.Progress("read", 5, lines.append, clock=lambda: now[0])
    for t in (1, 2, 70, 80, 90):
        now[0] = t
        p.step("ok")
    # the first, the one a minute after it, the last
    assert [x.split(" (")[0] for x in lines] == ["read: 1 of 5, ok", "read: 3 of 5, ok", "read: 5 of 5, ok"]


def test_progress_names_every_skipped_item_and_counts_them_at_the_end():
    lines = []
    p = common.Progress("read", 3, lines.append)
    p.step()
    p.skip("D2", "not finished within 60 seconds")
    p.step()
    assert p.end() == [("D2", "not finished within 60 seconds")]
    assert "read: 2 of 3: D2 skipped: not finished within 60 seconds" in lines
    assert lines[-1] == "read: 1 of 3 skipped (tried again on the next run): D2"
    # and the stage's exit status says it left something out
    assert common.exit_status(0) == common.PARTIAL and common.exit_status(1) == 1


def test_a_stage_that_skipped_nothing_exits_0():
    assert common.exit_status(0) == 0


# ------------------------------------------------------------ the time limit

def _slow(item):
    if item == "hang":
        time.sleep(60)
    if item == "raise":
        raise ValueError("a damaged file")
    if item == "die":
        os._exit(9)                               # the reader's process brought down
    return item.upper()


def test_one_item_that_hangs_or_fails_is_reported_and_the_others_are_done():
    started = time.time()
    got = {item: (value, error) for item, value, error in
           common.limited(_slow, ["a", "hang", "raise", "die", "b"], 1, workers=2)}
    assert time.time() - started < 20
    assert got["a"] == ("A", "") and got["b"] == ("B", "")
    assert got["hang"] == (None, "not finished within 1 seconds")
    assert got["raise"][1] == "ValueError: a damaged file"
    assert "stopped without an answer" in got["die"][1]


def test_items_run_side_by_side_up_to_the_workers():
    def nap(i):
        time.sleep(1)
        return i
    started = time.time()
    assert sorted(v for _, v, _ in common.limited(nap, range(4), 30, workers=4)) == [0, 1, 2, 3]
    assert time.time() - started < 3


def test_a_pdf_gets_a_limit_from_its_pages_and_at_least_a_minute(tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"not a pdf")
    assert common.pdf_seconds(str(bad)) == common.PDF_MIN_SECONDS


# ------------------------------------------------------------ a file written whole

def test_a_write_stopped_part_way_leaves_the_old_file_and_no_temporary(tmp_path, monkeypatch):
    path = tmp_path / "t.csv"
    common.write_csv(str(path), ["a"], [{"a": "1"}])

    def killed(*a):
        raise KeyboardInterrupt("killed here")
    monkeypatch.setattr(os, "replace", killed)
    with pytest.raises(KeyboardInterrupt):
        common.write_csv(str(path), ["a"], [{"a": "2"}])
    with pytest.raises(KeyboardInterrupt):
        common.write_atomic(str(path), "a\n3\n")
    assert path.read_text() == "a\n1\n"
    assert os.listdir(tmp_path) == ["t.csv"]


# ------------------------------------------------------------ the appraisal PDFs

def test_clean_reads_each_pdf_with_a_limit_caches_each_as_it_is_read_and_reads_only_the_rest(
        tmp_path, monkeypatch):
    from wbg_portfolio import clean
    data = tmp_path / "data"
    (data / "raw" / "pdf").mkdir(parents=True)
    docs = []
    for d in ("D1", "D2", "D3"):
        (data / "raw" / "pdf" / f"{d}.pdf").write_bytes(d.encode())
        docs.append({"doc_id": d, "pdf_sha256": "sha-" + d})

    def fake_layout(path):
        if path.endswith("D2.pdf"):
            time.sleep(60)
        return "text of " + os.path.basename(path), [[0, 4]], {"pages": 1}, "raw"
    monkeypatch.setattr(clean, "_layout_job", fake_layout)
    monkeypatch.setattr(common, "pdf_seconds", lambda p: 1)
    out = clean.layout_cached(str(data), docs, workers=2)
    assert out["D1"][0] == "text of D1.pdf" and out["D3"][3] == ""
    assert out["D2"][3] == "not finished within 1 seconds"
    cache = data / "appraisal" / "cache"
    assert sorted(os.listdir(cache)) == ["D1.layout.json", "D3.layout.json"]
    assert common.exit_status(0) == common.PARTIAL       # the stage runs again next time

    # the next run reads only D2; D1 and D3 come from the cache
    def only_d2(path):
        assert path.endswith("D2.pdf"), path
        return "text of D2", [], {}, "raw"
    monkeypatch.setattr(clean, "_layout_job", only_d2)
    out = clean.layout_cached(str(data), docs, workers=2)
    assert out["D2"][0] == "text of D2" and out["D1"][0] == "text of D1.pdf"


def test_a_layout_cache_left_unreadable_is_read_again_not_a_crash(tmp_path, monkeypatch):
    from wbg_portfolio import clean
    data = tmp_path / "data"
    (data / "raw" / "pdf").mkdir(parents=True)
    (data / "raw" / "pdf" / "D1.pdf").write_bytes(b"x")
    (data / "appraisal" / "cache").mkdir(parents=True)
    (data / "appraisal" / "cache" / "D1.layout.json").write_text('{"pdf_sha256": "s", "te')
    monkeypatch.setattr(clean, "_layout_job", lambda p: ("t", [], {}, "raw"))
    out = clean.layout_cached(str(data), [{"doc_id": "D1", "pdf_sha256": "s"}], workers=1)
    assert out["D1"][0] == "t"


# ------------------------------------------------------------ the prepare stages

def test_a_stage_that_left_items_out_runs_again_next_time(tmp_path, monkeypatch):
    """A stage that exits PARTIAL (it read what it could and named what it could not) lets the
    run go on, and is not recorded as up to date, so the next run tries those items again."""
    data = str(tmp_path)
    ran = []
    out = os.path.join(data, "appraisal", "o.csv")

    def fake_run(script, *a, data, tail=0):
        ran.append(script)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            fh.write("x")
        return common.PARTIAL if script == "s.py" and ran.count("s.py") == 1 else 0
    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli, "STAGES", [("s", "s.py", [], [], [("appraisal", "o.csv")])])
    cli.prepare(cli.Selection([], "t"), data=data, fetch=False)
    cli.prepare(cli.Selection([], "t"), data=data, fetch=False)
    cli.prepare(cli.Selection([], "t"), data=data, fetch=False)
    assert ran.count("s.py") == 2            # again after the partial run, not after the whole one


def test_every_stage_runs_with_unbuffered_output(tmp_path):
    assert cli._env(str(tmp_path))["PYTHONUNBUFFERED"] == "1"


# ------------------------------------------------------------ the spend guard

def test_a_paid_stage_does_not_start_without_a_cap(tmp_path, capsys):
    for stages in (["--from", "classify"], ["--only", "embed"], ["--only", "search"]):
        with pytest.raises(SystemExit) as stop:
            cli.main(["--data", str(tmp_path), "run", *stages])
        assert "--max-spend" in str(stop.value)
    with pytest.raises(SystemExit) as stop:
        cli.main(["--data", str(tmp_path), "try", "x"])
    assert "--max-spend" in str(stop.value)


def test_the_cap_window_is_kept_across_a_restart_and_closes_when_the_run_ends(tmp_path, monkeypatch):
    monkeypatch.delenv("WBG_SPEND_CAP_USD", raising=False)
    monkeypatch.delenv("WBG_SPEND_SINCE", raising=False)
    cmd = {"stages": ["classify"], "projects": None}
    first = api.open_window(str(tmp_path), cmd, 5)
    since = os.environ["WBG_SPEND_SINCE"]
    assert "did not end" not in first
    time.sleep(1.1)
    again = api.open_window(str(tmp_path), cmd, 7)           # the restart, with a higher cap
    assert os.environ["WBG_SPEND_SINCE"] == since and "did not end" in again
    assert os.environ["WBG_SPEND_CAP_USD"] == "7.0000"
    other = {"stages": ["link"], "projects": None}
    api.open_window(str(tmp_path), other, 1)
    assert os.environ["WBG_SPEND_SINCE"] >= since
    api.close_window(str(tmp_path), cmd)
    time.sleep(1.1)
    api.open_window(str(tmp_path), cmd, 5)
    assert os.environ["WBG_SPEND_SINCE"] > since               # a new run, a new window
    with open(tmp_path / "reports" / "spend_windows.json") as fh:
        assert len(json.load(fh)) == 2


def test_the_ledger_holds_each_call_as_it_is_paid_so_a_kill_keeps_the_record(pg, monkeypatch):
    """A step killed part way (no __exit__) has its calls in the ledger already, and a restarted
    run's cap counts them."""
    from wbg_portfolio import db
    monkeypatch.setenv("WBG_SPEND_CAP_USD", "1")
    monkeypatch.setenv("WBG_SPEND_SINCE", "2000-01-01")
    s = api.Spend(pg, "measure", "cra", "m")
    s.add(0.25)
    s.add(0.25, calls=2)
    # killed here: nothing more runs in this step
    calls, cost = db.one(pg, f"SELECT calls, cost_usd FROM {db.STORE}.spend")
    assert (calls, float(cost)) == (3, 0.5)
    assert api.cap_left(pg) == pytest.approx(0.5)
    with pytest.raises(api.CapReached):
        api.Spend(pg, "measure", "cra", "m").check(0.6)
    t = api.Spend(pg, "link", "", "m")
    with pytest.raises(api.CapReached):
        t.add(0.6)
    assert float(db.one(pg, f"SELECT sum(cost_usd) FROM {db.STORE}.spend")[0]) == pytest.approx(1.1)


def test_components_reads_each_table_with_a_limit_and_keeps_what_it_read(tmp_path, monkeypatch):
    """A component table read that never finishes is stopped and named; the documents read are
    in the cache before the stage ends, and the next run reads only the one left out."""
    import sys
    from wbg_portfolio import components
    data = tmp_path / "data"
    text = "Component name and cost of the work.\n\nMore words follow here for the record."
    docs, paras = [], []
    for d in ("D1", "D2"):
        (data / "raw" / "pdf").mkdir(parents=True, exist_ok=True)
        (data / "raw" / "pdf" / f"{d}.pdf").write_bytes(b"%PDF")
        (data / "appraisal" / "text").mkdir(parents=True, exist_ok=True)
        (data / "appraisal" / "text" / f"{d}.txt").write_text(text, encoding="utf-8")
        docs.append({"doc_id": d, "project_ids": "P1", "doc_kind": "project_paper_other",
                     "disclosure_date": "2024-01-01", "pdf_sha256": "sha" + d})
        paras.append({"paragraph_id": f"{d}:p1", "doc_id": d, "char_start": 0, "char_end": 36,
                      "page_from": 3, "block": "narrative"})
    common.write_csv(str(data / "raw" / "documents.csv"), list(docs[0]), docs)
    common.write_csv(str(data / "appraisal" / "paragraphs.csv"), list(paras[0]), paras)
    (data / "reports").mkdir()

    def hang_on_d2(path, pages):
        if path.endswith("D2.pdf"):
            time.sleep(60)
        return [{"number": "1", "name": "Roads", "cost_usd_m": "",
                 "action": "", "page": 3, "source": "datasheet"}]
    monkeypatch.setattr(components, "read_table_pages", hang_on_d2)
    monkeypatch.setattr(common, "pdf_seconds", lambda p: 1)
    monkeypatch.setattr(sys, "argv", ["components.py", "--data", str(data)])
    assert common.exit_status(components.main()) == common.PARTIAL
    with open(data / "appraisal" / "cache" / "components_cache.json") as fh:
        assert [k[:5] for k in json.load(fh)] == ["shaD1"]
    rows = common.read_csv(str(data / "appraisal" / "components.csv"))
    assert {r["doc_id"] for r in rows if r["source"] == "datasheet"} == {"D1"}

    monkeypatch.setattr(common, "SKIPPED", [])

    def only_d2(path, pages):
        assert path.endswith("D2.pdf"), path
        return hang_on_d2("D1.pdf", pages)
    monkeypatch.setattr(components, "read_table_pages", only_d2)
    assert common.exit_status(components.main()) == 0
    rows = common.read_csv(str(data / "appraisal" / "components.csv"))
    assert {r["doc_id"] for r in rows if r["source"] == "datasheet"} == {"D1", "D2"}
