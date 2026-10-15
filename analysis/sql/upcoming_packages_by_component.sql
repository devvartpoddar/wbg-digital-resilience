-- Packages still to come (STEP status Pending, Pending Implementation, Under
-- Review, Planned), with the component the plan names and the paragraphs
-- the search ranks highest for an asset under that component in the
-- project's own appraisal document. This is the join that says which task
-- teams to talk to, and when: a plan's planned date is what the plan
-- records, not evidence of delay.
SELECT p.project_id, p.package_id, p.description_clean, p.status, p.planned_date,
       p.estimated_amount, p.component_number, p.component,
       s.label_id AS asset, s.best_score, s.paragraph_id
FROM wbg.packages p
LEFT JOIN wbg.search_paragraphs s
       ON s.label_set = 'assets'
      AND s.project_ids LIKE '%' || p.project_id || '%'
      AND s.component_number = p.component_number
WHERE p.status IN ('Pending', 'Pending Implementation', 'Under Review', 'Planned')
ORDER BY p.planned_date NULLS LAST, p.project_id, p.package_id, s.best_score DESC;
