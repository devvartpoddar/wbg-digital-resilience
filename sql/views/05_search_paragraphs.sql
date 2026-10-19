-- The search rollup with the label's name and the paragraph's text.
DROP VIEW IF EXISTS wbg.search_paragraphs_with_text;
CREATE VIEW wbg.search_paragraphs_with_text AS
SELECT s.label_set, s.label_id, l.label_name, s.paragraph_id, s.project_ids,
       s.best_embedding_score, s.best_embedding_percentile, s.best_unit_id, s.best_query_id,
       s.units_hit_count, pa.component_number, pa.component_name, pa.section_title, pa.text
FROM wbg.search_paragraphs s
LEFT JOIN wbg.labels l ON l.label_set = s.label_set AND l.label_id = s.label_id
LEFT JOIN wbg.paragraphs pa USING (paragraph_id);
COMMENT ON VIEW wbg.search_paragraphs_with_text IS
  'Per label and paragraph, the best-scoring search unit, with the label''s name and the text. No cut-off is applied.';
