-- "Try it" on the New measure page: a phrase someone types, searched
-- against the corpus through wbg.search_phrase (one embedding call per new
-- phrase, through the box's openrouter() function; this file is skipped
-- where that function does not exist), with each unit's project and the
-- measures that already count it (is_committed: the posterior at the floor).
--   SELECT * FROM wbg.try_phrase({{phrase}}, 50)
DROP FUNCTION IF EXISTS wbg.try_phrase(text, integer);
CREATE FUNCTION wbg.try_phrase(phrase text, k integer DEFAULT 50)
RETURNS TABLE (unit_id text, unit_kind text, paragraph_id text, document_id text,
               project_id text, project_name text, country_name text, score double precision,
               text text, committed_in text[])
LANGUAGE sql VOLATILE AS $$
  SELECT s.unit_id, s.unit_kind, s.paragraph_id, s.document_id, pid, i.project_name,
         i.country_name, s.score, s.text,
         (SELECT array_agg(DISTINCT f.label_set ORDER BY f.label_set) FROM wbg.measure_findings f
           WHERE f.unit_id = s.unit_id AND f.is_committed) AS committed_in
  FROM wbg.search_phrase(try_phrase.phrase, try_phrase.k) s
  CROSS JOIN LATERAL unnest(s.project_ids) pid
  LEFT JOIN wbg.project_info i ON i.project_id = pid
  ORDER BY s.score DESC, s.unit_id, pid
$$;
COMMENT ON FUNCTION wbg.try_phrase IS
  'The k units nearest a typed phrase (wbg.search_phrase: one embedding call per new phrase), one row per unit and project, with the project''s name and country and the measures that already count the unit as committed (committed_in: the posterior at each measure''s floor). A way to see whether a new measure''s wording finds anything before it is run.';
