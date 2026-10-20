-- "Projects to consider": other projects whose documents say something
-- close to a commitment, by the stored embeddings (no model call).
--   SELECT * FROM wbg.similar_projects({{commitment_id}}, 6)
-- The commitment's three strongest sentences each fetch their 60 nearest
-- units (wbg.similar_units); the units' projects, less the commitment's own,
-- are ranked by the closest of them.
DROP FUNCTION IF EXISTS wbg.similar_projects(text, integer);
CREATE FUNCTION wbg.similar_projects(commitment_id text, k integer DEFAULT 6)
RETURNS TABLE (project_id text, project_name text, country_name text, closeness double precision,
               close_sentences bigint, commits_to_measure boolean, has_same_commitment boolean)
LANGUAGE sql STABLE AS $$
  WITH c AS (
    SELECT * FROM wbg.commitments x WHERE x.commitment_id = similar_projects.commitment_id
  ),
  seeds AS (
    SELECT DISTINCT ON (u.unit_id) u.unit_id, u.probability_grade_3
    FROM wbg.commitment_units u WHERE u.commitment_id = similar_projects.commitment_id
    ORDER BY u.unit_id, u.probability_grade_3 DESC
  ),
  top3 AS (SELECT unit_id FROM seeds ORDER BY probability_grade_3 DESC, unit_id LIMIT 3),
  near AS (
    SELECT s.unit_id AS near_unit, s.distance, unnest(s.project_ids) AS project_id
    FROM top3 t CROSS JOIN LATERAL wbg.similar_units(t.unit_id, 60) s
  )
  SELECT n.project_id, i.project_name, i.country_name,
         1 - min(n.distance) AS closeness, count(DISTINCT n.near_unit) AS close_sentences,
         EXISTS (SELECT 1 FROM wbg.commitments o, c
                 WHERE o.project_id = n.project_id AND o.label_set = c.label_set)
           AS commits_to_measure,
         EXISTS (SELECT 1 FROM wbg.commitments o, c
                 WHERE o.project_id = n.project_id AND o.label_set = c.label_set
                   AND o.type_id = c.type_id) AS has_same_commitment
  FROM near n
  JOIN wbg.project_info i ON i.project_id = n.project_id
  WHERE n.project_id <> (SELECT c.project_id FROM c)
  GROUP BY n.project_id, i.project_name, i.country_name
  ORDER BY closeness DESC, n.project_id
  LIMIT k
$$;
COMMENT ON FUNCTION wbg.similar_projects IS
  'The k other projects whose documents hold sentences nearest a commitment''s three strongest sentences (stored embeddings, 60 nearest each; no model call): closeness is 1 minus the smallest cosine distance, close_sentences how many of the near sentences are theirs, commits_to_measure whether the project has any commitment to the same measure, has_same_commitment whether it has one of the same type. Near wording is not the same commitment: read the sentences.';
