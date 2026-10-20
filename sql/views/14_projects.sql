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
       p.commitment_usd_millions, p.practice, p.lending_instrument,
       coalesce(p.region, '(record pending)') AS region,
       coalesce(p.country_name, '(record pending)') AS country_name,
       wbg.iso_country_code(p.country_code) AS country_code,
       coalesce(p.country_code ~ '^[0-9]', false) AS is_regional
FROM wbg.projects p;
COMMENT ON VIEW wbg.project_info IS
  'One row per cohort project with a name and place that are never blank: a project the projects interface has no record of is "(record pending: <id>)" and is_record_pending; country_code is ISO 3166 alpha-2 for the world map, NULL for a regional operation (is_regional, whose country_name is the region the interface gives).';

-- Documents as a reader counts them: the interface lists some papers twice
-- under two document ids (P171305 has two restructuring papers dated 27
-- June 2025 with the same title, 200 of whose 201 paragraphs are word for
-- word the same). Rows with the same project, kind, title and disclosure
-- date are one document here, under the smallest id.
DROP VIEW IF EXISTS wbg.documents_distinct CASCADE;
CREATE VIEW wbg.documents_distinct AS
WITH d AS (
  SELECT unnest(project_ids) AS project_id, document_id, document_kind, title,
         disclosure_date, pdf_url
  FROM wbg.documents
),
g AS (
  SELECT project_id, document_kind, title, disclosure_date,
         min(document_id) AS document_id,
         array_agg(document_id ORDER BY document_id) AS document_ids
  FROM d GROUP BY project_id, document_kind, title, disclosure_date
)
SELECT g.project_id, g.document_id, g.document_kind, g.title, g.disclosure_date,
       d.pdf_url,
       g.document_ids[2:] AS duplicate_document_ids,
       row_number() OVER (PARTITION BY g.project_id
                          ORDER BY g.disclosure_date DESC NULLS LAST, g.document_id DESC) = 1
         AS is_latest
FROM g JOIN d ON d.project_id = g.project_id AND d.document_id = g.document_id;
COMMENT ON VIEW wbg.documents_distinct IS
  'One row per project and distinct document: rows of the documents table with the same project, kind, title and disclosure date are one document (the interface lists some papers twice under two ids), kept under the smallest id with the others in duplicate_document_ids. is_latest marks the project''s most recently disclosed document.';

DROP VIEW IF EXISTS wbg.project_header CASCADE;
CREATE VIEW wbg.project_header AS
SELECT i.project_id, i.project_name, i.is_record_pending, i.cohort_set, i.country_name,
       i.country_code, i.region, i.is_regional, i.status, i.approval_fiscal_year,
       i.commitment_usd_millions AS commitment_usd_m, i.closing_date,
       (i.closing_date - current_date) AS days_to_closing,
       (SELECT count(*) FROM wbg.documents_distinct d WHERE d.project_id = i.project_id)
         AS documents_read,
       (SELECT max(d.disclosure_date) FROM wbg.documents_distinct d
         WHERE d.project_id = i.project_id) AS latest_document_date
FROM wbg.project_info i;
COMMENT ON VIEW wbg.project_header IS
  'The head of a project page: name, place, status, approval year, commitment (US$ millions, the projects interface''s current figure), closing date and days to it (negative once passed; the interface''s date, which can lag a restructuring), documents read and the latest one''s date.';

DROP VIEW IF EXISTS wbg.project_documents CASCADE;
CREATE VIEW wbg.project_documents AS
SELECT project_id, document_id, disclosure_date AS document_date, document_kind, title,
       pdf_url, is_latest, duplicate_document_ids
FROM wbg.documents_distinct;
COMMENT ON VIEW wbg.project_documents IS
  'A project''s appraisal documents and project papers, one row per distinct document (see documents_distinct), with the PDF address.';

-- The components of the project's latest document that lists any, a row
-- per component or sub-component as that document states it.
DROP VIEW IF EXISTS wbg.project_components CASCADE;
CREATE VIEW wbg.project_components AS
WITH c AS (
  SELECT unnest(c.project_ids) AS project_id, c.*
  FROM wbg.components c
),
latest AS (
  SELECT DISTINCT ON (c.project_id) c.project_id, c.document_id, c.disclosure_date
  FROM c
  JOIN wbg.documents_distinct d ON d.project_id = c.project_id AND d.document_id = c.document_id
  ORDER BY c.project_id, c.disclosure_date DESC NULLS LAST, c.document_id DESC
)
SELECT DISTINCT ON (c.project_id, c.component_level, c.component_number, c.component_name)
       c.project_id, c.document_id, c.disclosure_date AS document_date, c.component_level,
       c.component_number, c.component_name, c.cost_usd_millions,
       c.cost_before_restructuring_usd_millions, c.restructuring_action, c.read_from
FROM c JOIN latest l ON l.project_id = c.project_id AND l.document_id = c.document_id
ORDER BY c.project_id, c.component_level, c.component_number, c.component_name,
         (c.read_from = 'heading'), c.component_id;
COMMENT ON VIEW wbg.project_components IS
  'The components (and sub-components) of each project''s latest document that lists any, with cost in US$ millions as that document prints it. Read from the data sheet or the restructuring table where there is one, else from headings.';

-- Project x asset from the asset map: how many paragraphs the decision
-- model grades financed (3) or supported without paying for the asset (2).
DROP VIEW IF EXISTS wbg.project_assets CASCADE;
CREATE VIEW wbg.project_assets AS
SELECT pp.project_id, g.label_id AS asset_id, l.label_name AS asset_name,
       ag.group_id AS asset_group_id, ag.group_name AS asset_group_name,
       count(DISTINCT g.paragraph_id) FILTER (WHERE g.most_likely_grade = 3)
         AS paragraphs_financed,
       count(DISTINCT g.paragraph_id) FILTER (WHERE g.most_likely_grade = 2)
         AS paragraphs_supported,
       CASE WHEN count(*) FILTER (WHERE g.most_likely_grade = 3) > 0 THEN 'Financed'
            ELSE 'Supported only' END AS how
FROM wbg.paragraph_grades g
JOIN wbg.paragraph_projects pp ON pp.paragraph_id = g.paragraph_id
LEFT JOIN wbg.labels l ON l.label_set = 'assets' AND l.label_id = g.label_id
LEFT JOIN wbg.label_groups ag ON ag.label_set = 'assets' AND ag.label_id = g.label_id
WHERE g.label_set = 'assets' AND g.most_likely_grade >= 2
GROUP BY pp.project_id, g.label_id, l.label_name, ag.group_id, ag.group_name;
COMMENT ON VIEW wbg.project_assets IS
  'One row per project and digital asset the asset map finds there: paragraphs the decision model grades financed (built, bought, installed, connected or operated) and paragraphs graded supported without paying for the asset (technical assistance, policy, studies). how is Financed if any paragraph is graded financed. The model''s grades; documented is not done.';
