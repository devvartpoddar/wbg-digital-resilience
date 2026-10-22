-- One row per project and measure: what the measure run found there, for a
-- dashboard's project list and map. Counts are the pipeline's decisions
-- (is_committed: the posterior at each measure's floor), not a person's;
-- linked_asset_ids lists every asset the committed units link to.
DROP VIEW IF EXISTS wbg.measure_findings_by_project;
CREATE VIEW wbg.measure_findings_by_project AS
WITH u AS (
  SELECT m.*, pp.project_id, d.document_kind
  FROM wbg.measure_findings m
  JOIN wbg.paragraph_projects pp ON pp.paragraph_id = m.paragraph_id
  LEFT JOIN wbg.documents d ON d.document_id = m.document_id
),
lists AS (
  SELECT u.label_set, u.label_id, u.project_id,
         ARRAY_AGG(DISTINCT linked_asset_id ORDER BY linked_asset_id)
           FILTER (WHERE linked_asset_id IS NOT NULL) AS linked_asset_ids,
         ARRAY_AGG(DISTINCT t ORDER BY t) FILTER (WHERE t IS NOT NULL) AS type_ids
  FROM u
  LEFT JOIN LATERAL UNNEST(u.type_ids_above_threshold) t ON true
  WHERE u.is_committed
  GROUP BY u.label_set, u.label_id, u.project_id
)
SELECT u.label_set, u.label_id, l.label_name AS measure_name, u.project_id, pr.cohort_set,
       pr.project_name, pr.country_name, pr.region, pr.approval_fiscal_year, pr.practice,
       pr.commitment_usd_millions,
       COUNT(*) FILTER (WHERE u.is_committed) AS committed_units,
       COUNT(*) FILTER (WHERE u.is_committed AND u.document_kind = 'pad')
         AS committed_units_in_appraisal_document,
       COUNT(DISTINCT u.document_id) FILTER (WHERE u.is_committed) AS documents_with_commitment,
       COUNT(*) FILTER (WHERE u.was_sent_to_model) AS units_read,
       MAX(u.posterior_probability) FILTER (WHERE u.is_committed) AS best_posterior_probability,
       MAX(li.linked_asset_ids) AS linked_asset_ids,
       MAX(li.type_ids) AS type_ids
FROM u
LEFT JOIN lists li ON li.label_set = u.label_set AND li.label_id = u.label_id
                  AND li.project_id = u.project_id
LEFT JOIN wbg.projects pr ON pr.project_id = u.project_id
LEFT JOIN wbg.labels l ON l.label_set = u.label_set AND l.label_id = u.label_id
GROUP BY u.label_set, u.label_id, l.label_name, u.project_id, pr.cohort_set, pr.project_name, pr.country_name,
         pr.region, pr.approval_fiscal_year, pr.practice, pr.commitment_usd_millions;
COMMENT ON VIEW wbg.measure_findings_by_project IS
  'One row per project and measure: units that count (the posterior at the measure''s floor), the assets they link to and their types. Count projects, not units: one project with a long restructuring paper can hold hundreds of units (committed_units_in_appraisal_document leaves restructuring and additional-financing papers out). The pipeline''s decisions, not a person''s, and coverage is below 100 per cent.';
