"""Shared fixtures: a live control panel with the starting values the tests
were written against, a scratch Postgres schema, an invented corpus and a
fake decision model. No document text is committed."""
import csv
import hashlib
import json
import os
import sys
import tempfile
import uuid

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))

os.environ.setdefault("WBG_ASK_BATCH", "0")

STARTING = {"posterior.on": "true", "floor.default": "0.5", "floor.types": "0.5",
            "floor.pair_check": "0.5", "asset.floor": "0.3", "asset.decay": "0.4",
            "asset.multiple": "true", "link.asset_yes": "0.5", "floor.ask": "0.5",
            "asset.ask_floor": "0.3"}


def _test_panel():
    live = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "inputs", "config", "control_panel.csv")
    with open(live, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if r["setting"] in STARTING:
            r["value"] = STARTING[r["setting"]]
    fd, path = tempfile.mkstemp(prefix="wbg-panel-", suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


os.environ.setdefault("WBG_PANEL", _test_panel())


def write_csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


# ------------------------------------------------------------ Postgres

@pytest.fixture
def pg(monkeypatch):
    """A connection with its own schema pair (t_<hex>, t_<hex>_store), dropped
    afterwards. Skips when no Postgres answers at $WBG_PG."""
    import db
    try:
        conn = db.connect()
    except Exception as exc:                                    # noqa: BLE001
        pytest.skip(f"no Postgres at {db.DSN}: {exc}")
    name = "t_" + uuid.uuid4().hex[:10]
    monkeypatch.setattr(db, "SCHEMA", name)
    monkeypatch.setattr(db, "STORE", name + "_store")
    db.ensure(conn)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {name}")
    conn.commit()
    yield conn
    conn.rollback()
    conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    conn.execute(f"DROP SCHEMA IF EXISTS {name}_store CASCADE")
    conn.commit()
    conn.close()


# ------------------------------------------------------------ an invented corpus

PARAS = ["The project will finance a hazard assessment of tower sites. Floods are frequent.",
         "A new fibre backbone will be built. The road will be widened.",
         "Background on the economy."]
SENTS = [("D:p00001", "The project will finance a hazard assessment of tower sites."),
         ("D:p00001", "Floods are frequent."),
         ("D:p00002", "A new fibre backbone will be built."),
         ("D:p00002", "The road will be widened."),
         ("D:p00003", "Background on the economy.")]
PARA_COLS = ["paragraph_id", "doc_id", "project_ids", "ordinal", "section_path", "section_title",
             "block", "char_start", "char_end", "n_tokens", "text_sha256", "page_from", "page_to",
             "parser", "for_model", "list_item", "table_id", "raw_start", "raw_end",
             "component_number", "component_name", "subcomponent_number", "subcomponent_name",
             "component_mentions"]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """data/ with one document of five sentences (two about towers, one about
    fibre), embedded as unit vectors, one measure (cra) with a word pattern
    and one asset set (towers, fiber), each with a scored search run. Returns
    the data root."""
    data = tmp_path / "data"
    text = "\n\n".join(PARAS)
    (data / "appraisal" / "text").mkdir(parents=True)
    (data / "appraisal" / "text" / "D.txt").write_text(text, encoding="utf-8")
    paras, at = [], 0
    for i, t in enumerate(PARAS, 1):
        paras.append({"paragraph_id": f"D:p{i:05d}", "doc_id": "D", "project_ids": "P1",
                      "ordinal": i, "block": "narrative", "for_model": "true", "char_start": at,
                      "char_end": at + len(t), "section_title": "Project Components",
                      "component_number": "1", "n_tokens": len(t.split()),
                      "text_sha256": hashlib.sha256(t.encode()).hexdigest()})
        at += len(t) + 2
    write_csv(str(data / "appraisal" / "paragraphs.csv"), PARA_COLS, paras)
    sents = []
    for n, (pid, s) in enumerate(SENTS, 1):
        start = text.index(s)
        sents.append({"sentence_id": f"{pid}:s{n:03d}", "paragraph_id": pid, "doc_id": "D",
                      "char_start": start, "char_end": start + len(s), "n_tokens": len(s.split()),
                      "text_sha256": hashlib.sha256(s.encode()).hexdigest()})
    write_csv(str(data / "appraisal" / "sentences.csv"),
              ["sentence_id", "paragraph_id", "doc_id", "char_start", "char_end", "n_tokens",
               "text_sha256"], sents)
    write_csv(str(data / "appraisal" / "components.csv"), ["doc_id", "source", "number", "name"],
              [{"doc_id": "D", "source": "datasheet", "number": "1", "name": "Connectivity"}])
    import units
    us = [u for u in units.search_units(str(data)) if not u["left_out"] and not u["copy_of"]]
    index_cols = ["row"] + [c for c in units.UNIT_COLS
                            if c not in ("char_start", "char_end", "n_tokens", "left_out",
                                         "copy_of")] + ["copies"]
    emb = data / "embeddings"
    emb.mkdir()
    write_csv(str(emb / "sentence_index.csv"), index_cols,
              [dict(u, row=i, copies="") for i, u in enumerate(us)])
    m = numpy.zeros((len(us), 4), dtype="float32")
    for i in range(len(us)):
        m[i, i % 4] = 1.0
    numpy.save(str(emb / "sentence_emb.npy"), m)
    lab = tmp_path / "labels"
    write_csv(str(lab / "cra.csv"), ["measure_id", "measure_name", "definition", "active"],
              [{"measure_id": "cra", "measure_name": "Hazard assessment",
                "definition": "an assessment of hazards", "active": "true"}])
    write_csv(str(lab / "scale.csv"), ["grade", "description"],
              [{"grade": g, "description": d} for g, d in enumerate(
                  ["Not {name}.", "{name} as background.", "{name} planned.", "{name} committed."])])
    write_csv(str(lab / "cra_patterns.csv"), ["measure_id", "pattern_id", "pattern", "active"],
              [{"measure_id": "cra", "pattern_id": "hz", "pattern": r"\bhazard assessment\b",
                "active": "true"}])
    write_csv(str(lab / "cra_queries.csv"), ["measure_id", "query_id", "query", "active"],
              [{"measure_id": "cra", "query_id": "q1", "query": "assess hazards", "active": "true"}])
    write_csv(str(lab / "cra_types.csv"),
              ["measure_id", "type_id", "type_name", "definition", "active"],
              [{"measure_id": "cra", "type_id": "site", "type_name": "Site assessment",
                "definition": "at identified sites", "active": "true"},
               {"measure_id": "cra", "type_id": "route", "type_name": "Route assessment",
                "definition": "along a corridor", "active": "true"}])
    write_csv(str(lab / "assets.csv"), ["asset_id", "asset_name", "definition", "active"],
              [{"asset_id": "towers", "asset_name": "Mobile networks", "definition": "masts",
                "active": "true"},
               {"asset_id": "fiber", "asset_name": "Fibre networks", "definition": "cables",
                "active": "true"}])
    write_csv(str(lab / "assets_queries.csv"), ["asset_id", "query_id", "query", "active"],
              [{"asset_id": "towers", "query_id": "t1", "query": "towers", "active": "true"},
               {"asset_id": "fiber", "query_id": "f1", "query": "fibre", "active": "true"}])
    import labels
    monkeypatch.setattr(labels, "LABEL_DIRS", (str(lab),))
    import questions
    monkeypatch.setattr(questions, "ROOT", str(tmp_path))
    os.makedirs(tmp_path / "analysis" / "labels")
    os.replace(str(lab / "scale.csv"), str(tmp_path / "analysis" / "labels" / "scale.csv"))
    # scored search runs: cosine 0.9 for the sentences each label is about
    import search
    for name, hits in (("cra", {0: 0.9}), ("assets", {0: 0.9, 2: 0.9})):
        queries = search.load_queries(name)
        folder, _ = search.run_folder(name, str(data))
        os.makedirs(folder)
        search._write(os.path.join(folder, "queries.csv"), search.QUERY_COLS, queries)
        scores = numpy.zeros((len(us), len(queries)), dtype="float32")
        for j, q in enumerate(queries):
            for i, c in hits.items():
                if name == "cra" or (q["label_id"] == "towers") == (i == 0):
                    scores[i, j] = c
        numpy.save(os.path.join(folder, "scores.npy"), scores)
        with open(os.path.join(folder, "manifest.json"), "w") as fh:
            json.dump({"index_sha256": search._sha_file(str(emb / "sentence_index.csv"))}, fh)
    return str(data)


@pytest.fixture
def fake_model(monkeypatch):
    """A decision model that says yes (grade 3, or Yes) to any item whose text
    holds 'hazard assessment' or 'tower' for a towers question, 'fibre' for a
    fibre one; no otherwise. Records every request body."""
    import api
    calls = []

    def post(path, b, api_key, **kw):
        calls.append(b)
        out = {}
        for bq, q in b["questions"].items():
            field = bq.split(" | ")[0] if " | " in bq else None
            text = b["state"][field] if field else json.dumps(b["state"])
            asked = (b["state"].get(f"question {bq.split(' | ')[1]}") if field
                     else q["instructions"]).lower()
            yes = ("hazard assessment" in text.lower() and "hazard" in asked) or \
                  ("tower" in text.lower() and "mobile" in asked) or \
                  ("fibre" in text.lower() and "fibre" in asked) or \
                  ("site" in text.lower() and "site" in asked)
            n = len(q["criteria"])
            probs = {str(i): 0.0 for i in range(n)}
            probs[str(n - 1) if yes else "0"] = 0.9
            probs[str(n - 2) if yes else "1"] = 0.1
            out[bq] = {"type": "score", "probabilities": probs}
        return {"model": "m", "usage": {"cost": 0.0001}, "answers": out}
    monkeypatch.setattr(api, "post", post)
    return calls
