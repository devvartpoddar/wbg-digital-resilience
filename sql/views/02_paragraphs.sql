-- Paragraphs with their text and their document's title, kind and date.
DROP VIEW IF EXISTS wbg.v_paragraphs;
CREATE VIEW wbg.v_paragraphs AS
SELECT pa.paragraph_id, pa.doc_id, d.doc_kind, d.title AS document_title,
       d.disclosure_date, pa.project_ids, pa.section_path, pa.section_title, pa.block,
       pa.for_model, pa.component_number, pa.component_name, pa.subcomponent_number,
       pa.subcomponent_name, pa.n_tokens, pa.page_from, pa.page_to, pa.text
FROM wbg.paragraphs pa
LEFT JOIN wbg.documents d USING (doc_id);
