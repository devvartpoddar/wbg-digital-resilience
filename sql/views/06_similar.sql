-- The k search units nearest a given unit, by cosine distance between their
-- stored embeddings. No new embedding is made, so no outside call: in Metabase,
-- SELECT * FROM wbg.similar({{unit_id}}, 20).
DROP FUNCTION IF EXISTS wbg.similar(text, integer);
CREATE FUNCTION wbg.similar(unit text, k integer DEFAULT 20)
RETURNS TABLE (unit_id text, paragraph_id text, project_ids text, distance double precision,
               text text)
LANGUAGE sql STABLE AS $$
  SELECT v.unit_id, v.paragraph_id, v.project_ids,
         (v.embedding <=> (SELECT embedding FROM wbg.sentence_vectors WHERE unit_id = unit)),
         COALESCE(s.text, pa.text)
  FROM wbg.sentence_vectors v
  LEFT JOIN wbg.sentences s ON s.sentence_id = v.unit_id
  LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = v.unit_id
  WHERE v.unit_id <> unit
  ORDER BY v.embedding <=> (SELECT embedding FROM wbg.sentence_vectors WHERE unit_id = unit)
  LIMIT k
$$;
