-- Per label and paragraph, its best-scoring search unit, with the text.
DROP VIEW IF EXISTS wbg.v_search_paragraphs;
CREATE VIEW wbg.v_search_paragraphs AS
SELECT DISTINCT ON (s.label_set, s.label_id, s.paragraph_id)
       s.label_set, s.label_id, t.name AS label_name, s.paragraph_id, s.project_ids,
       s.score AS best_score, s.percentile AS best_percentile, s.unit_id AS best_unit_id,
       s.best_query, pa.component_number, pa.component_name, pa.text
FROM wbg.search_best s
LEFT JOIN wbg.taxonomy t ON t.label_set = s.label_set AND t.label_id = s.label_id
LEFT JOIN wbg.paragraphs pa USING (paragraph_id)
ORDER BY s.label_set, s.label_id, s.paragraph_id, s.score DESC;
