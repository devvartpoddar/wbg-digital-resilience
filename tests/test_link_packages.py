"""The link stage (src/link_packages.py): which asset a package buys and
which commitments it would carry out, on an invented store, a pre-filled
embedding cache and a fake decisions endpoint. No document text is
committed."""
import csv
import hashlib
import json
import os
import sys

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import classify  # noqa: E402
import embed  # noqa: E402
import labels  # noqa: E402
import link_packages as link  # noqa: E402
import measure  # noqa: E402

DIM = 4
# Each asset's phrase is one axis; a package text is embedded near the axis
# its words name, so the cosine ranking is known in advance.
AXIS = {"fiber": 0, "data_hosting": 1, "digital_systems": 2, "towers": 3}
PACKAGES = [
    ("P1:A", "P1", "Supply and installation of the GovNet core fibre links", "1 Connectivity", 0),
    ("P1:B", "P1", "Modular data centre and private cloud equipment", "2 Platforms", 1),
    ("P1:C", "P1", "Training of ministry staff in digital skills", "3 Skills", 2),
    ("P2:D", "P2", "Construction of twenty mobile towers", "1 Towers", 3),
    ("P1:E", "P1", "TBD", "", 2),
]
UNITS = [
    ("U1", "P1", "Resilience measures for the government network will be built into the "
                 "GovNet design.", "fiber", "fiber"),
    ("U2", "P1", "The data centre will be sited above the flood line.", "data_hosting", ""),
    ("U3", "P2", "Towers will be designed for the wind speeds projected for 2050.", "",
                 "towers"),
]


def _csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _vec(axis, lean=0.9):
    v = numpy.full(DIM, (1 - lean) / (DIM - 1), dtype="float32")
    v[axis] = lean
    return v / numpy.linalg.norm(v)


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    _csv(str(data / "procurement" / "packages.csv"),
         ["package_id", "project_id", "description_clean", "component_number", "component",
          "is_placeholder"],
         [{"package_id": i, "project_id": p, "description_clean": t,
           "component_number": c.split(" ")[0], "component": c.split(" ", 1)[-1] if " " in c else "",
           "is_placeholder": "true" if t == "TBD" else "false"}
          for i, p, t, c, _ in PACKAGES])
    lab = tmp_path / "labels"
    _csv(str(lab / "assets.csv"), ["asset_id", "asset_name", "definition", "active"],
         [{"asset_id": a, "asset_name": a.replace("_", " ").title(),
           "definition": f"{a} as infrastructure", "active": "true"} for a in AXIS])
    _csv(str(lab / "assets_queries.csv"), ["asset_id", "query_id", "query", "active"],
         [{"asset_id": a, "query_id": "q", "query": f"{a} phrase", "active": "true"}
          for a in AXIS])
    _csv(str(lab / "assets_groups.csv"), ["asset_id", "group_id", "group_name", "active"],
         [{"asset_id": "fiber", "group_id": "networks", "group_name": "Networks", "active": "true"},
          {"asset_id": "towers", "group_id": "networks", "group_name": "Networks", "active": "true"},
          {"asset_id": "data_hosting", "group_id": "data", "group_name": "Data", "active": "true"},
          {"asset_id": "digital_systems", "group_id": "systems", "group_name": "Systems",
           "active": "true"}])
    # The measure: its phrase points at data hosting (axis 1).
    _csv(str(lab / "cra.csv"), ["measure_id", "measure_name", "definition", "active"],
         [{"measure_id": "cra", "measure_name": "Climate risk", "definition": "risk to sites",
           "active": "true"}])
    _csv(str(lab / "cra_queries.csv"), ["measure_id", "query_id", "query", "active"],
         [{"measure_id": "cra", "query_id": "q", "query": "cra phrase", "active": "true"}])
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(lab),))
    # The embedding cache, pre-filled: no network.
    root = embed.cache_root(str(data), embed.DEFAULT_MODEL, DIM)
    for _, _, t, _, axis in PACKAGES:
        embed.write_cached(root, hashlib.sha256(t.encode()).hexdigest(),
                           embed.pack(_vec(axis), DIM))
    for a, axis in AXIS.items():
        for text in (f"{a} phrase", f"{a.replace('_', ' ').title()}: {a} as infrastructure"):
            embed.write_cached(root, hashlib.sha256(text.encode()).hexdigest(),
                               embed.pack(_vec(axis, 1.0), DIM))
    for text in ("cra phrase", "Climate risk: risk to sites"):
        embed.write_cached(root, hashlib.sha256(text.encode()).hexdigest(),
                           embed.pack(_vec(1, 1.0), DIM))
    # A measure run with three committed units.
    run = data / "measures" / "cra-abc"
    _csv(str(run / "units.csv"), measure.UNIT_COLS,
         [{"label_id": "cra", "unit_id": u, "paragraph_id": "D:p1", "doc_id": "D",
           "project_ids": p, "component_number": "1", "sent": "true", "grade": 3,
           "assets_yes": ay, "asset_link": al, "text": t} for u, p, t, ay, al in UNITS]
         + [{"label_id": "cra", "unit_id": "U4", "paragraph_id": "D:p1", "doc_id": "D",
             "project_ids": "P1", "sent": "true", "grade": 1, "assets_yes": "fiber",
             "text": "Floods are frequent."}])
    (run / "manifest.json").write_text(json.dumps({"scale": ["a", "b", "c", "d"]}))
    monkeypatch.setattr(measure, "current_run", lambda name, d, **kw: str(run))
    monkeypatch.setattr(labels, "label_sets", lambda dirs=None: ["assets", "cra"])
    calls = []

    def fake_decide(body, **kw):
        calls.append(body)
        s = body["state"]
        out = {}
        for qid in body["questions"]:
            if qid == "pair":
                yes = ("GovNet" in s["commitment"] and "GovNet" in s["package"]) or \
                      ("data centre" in s["commitment"] and "data centre" in s["package"])
            else:
                word = qid.split("_")[0]
                yes = word in s["package"].lower() or (qid == "fiber" and "fibre" in s["package"])
            out[qid] = {"type": "noul", "noul": 0.9 if yes else 0.1}
        return {"model": measure.MODEL, "usage": {"cost": 0.00001}, "answers": out}
    monkeypatch.setattr(classify, "decide", fake_decide)
    return str(data), calls


def _rows(folder, file):
    with open(os.path.join(folder, file), newline="") as fh:
        return list(csv.DictReader(fh))


def test_each_package_is_asked_its_best_assets_by_cosine(store):
    data, calls = store
    out = link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None)
    rows = _rows(out, "package_assets.csv")
    assert {r["package_id"] for r in rows} == {"P1:A", "P1:B", "P1:C", "P2:D"}   # TBD left out
    a = {r["label_id"]: r for r in rows if r["package_id"] == "P1:A"}
    assert len(a) == 2 and a["fiber"]["cosine_rank"] == "1"
    assert float(a["fiber"]["probability"]) == 0.9
    assert all(float(r["cosine"]) <= 1.0 for r in rows)


def test_pairs_need_a_shared_asset_or_group_and_the_same_project(store):
    data, calls = store
    out = link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None)
    pairs = {(r["unit_id"], r["package_id"]): r for r in _rows(out, "commitment_packages.csv")
             if r["route"] == "asset"}
    # U1 (fiber, P1) meets the fibre package by asset and the data-centre
    # package not at all; U3 (towers, P2) meets the towers package by asset.
    assert ("U1", "P1:A") in pairs and pairs[("U1", "P1:A")]["shared_asset_ids"] == "fiber"
    assert ("U1", "P1:B") not in pairs
    assert ("U3", "P2:D") in pairs
    assert ("U1", "P2:D") not in pairs           # another project, though the same group
    assert ("U2", "P1:B") in pairs and pairs[("U2", "P1:B")]["shared_group_ids"] == "data"
    assert float(pairs[("U1", "P1:A")]["probability"]) == 0.9
    assert float(pairs[("U2", "P1:B")]["probability"]) == 0.9
    assert not any(u == "U4" for u, _ in pairs)   # not the top grade


def test_packages_nearest_the_measure_are_paired_without_a_shared_asset(store, monkeypatch):
    """A commitment meets its project's packages nearest the measure even when
    they share no asset: U2 (data centre, P1) is paired with B by asset, and
    by the measure with the next nearest, A; U3 (P2) has only D, already
    paired by asset."""
    monkeypatch.setattr(link, "MEASURE_TOP", 1)
    data, calls = store
    out = link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None)
    rows = _rows(out, "commitment_packages.csv")
    by = {(r["unit_id"], r["package_id"]): r for r in rows if r["route"] == "measure"}
    assert ("U2", "P1:A") in by and by[("U2", "P1:A")]["measure_rank"] == "1"
    assert ("U2", "P1:B") not in by                    # already paired by asset
    assert not any(u == "U3" for u, _ in by)           # its one package is asset-paired
    assert not any(u == "U4" for u, _ in by)           # not the top grade


def test_a_second_run_asks_nothing_and_a_changed_depth_is_a_new_version(store, monkeypatch):
    monkeypatch.setattr(link, "MEASURE_TOP", 0)
    data, calls = store
    out = link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None)
    n = len(calls)
    assert link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None) == out
    assert len(calls) == n
    assert link.current_run(data, asset_top=2, dim=DIM) == out
    other = link.link(data, api_key="k", asset_top=3, dim=DIM, log=lambda *_: None)
    assert other != out
    # The deeper run asked only the third asset of each package: the first
    # two answers came from the store.
    assert len(calls) - n == 4
    with open(os.path.join(other, "manifest.json")) as fh:
        m = json.load(fh)
    assert m["asset_top"] == 3 and "pair" in m["question_sha256"]


def test_a_failed_request_writes_nothing(store, monkeypatch):
    data, calls = store

    def broken(body, **kw):
        raise classify.TransportError("down")
    monkeypatch.setattr(classify, "decide", broken)
    assert link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None) is None
    assert not os.path.exists(os.path.join(data, "links"))or not any(
        f.endswith("manifest.json") for _, _, fs in os.walk(os.path.join(data, "links")) for f in fs)


def test_the_ledger_holds_whole_counts_when_the_asset_answers_are_stored(store, monkeypatch):
    """A run whose asset questions all come from the store starts its count
    at zero and only the pair questions add to it: the ledger must still hold
    a whole number, or the next classify stage cannot parse the day's total
    (a float there crashed the run on 2026-10-20)."""
    data, _ = store
    link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None)
    # Revise the commitments and take a shallower measure depth: that names
    # another run and makes every pair question new, while the packages (and
    # so their asset questions) are unchanged and already stored.
    units = os.path.join(data, "measures", "cra-abc", "units.csv")
    rows = _rows(os.path.join(data, "measures", "cra-abc"), "units.csv")
    for r in rows:
        r["text"] = r["text"] + " Revised."
    _csv(units, measure.UNIT_COLS, rows)
    monkeypatch.setattr(link, "MEASURE_TOP", 2)
    assert link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None)
    ledger = [r for r in _rows(os.path.join(data, "reports"), "spend.csv")
              if r["stage"] == "link"]
    assert len(ledger) == 2                  # the first asked assets and pairs, the second pairs
    assert all(r["calls"].isdigit() for r in ledger)


def test_the_loader_reads_the_current_run(store):
    data, calls = store
    link.link(data, api_key="k", asset_top=2, dim=DIM, log=lambda *_: None)
    import load_pg
    # current_run needs the settings the run was made with (the loader uses
    # the defaults), so point it at this run's depth and dimension.
    folder = link.current_run(data, asset_top=2, dim=DIM)
    assert folder and os.path.exists(os.path.join(folder, "package_assets.csv"))
    assert set(load_pg.ASSET_LINK_COLS) == set(link.ASSET_COLS)
    assert set(load_pg.PAIR_LINK_COLS) == set(link.PAIR_COLS)
