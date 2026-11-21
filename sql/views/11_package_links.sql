-- The semantic layer between the appraisal document and the procurement
-- plan (src/wbg_portfolio/checks.py, decide.py): which asset a package buys, and which
-- commitments it is linked to (by the measure, or by a shared asset). Nothing is cut in the tables; the
-- views carry the probabilities so a question chooses its threshold.
DROP VIEW IF EXISTS wbg.package_assets_in_context;
CREATE VIEW wbg.package_assets_in_context AS
SELECT pa.package_id, pa.project_id, pr.cohort_set, pr.project_name, pr.country_name, pr.region,
       pa.label_id AS asset_id, l.label_name AS asset_name, pa.cosine, pa.cosine_rank, pa.probability, pa.posterior_probability,
       pa.has_asset, pa.subtype_id, st.subtype_name,
       p.description_clean, p.category, p.method_code, p.status, p.planned_date,
       p.estimated_amount_usd, p.in_latest_plan, p.component_number, p.component_name,
       wbg.is_upcoming(p.status) AS is_upcoming
FROM wbg.package_assets pa
JOIN wbg.packages p USING (package_id)
LEFT JOIN wbg.projects pr ON pr.project_id = pa.project_id
LEFT JOIN wbg.labels l ON l.label_set = 'assets' AND l.label_id = pa.label_id
LEFT JOIN wbg.asset_subtypes st ON st.asset_id = pa.label_id AND st.subtype_id = pa.subtype_id;
COMMENT ON VIEW wbg.package_assets_in_context IS
  'One row per package and asset the decision model was asked about, with the asset''s name, the package''s plan columns and its project. has_asset says whether the package has the asset (link.asset_yes, or the chat model''s yes from link.asset_band_low); subtype_id and subtype_name are its type, by the chat model; a package may have several assets.';

DROP VIEW IF EXISTS wbg.packages_with_commitments;
CREATE VIEW wbg.packages_with_commitments AS
SELECT p.package_id, p.project_id, pr.cohort_set, pr.project_name, pr.country_name, pr.region,
       p.description_clean, p.category, p.method_code, p.status, p.planned_date,
       p.estimated_amount_usd, p.in_latest_plan,
       wbg.is_upcoming(p.status) AS is_upcoming,
       (SELECT array_agg(DISTINCT pa.label_id ORDER BY pa.label_id) FROM wbg.package_assets pa
         WHERE pa.package_id = p.package_id AND pa.has_asset) AS asset_ids,
       (SELECT array_agg(DISTINCT cp.label_set ORDER BY cp.label_set) FROM wbg.commitment_package_links cp
         WHERE cp.package_id = p.package_id) AS measures_linked,
       (SELECT count(*) FROM wbg.commitment_package_links cp
         WHERE cp.package_id = p.package_id) AS commitments_linked,
       (SELECT max(cp.strong_p) FROM wbg.commitment_package_links cp
         WHERE cp.package_id = p.package_id AND cp.strength = 'strong') AS best_commitment_probability,
       (SELECT min(cp.level_rank) FROM wbg.commitment_package_links cp
         WHERE cp.package_id = p.package_id) AS best_level_rank
FROM wbg.packages p
LEFT JOIN wbg.projects pr ON pr.project_id = p.project_id;
COMMENT ON VIEW wbg.packages_with_commitments IS
  'One row per package with the assets it has (package_assets.has_asset), best_level_rank the best level of its links (1 strong to 5 same asset), the measures whose commitments link to it and how many committed sentences link to it (strong or by shared asset); best_commitment_probability is the best strong-link probability, null when every link is by shared asset: the table to read before a conversation about an upcoming tender. The tables underneath carry every probability.';
