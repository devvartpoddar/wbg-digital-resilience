-- The k search units nearest a given unit, by cosine distance between their
-- stored embeddings. No new embedding is made, so no outside call:
-- SELECT * FROM wbg.similar_units({{unit_id}}, 20).
DROP FUNCTION IF EXISTS wbg.similar(text, integer);
DROP FUNCTION IF EXISTS wbg.similar_units(text, integer);
CREATE FUNCTION wbg.similar_units(unit_id text, k integer DEFAULT 20)
RETURNS TABLE (unit_id text, unit_kind text, paragraph_id text, project_ids text[],
               distance double precision, text text)
LANGUAGE sql STABLE AS $$
  SELECT v.unit_id, u.unit_kind, v.paragraph_id, v.project_ids,
         (v.embedding <=> (SELECT embedding FROM wbg.sentence_vectors s WHERE s.unit_id = similar_units.unit_id)),
         u.text
  FROM wbg.sentence_vectors v
  LEFT JOIN wbg.search_units u ON u.unit_id = v.unit_id
  WHERE v.unit_id <> similar_units.unit_id
  ORDER BY v.embedding <=> (SELECT embedding FROM wbg.sentence_vectors s WHERE s.unit_id = similar_units.unit_id)
  LIMIT k
$$;
