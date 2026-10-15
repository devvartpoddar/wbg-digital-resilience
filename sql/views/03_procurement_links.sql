-- Notices and awards next to the plan package they are linked to.
DROP VIEW IF EXISTS wbg.v_notices;
CREATE VIEW wbg.v_notices AS
SELECT n.*, p.description_clean AS package_description, p.status AS package_status,
       p.planned_date AS package_planned_date, p.estimated_amount AS package_estimate,
       p.component AS package_component
FROM wbg.notices n
LEFT JOIN wbg.packages p USING (package_id);

DROP VIEW IF EXISTS wbg.v_awards;
CREATE VIEW wbg.v_awards AS
SELECT a.*, p.description_clean AS package_description, p.status AS package_status,
       p.planned_date AS package_planned_date, p.estimated_amount AS package_estimate,
       p.component AS package_component
FROM wbg.awards a
LEFT JOIN wbg.packages p USING (package_id);
