-- Commitments: the grain of every dashboard. A commitment is one project,
-- one digital asset, one measure and one type (Dev's definition), held up by
-- the committed sentences the decision model found. Which sentences count is
-- set in wbg.dashboard_settings (13_dashboard_settings.sql). The ids are a
-- hash of the four parts, so they are the same on every load.
--
-- Package status precedence for a commitment with several packages
-- (DECIDE): the first in this order, which puts what needs a conversation
-- first.
DROP VIEW IF EXISTS wbg.package_status_order CASCADE;
CREATE VIEW wbg.package_status_order AS
SELECT * FROM (VALUES (1, 'Overdue'), (2, 'Planned'), (3, 'Planned, no date'), (4, 'Tendered'),
                      (5, 'Contract signed'), (6, 'In process per plan'),
                      (7, 'Signed per plan'), (8, 'Canceled'), (9, 'Status unknown'))
  AS o(status_rank, package_status);
COMMENT ON VIEW wbg.package_status_order IS
  'The order in which a commitment with several linked packages takes its status: Overdue first, then Planned, Tendered, Contract signed and the plan-only states. A proposal for Dev to confirm.';

-- One row per committed unit that counts, per project it belongs to, with
-- the commitment it makes up. The base of the views below.
DROP VIEW IF EXISTS wbg.commitment_units CASCADE;
CREATE VIEW wbg.commitment_units AS
WITH u AS (
  SELECT f.label_set, f.label_id, pp.project_id, f.unit_id, f.unit_kind, f.paragraph_id,
         f.document_id, f.text, f.probability_grade_3, f.conditional_probability, f.firmness,
         f.template_project_count, f.copy_unit_ids, f.linked_asset_level,
         coalesce(f.linked_asset_id, '(no asset)') AS asset_id,
         CASE WHEN coalesce(cardinality(f.type_ids_above_threshold), 0) > 0
              THEN f.best_type_id ELSE '(no type)' END AS type_id,
         -- the wording, for collapsing word-for-word copies
         regexp_replace(lower(f.text), '\s+', ' ', 'g') AS wording
  FROM wbg.measure_findings f
  JOIN wbg.paragraph_projects pp ON pp.paragraph_id = f.paragraph_id
  CROSS JOIN wbg.dashboard_settings s
  WHERE f.was_sent_to_model AND f.most_likely_grade = 3
    AND f.probability_grade_3 >= s.commitment_min_probability
    AND coalesce(f.linked_asset_level, 'none') = ANY (s.commitment_link_levels)
)
SELECT left(encode(sha256(convert_to(project_id || '|' || label_set || '|' || asset_id || '|'
                                     || type_id, 'UTF8')), 'hex'), 16) AS commitment_id,
       u.*
FROM u;
COMMENT ON VIEW wbg.commitment_units IS
  'Every committed unit that counts towards a commitment (grade 3, the probability floor and link levels of dashboard_settings), per project, with its commitment_id: the first 16 hex characters of SHA-256 of project_id|label_set|asset_id|type_id. A unit with no linked asset has asset_id ''(no asset)'', one with no type above 0.5 type_id ''(no type)''.';

-- Each commitment's linked packages with where they stand.
DROP VIEW IF EXISTS wbg.commitment_package_status CASCADE;
CREATE VIEW wbg.commitment_package_status AS
SELECT u.commitment_id, cp.package_id, max(cp.probability) AS link_probability,
       count(DISTINCT u.unit_id) AS units_linked,
       s.project_id, s.description_clean, s.category, s.method_code, s.estimated_amount_usd,
       s.planned_date, s.plan_status, s.in_latest_plan, s.notice_date, s.notice_deadline,
       s.signed_date, s.supplier, s.package_status, s.next_date
FROM wbg.commitment_units u
JOIN wbg.commitment_packages cp ON cp.label_set = u.label_set AND cp.label_id = u.label_id
                               AND cp.unit_id = u.unit_id AND cp.project_id = u.project_id
CROSS JOIN wbg.dashboard_settings ds
JOIN wbg.package_status s ON s.package_id = cp.package_id
WHERE cp.probability >= ds.package_link_min_probability
GROUP BY u.commitment_id, cp.package_id, s.project_id, s.description_clean, s.category,
         s.method_code, s.estimated_amount_usd, s.planned_date, s.plan_status,
         s.in_latest_plan, s.notice_date, s.notice_deadline, s.signed_date, s.supplier,
         s.package_status, s.next_date;
COMMENT ON VIEW wbg.commitment_package_status IS
  'One row per commitment and procurement package the decision model links to any of its sentences at the reading threshold (dashboard_settings, 0.5): the package''s plan columns, latest notice, signed contract and package_status (see package_status). A link means the package is likely where the commitment is carried out; it is the model''s reading, unmeasured until a sample is graded.';

DROP VIEW IF EXISTS wbg.commitments CASCADE;
CREATE VIEW wbg.commitments AS
WITH c AS (
  SELECT u.commitment_id, u.project_id, u.label_set, u.asset_id, u.type_id,
         count(DISTINCT u.wording) AS sentences,
         count(DISTINCT u.paragraph_id) AS paragraphs,
         count(DISTINCT u.document_id) AS documents,
         max(u.probability_grade_3) AS strongest_p,
         bool_or(dd.is_latest) AS in_latest_document,
         avg((coalesce(u.conditional_probability, 0) < ds.not_conditional_below)::int)::numeric(4, 3)
           AS share_not_conditional,
         min(d.disclosure_date) AS first_seen_date,
         max(u.template_project_count) AS same_wording_projects
  FROM wbg.commitment_units u
  CROSS JOIN wbg.dashboard_settings ds
  LEFT JOIN wbg.documents d ON d.document_id = u.document_id
  LEFT JOIN wbg.documents_distinct dd
         ON dd.project_id = u.project_id
        AND (dd.document_id = u.document_id OR u.document_id = ANY (dd.duplicate_document_ids))
  GROUP BY u.commitment_id, u.project_id, u.label_set, u.asset_id, u.type_id
),
p AS (
  SELECT ps.commitment_id, count(*) AS linked_packages,
         (array_agg(ps.package_status ORDER BY o.status_rank, ps.package_id))[1] AS status,
         min(ps.next_date) AS next_date
  FROM wbg.commitment_package_status ps
  LEFT JOIN wbg.package_status_order o ON o.package_status = ps.package_status
  GROUP BY ps.commitment_id
)
SELECT c.commitment_id,
       i.project_id, i.project_name, i.cohort_set, i.country_name, i.country_code, i.region,
       i.is_regional,
       c.label_set, l.label_name AS measure_name,
       c.asset_id, coalesce(al.label_name, c.asset_id) AS asset_name,
       coalesce(ag.group_id, '(no asset)') AS asset_group_id,
       coalesce(ag.group_name, '(no asset)') AS asset_group_name,
       c.type_id, coalesce(t.type_name, c.type_id) AS type_name,
       c.sentences, c.paragraphs, c.documents, c.strongest_p,
       coalesce(c.in_latest_document, false) AS in_latest_document,
       c.share_not_conditional, c.first_seen_date, c.same_wording_projects,
       coalesce(p.linked_packages, 0) AS linked_packages,
       coalesce(p.status, 'No package found') AS status,
       p.next_date
FROM c
JOIN wbg.project_info i ON i.project_id = c.project_id
LEFT JOIN p ON p.commitment_id = c.commitment_id
LEFT JOIN (SELECT DISTINCT ON (label_set) label_set, label_name FROM wbg.labels
           WHERE label_set <> 'assets' ORDER BY label_set, label_id) l ON l.label_set = c.label_set
LEFT JOIN wbg.labels al ON al.label_set = 'assets' AND al.label_id = c.asset_id
LEFT JOIN wbg.label_groups ag ON ag.label_set = 'assets' AND ag.label_id = c.asset_id
LEFT JOIN (SELECT DISTINCT ON (label_set, type_id) label_set, type_id, type_name
           FROM wbg.measure_types ORDER BY label_set, type_id, label_id) t
       ON t.label_set = c.label_set AND t.type_id = c.type_id;
COMMENT ON VIEW wbg.commitments IS
  'One row per commitment: one project, one digital asset, one measure and one type, held up by the committed sentences the decision model found (dashboard_settings says which count). sentences collapses word-for-word copies across documents; strongest_p is the highest P(committed); in_latest_document says whether any sentence is in the project''s most recently disclosed document; share_not_conditional is the share of sentences below the conditional line; first_seen_date the earliest disclosure date; same_wording_projects the most other projects printing the same words. status is the first of its linked packages'' statuses in package_status_order, or No package found. A commitment in an appraisal document is design intent, not delivery; nothing here is causal; the pipeline misses real commitments.';

-- The commitment's sentences, one row per distinct wording (the strongest
-- unit printing it).
DROP VIEW IF EXISTS wbg.commitment_sentences CASCADE;
CREATE VIEW wbg.commitment_sentences AS
SELECT DISTINCT ON (u.commitment_id, u.wording)
       u.commitment_id, u.unit_id, u.text, u.probability_grade_3, u.conditional_probability,
       u.firmness, pa.section_title, d.document_kind, d.disclosure_date AS document_date,
       u.document_id,
       (SELECT count(DISTINCT split_part(x, ':', 1)) FROM unnest(u.copy_unit_ids) x
         WHERE split_part(x, ':', 1) <> u.document_id) AS also_in_documents
FROM wbg.commitment_units u
LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = u.paragraph_id
LEFT JOIN wbg.documents d ON d.document_id = u.document_id
ORDER BY u.commitment_id, u.wording, u.probability_grade_3 DESC, u.unit_id;
COMMENT ON VIEW wbg.commitment_sentences IS
  'One row per commitment and distinct sentence (word-for-word copies collapsed to the strongest unit): the text, P(committed), the conditional probability, the wording''s firmness, its section, document kind and date, and in how many other documents the same words are printed.';

-- The commitment's paragraphs, each with its sentences marked by offset.
DROP VIEW IF EXISTS wbg.commitment_evidence CASCADE;
CREATE VIEW wbg.commitment_evidence AS
WITH m AS (
  SELECT DISTINCT u.commitment_id, u.paragraph_id,
         su.text_start_offset - pa.text_start_offset AS s,
         su.text_end_offset - pa.text_start_offset AS e
  FROM wbg.commitment_units u
  JOIN wbg.search_units su ON su.unit_id = u.unit_id
  JOIN wbg.paragraphs pa ON pa.paragraph_id = u.paragraph_id
  WHERE su.text_start_offset >= pa.text_start_offset AND su.text_end_offset <= pa.text_end_offset
),
seq AS (
  SELECT m.*, coalesce(max(e) OVER (PARTITION BY commitment_id, paragraph_id ORDER BY s, e
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS pe
  FROM m
),
marked AS (
  SELECT q.commitment_id, q.paragraph_id,
         string_agg(substr(pa.text, q.pe + 1, greatest(q.s - q.pe, 0))
                    || '【' || substr(pa.text, greatest(q.s, q.pe) + 1, q.e - greatest(q.s, q.pe))
                    || '】', '' ORDER BY q.s, q.e)
           || substr(pa.text, max(q.e) + 1) AS paragraph_marked
  FROM seq q JOIN wbg.paragraphs pa ON pa.paragraph_id = q.paragraph_id
  WHERE q.e > q.pe
  GROUP BY q.commitment_id, q.paragraph_id, pa.text
),
agg AS (
  SELECT u.commitment_id, u.paragraph_id, count(DISTINCT u.wording) AS sentences,
         max(u.probability_grade_3) AS strongest_p,
         (array_agg(u.text ORDER BY u.probability_grade_3 DESC, u.unit_id))[1] AS key_sentence
  FROM wbg.commitment_units u GROUP BY u.commitment_id, u.paragraph_id
)
SELECT a.commitment_id, a.paragraph_id, pa.document_id, d.document_kind,
       d.disclosure_date AS document_date, pa.section_title,
       coalesce(nullif(pa.subcomponent_name, ''), pa.component_name) AS component_name,
       pa.page_from AS page_number,
       d.pdf_url || CASE WHEN pa.page_from IS NOT NULL THEN '#page=' || pa.page_from ELSE '' END
         AS pdf_url,
       a.sentences, a.strongest_p, a.key_sentence,
       coalesce(mk.paragraph_marked, pa.text) AS paragraph_marked
FROM agg a
JOIN wbg.paragraphs pa ON pa.paragraph_id = a.paragraph_id
LEFT JOIN wbg.documents d ON d.document_id = pa.document_id
LEFT JOIN marked mk ON mk.commitment_id = a.commitment_id AND mk.paragraph_id = a.paragraph_id;
COMMENT ON VIEW wbg.commitment_evidence IS
  'One row per commitment and paragraph holding its sentences: the document, its kind and date, section, component, page and a PDF link to that page, how many of the commitment''s sentences the paragraph holds, the strongest P(committed), the key sentence, and the paragraph text with each of the commitment''s sentences wrapped in 【 】 (placed by character offsets, not by search).';
