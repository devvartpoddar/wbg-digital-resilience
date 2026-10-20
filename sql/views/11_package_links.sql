-- The semantic layer between the appraisal document and the procurement
-- plan (src/link_packages.py): which asset a package buys, and which
-- commitments a package would carry out. Nothing is cut in the tables; the
-- views carry the probabilities so a question chooses its threshold.
DROP VIEW IF EXISTS wbg.package_assets_in_context;
CREATE VIEW wbg.package_assets_in_context AS
SELECT pa.package_id, pa.project_id, pr.cohort_set, pr.project_name, pr.country_name, pr.region,
       pa.label_id AS asset_id, l.label_name AS asset_name, g.group_id AS asset_group_id,
       g.group_name AS asset_group_name, pa.cosine, pa.cosine_rank, pa.probability,
       p.description_clean, p.category, p.method_code, p.status, p.planned_date,
       p.estimated_amount_usd, p.in_latest_plan, p.component_number, p.component_name,
       (p.status IN ('Pending', 'Pending Implementation', 'Under Review', 'Planned')) AS is_upcoming
FROM wbg.package_assets pa
JOIN wbg.packages p USING (package_id)
LEFT JOIN wbg.projects pr ON pr.project_id = pa.project_id
LEFT JOIN wbg.labels l ON l.label_set = 'assets' AND l.label_id = pa.label_id
LEFT JOIN wbg.label_groups g ON g.label_set = 'assets' AND g.label_id = pa.label_id;
COMMENT ON VIEW wbg.package_assets_in_context IS
  'One row per package and asset the decision model was asked about, with the asset''s name and reading group, the package''s plan columns and its project. Filter probability >= 0.5 for the assets a package buys; a package may buy several.';

DROP VIEW IF EXISTS wbg.commitment_packages_in_context;
CREATE VIEW wbg.commitment_packages_in_context AS
SELECT cp.label_set, cp.label_id, f.measure_name, cp.project_id, f.cohort_set, f.project_name,
       f.country_name, cp.unit_id, f.most_likely_grade, f.probability_grade_3,
       f.linked_asset_id, f.linked_asset_level, f.linked_asset_group_id, f.linked_asset_group_name,
       f.best_type_id, f.best_type_name, f.text AS commitment_text, f.component_number AS commitment_component_number,
       cp.package_id, p.description_clean AS package_description, p.category, p.method_code,
       p.status, p.planned_date, p.estimated_amount_usd, p.in_latest_plan,
       p.component_number AS package_component_number, p.component_name AS package_component_name,
       (p.status IN ('Pending', 'Pending Implementation', 'Under Review', 'Planned')) AS is_upcoming,
       cp.shared_asset_ids, cp.shared_group_ids, cp.probability
FROM wbg.commitment_packages cp
JOIN wbg.measure_findings_in_context f ON f.label_set = cp.label_set AND f.label_id = cp.label_id
                                       AND f.unit_id = cp.unit_id AND f.project_id = cp.project_id
JOIN wbg.packages p ON p.package_id = cp.package_id;
COMMENT ON VIEW wbg.commitment_packages_in_context IS
  'One row per committed unit and package of the same project the decision model paired (they share an asset or an asset reading group), with the commitment''s text, measure, linked asset and type, the package''s plan columns, and the model''s probability that carrying out the package is where the commitment is delivered. Filter probability >= 0.5 for the links; many to many.';

DROP VIEW IF EXISTS wbg.packages_with_commitments;
CREATE VIEW wbg.packages_with_commitments AS
SELECT p.package_id, p.project_id, pr.cohort_set, pr.project_name, pr.country_name, pr.region,
       p.description_clean, p.category, p.method_code, p.status, p.planned_date,
       p.estimated_amount_usd, p.in_latest_plan,
       (p.status IN ('Pending', 'Pending Implementation', 'Under Review', 'Planned')) AS is_upcoming,
       (SELECT array_agg(DISTINCT pa.label_id ORDER BY pa.label_id) FROM wbg.package_assets pa
         WHERE pa.package_id = p.package_id AND pa.probability >= 0.5) AS asset_ids,
       (SELECT array_agg(DISTINCT g.group_name ORDER BY g.group_name) FROM wbg.package_assets pa
         JOIN wbg.label_groups g ON g.label_set = 'assets' AND g.label_id = pa.label_id
         WHERE pa.package_id = p.package_id AND pa.probability >= 0.5) AS asset_group_names,
       (SELECT array_agg(DISTINCT cp.label_set ORDER BY cp.label_set) FROM wbg.commitment_packages cp
         WHERE cp.package_id = p.package_id AND cp.probability >= 0.5) AS measures_linked,
       (SELECT count(*) FROM wbg.commitment_packages cp
         WHERE cp.package_id = p.package_id AND cp.probability >= 0.5) AS commitments_linked,
       (SELECT max(cp.probability) FROM wbg.commitment_packages cp
         WHERE cp.package_id = p.package_id) AS best_commitment_probability
FROM wbg.packages p
LEFT JOIN wbg.projects pr ON pr.project_id = p.project_id;
COMMENT ON VIEW wbg.packages_with_commitments IS
  'One row per package with the assets it buys (at 0.5), their reading groups, the measures whose commitments link to it (at 0.5) and how many commitments: the table to read before a conversation about an upcoming tender. The 0.5 is the reading threshold; the tables underneath carry every probability.';
