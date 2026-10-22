# 5g. One decision system for every question to the decision model

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

Every question the pipeline puts to the decision model (TypeSafe's Jev) is handled the same way; only the thresholds differ, and each is one line of `inputs/config/control_panel.csv` (read by `src/panel.py`).

1. **Skip** (`src/skip.py`). A sentence is not asked a label's question when its best cosine to the label's phrases is under the label's gate and no word pattern of the label matches it. The gate is set by one rule for every label: the highest cosine that loses at most `skip.max_loss` (1 per cent) of the sentences a person graded yes under the current question, each at its design weight. It is fitted by the evaluation (`./wbg check <set> --evaluate`) once a label has `skip.min_positives` graded yes, written to `data/labels/<set>_gate.json` keyed on the question and the search run, and read by the measure pool and the asset map; until then the panel's starting gates apply (`measure.gate_cosine`, `asset.gate_cosine`). The gate is part of every run key.
2. **Ask.** The decision model answers one graded question about the sentence alone (no paragraph), in the same form for assets and measures (`classify.graded_question`). Every answer is stored and re-read (`src/answers.py`).
3. **Score.** The model's probability of grades 2 and 3 together (`read.grades`).
4. **Posterior** (`src/posterior.py`). The score goes through the label's probability layer, when one is fitted; otherwise the score stands.
5. **Decision.** A sentence counts when its posterior reaches the label's floor (`floor.<set>`, else `floor.default`, 0.5): `measure.is_committed`, loaded as `measure_findings.is_committed`. No stage and no view reads a single grade or the most likely grade; the grade probabilities are kept as the model's record only.

| Level | Skip | Decision |
|---|---|---|
| Asset map | fitted gate, else `asset.gate_cosine`; word patterns; `asset_feedback.*` | `asset.floor`; `asset.decay` and `asset.paragraph_floor` for the paragraph rule |
| Measure | fitted gate, else `measure.gate_cosine`; `measure.top`; `feedback.*` | `floor.<set>`, else `floor.default` |
| Type | none: every committed sentence | `floor.types` (every type at it with `types.multiple`) |
| Package to asset | `link.asset_top` | `link.asset_yes` |
| Commitment to package | `link.measure_top` and a shared asset | `dashboard.package_link_min_probability` |
| Pair check | none: every sentence two measures commit | `floor.pair_check` (the posterior that the sentence is not the measure) |

Every row has the whole loop: a graded source, a probability layer, a floor read on the posterior, and a suggested floor. Assets and measures have theirs here and in the tuning report; types, pair checks and both package links have theirs in `src/judged.py` and `data/reports/evaluate_after_measures.md`:

| Decision | Graded on | Layer (one fit per decision, pooled by `posterior.pool`) |
|---|---|---|
| Type | the type column of each measure tab (several joined by \|) | one label per measure and type, on the sentence's vector |
| Pair check | tab `pair checks` (only A, only B, both, neither: each side yes or no) | one label per measure (`keep:<set>`), on the sentence's vector |
| Package to asset | tab `package assets` (yes, no) | one label per asset, on the package's vector |
| Commitment to package | tab `package links` (yes, no) | one label, on the pair's vector (sentence and package averaged) |

Each layer is keyed on the exact question its label asks (a type list, a measure's boundary, an asset or link question), so a changed wording starts without one, and each stage's run key holds the layers it read. With no grades the posterior is the model's answer. Every decision asks one form of question (`classify.graded_question`: the item, the label's name and definition, a 0-3 scale), read as P(2)+P(3) (`classify.graded_score`). Several winners are allowed everywhere: a sentence may count for several measures, several assets (`asset.multiple`) and several types (`types.multiple`); a package may have several assets.

## The probability layer

A logistic regression stacked on three inputs: the logit of the model's score; a vote of the sentences a person graded for the label, each weighted by its design weight times exp(`posterior.sharpness` × (similarity − 1)); and the similarity to the weighted average graded yes minus that to the average graded no (`posterior.centroid`). Stacking, not strict Bayes: the score and the similarities read the same sentence, so they are not independent, and a fitted weight on each absorbs what they share. Strict Bayes was dropped on 2026-10-22.

- **Design weights.** Sheets are drawn by stratum, not at random, so every graded row carries its stratum's size over the rows drawn (`weight` in `data/labels/<set>_units.csv`) into the fit, the vote, the centroids and every precision and recall reported.
- **Who votes.** Every graded sentence but the sentence itself: its own grade is left out of its features when fitting, as an ungraded sentence's never include one. Near-copies and sentences of the same project vote (word-for-word copies are already merged; only 6 of 1,383 committed sentences had a neighbour in another project above 0.95; a rule leaving out the same project starved measures whose yes sit in a few projects). `posterior.near_copy` can leave near-copies out again; it is off. Leakage between projects is the out-of-sample check's job.
- **Pooled or per label.** `posterior.pool` (on) fits each decision's labels at once: the measures together, the assets together, and likewise the types, the pair checks, the package assets and the package links. One intercept per label, the weights on the score, vote and centroid shared. A measure has about 40 graded rows and a type or an asset fewer, few for four weights of its own; the tuning report compares pooled and per label on the measures' grades.
- **Grades follow the text.** A grade is matched to the sentence whose text it was given for (`text_sha256`), never to the stored unit id, which a re-parse moves; a grade whose text is no longer in the corpus is left out.
- **Out of sample.** The evaluation scores each graded sentence with a layer fitted on the other folds (`posterior.folds`, whole projects to a fold) and suggests the floor from those out-of-fold probabilities: the floor with the most recall whose weighted precision reaches `floor.target_precision`. The panel's floor is set by a person.
- **Current question only.** The layer learns only from grades given under the exact question now asked; a revised definition starts without one.
- **Training rows only.** Rows of an evaluation sheet (stratum `eval:*`, section 5c) never fit a layer or a gate.
- A label with fewer than `posterior.min_graded` graded sentences, or fewer than `posterior.min_each` of either kind, gets no layer (pooled: `min_each` per measure, `min_graded` over the pool). `posterior.ridge` is the precision of a normal prior on each standardised weight (1 = a prior standard deviation of 1); the fit is Newton's method to convergence. `posterior.on` false switches the layer off.

## Tuning from the grades (`src/tune.py`)

`./wbg check all --evaluate` ends by writing `data/reports/tune.md`; it changes nothing, a person reads it and sets the panel. It holds: the layer settings on a grid (sharpness 5 to 80, near-copy off/0.97/0.95, ridge 0.25/1/4, centroid on/off, pooled or not), each out of sample by project fold and ranked by weighted log loss, with mean AUC and precision and recall at each floor; which reading of the model's answer ranks graded sentences best (P(2)+P(3), P(3) or the expected grade); `asset.floor` by `asset.decay` against the assets people gave (in sample); and the settings these grades cannot set, with the sheet each needs. The skip rule's report gives, beside each fitted gate, the 95 per cent upper bound on the share of yes it loses: with n graded yes and none lost that is about 3/n, so 30 yes vouch for a loss under 10 per cent, not 1; the elusion sheet checks below the gate.

The layer is written to `data/labels/<set>_posterior.json` (assets: `assets_posterior.json`) and is part of the run key of every stage that reads it.

## The prior inputs (`src/prior.py`): stored, not yet read

Every question stores, beside its answer, what is known before asking: `prior_phrase_cosine` (best cosine to the label's phrases) and `prior_phrase_match` (the best phrase's share of words found in the text, each weighted by its rarity in the corpus). After every stage, `wbg.unit_paragraph_context` counts the other sentences of each paragraph the pipeline gave an asset (`prior_para_assets`) or committed for a measure (`prior_para_measures`), from the finished classification read through its layers. Types store a phrase match per type; package links their cosine and phrase match; pair checks each measure's probability and cosine.

**Planned.** A prior fitted over these inputs enters the layer as one more stacked input with its own weight, and is tested on a held-out sheet before it is used. Until then nothing reads them.
