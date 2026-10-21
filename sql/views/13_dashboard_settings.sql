-- The few readings the dashboard views apply, in one place. Each is a
-- setting Dev may change; a change is
-- one edit here and a publish. Nothing upstream reads these: the files and
-- the model's answers do not depend on them.
DROP VIEW IF EXISTS wbg.dashboard_settings CASCADE;
CREATE VIEW wbg.dashboard_settings AS
SELECT
  -- A unit counts towards a commitment when the model's most likely grade is
  -- 3 (committed), P(grade 3) is at least this (Dev: no floor; 0),
  0.0::numeric AS commitment_min_probability,
  -- and its asset is found at one of these levels: 'unit', the sentence's
  -- own words name it (every asset the model says yes to); 'paragraph', the
  -- sentence names none and the paragraph's asset map gives it (Dev,
  -- 2026-10-20: a measure found in a sentence takes its asset from the
  -- sentence or, failing that, its paragraph). 'component' and 'none' widen.
  ARRAY['unit', 'paragraph']::text[] AS commitment_link_levels,
  -- Only Investment Project Financing operations (Dev, 2026-10-20).
  true AS ipf_only,
  -- A sentence counts under its one best asset and one best type (false), or
  -- under every asset and type the model says yes to (true; on the test
  -- slice that made about seven commitments of each sentence). Undecided.
  false AS every_asset_and_type,
  -- A unit is "not waiting on a later study" below this conditional
  -- probability.
  0.3::numeric AS not_conditional_below,
  -- A package is linked to a commitment at this probability or more (the
  -- link stage's reading threshold, link_packages.YES).
  0.5::numeric AS package_link_min_probability,
  -- Another project prints a sentence near-identically when its committed
  -- sentences hold at least this share of the sentence's six-word runs.
  0.5::numeric AS near_copy_share,
  -- A unit two measures both commit leaves a measure the pair check says it
  -- is not (only the other measure, or neither; src/commit_checks.py).
  true AS apply_measure_pair_checks,
  -- A unit whose scope is one investment links to at most this many of its
  -- project's packages (its most likely ones); a rule links to all.
  3 AS investment_package_top;
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
