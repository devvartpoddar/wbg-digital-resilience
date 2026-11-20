-- The views the band rule, the digital rule and (later) the link strengths and
-- activities add. Existing views are unchanged.

-- Committed sentences the digital question says are not digital: they stay in
-- measure_findings (is_committed true, is_digital false) and leave the
-- dashboards' commitment views (16_commitments.sql). One row per project, so
-- a paragraph in two projects gives two rows.
DROP VIEW IF EXISTS wbg.not_digital_findings CASCADE;
CREATE VIEW wbg.not_digital_findings AS
SELECT m.label_set, m.label_id, l.label_name AS measure_name, pp.project_id, pr.cohort_set,
       pr.project_name, pr.country_name, m.document_id, m.component_number, m.unit_id,
       m.posterior_probability, m.score_first, m.score_context, m.second_grade, m.decided_by,
       m.digital_p, m.instrument, m.text, pa.section_title
FROM wbg.measure_findings m
LEFT JOIN wbg.labels l ON l.label_set = m.label_set AND l.label_id = m.label_id
LEFT JOIN wbg.paragraphs pa USING (paragraph_id)
LEFT JOIN wbg.paragraph_projects pp ON pp.paragraph_id = m.paragraph_id
LEFT JOIN wbg.projects pr ON pr.project_id = pp.project_id
WHERE m.was_sent_to_model AND m.is_committed AND NOT m.is_digital;
COMMENT ON VIEW wbg.not_digital_findings IS
  'Committed sentences the digital question says are not a digital asset or the digital sector (digital_p below digital.floor, or never asked): kept here, left out of every commitment. One row per sentence, measure and project; with the score, the two second looks and who decided. The model''s reading, not a person''s.';
