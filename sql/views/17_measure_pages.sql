-- The measure and overview pages. Flat views with plain columns (no arrays,
-- no table functions), so Metabase's query builder can pivot and filter
-- them field by field. Every count is of commitments (16_commitments.sql)
-- or of the projects holding them.

-- measure_quality is loaded from data/labels/measure_quality.csv once a sheet
-- has been evaluated; until then an empty table keeps the summary working.
CREATE TABLE IF NOT EXISTS wbg.measure_quality (label_set text, version text, question_sha256 text,
  run_id text, graded_units integer, precision numeric, recall numeric,
  evaluated_at timestamptz, report_path text);

DROP VIEW IF EXISTS wbg.measure_summary CASCADE;
CREATE VIEW wbg.measure_summary AS
WITH m AS (
  SELECT DISTINCT ON (label_set) label_set, label_name AS measure_name, definition, version
  FROM wbg.labels WHERE label_set <> 'assets' AND is_active
  ORDER BY label_set, label_id
),
c AS (
  SELECT label_set, count(*) AS commitments, count(DISTINCT project_id) AS projects,
         count(DISTINCT country_name) FILTER (WHERE NOT is_regional) AS countries,
         count(DISTINCT type_id) FILTER (WHERE type_id <> '(no type)') AS types
  FROM wbg.commitments GROUP BY label_set
),
q AS (
  SELECT DISTINCT ON (label_set) label_set, version AS graded_version, graded_units, precision, recall,
         run_id AS graded_run_id, question_sha256 AS graded_question_sha256, evaluated_at
  FROM wbg.measure_quality ORDER BY label_set, evaluated_at DESC
)
SELECT m.label_set, m.measure_name, m.definition, m.version,
       coalesce(c.commitments, 0) AS commitments, coalesce(c.projects, 0) AS projects,
       coalesce(c.countries, 0) AS countries, coalesce(c.types, 0) AS types,
       q.graded_units, q.precision, q.recall, q.graded_version, q.graded_run_id,
       q.graded_question_sha256,
       q.evaluated_at AS graded_at
FROM m
LEFT JOIN c USING (label_set)
LEFT JOIN q USING (label_set);
COMMENT ON VIEW wbg.measure_summary IS
  'One row per measure: its definition and version, how many commitments, projects, countries (regional operations not counted as countries) and types the model finds, and the latest graded sheet''s numbers (graded_units, precision, recall; null where no sheet is graded). A sheet graded under an earlier wording of the definition says so in graded_question_sha256: compare it with the current question before quoting.';

DROP VIEW IF EXISTS wbg.measure_asset_matrix CASCADE;
CREATE VIEW wbg.measure_asset_matrix AS
SELECT label_set, measure_name, asset_name, count(*) AS commitments,
       count(DISTINCT project_id) AS projects
FROM wbg.commitments GROUP BY label_set, measure_name, asset_name;
COMMENT ON VIEW wbg.measure_asset_matrix IS
  'Commitments and projects by measure and asset: the overview heatmap.';

DROP VIEW IF EXISTS wbg.measure_type_asset_flow CASCADE;
CREATE VIEW wbg.measure_type_asset_flow AS
SELECT label_set, measure_name, type_name, asset_name, count(*) AS commitments,
       count(DISTINCT project_id) AS projects
FROM wbg.commitments GROUP BY label_set, measure_name, type_name, asset_name;
COMMENT ON VIEW wbg.measure_type_asset_flow IS
  'Commitments and projects by measure, type and asset: the flow from what is committed to which assets it concerns.';

DROP VIEW IF EXISTS wbg.measure_country_type CASCADE;
CREATE VIEW wbg.measure_country_type AS
SELECT label_set, measure_name, country_name, country_code, is_regional, type_name,
       count(*) AS commitments, count(DISTINCT project_id) AS projects
FROM wbg.commitments
GROUP BY label_set, measure_name, country_name, country_code, is_regional, type_name;
COMMENT ON VIEW wbg.measure_country_type IS
  'Commitments and projects by measure, country and type. country_code is ISO alpha-2 for the map and NULL for a regional operation (is_regional), whose country_name is its region.';

-- Linked packages by when they fall due: a quarter of the next date for
-- packages still to come, 'Overdue' for pending packages whose planned date
-- has passed, 'No date' where neither the plan nor a notice gives one.
DROP VIEW IF EXISTS wbg.commitment_status_by_quarter CASCADE;
CREATE VIEW wbg.commitment_status_by_quarter AS
SELECT c.label_set, c.measure_name, c.type_name, c.country_name, ps.package_status,
       CASE WHEN ps.package_status = 'Overdue' THEN 'Overdue'
            WHEN ps.signed_date IS NOT NULL
              THEN to_char(ps.signed_date, 'YYYY') || '-Q' || to_char(ps.signed_date, 'Q')
            WHEN ps.next_date IS NOT NULL
              THEN to_char(ps.next_date, 'YYYY') || '-Q' || to_char(ps.next_date, 'Q')
            ELSE 'No date' END AS quarter,
       count(DISTINCT ps.package_id) AS packages
FROM wbg.commitments c
JOIN wbg.commitment_package_status ps USING (commitment_id)
GROUP BY 1, 2, 3, 4, 5, 6;
COMMENT ON VIEW wbg.commitment_status_by_quarter IS
  'Packages linked to commitments, by measure, type, country, package status and quarter: the quarter of the next planned date or notice deadline for packages still to come, of the signing date for signed contracts, Overdue for pending packages past their planned date, No date otherwise.';

DROP VIEW IF EXISTS wbg.type_procurement_profile CASCADE;
CREATE VIEW wbg.type_procurement_profile AS
WITH p AS (
  -- one row per type and package, however many of the type's commitments link to it
  SELECT c.label_set, c.measure_name, c.type_name, ps.package_id,
         min(ps.method_code) AS method_code, min(ps.category) AS category,
         max(ps.estimated_amount_usd) AS estimated_amount_usd,
         min(ps.description_clean) AS description_clean,
         max(ps.link_probability) AS link_probability
  FROM wbg.commitments c
  JOIN wbg.commitment_package_status ps USING (commitment_id)
  GROUP BY c.label_set, c.measure_name, c.type_name, ps.package_id
)
SELECT label_set, measure_name, type_name, count(DISTINCT package_id) AS packages_linked,
       mode() WITHIN GROUP (ORDER BY method_code) AS usual_method,
       mode() WITHIN GROUP (ORDER BY category) AS usual_category,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY estimated_amount_usd)
         FILTER (WHERE estimated_amount_usd > 0) AS median_estimate_usd,
       (array_agg(description_clean ORDER BY link_probability DESC, package_id))[1]
         AS example_package
FROM p GROUP BY label_set, measure_name, type_name;
COMMENT ON VIEW wbg.type_procurement_profile IS
  'For each measure and type, the packages linked to its commitments: how many, the usual procurement method and category, the median estimated amount (US$, packages with an estimate above zero) and the most strongly linked package''s description as an example.';

-- How many committed units two measures share: a report, not a
-- correction. A unit can be a commitment to several measures (on 2026-10-20
-- 42 per cent of committed units counted under two or more; energy
-- efficiency and green data centres shared 207), and every count above
-- counts it under each.
DROP VIEW IF EXISTS wbg.measure_overlap CASCADE;
CREATE VIEW wbg.measure_overlap AS
WITH u AS (
  SELECT DISTINCT label_set, unit_id FROM wbg.measure_findings
  WHERE was_sent_to_model AND most_likely_grade = 3
),
n AS (SELECT label_set, count(*) AS committed_units FROM u GROUP BY label_set)
SELECT a.label_set AS label_set_a, b.label_set AS label_set_b,
       count(*) AS shared_units,
       na.committed_units AS units_a, nb.committed_units AS units_b,
       round(count(*)::numeric / least(na.committed_units, nb.committed_units), 3)
         AS share_of_smaller
FROM u a JOIN u b ON b.unit_id = a.unit_id AND b.label_set > a.label_set
JOIN n na ON na.label_set = a.label_set
JOIN n nb ON nb.label_set = b.label_set
GROUP BY a.label_set, b.label_set, na.committed_units, nb.committed_units;
COMMENT ON VIEW wbg.measure_overlap IS
  'For each pair of measures, how many committed units (the model''s grade 3, any asset link) both count, and that as a share of the smaller measure. A report only: nothing is deduplicated, and a unit committed to two measures counts under each everywhere else.';

-- Filter values, one view per dashboard filter.
DROP VIEW IF EXISTS wbg.filter_measures CASCADE;
CREATE VIEW wbg.filter_measures AS
SELECT DISTINCT label_set, measure_name FROM wbg.commitments;
COMMENT ON VIEW wbg.filter_measures IS 'Measures with commitments, for a dashboard filter.';

DROP VIEW IF EXISTS wbg.filter_types CASCADE;
CREATE VIEW wbg.filter_types AS
SELECT DISTINCT label_set, type_id, type_name FROM wbg.commitments;
COMMENT ON VIEW wbg.filter_types IS 'Type names per measure, for a dashboard filter (including ''(no type)'').';

DROP VIEW IF EXISTS wbg.filter_regions CASCADE;
CREATE VIEW wbg.filter_regions AS
SELECT DISTINCT region FROM wbg.project_info;
COMMENT ON VIEW wbg.filter_regions IS 'Regions of the cohort''s projects, for a dashboard filter.';

DROP VIEW IF EXISTS wbg.filter_countries CASCADE;
CREATE VIEW wbg.filter_countries AS
SELECT DISTINCT country_name, country_code, is_regional, region FROM wbg.project_info;
COMMENT ON VIEW wbg.filter_countries IS 'Countries (and, for regional operations, regions) of the cohort''s projects, for a dashboard filter.';

-- One filter for the SQL cards over commitments, as findings_under() is
-- for units:
--   SELECT * FROM wbg.commitments_under({{measure}} [[, type_ => {{type}}]]
--                                       [[, region => {{region}}]] [[, country => {{country}}]]
--                                       [[, months => {{months}}]])
-- months: commitments whose status is Overdue or whose next date falls
-- within that many months.
DROP FUNCTION IF EXISTS wbg.commitments_under(text, text, text, text, double precision);
CREATE FUNCTION wbg.commitments_under(
    measure text,
    type_ text DEFAULT NULL,
    region text DEFAULT NULL,
    country text DEFAULT NULL,
    months double precision DEFAULT NULL)
-- The rows of wbg.commitments: rebuilt with it on every load (17 runs after 16).
RETURNS SETOF wbg.commitments
LANGUAGE sql STABLE AS $$
  SELECT c.* FROM wbg.commitments c
  WHERE c.label_set = commitments_under.measure
    AND (commitments_under.type_ IS NULL OR c.type_id = commitments_under.type_
         OR c.type_name = commitments_under.type_)
    AND (commitments_under.region IS NULL OR c.region = commitments_under.region)
    AND (commitments_under.country IS NULL OR c.country_name = commitments_under.country
         OR c.country_code = commitments_under.country)
    AND (commitments_under.months IS NULL OR c.status = 'Overdue'
         OR c.next_date <= current_date + commitments_under.months * interval '1 month')
$$;
COMMENT ON FUNCTION wbg.commitments_under IS
  'The commitments of a measure under the dashboard filters (type, region, country, months to the next date); every argument but the measure may be NULL. Rows as wbg.commitments.';

-- The "How it's defined" tab: each pattern with the units it matched in the
-- measure's current run (measure_findings holds the run's pool, and each
-- unit's matched_patterns). The phrases and the grade scale are read from
-- label_queries and label_scale as they are.
DROP VIEW IF EXISTS wbg.label_pattern_hits CASCADE;
CREATE VIEW wbg.label_pattern_hits AS
SELECT p.label_set, p.label_id, p.pattern_id, p.pattern, p.is_active, p.version, p.file_sha256,
       count(f.unit_id) AS units_matched,
       count(f.unit_id) FILTER (WHERE f.most_likely_grade = 3) AS units_matched_committed
FROM wbg.label_patterns p
LEFT JOIN wbg.measure_findings f
  ON f.label_set = p.label_set AND f.label_id = p.label_id AND p.pattern_id = ANY(f.matched_patterns)
GROUP BY p.label_set, p.label_id, p.pattern_id, p.pattern, p.is_active, p.version, p.file_sha256;
COMMENT ON VIEW wbg.label_pattern_hits IS
  'Each word pattern of each set (label_patterns) with how many units of the measure''s current run it matched, and how many of those the decision model graded committed. A pattern only adds units to the pool; the model still grades every one.';
