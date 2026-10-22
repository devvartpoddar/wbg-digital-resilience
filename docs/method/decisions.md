# One decision system for every question to the decision model

Every question the pipeline puts to the decision model (Jev) is handled the
same way. Only the thresholds differ from one question to another; the
method and the inputs do not.

1. **Prior.** What is known about the item before asking: a few numbers that
   need no model call (the inputs below).
2. **Skip.** Items whose prior is so low that no answer could make them count
   are not asked. The cut-off is set by one rule for every question: skip as
   much as possible while losing at most 1 per cent of the items a person
   graded yes.
3. **Ask.** The decision model answers, and the answer is stored (section 5a).
4. **Posterior.** The prior and the model's answer are combined into the
   probability that a person would say yes (`src/posterior.py`).
5. **Decision.** The floor reads the posterior, never the model's answer alone.

Precision comes before cost: a skip exists to drop items that cannot count,
not to save money at the price of recall.

## The questions

| Question to the decision model | Unit | Prior inputs stored (columns `prior_*`) |
|---|---|---|
| Asset map: a sentence and one asset, graded 0 to 3 (`src/classify.py`) | sentence | all |
| Measure: a sentence and one measure, graded 0 to 3 (`src/measure.py`) | sentence | all |
| Type: one choice among a measure's types (`src/kinds.py`) | committed sentence | phrase match to each type's name and definition; structure |
| Package to asset, yes or no (`src/link_packages.py`) | package description | phrase match to the asset's phrases (the phrase cosine is the `cosine` already kept) |
| Commitment to package, yes or no (`src/link_packages.py`) | sentence and package | phrase match between the sentence and the package description (the measure route's cosine is `measure_cosine`) |
| Pair check: only A, only B, both or neither (`src/commit_checks.py`) | sentence two or more measures commit (asked per pair) | each measure's probability and phrase cosine (the one prior that holds earlier answers: the pair is asked only because both measures committed the sentence) |

## The prior inputs that need no grading (`src/prior.py`)

- `prior_phrase_cosine`: the sentence's best embedding cosine to the label's
  search phrases (the number the cosine gates read).
- `prior_phrase_match`: how much of a phrase's information the sentence
  contains: for the best-matching phrase, the share of its words' weight
  found in the sentence, each word weighted by how rare it is in the corpus
  (inverse document frequency over every search unit). A rare word ("fibre",
  "early warning") counts for much more than a common one ("project").
- `prior_table_row`, `prior_position` (the sentence's place in its
  paragraph, 0 for a table row), `prior_words`, `prior_has_component`:
  where and what the sentence is.
- `prior_para_likely`: how many other sentences in the same paragraph look
  like the same label set (their best phrase cosine reaches the set's gate);
  `prior_para_best`: the best cosine to this label among those other
  sentences. A paragraph full of likely commitments makes each of its
  sentences likelier. Only the neighbours' similarity is used, never their
  model answers, so no sentence depends on the order the others were asked.

These are stored next to the model's answers and loaded into Postgres with
them (`paragraph_grades`, `measure_findings`, the links and checks tables).
Nothing reads them yet.

## What waits for grading

The inputs that learn from a person's grades, and every weight and cut-off,
are fitted only after an evaluation sheet is graded:

- the similarity to graded sentences (weighted vote and centroid, already in
  `src/posterior.py`);
- the posterior's weights over the model's answer and the prior inputs;
- the skip cut-off for each question (the 1 per cent rule);
- the floors (the 0.8 precision rule);
- the pair check as one more posterior input instead of a cut.

No number from an exploratory sample is written into the code or the panel.
The order is: run with no prior (the model's answers and the stored inputs),
look at the data, grade an evaluation sheet, fit the weights and cut-offs on
it, then test on graded items kept apart from those used to fit.

## Evidence so far (exploratory, 2026-10-22; not used to set anything)

On Dev's graded sentences (742 measure, 555 asset sentences), held-out halves:
the phrase match weighted by rarity and the sentence's structure made the
measures' prior clearly better (ranking quality 0.74 to 0.80), but added
nothing once the model had answered; for assets the similarity prior was
already strong (0.95) and a richer prior added about 2 points of sentences
given the right asset after the model's answer. A skip on the asset prior
dropped about half the asset questions at about 1 per cent of graded asset
pairs lost; for measures the cosine gate already removes most of what could
be skipped.
