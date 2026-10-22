-- For each project and asset: how many sentences the pipeline gives the
-- asset (wbg.unit_assets: its posterior at the control panel's asset.floor,
-- the sentence's own or from earlier sentences of its paragraph). The
-- model's reading, unmeasured until a graded sample has been evaluated:
-- "where to look", not counts of financed assets.
SELECT pp.project_id, ua.asset_id, l.label_name AS asset,
       count(*) FILTER (WHERE ua.asset_level = 'unit') AS sentences_naming_it,
       count(*) FILTER (WHERE ua.asset_level = 'paragraph') AS sentences_from_paragraph,
       count(DISTINCT ua.paragraph_id) AS paragraphs
FROM wbg.unit_assets ua
JOIN wbg.paragraph_projects pp ON pp.paragraph_id = ua.paragraph_id
LEFT JOIN wbg.labels l ON l.label_set = 'assets' AND l.label_id = ua.asset_id
WHERE ua.asset_level <> 'none'
GROUP BY pp.project_id, ua.asset_id, l.label_name
ORDER BY pp.project_id, sentences_naming_it DESC;
