-- Packages with their project. Metabase models and dashboards sit on these
-- views, not on the tables; the names follow src/schema.py.
DROP VIEW IF EXISTS wbg.packages_with_project CASCADE;
CREATE VIEW wbg.packages_with_project AS
SELECT p.*, pr.project_name, pr.country_name, pr.region, pr.practice, pr.managing_unit,
       pr.approval_fiscal_year, pr.approval_date
FROM wbg.packages p
LEFT JOIN wbg.projects pr USING (project_id);
COMMENT ON VIEW wbg.packages_with_project IS
  'Every procurement-plan package (one row each, newest plan version) with its project''s name, country, region, practice and unit.';

DROP VIEW IF EXISTS wbg.latest_plan_packages;
CREATE VIEW wbg.latest_plan_packages AS
SELECT * FROM wbg.packages_with_project WHERE in_latest_plan;
COMMENT ON VIEW wbg.latest_plan_packages IS
  'Packages that are in their project''s most recently disclosed procurement plan.';

-- upcoming_packages is defined in 03_procurement_links.sql, from the package
-- status that notices and awards give.
