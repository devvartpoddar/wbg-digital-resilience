-- The semantic layer between the appraisal document and the procurement
-- plan (src/wbg_portfolio/checks.py, decide.py): which asset a package buys, and which
-- commitments it is linked to (by the measure, or by a shared asset). Nothing is cut in the tables; the
-- views carry the probabilities so a question chooses its threshold.
DROP VIEW IF EXISTS wbg.package_assets_in_context;
CREATE VIEW wbg.package_assets_in_context AS
SELECT pa.package_id, pa.project_id, pr.cohort_set, pr.project_name, pr.country_name, pr.region,
       pa.label_id AS asset_id, l.label_name AS asset_name, pa.cosine, pa.cosine_rank, pa.probability, pa.posterior_probability,
       p.description_clean, p.category, p.method_code, p.status, p.planned_date,
       p.estimated_amount_usd, p.in_latest_plan, p.component_number, p.component_name,
       wbg.is_upcoming(p.status) AS is_upcoming
FROM wbg.package_assets pa
JOIN wbg.packages p USING (package_id)
LEFT JOIN wbg.projects pr ON pr.project_id = pa.project_id
LEFT JOIN wbg.labels l ON l.label_set = 'assets' AND l.label_id = pa.label_id;
COMMENT ON VIEW wbg.package_assets_in_context IS
  'One row per package and asset the decision model was asked about, with the asset''s name, the package''s plan columns and its project. Filter posterior_probability (the model''s answer through the asset''s layer once fitted) >= the control panel''s link.asset_yes for the assets a package buys; a package may buy several.';

DROP VIEW IF EXISTS wbg.commitment_packages_in_context;
CREATE VIEW wbg.commitment_packages_in_context AS
SELECT cp.label_set, f.label_id, f.measure_name, cp.project_id, f.cohort_set, f.project_name,
       f.country_name, cp.unit_id, f.is_committed, f.posterior_probability,
       f.linked_asset_id, f.linked_asset_level,
       f.best_type_id, f.best_type_name, f.text AS commitment_text, f.component_number AS commitment_component_number,
       cp.package_id, p.description_clean AS package_description, p.category, p.method_code,
       p.status, p.planned_date, p.estimated_amount_usd, p.in_latest_plan,
       p.component_number AS package_component_number, p.component_name AS package_component_name,
       wbg.is_upcoming(p.status) AS is_upcoming,
       cp.shared_asset_ids,
       CASE WHEN cp.strength = 'strong' THEN cp.strong_p END AS probability,
       CASE WHEN cp.strength = 'strong' THEN cp.strong_p END AS link_posterior_probability,
       cp.strength
FROM wbg.commitment_package_links cp
JOIN wbg.measure_findings_in_context f ON f.label_set = cp.label_set
                                       AND f.unit_id = cp.unit_id AND f.project_id = cp.project_id
JOIN wbg.packages p ON p.package_id = cp.package_id;
COMMENT ON VIEW wbg.commitment_packages_in_context IS
  'One row per committed, digital unit and package of the same project that are linked (the same rows as commitment_package_links_in_context, under the name earlier readers use), with the commitment''s text, measure, linked asset and type and the package''s plan columns. strength is strong (the package, asked alone, is about the measure) or asset (they share a top-level asset). probability and link_posterior_probability are the strong-link probability (strong_p, at link.strong_yes or more), null for a link by shared asset (whatever strong_p holds). Many to many.';

DROP VIEW IF EXISTS wbg.packages_with_commitments;
CREATE VIEW wbg.packages_with_commitments AS
SELECT p.package_id, p.project_id, pr.cohort_set, pr.project_name, pr.country_name, pr.region,
       p.description_clean, p.category, p.method_code, p.status, p.planned_date,
       p.estimated_amount_usd, p.in_latest_plan,
       wbg.is_upcoming(p.status) AS is_upcoming,
       (SELECT array_agg(DISTINCT pa.label_id ORDER BY pa.label_id) FROM wbg.package_assets pa
         WHERE pa.package_id = p.package_id AND pa.posterior_probability >= {{panel:link.asset_yes}}) AS asset_ids,
       (SELECT array_agg(DISTINCT cp.label_set ORDER BY cp.label_set) FROM wbg.commitment_package_links cp
         WHERE cp.package_id = p.package_id) AS measures_linked,
       (SELECT count(*) FROM wbg.commitment_package_links cp
         WHERE cp.package_id = p.package_id) AS commitments_linked,
       (SELECT max(cp.strong_p) FROM wbg.commitment_package_links cp
         WHERE cp.package_id = p.package_id AND cp.strength = 'strong') AS best_commitment_probability
FROM wbg.packages p
LEFT JOIN wbg.projects pr ON pr.project_id = p.project_id;
COMMENT ON VIEW wbg.packages_with_commitments IS
  'One row per package with the assets it buys (at the control panel''s link.asset_yes), the measures whose commitments link to it and how many committed sentences link to it (strong or by shared asset); best_commitment_probability is the best strong-link probability, null when every link is by shared asset: the table to read before a conversation about an upcoming tender. The tables underneath carry every probability.';
