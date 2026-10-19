-- A measure's candidate units in reading order, with their text, for
-- reading in Metabase. The order (src/candidates.py prioritise()): a word
-- pattern hit first, then the commitment probability and the embedding score.
-- Firmness 'outcome' ("will enable faster relief") is shown, not filtered.
-- Set label_set to the set being read; add "AND reading_order <= 50" to
-- read the top of the list.
SELECT *
FROM wbg.candidates_in_reading_order
WHERE label_set = '<set>'
ORDER BY label_id, reading_order;
