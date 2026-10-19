-- For each project and asset: how many graded paragraphs the asset map put
-- at each grade (0 not about it ... 3 financed). Grades are the model's most
-- likely grade, unmeasured until a graded sample has been evaluated; read
-- them as "where to look", not as counts of financed assets.
SELECT pl.project_id, g.label_id AS asset_id, g.label_name AS asset,
       count(*) FILTER (WHERE g.most_likely_grade = 3) AS financed,
       count(*) FILTER (WHERE g.most_likely_grade = 2) AS supported,
       count(*) FILTER (WHERE g.most_likely_grade = 1) AS mentioned,
       count(*) AS paragraphs
FROM wbg.paragraph_grades_with_text g
JOIN wbg.paragraph_project_links pl USING (paragraph_id)
WHERE g.label_set = 'assets' AND g.was_asked
GROUP BY pl.project_id, g.label_id, g.label_name
ORDER BY pl.project_id, financed DESC, supported DESC;
