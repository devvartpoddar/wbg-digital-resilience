-- Each measure's candidate sentences, strongest first, with their text, for
-- reading in Metabase. Tier 1: a word pattern matched and p_commitment is at
-- least 0.5. Tier 2: found only by embedding, within its best 150, and
-- p_commitment at least 0.5. Tier 3: everything else (kept: some real
-- commitments score under 0.5).
-- On the six-measure trial (local embeddings, 312 hand-graded sentences),
-- tiers 1 and 2 held 57 to 158 sentences per measure and 73 to 100 per cent of
-- the known commitments; 0.5 and 150 are trial settings, not chosen cut-offs.
-- Firmness 'outcome' ("will enable faster relief") is shown, not filtered.
-- Change the label_set to read another set.
SELECT c.label_id, t.name AS measure,
       CASE WHEN c.route <> 'embedding' AND c.p_commitment >= 0.5 THEN 1
            WHEN c.route = 'embedding' AND c.rank <= 150 AND c.p_commitment >= 0.5 THEN 2
            ELSE 3 END AS tier,
       c.p_commitment, c.firmness, c.types, c.route, c.rank,
       pp.project_id, c.doc_id, c.component_number,
       coalesce(s.text, p.text) AS text, c.copies
FROM wbg.candidates c
JOIN wbg.paragraph_projects pp USING (paragraph_id)
LEFT JOIN wbg.sentences s ON s.sentence_id = c.unit_id
LEFT JOIN wbg.paragraphs p ON p.paragraph_id = c.unit_id   -- footnotes and table rows
LEFT JOIN wbg.taxonomy t ON t.label_set = c.label_set AND t.label_id = c.label_id
WHERE c.label_set = 'six_measures'
ORDER BY c.label_id, tier, c.p_commitment DESC, c.rank;
