-- The asset map: every sentence's grade for every asset, and each
-- sentence's asset as the pipeline decides it (wbg.unit_assets).
DROP VIEW IF EXISTS wbg.paragraph_grades_with_text;
CREATE VIEW wbg.paragraph_grades_with_text AS
SELECT g.label_set, g.label_id, l.label_name, g.unit_id, g.was_asked, g.best_cosine,
       g.posterior_probability, g.probability_grade_0, g.probability_grade_1,
       g.probability_grade_2, g.probability_grade_3, g.paragraph_id, g.document_id,
       g.project_ids, pa.component_number, pa.component_name, pa.section_title, pa.block,
       su.text AS sentence_text, pa.text AS paragraph_text
FROM wbg.paragraph_grades g
LEFT JOIN wbg.labels l ON l.label_set = g.label_set AND l.label_id = g.label_id
LEFT JOIN wbg.search_units su ON su.unit_id = g.unit_id
LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = g.paragraph_id;
COMMENT ON VIEW wbg.paragraph_grades_with_text IS
  'Every sentence''s answer for every asset, with the asset''s name, the sentence and its paragraph. posterior_probability is what decisions read; the grade probabilities are the model''s raw answer. Unmeasured until a graded sample has been evaluated.';

DROP VIEW IF EXISTS wbg.asset_map;
CREATE VIEW wbg.asset_map AS
SELECT ua.unit_id, ua.paragraph_id, su.document_id, ua.project_ids, pa.component_number,
       pa.component_name, ua.asset_id, l.label_name AS asset_name, ua.probability,
       ua.asset_level, pa.section_title, pa.block, su.text AS sentence_text
FROM wbg.unit_assets ua
JOIN wbg.search_units su ON su.unit_id = ua.unit_id
LEFT JOIN wbg.labels l ON l.label_set = 'assets' AND l.label_id = ua.asset_id
LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = ua.paragraph_id
WHERE ua.asset_level <> 'none';
COMMENT ON VIEW wbg.asset_map IS
  'One row per sentence the pipeline gives an asset: its own best asset at the control panel''s asset.floor (asset_level unit), else the decayed sum over its paragraph''s earlier sentences (asset_level paragraph). The model''s reading, not a person''s; documented is not done.';
