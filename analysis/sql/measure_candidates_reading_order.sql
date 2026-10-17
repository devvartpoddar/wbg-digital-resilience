-- Each measure's candidate sentences in reading order, with their text, for
-- reading in Metabase. The order (src/candidates.py prioritise()): a word
-- pattern hit first, then the commitment score and the embedding score.
-- On the six-measure trial (local embeddings, 1,250 sentences judged by one
-- reader), 71 per cent of each measure's first 30 were commitments of the
-- measure (embedding score alone: 34 per cent); by measure, from 27 per cent
-- (ID used in shocks, where most sentences state an outcome) to 90 per cent.
-- Firmness 'outcome' ("will enable faster relief") is shown, not filtered.
-- Change the label_set to read another set; add "AND c.read_order <= 50" to
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
WHERE c.label_set = 'six_measures'
ORDER BY c.label_id, c.read_order;
