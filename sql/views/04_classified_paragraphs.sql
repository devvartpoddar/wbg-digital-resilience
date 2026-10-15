-- Each paragraph's grade for each label, with the label's name and the text.
DROP VIEW IF EXISTS wbg.v_classified_paragraphs;
CREATE VIEW wbg.v_classified_paragraphs AS
SELECT c.label_set, c.label_id, t.name AS label_name, c.grade, c.expected, c.confidence,
       c.p0, c.p1, c.p2, c.p3, c.paragraph_id, c.project_ids, pa.component_number,
       pa.component_name, pa.section_title, pa.text
FROM wbg.classify_scores c
LEFT JOIN wbg.taxonomy t ON t.label_set = c.label_set AND t.label_id = c.label_id
LEFT JOIN wbg.paragraphs pa USING (paragraph_id);
