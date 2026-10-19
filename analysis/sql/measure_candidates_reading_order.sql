-- Each measure's candidate sentences in reading order, with their text, for
-- reading in Metabase. The order (src/candidates.py prioritise()): a word
-- pattern hit first, then the commitment score and the embedding score.
-- Firmness 'outcome' ("will enable faster relief") is shown, not filtered.
-- Set label_set to the set being read; add "AND c.read_order <= 50" to
-- read the top of each list.
SELECT c.label_id, t.name AS measure, c.read_order, c.priority,
       c.p_commitment, c.firmness, c.types, c.route, c.rank,
       pp.project_id, c.doc_id, c.component_number,
       coalesce(s.text, p.text) AS text, c.copies
FROM wbg.candidates c
JOIN wbg.paragraph_projects pp USING (paragraph_id)
LEFT JOIN wbg.sentences s ON s.sentence_id = c.unit_id
LEFT JOIN wbg.paragraphs p ON p.paragraph_id = c.unit_id   -- footnotes and table rows
LEFT JOIN wbg.taxonomy t ON t.label_set = c.label_set AND t.label_id = c.label_id
WHERE c.label_set = '<set>'
ORDER BY c.label_id, c.read_order;
