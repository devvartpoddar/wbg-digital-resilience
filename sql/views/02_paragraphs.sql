-- Paragraphs with their document's title, kind and date.
DROP VIEW IF EXISTS wbg.paragraphs_with_document CASCADE;
CREATE VIEW wbg.paragraphs_with_document AS
SELECT pa.paragraph_id, pa.document_id, d.document_kind, d.title AS document_title,
       d.disclosure_date, pa.project_ids, pa.section_path, pa.section_title, pa.block,
       pa.is_for_model, pa.component_number, pa.component_name, pa.subcomponent_number,
       pa.subcomponent_name, pa.word_count, pa.page_from, pa.page_to, pa.text
FROM wbg.paragraphs pa
LEFT JOIN wbg.documents d USING (document_id);
COMMENT ON VIEW wbg.paragraphs_with_document IS
  'One paragraph with its text and its document''s title, kind and disclosure date.';

DROP VIEW IF EXISTS wbg.paragraph_project_links;
CREATE VIEW wbg.paragraph_project_links AS
SELECT pp.paragraph_id, pp.project_id, pp.document_id, pr.project_name, pr.country_name
FROM wbg.paragraph_projects pp
LEFT JOIN wbg.projects pr USING (project_id);
COMMENT ON VIEW wbg.paragraph_project_links IS
  'One row per paragraph and project (a regional document''s paragraph can belong to several), with the project''s name and country.';

DROP VIEW IF EXISTS wbg.data_as_of;
CREATE VIEW wbg.data_as_of AS
SELECT release_id, commit, has_uncommitted_code, loaded_at, appraisal_fetched_date,
       procurement_fetched_date, projects_fetched_date
FROM wbg.data_release;
COMMENT ON VIEW wbg.data_as_of IS
  'The one data release this database holds: when it was loaded and when each kind of record was last fetched.';
