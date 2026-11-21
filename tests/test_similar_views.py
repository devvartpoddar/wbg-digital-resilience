"""Views 07 (similar_units) and 18 (similar_projects) really run on the invented dataset of
tests/demo_seed.py. Where pgvector is absent, a stand-in gives float8[] embeddings a cosine distance
operator <=> in the test's own schema (put first on the search path), so the SQL is executed, not
skipped."""
import pytest

import demo_seed
from wbg_portfolio import db
from wbg_portfolio import load_pg

STAND_IN = """
CREATE FUNCTION {s}.cosdist(a float8[], b float8[]) RETURNS float8 LANGUAGE sql IMMUTABLE AS
$$ SELECT 1 - (SELECT sum(x * y) FROM unnest(a, b) t(x, y))
               / (sqrt((SELECT sum(x * x) FROM unnest(a) x)) * sqrt((SELECT sum(y * y) FROM unnest(b) y))) $$;
CREATE OPERATOR {s}.<=> (LEFTARG = float8[], RIGHTARG = float8[], FUNCTION = {s}.cosdist);
"""


@pytest.fixture
def demo(pg, monkeypatch):
    s = db.SCHEMA
    has_vector = db.one(pg, "SELECT count(*) FROM pg_extension WHERE extname = 'vector'")[0] > 0
    finish = load_pg.finish

    def with_vectors(conn, log=print):
        typ = "vector(3)" if has_vector else "float8[]"
        if not has_vector:
            conn.execute(STAND_IN.format(s=s))
            conn.execute(f"SET search_path = {s}, public")
        # three-number embeddings, the same for sentences of the same measure, so near units share a measure
        conn.execute(f"""CREATE TABLE {s}.sentence_vectors AS
            SELECT unit_id, paragraph_id, ARRAY[project_id] AS project_ids,
                   (ARRAY[1 + (hashtext(label_set) % 7 + 7) % 7, 1 + length(unit_id) % 3, 1])::{typ}
                     AS embedding
            FROM (SELECT DISTINCT ON (f.unit_id) f.unit_id, f.paragraph_id, f.label_set, pp.project_id
                  FROM {s}.measure_findings f JOIN {s}.paragraph_projects pp USING (paragraph_id)
                  ORDER BY f.unit_id, f.label_set, pp.project_id) x""")
        return finish(conn, log=log)
    monkeypatch.setattr(load_pg, "finish", with_vectors)
    logged = []
    demo_seed.publish(pg, demo_seed.Demo(seed=3, n_projects=14).build().finish_tables(), log=logged.append)
    pg.commit()
    assert not any("skipped (no sentence_vectors" in line for line in logged)
    return pg


def test_similar_units_and_similar_projects_run(demo):
    s = db.SCHEMA
    u = db.one(demo, f"SELECT unit_id FROM {s}.sentence_vectors ORDER BY unit_id LIMIT 1")[0]
    near = db.rows(demo, f"SELECT unit_id, distance FROM {s}.similar_units(%s, 5)", (u,))
    assert len(near) == 5 and all(d is not None for _, d in near)
    assert [d for _, d in near] == sorted(d for _, d in near)


def test_has_same_commitment_is_true_for_a_project_with_the_same_key(demo):
    s = db.SCHEMA
    rows, same, expected = 0, 0, 0
    for (cid,) in db.rows(demo, f"SELECT commitment_id FROM {s}.commitments WHERE commitment_key <> 'paragraph'"):
        for r in db.rows(demo, f"SELECT project_id, has_same_commitment FROM {s}.similar_projects(%s, 50)", (cid,)):
            rows += 1
            same += bool(r[1])
            expected += db.one(demo, f"""
                SELECT EXISTS (SELECT 1 FROM {s}.commitments o, {s}.commitments c
                  WHERE c.commitment_id = %s AND o.project_id = %s AND o.label_set = c.label_set
                    AND o.commitment_key = c.commitment_key
                    AND coalesce(o.asset_id, '') = coalesce(c.asset_id, '')
                    AND coalesce(o.subtype_id, '') = coalesce(c.subtype_id, '')
                    AND coalesce(o.type_id, '') = coalesce(c.type_id, ''))""", (cid, r[0]))[0]
    assert rows and expected, "the demo has projects near a commitment that hold the same key"
    assert same == expected
