-- Where a measure is committed, by project: the committed units of a
-- measure run with their linked asset and how the commitment is delivered.
-- Set label_set to the measure. Grade 3 is the model's most likely grade;
-- its precision is what ./wbg check measures.
SELECT project_id, project_name, country_name, count(*) AS committed_units,
       count(DISTINCT document_id) AS documents,
       string_agg(DISTINCT linked_asset_id, ', ') AS linked_assets,
       string_agg(DISTINCT best_activity_kind, ', ') AS activity_kinds
FROM wbg.measure_findings_in_context
WHERE label_set = '<set>' AND most_likely_grade = 3
GROUP BY project_id, project_name, country_name
ORDER BY committed_units DESC, project_id;
