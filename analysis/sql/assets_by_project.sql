-- For each project and asset: how many model paragraphs the classifier put at
-- each grade (0 not about it ... 3 financed). Grades are the model's most
-- likely grade, unmeasured until src/evaluate.py has run on a graded sheet;
-- read them as "where to look", not as counts of financed assets.
SELECT pp.project_id, c.label_set, c.label_id, t.name AS asset,
       count(*) FILTER (WHERE c.grade = 3) AS financed,
       count(*) FILTER (WHERE c.grade = 2) AS supported,
       count(*) FILTER (WHERE c.grade = 1) AS mentioned,
       count(*) AS paragraphs
FROM wbg.classify_scores c
JOIN wbg.paragraph_projects pp USING (paragraph_id)
LEFT JOIN wbg.taxonomy t ON t.label_set = c.label_set AND t.label_id = c.label_id
WHERE c.label_set = 'assets'
GROUP BY pp.project_id, c.label_set, c.label_id, t.name
ORDER BY pp.project_id, financed DESC, supported DESC;
