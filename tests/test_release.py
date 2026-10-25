"""Data releases (src/wbg_portfolio/release.py) on an invented store."""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
from wbg_portfolio import release  # noqa: E402


def _store(tmp_path):
    data = tmp_path / "data"
    (data / "procurement").mkdir(parents=True)
    with open(data / "procurement" / "packages.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["package_id", "project_id"])
        w.writerow(["P1:A-1", "P1"])
    return str(data)


def test_the_same_data_is_the_same_release(tmp_path):
    data = _store(tmp_path)
    first = release.cut(data, log=lambda *_: None)
    assert release.cut(data, log=lambda *_: None) == first
    rows = release.releases(data)
    assert [r["release_id"] for r in rows] == [first] and rows[0]["packages"] == "1"
    assert os.path.exists(os.path.join(data, "releases", first, "manifest.json"))


def test_changed_data_is_a_new_release_and_the_old_one_is_kept(tmp_path):
    data = _store(tmp_path)
    first = release.cut(data, log=lambda *_: None)
    with open(os.path.join(data, "procurement", "packages.csv"), "a") as fh:
        fh.write("P1:B-2,P1\n")
    second = release.cut(data, log=lambda *_: None)
    assert second != first
    assert [r["release_id"] for r in release.releases(data)] == [first, second]
    assert os.path.exists(os.path.join(data, "releases", first, "manifest.json"))


def test_status_names_the_code_and_the_release(tmp_path):
    data = _store(tmp_path)
    rid = release.cut(data, log=lambda *_: None)
    lines = release.status(data)
    assert lines[0].startswith("code:") and rid in lines[1]


def test_an_interrupted_cut_is_finished_by_the_next_one(tmp_path, monkeypatch):
    import json, os
    data = _store(tmp_path)
    rid = release.cut(data, log=lambda *_: None)
    folder = os.path.join(data, "releases", rid)
    assert os.path.exists(os.path.join(folder, "manifest.json"))
    # The index row is lost (the process died between the manifest and it).
    os.remove(os.path.join(data, "releases", "index.csv"))
    assert release.cut(data, log=lambda *_: None) == rid
    assert [r["release_id"] for r in release.releases(data)] == [rid]
    assert release.status(data)[1].startswith(f"data:     release {rid}")


def test_the_latest_fetch_date_ignores_the_old_stamp(tmp_path):
    """A project stamped "before stages.json" must not hide the latest date
    (as text it sorts after every ISO date and loaded as NULL)."""
    import json
    from wbg_portfolio import release
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "stages.json").write_text(json.dumps({"fetched": {
        "procurement": {"P1": "before stages.json", "P2": "2026-10-19", "P3": "2026-10-13"}}}))
    assert release._fetched(str(tmp_path))["procurement"] == {"projects": 3, "latest": "2026-10-19"}
