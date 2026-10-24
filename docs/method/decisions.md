# 5g. One decision system for every question to the decision model

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

Every question the pipeline puts to the decision model (TypeSafe's Jev, pinned to the snapshot in `questions.MODEL`, through OpenRouter's decisions endpoint) is handled the same way; only the thresholds differ, and each is one line of `inputs/config/control_panel.csv` (`src/panel.py`; a line the code needs and the panel lacks is an error, never a default).

## The asker (`src/ask.py`, `src/questions.py`, `src/api.py`)

Two forms of question. A classification (an asset, a measure) asks `questions.graded_question`: how the sentence relates to the label, on the shared 0-3 scale (`analysis/labels/scale.csv`: 0 not described, 1 background or another party's work, 2 the project may deliver it, 3 the project explicitly finances, delivers or requires it). Its score is the probability of the grades the panel counts (`read.grades`, 2 and 3), out of the total. A check made after a classification (a type, a pair check, a package to an asset, a commitment to a package) asks `questions.fit_question`: does the label apply, yes, unsure or no, told what is already decided; its score is P(yes). Both read the raw probabilities (`questions.score`), never a rounded column.

Every sentence-level question sends the same state (`questions.Corpus.state`): the sentence with its list lead-in, the section title and the component heading, and with `ask.context` = `paragraph` the paragraph too. A package sends its description and plan component; a commitment-to-package pair sends both texts and both components.

`ask.ask` takes items (an id, a state, the questions for it), reads the store (`wbg_store.answers`, keyed on model, state and question), reads the old file store once for anything it holds and copies it in, and asks the rest in batches (`ask.batch_items` items sharing a question set, up to `ask.batch_max_chars` characters, ordered by the hash of their state so which items share a request depends on the items alone; one item a request when `ask.batch_items` is 0). Every answer is stored under its own item and question, with the snapshot that served it and its share of the call's cost. An incomplete answer is a failed request; a run with failed requests writes nothing and resumes next time; a refusal (bad key, no credit) stops at once. Spend is recorded per step in `wbg_store.spend`, in a `finally`, and `--max-spend` is enforced from that ledger (`api.Spend`).

## The funnel (`src/funnel.py`), the same for assets and measures

1. **Round 0.** A label's question goes to every sentence in scope that a word pattern of the label matches (`<set>_patterns.csv`), every sentence whose best cosine over the label's phrases and definition reaches the label's gate (the skip rule's fitted gate, else `measure.gate_cosine` or `asset.gate_cosine`; 0 asks every sentence), and every sentence the store already answers under the current question (nothing paid for is dropped). `measure.top` adds the best N by cosine (off).
2. **Rounds** (`src/feedback.py`): the sentences that count at the ask floor (`floor.ask`, `asset.ask_floor`) are averaged, minus `feedback.negative_weight` times the sentences asked that do not (Rocchio, no query term); the corpus is ranked against that query and the next `feedback.batch` unasked sentences are asked; a label stops when a round yields fewer than `feedback.min_yield` or after `feedback.max_rounds` (`asset_feedback.max_rounds` for the asset map; 0 turns rounds off). A yield rule for depth, not a recall guarantee; the elusion draw looks below the gate. Rounds run only when round 0 asked something new of a set already scored: re-seeding them from a larger stored pool would otherwise move the admitted set between runs that changed nothing else. A set whose questions, gates, patterns, search run and settings match its last finished run is skipped whole (`wbg.funnel_state`).
3. **Scores.** Every answer is one row of `wbg.scores`: kind (asset, measure), label set, label, unit, the score, the raw answer, the snapshot served, the route it was first asked by (embedding, pattern, both, stored, feedback), its round and its cosine.

## The checks (`src/checks.py`)

| Kind | Items | Question | Writes |
|---|---|---|---|
| type | every unit a measure counts at `floor.ask` x the measure's approved types (and the OTH types the gap step proposed) | fit: is it this kind, told the sentence is already a commitment to the measure | `scores` kind type |
| pair | every unit two measures both count at `floor.ask`, each side | fit: does the measure apply, the other's boundary (its `boundary` column, else its definition) beside it | kind pair, label set `a|b`, label `keep_a` or `keep_b` |
| package_asset | every package x its best `link.asset_top` assets by cosine | fit: does the package buy, build, install, connect, operate or design the asset | kind package_asset |
| link | every committed unit x the packages of its project that share an asset (at `link.asset_yes`, at the levels `dashboard.commitment_link_levels` counts), plus its `link.measure_top` nearest by the measure's phrases | fit: would carrying out the package deliver the commitment | kind link, item `unit|package` |

A measure with no type list a person wrote has one proposed from its committed sentences by a generative model (`checks.GEN_MODEL`, the response stored in the answer store under its prompt) into `data/types/<set>_types.csv`; with `types.gaps` on, OTH types are proposed from the sentences no approved type reaches and asked of those sentences.

## The decision (`src/decide.py`)

`./wbg publish` reads every kind's scores once, through the label's probability layer when one is fitted and `posterior.on` is true (else the score stands), against the label's floor, and writes the tables the views read: `measure_findings` (`is_committed` at `floor.<set>` else `floor.default`; the unit's assets; its types at `floor.types`, every one with `types.multiple`), `paragraph_grades` and `unit_assets` (a sentence's own assets at `asset.floor`, else with `asset.decay` on the assets whose decayed sum over the earlier sentences of its paragraph reaches `asset.paragraph_floor`, every one with `asset.multiple` else the best), `unit_pair_checks` (`p_keep_a`, `p_keep_b`; the views drop a unit from a measure when its side is under `floor.pair_check`), `package_assets` (a package has an asset at `link.asset_yes`), `commitment_packages` (a link counts at `dashboard.package_link_min_probability`). The views join and aggregate; `commitment_units` (section 5f) is the one place that adds the dashboards' own filters. Every comparison is "at or above".

| Decision | Gate | Floor |
|---|---|---|
| Asset map | fitted gate, else `asset.gate_cosine`; patterns; rounds | `asset.floor`; `asset.decay`, `asset.paragraph_floor`, `asset.multiple` |
| Measure | fitted gate, else `measure.gate_cosine`; patterns; rounds | `floor.<set>`, else `floor.default` |
| Type | every unit at `floor.ask` | `floor.types`, `types.multiple` |
| Pair check | every unit two measures count at `floor.ask` | `floor.pair_check` |
| Package to asset | `link.asset_top` by cosine | `link.asset_yes` |
| Commitment to package | a shared asset, or `link.measure_top` by cosine | `dashboard.package_link_min_probability` |

## The probability layer (`src/posterior.py`) and the skip rule (`src/skip.py`)

The layer is a logistic regression stacked on the logit of the model's score, a vote of the sentences a person graded for the label (each weighted by exp(`posterior.sharpness` x (similarity - 1))) and the difference between the sentence's similarity to the graded-yes centroid and to the graded-no centroid (`posterior.centroid`). A graded sentence never votes for itself. `posterior.pool` fits each decision kind's labels together, one intercept per label and shared slopes, under a ridge prior (`posterior.ridge`); a label needs `posterior.min_each` graded yes and no, the pool `posterior.min_graded` rows. The fit weights each graded row by its design weight raised to `posterior.fit_weight_power` (0: every row once); the reports always use the full design weight. It is checked out of fold by project (`posterior.folds`), whether or not `posterior.on` is true, so the evaluation can always say whether the layer beats the raw score. Each layer is keyed on the exact question its label asks and stored in `wbg_store.fits`.

The skip rule: a label's gate is the highest cosine that leaves at most `skip.max_loss` of the graded yes (training rows, at their design weights) below it, a yes a pattern matches never counting against it; fitted from `skip.min_positives` yes or more, keyed on the question and the search run whose cosines it read. The report gives the 95 per cent upper bound on the loss (about 3/n with n yes and none lost). Sentences a grader named on the closing task as committing a measure the funnel never asked count as graded yes below the gate.

## Grading and evaluation (`src/grading.py`)

**Tasks.** `./wbg check draw --seed n --design train|eval|elusion` draws items by stratum (training: 30 sentences a measure within 0.2 of its floor and 10 further, 15 and 5 an asset, 60 sentences whose asset comes from their paragraph, 15 pair checks a band, 15 package assets and 15 links a band, 50 random sentences; evaluation: 900 committed sentences over the measures and 30 not committed a measure, 20 and 5 an asset; elusion: 25 sentences a measure just below its gate and 25 far below) with Poisson sampling on one uniform per item (a hash of the seed and the item, so a draw is repeatable), each line shown with exactly its own chance, the assets of a shown commitment drawn with its measure line. It writes one JSONL task per decision: the sentence (or package), its paragraph with the sentence marked, project, section, component, the label's name and definition, the exact question the model was asked, the scale, the allowed values, the design weight, and for a type its parent measure task. A block's last task asks which other listed ids apply (`none` or ids joined by `|`).

**Fills.** The grader writes `{"task_id", "grade", "rationale"}` per task. `./wbg check fill --seed n --file <jsonl> --grader <who>` refuses the whole file when any task is unanswered, any answer is off its list or any id is unknown, then stores the grades in `wbg_store.grades`, keyed on the kind, the label and the item's text (a package: its description's checksum; a link: text and package together), with the design, the grader, the rationale, the stratum, the weight and the question hash as provenance. A later grade of the same item under the same label and design replaces an earlier one. The pair checks are derived from the two measure grades on a sentence; a type counts only under a measure graded 2 or more; `unsure` leaves a task out. A wording edit keeps the grades: the item and the label are the key, the question is the record.

**Evaluation.** `./wbg check evaluate` matches every grade of every kind to the current scores by the item's text, and for each kind reports weighted precision and recall at a range of floors with 95 per cent Wilson intervals on Kish's effective sample size, the layer's out-of-fold figures beside the raw score's, weighted AUC, the suggested floor (the most recall at `floor.target_precision`, on a 0.01 grid), and for measures and assets the fitted gates. Training rows fit; evaluation rows never do, and only their figures are quoted (a floor read off the rows it is measured on is an upper estimate). `--grid` also ranks the layer settings out of fold (slow; off by default). Output: `data/reports/evaluate.md`, `wbg.measure_quality`, the fits.

## v1.0 (provisional): the raw score and floors read off seed 7

Release v1.0 (2026-10-23) decides on the raw score: `posterior.on` is false. On the seed-7 training sheet every layer tried scored below the raw score at 85 per cent precision (issue #79): the design weights ran from about 2 to 9,000 and one free intercept per label was fitted from 1 to 15 graded yes. `posterior.fit_weight_power` (2026-10-24) is the answer to the first cause; the evaluation now checks the layer out of fold whatever `posterior.on` says. The floors below were read off the same sheet the figures are measured on, so the figures are upper estimates until an evaluation draw is graded. "Sheet" counts each graded line once; "weighted" counts each at one over its chance of being drawn.

| Decision | Panel setting | Precision | Recall | Graded |
|---|---|---|---|---|
| Measure | `floor.default` 0.73 | 85% sheet, 94% weighted | 59% sheet, 71% weighted | 540 lines, 96 yes |
| Measure and asset together (asset optional) | `asset.floor` 0.8, `asset.multiple` false, `asset.decay` 0 | 85% sheet (no wrong asset given) | of right commitments 47% carry the right asset, 53% none | as above |
| Type | `floor.types` 0.7 | 85% | 42% | 758 type lines, 110 yes |
| Pair check (keep a side) | `floor.pair_check` 0.58 | 85% (58% with no check) | 74% of the right sides kept | 154 sides |
| Package to asset | `link.asset_yes` 0.67 | 85% | 73% | 65 package lines |
| Commitment to package | `dashboard.package_link_min_probability` 0.7 | never reaches 85% (AUC 0.66) | | 60 links, 13 yes |

Read the commitment-to-package links as low confidence. A commitment counts without an asset; when one is given it is the sentence's best asset at 0.8 or more, read from the sentence alone. The tuning notes behind each panel value are in `docs/method/history.md` ("Why each control panel value is what it is").
