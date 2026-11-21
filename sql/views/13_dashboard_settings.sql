-- The few readings the dashboard views apply, in one place. Each value is a
-- control-panel setting (inputs/config/control_panel.csv), filled in at
-- publish: a change is one edit there and a publish. The model's answers depend on none.
DROP VIEW IF EXISTS wbg.dashboard_settings CASCADE;
CREATE VIEW wbg.dashboard_settings AS
SELECT
  -- A unit counts towards a commitment when it is kept (measure_findings.is_committed: the band rule
  -- and digital) in an operation the dashboards count: only Investment Project Financing operations.
  {{panel:dashboard.ipf_only}} AS ipf_only,
  -- The probability at which a package is strongly linked to a measure
  -- (link.strong_yes). A package linked only by a shared asset has none.
  {{panel:link.strong_yes}}::numeric AS package_link_min_probability,
  -- Another project prints a sentence near-identically when its committed
  -- sentences hold at least this share of the sentence's six-word runs.
  {{panel:dashboard.near_copy_share}}::numeric AS near_copy_share,
  -- The band rule (control panel band.*): a first-pass score of band_keep or
  -- more is committed; one from band_low to band_keep is committed only when the
  -- paragraph-aware score reaches band_context and the chat model's grade
  -- reaches band_second_grade. Decided at publish (measure_findings.passes_measure_rule;
  -- is_committed is that and digital);
  -- shown here so a reader sees the rule behind the counts.
  {{panel:band.low}}::numeric AS band_low,
  {{panel:band.keep}}::numeric AS band_keep,
  {{panel:band.context}}::numeric AS band_context,
  {{panel:band.second_grade}}::numeric AS band_second_grade,
  -- The digital rule: a sentence is kept (is_committed) only when is_digital (digital_floor).
  {{panel:digital.floor}}::numeric AS digital_floor,
  -- Package links: strong when the package, asked alone, is about the measure
  -- (link_strong_yes); asset when it shares a top-level asset.
  {{panel:link.strong_yes}}::numeric AS link_strong_yes,
  {{panel:link.asset_band_low}}::numeric AS link_asset_band_low,
  {{panel:link.asset_yes}}::numeric AS link_asset_yes,
  {{panel:second.model}} AS second_model;
COMMENT ON VIEW wbg.dashboard_settings IS
  'The readings the dashboard views apply (one row): whether only Investment Project Financing operations count, the probability at which a package is strongly linked (package_link_min_probability, the same as link_strong_yes), the share of six-word runs that makes another project''s wording a near copy, the band rule''s thresholds, the digital rule and its floor, the package-link settings and the chat model of the second looks.';

-- Country codes for a map: the projects interface gives regional
-- operations a region code (3E, 3W, 6R, 7C, 3T: a digit first) and the
-- Democratic Republic of Congo its old code ZR. A world map needs
-- ISO 3166 alpha-2, so a region code becomes NULL (is_regional) and ZR is CD.
DROP FUNCTION IF EXISTS wbg.iso_country_code(text) CASCADE;
CREATE FUNCTION wbg.iso_country_code(code text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN code IS NULL OR code ~ '^[0-9]' THEN NULL
              WHEN upper(code) = 'ZR' THEN 'CD'
              ELSE upper(code) END
$$;
COMMENT ON FUNCTION wbg.iso_country_code(text) IS
  'The ISO 3166 alpha-2 code for a projects-interface country code: NULL for a regional code (a digit first, such as 3E), CD for the old ZR.';

-- Document links as https: 115 of 317 stored addresses are http, which
-- browsers may refuse inside a page served over https.
DROP FUNCTION IF EXISTS wbg.https(text) CASCADE;
CREATE FUNCTION wbg.https(url text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT regexp_replace(url, '^http://', 'https://') $$;
COMMENT ON FUNCTION wbg.https(text) IS 'A link with http:// made https://.';

-- Two component names are the same component when their keys are equal or
-- one is a prefix of the other and both are at least 12 characters (a
-- restructuring paper shortens a heading; a short key would match too much).
DROP FUNCTION IF EXISTS wbg.same_component(text, text) CASCADE;
CREATE FUNCTION wbg.same_component(a text, b text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT coalesce(a, '') <> '' AND (a = b OR (least(length(a), length(b)) >= 12
         AND (a LIKE b || '%' OR b LIKE a || '%')))
$$;
COMMENT ON FUNCTION wbg.same_component(text, text) IS
  'Whether two component name keys name the same component: equal, or one a prefix of the other with both at least 12 characters.';
