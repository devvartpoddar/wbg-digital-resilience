-- One row per project and measure: what the measure run found there, for a
-- dashboard's project list and map. Counts are the decision model's grades
-- (not a person's); committed is each measure's commit floor (is_committed); linked_asset_ids lists every asset the committed units
-- link to, kinds and actors likewise. Nothing is cut off beyond the run's
-- own thresholds (0.5 on the yes/no answers).
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
         ARRAY_AGG(DISTINCT ag.group_id ORDER BY ag.group_id)
           FILTER (WHERE ag.group_id IS NOT NULL) AS linked_asset_group_ids,
         ARRAY_AGG(DISTINCT k ORDER BY k) FILTER (WHERE k IS NOT NULL) AS activity_kinds,
         ARRAY_AGG(DISTINCT a ORDER BY a) FILTER (WHERE a IS NOT NULL) AS actors,
         ARRAY_AGG(DISTINCT t ORDER BY t) FILTER (WHERE t IS NOT NULL) AS type_ids
  FROM u
  LEFT JOIN wbg.label_groups ag ON ag.label_set = 'assets' AND ag.label_id = u.linked_asset_id
  LEFT JOIN LATERAL UNNEST(u.activity_kinds_above_threshold) k ON true
  LEFT JOIN LATERAL UNNEST(u.actor_ids_above_threshold) a ON true
  LEFT JOIN LATERAL UNNEST(u.type_ids_above_threshold) t ON true
  WHERE coalesce(u.is_committed, u.most_likely_grade = 3)
  GROUP BY u.label_set, u.label_id, u.project_id
)
SELECT u.label_set, u.label_id, l.label_name AS measure_name, u.project_id, pr.cohort_set,
       pr.project_name, pr.country_name, pr.region, pr.approval_fiscal_year, pr.practice,
       pr.commitment_usd_millions,
       COUNT(*) FILTER (WHERE coalesce(u.is_committed, u.most_likely_grade = 3)) AS committed_units,
       COUNT(*) FILTER (WHERE coalesce(u.is_committed, u.most_likely_grade = 3) AND u.document_kind = 'pad')
         AS committed_units_in_appraisal_document,
       COUNT(DISTINCT u.document_id) FILTER (WHERE coalesce(u.is_committed, u.most_likely_grade = 3))
         AS documents_with_commitment,
       COUNT(*) FILTER (WHERE u.most_likely_grade = 2) AS planned_units,
       COUNT(*) FILTER (WHERE u.was_sent_to_model) AS units_read,
       MAX(u.committed_probability_calibrated) FILTER (WHERE coalesce(u.is_committed, u.most_likely_grade = 3))
         AS best_committed_probability_calibrated,
       MAX(li.linked_asset_ids) AS linked_asset_ids,
       MAX(li.linked_asset_group_ids) AS linked_asset_group_ids,
       MAX(li.activity_kinds) AS activity_kinds,
       MAX(li.actors) AS actors,
       MAX(li.type_ids) AS type_ids,
       COUNT(*) FILTER (WHERE coalesce(u.is_committed, u.most_likely_grade = 3) AND u.conditional_probability >= 0.5)
         AS conditional_committed_units
FROM u
LEFT JOIN lists li ON li.label_set = u.label_set AND li.label_id = u.label_id
                  AND li.project_id = u.project_id
LEFT JOIN wbg.projects pr ON pr.project_id = u.project_id
LEFT JOIN wbg.labels l ON l.label_set = u.label_set AND l.label_id = u.label_id
GROUP BY u.label_set, u.label_id, l.label_name, u.project_id, pr.cohort_set, pr.project_name, pr.country_name,
         pr.region, pr.approval_fiscal_year, pr.practice, pr.commitment_usd_millions;
COMMENT ON VIEW wbg.measure_findings_by_project IS
  'One row per project and measure: committed and planned units by the decision model''s grade, the assets those commitments link to, how they are delivered and by whom. Count projects, not units: one project with a long restructuring paper can hold hundreds of units (committed_units_in_appraisal_document leaves restructuring and additional-financing papers out). The model''s grades, not a person''s, and coverage is below 100 per cent.';
