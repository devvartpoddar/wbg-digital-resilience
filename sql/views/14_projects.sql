-- The project pages: who, where, which documents, which components, which
-- assets. Every view here has a name for every project and a map-ready
-- country code (13_dashboard_settings.sql).

-- One row per cohort project, with the name and place every dashboard uses.
DROP VIEW IF EXISTS wbg.project_info CASCADE;
CREATE VIEW wbg.project_info AS
SELECT p.project_id,
       coalesce(p.project_name, '(record pending: ' || p.project_id || ')') AS project_name,
       p.project_name IS NULL AS is_record_pending,
       p.cohort_set, p.status, p.approval_date, p.approval_fiscal_year, p.closing_date,
       p.commitment_usd_millions, p.commitment_v2_usd_millions, p.commitment_v3_usd_millions,
       p.practice, p.lending_instrument,
       coalesce(p.region, '(record pending)') AS region,
       coalesce(p.country_name, '(record pending)') AS country_name,
       wbg.iso_country_code(p.country_code) AS country_code,
       coalesce(p.country_code ~ '^[0-9]', false) AS is_regional,
       -- Investment Project Financing: the projects interface says so, or (a
       -- record from its v3 form gives no instrument) the project has a
       -- Project Appraisal Document, which only that instrument is appraised
       -- in (results programmes have a Program Appraisal Document, policy
       -- loans a Program Document, and neither is fetched).
       coalesce(p.lending_instrument = 'Investment Project Financing'
                OR (coalesce(p.lending_instrument, '') = ''
                    AND EXISTS (SELECT 1 FROM wbg.documents d
                                WHERE p.project_id = ANY (d.project_ids)
                                  AND d.document_type = 'Project Appraisal Document')),
                false) AS is_ipf
FROM wbg.projects p;
COMMENT ON VIEW wbg.project_info IS
  'One row per cohort project with a name and place that are never blank: a project the projects interface has no record of is "(record pending: <id>)" and is_record_pending; country_code is ISO 3166 alpha-2 for the world map, NULL for a regional operation (is_regional, whose country_name is the region the interface gives). is_ipf marks Investment Project Financing, the operations the commitments count.';

-- A project's documents, one row per document id. Two papers can share a
-- title and a date (P171305 has two restructuring papers of 27 June 2025,
-- with different PDFs): both are kept and same_title_and_date_as names the
-- other, so a reader can tell; components and counts never add them twice
-- (project_components reads one document; commitments collapse copied
-- wording).
DROP VIEW IF EXISTS wbg.documents_distinct CASCADE;
DROP VIEW IF EXISTS wbg.project_documents CASCADE;
CREATE VIEW wbg.project_documents AS
WITH d AS (
  SELECT unnest(project_ids) AS project_id, document_id, document_kind, title,
         disclosure_date, pdf_url
  FROM wbg.documents
)
SELECT d.project_id, d.document_id, d.disclosure_date AS document_date, d.document_kind,
       d.title, wbg.https(d.pdf_url) AS pdf_url,
       row_number() OVER (PARTITION BY d.project_id
                          ORDER BY d.disclosure_date DESC NULLS LAST, d.document_id DESC) = 1
         AS is_latest,
       (SELECT array_agg(o.document_id ORDER BY o.document_id) FROM d o
         WHERE o.project_id = d.project_id AND o.document_id <> d.document_id
           AND o.title = d.title AND o.disclosure_date = d.disclosure_date)
         AS same_title_and_date_as
FROM d;
COMMENT ON VIEW wbg.project_documents IS
  'A project''s appraisal documents and project papers, one row per document, with the PDF address. is_latest marks the most recently disclosed (on a tie, the larger id); same_title_and_date_as lists other documents of the project with the same title and date (two papers, kept both).';

DROP VIEW IF EXISTS wbg.project_header CASCADE;
CREATE VIEW wbg.project_header AS
SELECT i.project_id, i.project_name, i.is_record_pending, i.cohort_set, i.country_name,
       i.country_code, i.region, i.is_regional, i.status, i.approval_fiscal_year,
       i.approval_fiscal_year AS approval_fy,
       i.commitment_usd_millions AS commitment_usd_m,
       i.commitment_v2_usd_millions AS commitment_v2_usd_m,
       i.commitment_v3_usd_millions AS commitment_v3_usd_m, i.closing_date,
       (i.closing_date - current_date) AS days_to_closing,
       (SELECT count(*) FROM wbg.project_documents d WHERE d.project_id = i.project_id)
         AS documents_read,
       (SELECT max(d.document_date) FROM wbg.project_documents d
         WHERE d.project_id = i.project_id) AS latest_document_date
FROM wbg.project_info i;
COMMENT ON VIEW wbg.project_header IS
  'The head of a project page: name, place, status, approval year, commitment (US$ millions, the projects interface''s current figure), closing date and days to it (negative once passed), documents read and the latest one''s date.';

-- The components of the project's latest document that lists any (one
-- document, so two papers of the same date are never added together), one
-- row per number. A latest paper often prints no costs (P148850's of 30
-- September 2026): each component then takes its cost from the most recent
-- document of the project that states one for the same component, by name
-- (equal, or one the start of the other) and failing that by number, and
-- says which document and date that is.
DROP VIEW IF EXISTS wbg.project_components CASCADE;
CREATE VIEW wbg.project_components AS
WITH c AS (
  SELECT unnest(c.project_ids) AS project_id, c.*
  FROM wbg.components c
),
latest AS (
  SELECT DISTINCT ON (c.project_id) c.project_id, c.document_id
  FROM c ORDER BY c.project_id, c.disclosure_date DESC NULLS LAST, c.document_id DESC
),
listed AS (
  SELECT DISTINCT ON (c.project_id, c.component_level, coalesce(c.component_number, c.component_name))
         c.*
  FROM c JOIN latest l ON l.project_id = c.project_id AND l.document_id = c.document_id
  ORDER BY c.project_id, c.component_level, coalesce(c.component_number, c.component_name),
           (c.read_from = 'heading'), (c.cost_usd_millions IS NULL), c.component_id
)
SELECT x.project_id, x.document_id, x.disclosure_date AS document_date, x.component_level,
       x.component_number, x.component_name, k.cost_usd_millions,
       k.cost_before_restructuring_usd_millions, k.restructuring_action,
       k.document_id AS cost_document_id, k.disclosure_date AS cost_document_date,
       k.matched_by AS cost_matched_by,
       -- The tables give one cost per component, all funding sources together,
       -- which can exceed the Bank's financing; no table read here splits them.
       CASE WHEN k.cost_usd_millions IS NOT NULL THEN 'All sources (Bank and others)' END
         AS cost_covers,
       x.read_from
FROM listed x
LEFT JOIN LATERAL (
  SELECT o.cost_usd_millions, o.cost_before_restructuring_usd_millions, o.restructuring_action,
         o.document_id, o.disclosure_date,
         CASE WHEN o.document_id = x.document_id THEN 'same document'
              WHEN wbg.same_component(o.name_key, x.name_key) THEN 'name'
              ELSE 'number' END AS matched_by
  FROM c o
  WHERE o.project_id = x.project_id AND o.component_level = x.component_level
    AND o.cost_usd_millions IS NOT NULL
    AND (wbg.same_component(o.name_key, x.name_key) OR o.component_number = x.component_number)
  ORDER BY (o.document_id = x.document_id) DESC,
           wbg.same_component(o.name_key, x.name_key) DESC,
           o.disclosure_date DESC NULLS LAST, o.document_id DESC
  LIMIT 1
) k ON true;
COMMENT ON VIEW wbg.project_components IS
  'The components (and sub-components) of each project''s latest document that lists any, one row per number. The cost (US$ millions) is that document''s where it prints one, else the most recent document of the project that states a cost for the same component (by name, failing that by number); cost_document_id, cost_document_date and cost_matched_by say where it came from. The cost covers all funding sources, not the Bank''s financing alone (cost_covers). A component no document costs has none.';

-- Project x asset from the asset map: the sentences the pipeline gives the
-- asset (wbg.unit_assets: its posterior at asset.floor, the sentence's own
-- or by the paragraph rule).
DROP VIEW IF EXISTS wbg.project_assets CASCADE;
CREATE VIEW wbg.project_assets AS
SELECT pp.project_id, ua.asset_id, l.label_name AS asset_name,
       count(DISTINCT ua.unit_id) FILTER (WHERE ua.asset_level = 'unit') AS sentences_naming_it,
       count(DISTINCT ua.unit_id) FILTER (WHERE ua.asset_level = 'paragraph')
         AS sentences_from_paragraph,
       count(DISTINCT ua.paragraph_id) AS paragraphs,
       max(ua.probability) FILTER (WHERE ua.asset_level = 'unit') AS best_probability
FROM wbg.unit_assets ua
JOIN wbg.paragraph_projects pp ON pp.paragraph_id = ua.paragraph_id
LEFT JOIN wbg.labels l ON l.label_set = 'assets' AND l.label_id = ua.asset_id
WHERE ua.asset_level <> 'none'
GROUP BY pp.project_id, ua.asset_id, l.label_name;
COMMENT ON VIEW wbg.project_assets IS
  'One row per project and digital asset the asset map finds there: sentences that name it (their own posterior at the control panel''s asset.floor; grades 2 supported and 3 financed counted together) and sentences that take it from earlier sentences of their paragraph. The model''s reading, not a person''s; documented is not done.';
