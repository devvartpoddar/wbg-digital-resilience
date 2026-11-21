-- The views of the band rule, the digital rule and the link levels.

-- Sentences that pass the measure rule but that the digital question says are not
-- digital: they stay in measure_findings (passes_measure_rule true, is_digital
-- false, so is_committed false) and leave the
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
WHERE m.was_sent_to_model AND m.passes_measure_rule AND NOT m.is_digital;
COMMENT ON VIEW wbg.not_digital_findings IS
  'Sentences that pass the measure rule but that the digital question says are not about a digital technology (digital_p below digital.floor, or never asked): listed here, never kept (is_committed false), left out of every commitment. One row per sentence, measure and project; with the score, the two second looks and who decided. The model''s reading, not a person''s.';

-- Link strengths: a committed, digital sentence and a package of the same
-- project, with the sentence's measure, asset and type and the package's plan
-- columns. strength strong: the package, asked on its own, is about the
-- measure; asset: they share a top-level asset and nothing more. Filter on
-- strength.
DROP VIEW IF EXISTS wbg.commitment_package_links_in_context;
CREATE VIEW wbg.commitment_package_links_in_context AS
SELECT cl.label_set, f.label_id, f.measure_name, cl.project_id, f.cohort_set, f.project_name,
       f.country_name, cl.unit_id, f.is_committed, f.posterior_probability,
       f.linked_asset_id, f.linked_asset_level, f.best_type_id, f.best_type_name,
       f.text AS commitment_text, f.component_number AS commitment_component_number,
       cl.package_id, p.description_clean AS package_description, cl.package_category, p.method_code,
       p.status, p.planned_date, p.estimated_amount_usd, p.in_latest_plan,
       p.component_number AS package_component_number, p.component_name AS package_component_name,
       wbg.is_upcoming(p.status) AS is_upcoming,
       cl.strength, cl.shared_asset_ids, cl.strong_p, cl.level, cl.level_rank, cl.same_type,
       cl.component_relation, cl.sentence_component, cl.package_component, cl.package_cancelled,
       cl.package_generic
FROM wbg.commitment_package_links cl
JOIN wbg.measure_findings_in_context f ON f.label_set = cl.label_set AND f.unit_id = cl.unit_id
                                       AND f.project_id = cl.project_id
JOIN wbg.packages p ON p.package_id = cl.package_id
ORDER BY cl.label_set, cl.unit_id, cl.level_rank, cl.package_id;
COMMENT ON VIEW wbg.commitment_package_links_in_context IS
  'One row per kept sentence and package of the same project that are linked, with the commitment''s text, measure, asset and type and the package''s plan columns, in order of level (level_rank 1 strong, 2 same asset type and component, 3 same asset type, 4 same asset and component, 5 same asset). strength is strong when the package, asked on its own, is about the measure (strong_p at link.strong_yes or more), or asset when they share a top-level asset (shared_asset_ids) and nothing more; package_cancelled and package_generic flag the package. Both are models'' readings; an asset link says the same asset, not that the package delivers the commitment.';
