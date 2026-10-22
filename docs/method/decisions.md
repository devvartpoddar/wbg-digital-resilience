# One decision system for every question to the decision model

Every question the pipeline puts to the decision model (Jev) is handled the
same way. Only the thresholds differ from one question to another; the
method and the inputs do not.

1. **Prior.** What is known about the item before asking, as one probability:
   a fit over the prior inputs below.
2. **Skip.** Items whose prior is so low that no answer could make them count
   are not asked. The cut-off is set by one rule for every question: skip as
   much as possible while losing at most 1 per cent of the items a person
   graded yes. Until it is fitted, the cosine gates decide what is asked.
3. **Ask.** The decision model answers, and the answer is stored (section 5a).
4. **Posterior.** The prior and the model's answer are combined into the
   probability that a person would say yes (`src/posterior.py`).
5. **Decision.** The floor reads the posterior, never the model's answer alone.

## The thresholds, one line each in the control panel

The method is the same at every level; only these numbers differ, and each
is one line of `inputs/config/control_panel.csv`. Until a graded sheet fits
the skip rule, the cosine gates are the skip; until it fits the posterior,
the floors read the model's score (through the similarity layer when one is
fitted).

| Level | Skip (what is not asked) | Decision (floor) |
|---|---|---|
| Asset map | `asset.gate_cosine` | `asset.floor` (with `asset.decay` for the paragraph's earlier sentences) |
| Measure | `measure.gate_cosine`, `measure.top`, `feedback.*` | `floor.default`, `floor.<measure>` |
| Type | none: every committed sentence | `floor.types` (0: its most likely type) |
| Package to asset | `link.asset_top` | `link.asset_yes` |
| Commitment to package | `link.measure_top` and shared assets | `dashboard.package_link_min_probability` |
| Pair check | none: every sentence two measures commit | `floor.pair_check` (0: its most likely answer) |

There is one way to ask each question: the sentence alone (no paragraph
context, no asset gate on a measure, no paragraph map), in the same graded
form for assets and measures. The options that asked otherwise were removed
on 2026-10-22.

Precision comes before cost: a skip exists to drop items that cannot count,
not to save money at the price of recall.

## A worked example (one graded sentence, 2026-10-22)

A climate-annex table row, "Secondary sites will be identified for disaster
recovery purposes based on the climate risk assessment", asked against
climate risk assessment; Dev graded it 3. One fit over every measure's graded
sentences, with a starting point per measure (in-sample, to show the
arithmetic):

- **Prior.** On the log-odds scale (0 is even odds), each input's distance
  from its average, in standard deviations, times its weight: baseline 0.46,
  the measure's own starting point -0.04 (its share of yes is a little below
  the average), centroid +0.60, phrase match +0.40, other sentences of the
  paragraph committed for a measure (5 of 14) +0.38, vote +0.26, phrase
  cosine +0.02. Total 1.90: a prior of 0.87.
- **The model's answer.** Grades 0 to 3: 0.12, 0.35, 0.11, 0.42; its score
  (grades 2 and 3) 0.53, low for a graded commitment.
- **Posterior.** Baseline 0.54, the prior +1.08 (weight 1.06), the model's
  score -0.81 (weight 2.27): 0.81 on the log-odds scale, 0.69.
- **Decision.** 0.69 is above the floor of 0.5: committed, as Dev graded it.
  The prior's weight came out near 1, as strict Bayes would have it.

## The questions

| Question to the decision model | Unit | Prior inputs stored (columns `prior_*`) |
|---|---|---|
| Asset map: a sentence and one asset, graded 0 to 3 (`src/classify.py`) | sentence | phrase cosine, phrase match; paragraph counts |
| Measure: a sentence and one measure, graded 0 to 3 (`src/measure.py`) | sentence | phrase cosine, phrase match; paragraph counts |
| Type: one choice among a measure's types (`src/kinds.py`) | committed sentence | phrase match to each type's name and definition (types have no embedded phrases) |
| Package to asset, yes or no (`src/link_packages.py`) | package description | phrase cosine (also kept as `cosine`), phrase match to the asset's phrases |
| Commitment to package, yes or no (`src/link_packages.py`) | sentence and package | the package's cosine to the measure's phrases; phrase match between the sentence and the package description |
| Pair check: only A, only B, both or neither (`src/commit_checks.py`) | sentence two or more measures commit (asked per pair) | each measure's probability and phrase cosine (a prior that holds earlier answers: the pair is asked only because both measures committed the sentence) |

## The prior inputs (`src/prior.py`)

Made from the text and its embedding, stored with each stage's answers:

- `prior_phrase_cosine`: the best embedding cosine to the label's search
  phrases (meaning: it catches a paraphrase). It is the only similarity a
  label has before anyone grades it.
- `prior_phrase_match`: how much of a phrase's words the sentence holds:
  for the best-matching phrase, the share of its words' weight found in the
  sentence, each word weighted by how rare it is in the corpus (inverse
  document frequency over every search unit). A rare word ("fibre",
  "disaster") counts for far more than a common one ("project", "will").

Made once every stage has run, from what the pipeline classified, and
loaded as `wbg.unit_paragraph_context` (rebuilt whole at every publish):

- `prior_para_assets`: other sentences of the paragraph the asset map gives
  an asset (the best asset's probability at `asset.floor` or more).
- `prior_para_measures`: other sentences of the paragraph committed for any
  measure (each measure's floor).

A paragraph about data centres with two committed measure sentences raises
each of its sentences, whichever asset or measure is asked about. The counts
read the classification, not similarity: a cosine of 0.45 means one thing
for one label and another for the next, so similarities are not comparable
across labels. They move when the classification is tuned, as they should.
They read the finished classification, so no sentence depends on the order
the others were asked; the classification they read is the one made without
them, so the counts and the posterior do not feed each other.

Read by nothing yet. The structure of a sentence (table row, position,
length, component) was stored for a day and dropped (Dev, 2026-10-22): it
carried almost no weight, and the component depends on tagging not yet
checked.

## What waits for grading

The inputs that learn from a person's grades, and every weight and cut-off,
are fitted only after an evaluation sheet is graded:

- the similarity to graded sentences (weighted vote and centroid, already in
  `src/posterior.py`): the closest graded sentences "vote" yes or no, the
  closer the more, and the sentence's similarity to the average graded yes
  minus the average graded no;
- the prior's weights, in one fit across every measure (a starting point per
  measure) rather than one per measure: a fit on about 60 sentences gave
  vote and centroid almost no weight, a pooled fit gave them real weight;
- the posterior: the prior and the model's answer, combined either by a
  logistic fit or by strict Bayes (below);
- the skip cut-off for each question (the 1 per cent rule) and the floors
  (the 0.8 precision rule);
- the pair check as one more posterior input instead of a cut.

No number from an exploratory sample is written into the code or the panel.
The order is: run with no prior (the model's answers and the stored inputs),
look at the data, grade an evaluation sheet, fit the weights and cut-offs on
it, then test on graded items kept apart from those used to fit.

## Evidence so far (exploratory, 2026-10-22; not used to set anything)

Dev's graded sentences (742 measure, 555 asset sentences x 13 assets),
held-out halves, the prior fitted out-of-fold inside the training half. The
prior: phrase cosine, phrase match, vote, centroid, other sentences of the
paragraph committed for any measure (from the existing measure runs; the
asset count needs the sentence asset map, not yet run); for assets the
paragraph's phrase cosine stood in for the sentence's. Ranking quality is on
a 0 to 1 scale (0.5 is chance); R@.85 is recall at 0.85 precision.

| Measures | ranking | R@.85 | R@.90 | at floor 0.5: precision / recall |
|---|---|---|---|---|
| The model's score alone | 0.874 | 0.78 | 0.60 | 0.75 / 0.92 |
| Today's layer (score, vote, centroid) | 0.885 | 0.81 | 0.66 | 0.82 / 0.86 |
| Prior alone, no model call | 0.765 | 0.32 | 0.21 | 0.73 / 0.81 |
| Prior + answer, logistic | 0.890 | 0.83 | 0.67 | 0.82 / 0.87 |
| Prior + answer, strict Bayes | 0.886 | 0.82 | 0.64 | 0.83 / 0.86 |

| Assets (sentence x asset) | ranking | R@.85 | sentences given Dev's asset (floor 0.5) |
|---|---|---|---|
| The model's score alone | 0.934 | 0.03 | 0.64 |
| Today's layer | 0.958 | 0.25 | 0.60 |
| Prior alone | 0.949 | 0.22 | 0.57 |
| Prior + answer, logistic | 0.963 | 0.22 | 0.59 |
| Prior + answer, strict Bayes | 0.963 | 0.22 | 0.67 |

Strict Bayes: the posterior's log-odds are the prior's plus the log of how
much likelier the model's answer is for a yes than for a no (from a fit of
the model's score alone, less the base rate). For measures the two ways are
within a point, the logistic slightly ahead at 0.90 precision; for assets
strict Bayes gave the most sentences their right asset at a floor of 0.5,
because the logistic fit, calibrated to the 5 per cent of pairs that are
yes, put fewer assets over the floor (the floors are not tuned here). The
evaluation sheet decides between them. Earlier the same day, the structure
inputs and a count of similarity-defined neighbours moved recall after the
answer by a point or two, within the noise between splits.
