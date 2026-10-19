-- Search the corpus for a new phrase from inside the database: the phrase
-- is embedded by the same pinned model and provider as the units, through
-- the box's openrouter() function (part of the Metabase setup in the
-- yggdrasil repo; this file is skipped where it does not exist), and ranked
-- against the stored vectors. In Metabase:
--
--   SELECT * FROM wbg.search_phrase({{phrase}}, 50)
--
-- {VECTOR} is replaced by the load with the sentence_vectors column type.
-- openrouter() is VOLATILE and called once per query, never per row.
DROP FUNCTION IF EXISTS wbg.search(text, integer);
DROP FUNCTION IF EXISTS wbg.search_phrase(text, integer);
CREATE FUNCTION wbg.search_phrase(phrase text, k integer DEFAULT 50)
RETURNS TABLE (unit_id text, unit_kind text, paragraph_id text, document_id text,
               project_ids text[], score double precision, text text)
LANGUAGE sql VOLATILE AS $$
  WITH q AS (
    SELECT ((openrouter('embeddings', jsonb_build_object(
                'model', 'openai/text-embedding-3-large',
                'input', jsonb_build_array(phrase),
                'provider', jsonb_build_object('order', jsonb_build_array('openai'),
                                               'allow_fallbacks', false)))
             -> 'data' -> 0 ->> 'embedding')::{VECTOR}) AS v)
  SELECT v.unit_id, u.unit_kind, v.paragraph_id, v.document_id, v.project_ids,
         1 - (v.embedding <=> (SELECT q.v FROM q)) AS score,
         u.text
  FROM wbg.sentence_vectors v
  LEFT JOIN wbg.search_units u ON u.unit_id = v.unit_id
  ORDER BY v.embedding <=> (SELECT q.v FROM q)
  LIMIT k
$$;
