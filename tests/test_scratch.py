"""A scratch measure (src/scratch.py): files under data/scratch/, never
listed as a set, run through the measures' funnel as kind scratch, read by the
Explore views and by nothing the commitments read."""
import json

import db
import decide
import funnel
import labels
import questions as Q
import scratch
import search


def _scratch(corpus, monkeypatch, slug="e_waste", phrases=("assess hazards",), patterns=()):
    monkeypatch.setenv("WBG_DATA", corpus)
    scratch.write_files(corpus, slug, "E-waste", "Hazard assessment of e-waste at ICT sites.", list(phrases),
                        list(patterns))
    return slug


def test_a_scratch_set_is_found_by_name_but_never_listed(corpus, monkeypatch):
    slug = _scratch(corpus, monkeypatch)
    assert labels.is_scratch(slug) and not labels.is_scratch("cra")
    assert slug not in labels.label_sets() and slug not in labels.label_sets(include_inactive=True)
    assert labels.load_set(slug)[0]["name"] == "E-waste"
    assert funnel.kind_of(slug) == "scratch" and funnel.kind_of("cra") == "measure"


def test_a_scratch_run_scores_as_kind_scratch_and_the_explore_views_read_it(pg, corpus, fake_model, monkeypatch):
    slug = _scratch(corpus, monkeypatch, patterns=(r"\bhazard assessment\b",))
    log = lambda *_: None                                                      # noqa: E731
    import numpy
    import embed
    # the definition is not in the fixture's cache: a fake embedder gives it a vector near nothing
    monkeypatch.setattr(embed, "embed_texts", lambda texts, **kw: (
        [numpy.eye(4, dtype="float32")[3] for _ in texts], {"model": embed.DEFAULT_MODEL, "tokens": 0, "cost": 0.0, "rate_limit": {}}))
    assert search.search(slug, corpus, api_key="k", log=log) is not None
    c = Q.Corpus(corpus)
    out = funnel.run(pg, corpus, slug, corpus=c, api_key="k", log=log)
    assert out is not None
    rows = db.rows(pg, f"SELECT kind, item_id, score, route FROM {db.SCHEMA}.scores WHERE label_set = %s", (slug,))
    assert rows and {r[0] for r in rows} == {"scratch"}
    assert max(float(r[2]) for r in rows) > 0.85                               # the hazard sentence
    scratch.record(pg, slug, "E-waste", "Collection and recycling.", ["assess hazards"], [r"\bhazard assessment\b"], "test")
    # the publish neither reads the scratch set nor fails on the Explore views
    import load_pg
    funnel.run(pg, corpus, "assets", corpus=c, api_key="k", log=log)
    funnel.run(pg, corpus, "cra", corpus=c, api_key="k", log=log)
    with load_pg.transaction(pg):
        load_pg.load(pg, corpus, log=log)
    decide.publish(pg, corpus, corpus=c, log=log)
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.measure_findings WHERE label_set = %s", (slug,))[0] == 0
    assert db.one(pg, f"SELECT count(*) FROM {db.SCHEMA}.labels WHERE label_set = %s", (slug,))[0] == 0
    found = db.rows(pg, f"SELECT project_id, is_committed, text FROM {db.SCHEMA}.scratch_findings WHERE label_set = %s", (slug,))
    assert found and any(f[1] for f in found) and all(f[0] == "P1" for f in found)
    summ = db.one(pg, f"SELECT sentences_asked, sentences_committed, projects FROM {db.SCHEMA}.scratch_summary "
                      f"WHERE label_set = %s", (slug,))
    assert summ[0] >= summ[1] >= 1 and summ[2] == 1
    lines = scratch.summary(pg, c, slug, 0.5)
    assert lines[0].startswith(f"{slug}:") and "P1" in lines[1]
    assert any(r[0] == slug for r in scratch.listing(pg))


def test_expand_parses_the_generative_answer_and_drops_a_bad_pattern(pg, monkeypatch):
    import checks
    answer = {"name": "E-waste management", "definition": "Collection, recycling or safe disposal of ICT equipment.",
              "phrases": ["The project will finance e-waste collection.", "  Recycling of obsolete ICT equipment  "],
              "patterns": [r"\be-?waste\b", "(unbalanced"]}
    monkeypatch.setattr(checks, "generate", lambda conn, body, key, dry: "Here you go:\n" + json.dumps(answer))
    got = scratch.expand(pg, "e-waste", "k")
    assert got["name"] == "E-waste management" and got["patterns"] == [r"\be-?waste\b"]
    assert got["phrases"][1] == "Recycling of obsolete ICT equipment"
    assert scratch.slug_of("E-Waste!! from ICT") == "e_waste_from_ict"
