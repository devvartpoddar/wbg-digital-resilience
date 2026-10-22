-- Where a measure is committed, by project: the units that count (the
-- posterior at the measure's floor) with their linked asset. Set label_set
-- to the measure; its precision is what ./wbg check measures.
SELECT project_id, project_name, country_name, count(*) AS committed_units,
       count(DISTINCT document_id) AS documents,
       string_agg(DISTINCT linked_asset_id, ', ') AS linked_assets
FROM wbg.measure_findings_in_context
WHERE label_set = '<set>' AND is_committed
GROUP BY project_id, project_name, country_name
ORDER BY committed_units DESC, project_id;
