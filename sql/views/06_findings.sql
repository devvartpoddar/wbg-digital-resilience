-- Measure findings and candidates in the context a reader needs.
DROP VIEW IF EXISTS wbg.measure_findings_in_context;
CREATE VIEW wbg.measure_findings_in_context AS
SELECT m.label_set, m.label_id, l.label_name AS measure_name, pp.project_id, pr.project_name,
       pr.country_name, m.document_id, d.document_title, d.document_kind, m.component_number,
       pa.component_name, m.unit_id, m.unit_kind, m.feedback_round, m.route, m.reading_order,
       m.most_likely_grade, m.expected_grade, m.grade_confidence, m.probability_grade_3,
       m.committed_probability_calibrated,
       m.commitment_probability, m.firmness, m.linked_asset_id, m.linked_asset_level,
       m.linked_asset_probability, m.asset_ids_above_threshold,
       m.activity_kinds_above_threshold, m.best_activity_kind, m.conditional_probability,
       m.actor_ids_above_threshold, m.best_actor_id, m.template_project_count,
       m.template_wording_share, m.text,
       pa.text AS paragraph_text, pa.section_title
FROM wbg.measure_findings m
LEFT JOIN wbg.labels l ON l.label_set = m.label_set AND l.label_id = m.label_id
LEFT JOIN wbg.paragraphs pa USING (paragraph_id)
LEFT JOIN wbg.paragraphs_with_document d ON d.paragraph_id = m.paragraph_id
LEFT JOIN wbg.paragraph_projects pp ON pp.paragraph_id = m.paragraph_id
LEFT JOIN wbg.projects pr ON pr.project_id = pp.project_id
WHERE m.was_sent_to_model;
COMMENT ON VIEW wbg.measure_findings_in_context IS
  'Every unit a measure run graded, with the measure''s name, its project (a paragraph in two projects gives two rows), document, component, the grade, the linked asset, how the commitment is delivered, the unit''s text and its paragraph. Filter most_likely_grade = 3 for commitments.';

DROP VIEW IF EXISTS wbg.candidates_in_reading_order;
CREATE VIEW wbg.candidates_in_reading_order AS
SELECT m.label_set, m.label_id, l.label_name AS measure_name, m.reading_order,
       m.commitment_probability, m.firmness, m.matched_patterns, m.route,
       m.embedding_rank, m.embedding_score, pp.project_id, m.document_id, m.component_number,
       m.unit_id, m.unit_kind, u.lead_in, u.text, m.copy_unit_ids
FROM wbg.measure_findings m
LEFT JOIN wbg.labels l ON l.label_set = m.label_set AND l.label_id = m.label_id
LEFT JOIN wbg.search_units u USING (unit_id)
LEFT JOIN wbg.paragraph_projects pp ON pp.paragraph_id = m.paragraph_id
WHERE m.feedback_round = 0;
COMMENT ON VIEW wbg.candidates_in_reading_order IS
  'Each measure''s candidate pool (the units its word patterns or the cosine pool put forward, before any model read them) in the order a person should read them: a pattern hit first, then the commitment probability and the embedding score. The same units as measure_findings at feedback_round 0.';
