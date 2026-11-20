-- The views of the band rule, the digital rule, the link strengths and the
-- activities.

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
       cl.strength, cl.shared_asset_ids, cl.strong_p
FROM wbg.commitment_package_links cl
JOIN wbg.measure_findings_in_context f ON f.label_set = cl.label_set AND f.unit_id = cl.unit_id
                                       AND f.project_id = cl.project_id
JOIN wbg.packages p ON p.package_id = cl.package_id;
COMMENT ON VIEW wbg.commitment_package_links_in_context IS
  'One row per committed, digital sentence and package of the same project that are linked, with the commitment''s text, measure, asset and type and the package''s plan columns. strength is strong when the package, asked on its own, is about the measure (strong_p at link.strong_yes or more), or asset when they share a top-level asset (shared_asset_ids) and nothing more. Both are models'' readings; an asset link says the same kind of asset, not that the package delivers the commitment.';

-- Activities: the distinct things a project commits to do for a measure, with
-- what its sentences say about assets, kinds, instrument, types and links.
DROP VIEW IF EXISTS wbg.activity_summary;
CREATE VIEW wbg.activity_summary AS
SELECT a.activity_id, a.project_id, a.label_set, l.label_name AS measure_name,
       min(a.activity_label) AS activity_label, count(DISTINCT a.unit_id) AS sentences,
       (SELECT array_agg(DISTINCT x.asset_id ORDER BY x.asset_id) FROM wbg.unit_assets x
         WHERE x.unit_id = ANY (array_agg(a.unit_id)) AND x.asset_id <> '') AS asset_ids,
       (SELECT array_agg(DISTINCT x.subtype_id ORDER BY x.subtype_id) FROM wbg.unit_assets x
         WHERE x.unit_id = ANY (array_agg(a.unit_id)) AND x.subtype_id <> '') AS subtype_ids,
       (SELECT array_agg(DISTINCT f.instrument ORDER BY f.instrument) FROM wbg.measure_findings f
         WHERE f.label_set = a.label_set AND f.unit_id = ANY (array_agg(a.unit_id)) AND f.instrument <> '') AS instruments,
       (SELECT array_agg(DISTINCT t ORDER BY t) FROM wbg.measure_findings f,
               LATERAL unnest(f.type_ids_above_threshold) AS t
         WHERE f.label_set = a.label_set AND f.unit_id = ANY (array_agg(a.unit_id))) AS type_ids,
       (SELECT array_agg(DISTINCT c.strength ORDER BY c.strength) FROM wbg.commitment_package_links c
         WHERE c.label_set = a.label_set AND c.project_id = a.project_id
           AND c.unit_id = ANY (array_agg(a.unit_id))) AS link_strengths,
       (SELECT count(DISTINCT c.package_id) FROM wbg.commitment_package_links c
         WHERE c.label_set = a.label_set AND c.project_id = a.project_id
           AND c.unit_id = ANY (array_agg(a.unit_id)) AND c.strength = 'strong') AS strong_packages,
       (SELECT count(DISTINCT c.package_id) FROM wbg.commitment_package_links c
         WHERE c.label_set = a.label_set AND c.project_id = a.project_id
           AND c.unit_id = ANY (array_agg(a.unit_id))) AS linked_packages
FROM wbg.activities a
LEFT JOIN wbg.labels l ON l.label_set = a.label_set
GROUP BY a.activity_id, a.project_id, a.label_set, l.label_name;
COMMENT ON VIEW wbg.activity_summary IS
  'One row per activity (a project''s distinct action for a measure, grouped by the chat model from its committed, digital sentences): its sentence count and, gathered from those sentences, the asset ids and kinds, the instruments (investment, policy), the types, the strengths of the package links (strong, asset) and how many packages are linked and how many strongly. The models'' reading, not a person''s.';
