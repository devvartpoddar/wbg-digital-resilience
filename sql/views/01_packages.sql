-- Packages with their project's name, country, region, practice and unit.
DROP VIEW IF EXISTS wbg.v_packages;
CREATE VIEW wbg.v_packages AS
SELECT p.*, pr.project_name, pr.country_name, pr.region, pr.practice, pr.managing_unit,
       pr.approval_fy
FROM wbg.packages p
LEFT JOIN wbg.projects pr USING (project_id);
