-- The asset map: for each paragraph the asset the decision model puts
-- highest, and every grade with the label's name and the text.
DROP VIEW IF EXISTS wbg.paragraph_grades_with_text;
CREATE VIEW wbg.paragraph_grades_with_text AS
SELECT g.label_set, g.label_id, l.label_name, g.was_asked, g.best_cosine, g.most_likely_grade,
       g.expected_grade, g.grade_confidence, g.probability_grade_0, g.probability_grade_1,
       g.probability_grade_2, g.probability_grade_3, g.paragraph_id, g.document_id,
       g.project_ids, pa.component_number, pa.component_name, pa.section_title, pa.block, pa.text
FROM wbg.paragraph_grades g
LEFT JOIN wbg.labels l ON l.label_set = g.label_set AND l.label_id = g.label_id
LEFT JOIN wbg.paragraphs pa USING (paragraph_id);
COMMENT ON VIEW wbg.paragraph_grades_with_text IS
  'Every paragraph''s grade for every label, with the label''s name and the paragraph''s text. Grades are the model''s; unmeasured until a graded sample has been evaluated.';

DROP VIEW IF EXISTS wbg.asset_map;
CREATE VIEW wbg.asset_map AS
SELECT DISTINCT ON (g.paragraph_id)
       g.paragraph_id, g.document_id, g.project_ids, pa.component_number, pa.component_name,
       g.label_id AS best_asset_id, l.label_name AS best_asset_name,
       ag.group_id AS best_asset_group_id, ag.group_name AS best_asset_group_name,
       (g.probability_grade_2 + g.probability_grade_3) AS probability_supported_or_financed,
       g.most_likely_grade, pa.section_title, pa.block, pa.text
FROM wbg.paragraph_grades g
LEFT JOIN wbg.labels l ON l.label_set = g.label_set AND l.label_id = g.label_id
LEFT JOIN wbg.label_groups ag ON ag.label_set = g.label_set AND ag.label_id = g.label_id
LEFT JOIN wbg.paragraphs pa USING (paragraph_id)
WHERE g.label_set = 'assets' AND g.was_asked
ORDER BY g.paragraph_id, (g.probability_grade_2 + g.probability_grade_3) DESC, g.label_id;
COMMENT ON VIEW wbg.asset_map IS
  'One row per graded paragraph: the digital asset the decision model puts highest, its reading group (label_groups) and its probability of being supported or financed (grade 2 or more). Filter on the probability; no cut-off is built in.';
