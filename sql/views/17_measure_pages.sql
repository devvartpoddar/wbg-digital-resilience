-- Per measure: what the app's measure view reads beside the commitments.
-- Every count is of commitments (16_commitments.sql) or of the projects
-- holding them. commitments counts every commitment, paragraph groups included:
-- keyed_commitments (grouped by an asset type or a measure type) plus paragraph_groups
-- (sentences with nothing else to group them by, a bucket, not a finding).

-- measure_quality is written by the evaluation (src/wbg_portfolio/grading.py);
-- the load creates it empty until then.
DROP VIEW IF EXISTS wbg.measure_summary CASCADE;
CREATE VIEW wbg.measure_summary AS
WITH m AS (
  SELECT DISTINCT ON (label_set) label_set, label_name AS measure_name, definition, version, family
  FROM wbg.labels WHERE label_set <> 'assets' AND is_active
  ORDER BY label_set, label_id
),
c AS (
  SELECT label_set, count(*) AS commitments,
         count(*) FILTER (WHERE commitment_key <> 'paragraph') AS keyed_commitments,
         count(*) FILTER (WHERE commitment_key = 'paragraph') AS paragraph_groups,
         count(DISTINCT project_id) AS projects,
         count(DISTINCT country_name) FILTER (WHERE NOT is_regional) AS countries,
         (SELECT count(DISTINCT t) FROM wbg.commitment_summary x, unnest(x.type_ids) t
           WHERE x.label_set = cs.label_set) AS types
  FROM wbg.commitment_summary cs GROUP BY label_set
),
q AS (
  -- the evaluation sample's holdout sheet, then an evaluation sheet (a random draw), then a
  -- training sheet; the latest of each
  SELECT DISTINCT ON (label_set) label_set, version AS graded_version, sheet AS graded_sheet,
         graded_units, precision, recall, question_sha256 AS graded_question_sha256, evaluated_at,
         scope AS graded_scope
  FROM wbg.measure_quality
  ORDER BY label_set, (sheet = 'holdout') DESC, (sheet = 'evaluation') DESC, evaluated_at DESC
)
SELECT m.label_set, m.measure_name, m.family, m.definition, m.version,
       coalesce(c.commitments, 0) AS commitments, coalesce(c.keyed_commitments, 0) AS keyed_commitments,
       coalesce(c.paragraph_groups, 0) AS paragraph_groups, coalesce(c.projects, 0) AS projects,
       coalesce(c.countries, 0) AS countries, coalesce(c.types, 0) AS types,
       q.graded_sheet, q.graded_units, q.precision, q.recall, q.graded_version,
       q.graded_question_sha256,
       q.evaluated_at AS graded_at, q.graded_scope
FROM m
LEFT JOIN c USING (label_set)
LEFT JOIN q USING (label_set);
COMMENT ON VIEW wbg.measure_summary IS
  'One row per measure: its definition and version, how many commitments (commitments: every one, paragraph groups included; keyed_commitments: those grouped by an asset type or a measure type; paragraph_groups: the buckets of sentences with nothing else to group them by), projects, countries (regional operations not counted as countries) and types the model finds, and a graded sheet''s numbers (graded_units, precision, recall of the pipeline''s decision, weighted; null where no sheet is graded): the evaluation sample''s holdout sheet when one exists (graded by three models; graded_scope overall means the figures are over every measure because this one has too few holdout rows), else an evaluation sheet (a random draw, the dashboards'' figures) when one exists, else the latest training sheet (the hard cases near the floor; graded_sheet says which). A sheet graded under an earlier wording of the definition says so in graded_question_sha256: compare it with the current question before quoting.';

-- How many committed units two measures share: a report, not a
-- correction. A unit can be a commitment to several measures (on 2026-10-20
-- 42 per cent of committed units counted under two or more; energy
-- efficiency and green data centres shared 207), and every count above
-- counts it under each.
DROP VIEW IF EXISTS wbg.measure_overlap CASCADE;
CREATE VIEW wbg.measure_overlap AS
WITH u AS (
  SELECT DISTINCT label_set, unit_id FROM wbg.measure_findings
  WHERE was_sent_to_model AND is_committed
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
  'For each pair of measures, how many committed units (committed by the band rule, any asset link) both count, and that as a share of the smaller measure. A report only: nothing is deduplicated, and a unit committed to two measures counts under each everywhere else.';
