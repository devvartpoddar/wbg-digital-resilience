-- Per label and paragraph, its best-scoring search unit, with the label's
-- name and the text. The rollup itself is the table wbg.search_paragraphs,
-- written by the load; this view only joins names and text to it.
DROP VIEW IF EXISTS wbg.v_search_paragraphs;
CREATE VIEW wbg.v_search_paragraphs AS
SELECT s.label_set, s.label_id, t.name AS label_name, s.paragraph_id, s.project_ids,
       s.best_score, s.best_percentile, s.best_unit_id, s.best_query_id, s.units_hit,
       pa.component_number, pa.component_name, pa.section_title, pa.text
FROM wbg.search_paragraphs s
LEFT JOIN wbg.taxonomy t ON t.label_set = s.label_set AND t.label_id = s.label_id
LEFT JOIN wbg.paragraphs pa USING (paragraph_id);
