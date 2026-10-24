-- The Explore dashboard: scratch measures (./wbg try) read from wbg.scores,
-- kind scratch, never from the decision tables. Nothing here reaches the
-- commitments views.
DROP VIEW IF EXISTS wbg.scratch_findings;
CREATE VIEW wbg.scratch_findings AS
SELECT s.label_set, t.label_name, t.definition, pp.project_id, pr.project_name, pr.country_name,
       pr.country_code, pr.region, pr.approval_fiscal_year, pr.cohort_set,
       u.unit_id, u.unit_kind, u.paragraph_id, u.document_id, d.document_title, d.document_kind,
       u.component_number, pa.component_name, pa.section_title,
       s.score::double precision AS probability, s.score >= coalesce(t.floor, {{panel:floor.default}}) AS is_committed,
       s.route, s.round AS feedback_round, s.cosine::double precision AS cosine,
       u.text, pa.text AS paragraph_text,
       wbg.https(dd.pdf_url) || CASE WHEN pa.page_from IS NOT NULL THEN '#page=' || pa.page_from ELSE '' END
         AS pdf_url
FROM wbg.scores s
JOIN wbg.search_units u ON u.unit_id = s.item_id
LEFT JOIN wbg.scratch_sets t ON t.label_set = s.label_set
LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = u.paragraph_id
LEFT JOIN wbg.paragraphs_with_document d ON d.paragraph_id = u.paragraph_id
LEFT JOIN wbg.documents dd ON dd.document_id = u.document_id
LEFT JOIN wbg.paragraph_projects pp ON pp.paragraph_id = u.paragraph_id
LEFT JOIN wbg.project_info pr ON pr.project_id = pp.project_id
WHERE s.kind = 'scratch';
COMMENT ON VIEW wbg.scratch_findings IS
  'Every sentence a scratch measure (./wbg try) asked the model about, with its probability, whether it reaches the floor the set was tried at (is_committed), how it entered the pool (route: embedding, pattern, both, feedback or stored), its project, document, component and text. Exploration only: nothing here is a commitment on the other pages.';

DROP VIEW IF EXISTS wbg.scratch_summary;
CREATE VIEW wbg.scratch_summary AS
SELECT t.label_set, t.label_name, t.definition, t.phrases, t.patterns, t.written_at, t.asked_by, t.floor,
       count(f.unit_id) AS sentences_asked,
       count(f.unit_id) FILTER (WHERE f.is_committed) AS sentences_committed,
       count(DISTINCT f.project_id) FILTER (WHERE f.is_committed) AS projects,
       count(DISTINCT f.country_name) FILTER (WHERE f.is_committed AND f.country_code IS NOT NULL) AS countries
FROM wbg.scratch_sets t
LEFT JOIN wbg.scratch_findings f ON f.label_set = t.label_set
GROUP BY t.label_set, t.label_name, t.definition, t.phrases, t.patterns, t.written_at, t.asked_by, t.floor;
COMMENT ON VIEW wbg.scratch_summary IS
  'One row per scratch measure: its wording as tried, when and by whom, and how many sentences, projects and countries it finds at its floor.';
