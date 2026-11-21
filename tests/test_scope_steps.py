"""A run over named projects asks nothing about another project's sentences or packages, in any step
of ./wbg_portfolio run (cli.main, classify to publish), the types and their proposals included.
The fake models only."""
import csv
import hashlib
import json
import shutil

import numpy

from test_pipeline_e2e import run, schema, wide  # noqa: F401  (fixtures)
from wbg_portfolio import db
from wbg_portfolio import embed
from wbg_portfolio import search

OTHER = ("A new fibre backbone will be built.", "The road will be widened.", "Supply of fibre cable")


def _second_project(corpus):
    """Paragraph 2's sentences belong to P2, and P2 has a package of its own."""
    before = {name: search.run_folder(name, corpus)[0] for name in ("cra", "assets")}
    path = f"{corpus}/embeddings/sentence_index.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if r["paragraph_id"] == "D:p00002":
            r["project_ids"] = "P2"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    path = f"{corpus}/procurement/packages.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        pk = list(csv.DictReader(fh))
    pk.append(dict(pk[0], package_id="P2-1", project_id="P2", description=OTHER[2], description_clean=OTHER[2]))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(pk[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(pk)
    for name in ("cra", "assets"):                          # the search runs read the index they were made on
        folder, _ = search.run_folder(name, corpus)
        if folder != before[name]:
            shutil.copytree(before[name], folder)
        with open(f"{folder}/manifest.json", "w") as fh:
            json.dump({"index_sha256": search._sha_file(f"{corpus}/embeddings/sentence_index.csv")}, fh)
    root = embed.cache_root(corpus, embed.DEFAULT_MODEL, 4)
    embed.write_cached(root, hashlib.sha256(OTHER[2].encode()).hexdigest(), numpy.eye(4, dtype="float32")[1].tobytes())


def _asked(fake_model, fake_chat):
    return [json.dumps(b, ensure_ascii=False) for b in fake_model] + fake_chat.prompts()


def test_a_one_project_run_asks_nothing_about_another_project_in_any_step(schema, wide, fake_model, fake_chat,  # noqa: F811
                                                                          monkeypatch, capsys):
    corpus = wide
    _second_project(corpus)
    # every project first: P2's sentences are scored, committed and digital, and asked in every step
    assert run(corpus, "--from", "classify", "--to", "link", "--max-spend", "1") == 0
    first = _asked(fake_model, fake_chat)
    assert any(OTHER[0] in a for a in first) and any(OTHER[2] in a for a in first)
    assert any(OTHER[0] in a and "which type it is" in a for a in first)
    assert any(OTHER[0] in a and "investment or to a policy instrument" in a for a in first)
    # then a new, empty answer store, so every step must ask again whatever is in its scope
    fresh = db.SCHEMA + "_store2"
    monkeypatch.setattr(db, "STORE", fresh)
    monkeypatch.setenv("WBG_STORE", fresh)
    try:
        fake_model.clear()
        fake_chat.calls.clear()
        capsys.readouterr()
        assert run(corpus, "--from", "classify", "--to", "link", "--project", "P1", "--max-spend", "1") == 0
        out = capsys.readouterr().out
        for stage in ("classify", "measure", "link"):
            assert f"#### {stage}" in out
        asked = _asked(fake_model, fake_chat)
        for step in ("hazard assessment of tower sites", "which type it is", "investment or to a policy instrument",
                     "Which digital asset", "Supply of towers"):
            assert any(step in a for a in asked), step                      # every step asked about P1
        for text in OTHER:
            assert not any(text in a for a in asked), text
    finally:
        schema.rollback()
        schema.execute(f"DROP SCHEMA IF EXISTS {fresh} CASCADE")
        schema.commit()
