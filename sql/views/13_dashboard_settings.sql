-- The few readings the dashboard views apply, in one place. Each value is a
-- control-panel setting (inputs/config/control_panel.csv, dashboard.*),
-- filled in at publish: a change is one edit there and a publish. Nothing
-- upstream reads these: the files and the model's answers do not depend on them.
DROP VIEW IF EXISTS wbg.dashboard_settings CASCADE;
CREATE VIEW wbg.dashboard_settings AS
SELECT
  -- A unit counts towards a commitment when it is committed (its probability
  -- at its measure's floor, control panel floor.*), its probability is at
  -- least this (a further floor over every measure; 0),
  {{panel:dashboard.commitment_min_probability}}::numeric AS commitment_min_probability,
  -- and its asset is found at one of these levels: 'unit', the sentence's
  -- own words name it; 'paragraph', the sentence names none and an earlier
  -- sentence of its paragraph does (the last one named, control panel
  -- asset.decay). 'none' widens.
  {{panel:dashboard.commitment_link_levels}} AS commitment_link_levels,
  -- Only Investment Project Financing operations (Dev, 2026-10-20).
  {{panel:dashboard.ipf_only}} AS ipf_only,
  -- A sentence counts under its one best asset and one best type (false), or
  -- under every asset and type the model says yes to (true; on the test
  -- slice that made about seven commitments of each sentence). Undecided.
  {{panel:dashboard.every_asset_and_type}} AS every_asset_and_type,
  -- A unit is "not waiting on a later study" below this conditional
  -- probability.
  {{panel:dashboard.not_conditional_below}}::numeric AS not_conditional_below,
  -- A package is linked to a commitment at this probability or more. On Dev's
  -- graded sheet (2026-10-21, 70 pairs) links from 0.5 to 0.7 were 3 yes, 7
  -- no, 5 unsure; from 0.7 up, 25 yes of 26.
  {{panel:dashboard.package_link_min_probability}}::numeric AS package_link_min_probability,
  -- Another project prints a sentence near-identically when its committed
  -- sentences hold at least this share of the sentence's six-word runs.
  {{panel:dashboard.near_copy_share}}::numeric AS near_copy_share,
  -- A unit two measures both commit leaves a measure the pair check says it
  -- is not (only the other measure, or neither; src/commit_checks.py).
  {{panel:dashboard.apply_measure_pair_checks}} AS apply_measure_pair_checks,
  -- A unit whose scope is one investment links to at most this many of its
  -- project's packages (its most likely ones); a rule links to all.
  {{panel:dashboard.investment_package_top}} AS investment_package_top;
COMMENT ON VIEW wbg.dashboard_settings IS
  'The readings the dashboard views apply (one row): the floor on P(committed) for a unit to count, the asset-link levels that count, whether only Investment Project Financing operations count, whether a sentence counts under every asset and type it is given or only its best, the conditional-probability line for "not waiting on a later study", the probability at which a package counts as linked, the share of six-word runs that makes another project''s wording a near copy, whether the measure pair checks drop units, and how many packages one investment links to.';

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
