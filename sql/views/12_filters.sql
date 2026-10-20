-- One filter for every Metabase card on the measure view: the committed
-- units under the dashboard's filters, as a table function. A card is then
--   SELECT ... FROM wbg.findings_under(measure => {{measure}}, min_p => {{min_p}},
--                                      link => {{link}} [[, cohort_set => {{set}}]]
--                                      [[, country => {{country}}]] ...) f
-- so the filter logic lives here, once, in git, and a change is one edit.
-- Every argument but the measure may be NULL (an unset dashboard filter);
-- the Metabase optional clause [[, name => {{tag}}]] leaves it out.
--   link: 'sentence' (the sentence's own words name a digital asset),
--         'sentence or paragraph', or anything else for every link level.
--   months: a project with a pending package due (planned date past) or
--         planned within this many months.
--   asset: an asset id, an asset name, a reading-group name, or '(no asset)'.
--   type_: a type id or name, or 'No type (other)'.
DROP FUNCTION IF EXISTS wbg.findings_under(text, double precision, text, text, text, text, text,
                                            double precision, text, text, text, text);
CREATE FUNCTION wbg.findings_under(
    measure text,
    min_p double precision DEFAULT 0,
    link text DEFAULT 'sentence',
    cohort_set text DEFAULT NULL,
    country text DEFAULT NULL,
    region text DEFAULT NULL,
    status text DEFAULT NULL,
    months double precision DEFAULT NULL,
    asset text DEFAULT NULL,
    type_ text DEFAULT NULL,
    kind text DEFAULT NULL,
    actor text DEFAULT NULL)
-- The columns of wbg.measure_findings_in_context, spelled out: a function returning
-- the view's row type would be dropped with the view on every reload.
RETURNS TABLE (label_set text, label_id text, measure_name text, project_id text, cohort_set text, project_name text, country_name text, document_id text, document_title text, document_kind text, component_number text, component_name text, unit_id text, unit_kind text, feedback_round integer, route text, reading_order integer, most_likely_grade integer, expected_grade numeric, grade_confidence numeric, probability_grade_3 numeric, committed_probability_calibrated numeric, commitment_probability numeric, firmness text, linked_asset_id text, linked_asset_level text, linked_asset_probability numeric, linked_asset_group_id text, linked_asset_group_name text, asset_ids_above_threshold text[], activity_kinds_above_threshold text[], best_activity_kind text, conditional_probability numeric, actor_ids_above_threshold text[], best_actor_id text, type_ids_above_threshold text[], best_type_id text, best_type_name text, best_type_probability numeric, template_project_count integer, template_wording_share numeric, text text, paragraph_text text, section_title text)
LANGUAGE sql STABLE AS $$
  SELECT f.*
  FROM wbg.measure_findings_in_context f
  WHERE f.label_set = findings_under.measure AND f.most_likely_grade = 3
    AND f.probability_grade_3 >= coalesce(findings_under.min_p, 0)
    AND (CASE coalesce(findings_under.link, 'sentence')
           WHEN 'sentence' THEN f.linked_asset_level = 'unit'
           WHEN 'sentence or paragraph' THEN f.linked_asset_level IN ('unit', 'paragraph')
           ELSE true END)
    AND (findings_under.cohort_set IS NULL OR f.cohort_set = findings_under.cohort_set)
    AND (findings_under.country IS NULL OR f.country_name = findings_under.country)
    AND (findings_under.region IS NULL OR f.project_id IN (SELECT p.project_id FROM wbg.projects p WHERE p.region = findings_under.region))
    AND (findings_under.status IS NULL OR f.project_id IN (SELECT p.project_id FROM wbg.projects p WHERE p.status = findings_under.status))
    AND (findings_under.months IS NULL OR EXISTS (SELECT 1 FROM wbg.upcoming_packages u
                                   WHERE u.project_id = f.project_id
                                     AND u.planned_date <= current_date + findings_under.months * interval '1 month'))
    AND (findings_under.asset IS NULL
         OR (findings_under.asset = '(no asset)' AND f.linked_asset_id IS NULL)
         OR EXISTS (SELECT 1 FROM wbg.measure_finding_lists x
                    LEFT JOIN wbg.labels a ON a.label_set = 'assets' AND a.label_id = x.member
                    LEFT JOIN wbg.label_groups g ON g.label_set = 'assets' AND g.group_id = x.member
                    WHERE x.unit_id = f.unit_id AND x.project_id = f.project_id
                      AND x.kind IN ('asset', 'linked_asset', 'asset_group')
                      AND (x.member = findings_under.asset OR a.label_name = findings_under.asset OR g.group_name = findings_under.asset)))
    AND (findings_under.type_ IS NULL
         OR (findings_under.type_ = 'No type (other)' AND coalesce(cardinality(f.type_ids_above_threshold), 0) = 0)
         OR EXISTS (SELECT 1 FROM wbg.measure_finding_lists x
                    JOIN wbg.measure_types t ON t.label_set = x.label_set AND t.type_id = x.member
                    WHERE x.unit_id = f.unit_id AND x.project_id = f.project_id AND x.kind = 'type'
                      AND (x.member = findings_under.type_ OR t.type_name = findings_under.type_)))
    AND (findings_under.kind IS NULL OR EXISTS (SELECT 1 FROM wbg.measure_finding_lists x
                                 WHERE x.unit_id = f.unit_id AND x.project_id = f.project_id
                                   AND x.kind = 'activity_kind' AND x.member = findings_under.kind))
    AND (findings_under.actor IS NULL OR EXISTS (SELECT 1 FROM wbg.measure_finding_lists x
                                  WHERE x.unit_id = f.unit_id AND x.project_id = f.project_id
                                    AND x.kind = 'actor' AND x.member = findings_under.actor))
$$;
COMMENT ON FUNCTION wbg.findings_under IS
  'The committed units of a findings_under.measure under the dashboard filters: one filter for every card on the findings_under.measure view. Every argument but the findings_under.measure may be NULL.';
