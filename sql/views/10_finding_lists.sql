-- One row per finding and list member, for dashboard filters: Metabase
-- cannot filter inside a text[] column, so the assets, their groups, kinds, actors and
-- types a committed unit carries are unnested here. Join back on unit_id.
DROP VIEW IF EXISTS wbg.measure_finding_lists;
CREATE VIEW wbg.measure_finding_lists AS
SELECT m.label_set, m.label_id, m.unit_id, m.paragraph_id, m.document_id, pp.project_id,
       m.most_likely_grade, x.kind, x.member
FROM wbg.measure_findings m
JOIN wbg.paragraph_projects pp ON pp.paragraph_id = m.paragraph_id
CROSS JOIN LATERAL (
  SELECT 'asset' AS kind, a AS member FROM UNNEST(m.asset_ids_above_threshold) a
  UNION ALL SELECT 'activity_kind', k FROM UNNEST(m.activity_kinds_above_threshold) k
  UNION ALL SELECT 'actor', c FROM UNNEST(m.actor_ids_above_threshold) c
  UNION ALL SELECT 'type', t FROM UNNEST(m.type_ids_above_threshold) t
  UNION ALL SELECT 'linked_asset', m.linked_asset_id WHERE m.linked_asset_id IS NOT NULL
  UNION ALL SELECT DISTINCT 'asset_group', ag.group_id FROM wbg.label_groups ag
            WHERE ag.label_set = 'assets'
              AND (ag.label_id = m.linked_asset_id OR ag.label_id = ANY(m.asset_ids_above_threshold))
) x
WHERE m.was_sent_to_model;
COMMENT ON VIEW wbg.measure_finding_lists IS
  'One row per graded unit and list member (kind: asset, linked_asset, asset_group, activity_kind, actor, type), so a dashboard filter can select on what a text[] column holds. Join measure_findings on unit_id.';
