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
- `prior_para_assets_mean`, `prior_para_assets_max`,
  `prior_para_measures_mean`, `prior_para_measures_max`: how dense the
  paragraph is in assets and measures. Among the other sentences of the
  paragraph, the average and the highest of their best cosine over every
  asset's phrases, and over every measure's phrases. No cut-off goes in
  (the cosine gates are not used here): the fit decides what level counts.
  A paragraph about data centres with two measure sentences raises each of
  its sentences, whichever asset or measure is asked about; a measure added
  later counts from its first search. Made once per corpus from every set's
  search run (`data/prior/paragraphs-<key>.json`; the key is in every
  stage's run key).
  Only the neighbours' similarity is used, never their model answers, so no
  sentence depends on the order the others were asked.

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

The stored inputs were tested on Dev's graded sentences (742 measure, 555
asset sentences x 13 assets), held-out halves, computed with the functions in
`src/prior.py`. For assets the paragraph count of asset-like sentences was
approximated (sentence-level asset cosines are not in Postgres); the measure
count was exact. These counts used the cosine gates as cut-offs; they were
replaced the same day by the cut-off-free average and highest similarity,
which could not be tested off the box (the sentences' similarities to every
phrase are only in the search runs there). Ranking quality is on a 0 to 1 scale (0.5 is chance); R@.85
is recall at 0.85 precision.

| | prior before the answer: ranking, R@.85 | after the answer: R@.85, R@.90 |
|---|---|---|
| Measures, similarity only | 0.741, 0.21 | 0.81, 0.66 |
| + phrase match | 0.770, 0.33 | 0.81, 0.66 |
| + structure | 0.750, 0.26 | 0.83, 0.65 |
| + paragraph counts | 0.749, 0.22 | 0.83, 0.67 |
| + all stored inputs | 0.776, 0.38 | 0.83, 0.65 |
| Assets, similarity only | 0.934, 0.21 | 0.25, 0.19 |
| + paragraph counts | 0.935, 0.23 | 0.27, 0.20 |
| + all stored inputs | 0.936, 0.20 | 0.27, 0.17 |

The inputs make the measures' prior clearly better, the phrase match most;
once the model has answered, every input moves recall by a point or two,
about the noise between splits. The skip a prior allows at 1 per cent of the
graded yes lost did not change (measures about 6 per cent of questions,
assets about a third). They are stored because they cost no model call and
the graded evaluation sheet, not this sample, decides which the fit keeps.
