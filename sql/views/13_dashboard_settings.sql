-- The few readings the dashboard views apply, in one place. Each value is a
-- control-panel setting (inputs/config/control_panel.csv, dashboard.*),
-- filled in at publish: a change is one edit there and a publish. Upstream,
-- only dashboard.commitment_link_levels is also read (by the package links,
-- src/link_packages.py, keyed on it); the model's answers depend on none.
DROP VIEW IF EXISTS wbg.dashboard_settings CASCADE;
CREATE VIEW wbg.dashboard_settings AS
SELECT
  -- A unit counts towards a commitment when it is committed (its posterior
  -- at its measure's floor, control panel floor.*), its posterior is at
  -- least this (a further floor over every measure; 0),
  {{panel:dashboard.commitment_min_probability}}::numeric AS commitment_min_probability,
  -- and its asset is found at one of these levels: 'unit', the sentence's
  -- own words name it; 'paragraph', the sentence names none and an earlier
  -- sentences of its paragraph do (their scores summed, each weighted by
  -- control panel asset.decay per sentence back). 'none' widens.
  {{panel:dashboard.commitment_link_levels}} AS commitment_link_levels,
  -- Only Investment Project Financing operations.
  {{panel:dashboard.ipf_only}} AS ipf_only,
  -- A package is linked to a commitment at this probability or more.
  {{panel:dashboard.package_link_min_probability}}::numeric AS package_link_min_probability,
  -- Another project prints a sentence near-identically when its committed
  -- sentences hold at least this share of the sentence's six-word runs.
  {{panel:dashboard.near_copy_share}}::numeric AS near_copy_share,
  -- A unit two measures both commit leaves a measure the pair check says it
  -- is not (only the other measure, or neither; src/commit_checks.py).
  {{panel:dashboard.apply_measure_pair_checks}} AS apply_measure_pair_checks,
  -- when its side's posterior (p_keep_a or p_keep_b: the measure's graded
  -- question again, the other beside it) falls short of this (floor.pair_check).
  {{panel:floor.pair_check}}::numeric AS pair_check_floor;
COMMENT ON VIEW wbg.dashboard_settings IS
  'The readings the dashboard views apply (one row): a further floor on the posterior for a unit to count, the asset-link levels that count, whether only Investment Project Financing operations count, the probability at which a package counts as linked, the share of six-word runs that makes another project''s wording a near copy, whether the measure pair checks drop units, and the posterior each side needs to stay.';

-- Country codes for a map: the projects interface gives regional
-- operations a region code (3E, 3W, 6R, 7C, 3T: a digit first) and the
-- Democratic Republic of Congo its old code ZR. Metabase's world map needs
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
-- browsers may refuse inside Metabase.
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
