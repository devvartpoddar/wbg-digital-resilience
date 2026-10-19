-- Notices and awards next to the plan package they are linked to and their project.
DROP VIEW IF EXISTS wbg.notices_with_package;
CREATE VIEW wbg.notices_with_package AS
SELECT n.*, p.description_clean AS package_description, p.status AS package_status,
       p.planned_date AS package_planned_date, p.estimated_amount_usd AS package_estimated_amount_usd,
       p.component_number AS package_component_number, p.component_name AS package_component_name,
       pr.project_name, pr.country_name AS project_country_name, pr.region
FROM wbg.notices n
LEFT JOIN wbg.packages p USING (package_id)
LEFT JOIN wbg.projects pr ON pr.project_id = n.project_id;
COMMENT ON VIEW wbg.notices_with_package IS
  'One procurement notice with its linked plan package (where the borrower reference identified one) and its project.';

DROP VIEW IF EXISTS wbg.awards_with_package;
CREATE VIEW wbg.awards_with_package AS
SELECT a.*, p.description_clean AS package_description, p.status AS package_status,
       p.planned_date AS package_planned_date, p.estimated_amount_usd AS package_estimated_amount_usd,
       p.component_number AS package_component_number, p.component_name AS package_component_name,
       pr.project_name, pr.country_name AS project_country_name
FROM wbg.awards a
LEFT JOIN wbg.packages p USING (package_id)
LEFT JOIN wbg.projects pr ON pr.project_id = a.project_id;
COMMENT ON VIEW wbg.awards_with_package IS
  'One signed contract with its linked plan package (where the borrower reference identified one) and its project.';
