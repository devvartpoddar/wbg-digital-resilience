# History: what runs and trials showed

Dated results the method was tuned on. Not needed to change the code; read when a choice in the method needs its evidence.

## From: Running the pipeline

**What was cut on 2026-10-20, and why.** The pipeline had grown layers that cost minutes on every run and read nothing a dashboard uses: a separate load step with its own scope flags; a release tarball of every table after every model stage; a commitment classifier and a candidates folder with a reading order; a per-request response cache beside the per-question store, with migration code between them; three copies of the CSV reader and the file checksum; two evaluation modules and a sheet writer unreachable from the command line; audits inside every `prepare`. Each is gone or off the default path, and the time a measure takes is now the decision model's (section 5c) plus seconds.

## From: Checks, Postgres and releases

On the current run (local copy): paragraph joining is stable (a tighter line gap changes 1.2% of the model text in the sample, a looser one 3.7%, the loose gap nothing); closing component tags at the section change matters a great deal (running on, 10,086 model paragraphs would be tagged instead of 3,349); sentences move a little (word minimum 2 or 4: 5.6% or 3.6% of the 10,835 narrative and annex model paragraphs split differently; no abbreviation list: 2.2%).

## From: The asset map

**Cheaper decision models and a pre-filter, tried and not adopted (2026-10-21, US$0.08).** On 160 climate risk assessment units drawn by Jev's grade, Jev 1.13 asked again gave its stored grade on 95 per cent; Perplexity's decider flash (about a sixth of Jev's cost per unit), Perplexity's decider, Liquid d1 and Inception's Mercury gave it on 53 to 64 per cent, and Jev 1.14 on 82 per cent. Against a sheet on which Jev's precision was 0.96, that disagreement is too large to swap models without a new graded sheet. A single yes/no pre-filter ("does this passage concern any digital asset?") asked before the thirteen asset questions kept 99 per cent of the paragraphs the map grades 2 or more at a reading of 0.2 while sending 48 per cent on, but it costs a quarter of a full asset request itself, so it saves about a quarter: less than the gate, and not built.

## From: Measures and their types

*What the first graded sheet showed (climate risk assessment, 95 units, 2026-10-19).* The person graded 32 units committed. Against the model's most likely grade: precision 0.77 (95 per cent interval 0.60 to 0.89), recall within the pool 0.75 (0.58 to 0.87); stratum-weighted 0.82 and 0.89. Every one of the seven false positives was the Bank's own concept-stage climate and disaster risk screening, which the grader marked 1 with a note that it flips to 3 if the definition counts it; counted that way precision is 1.00 on the sheet and weighted recall 0.56, because the model finds the screening sentence only some of the time. The eight misses were all graded 2 by the model, with P(committed) between 0.16 and 0.49, so a cut at 0.3 gives recall 0.88 at precision 0.74. The asset link agreed with the grader on only 14 of the 32 committed units: the unit-level answer leans to `digital_systems` and `data_hosting` where the grader, reading the paragraph, said `fiber` or `access_connectivity`. That is the weakness to work on before a cut-off is chosen, and it is a definition and context question, not a threshold.

## From: Measures and their types

*The second run, same sheet (definition v2, which excludes the Bank's appraisal-stage screening; asset, kind and facet questions read with the paragraph; US$0.13).* On the same 95 units, re-scored against the grades as given: precision 0.96 (25 of 26 predicted), recall 0.78 (25 of 32); the one false positive is a backup-and-recovery sentence, and the seven misses are the model's grade 2 with P(committed) 0.18 to 0.47. Committed units fell from 182 to 140 in 49 projects. With the paragraph in the state the grader's asset is among the sentence's yes-answers on 17 of 26 units (13 of 28 before), and all four units the grader marked as not digital get no yes-answer; the single link still agrees on 13 of 26, because a climate-annex sentence often names several assets and the link picks one. The link rule was tried four other ways on these rows and moved by at most two, so the remaining gap is in the asset answers and the map, not the rule.

## From: Measures and their types

*The second measure, early warning and emergency communication (`analysis/labels/ews*`, 2026-10-19, US$0.11).* 1,916 units read in three rounds; 413 graded committed in 39 projects, 287 of them in one project whose restructuring paper reports the roll-out of an emergency call platform sentence by sentence (operator training, operations and maintenance specifications, results indicators). Three lessons. A measure that is itself an asset (the asset map's early-warning row) has a prevalence of 22 per cent in its pool against 4 per cent for climate risk assessment, and a definition that says "operates" counts every implementation-progress sentence; the definition should say what establishing or extending the system looks like, and a dashboard should count projects, not units (`measure_findings_by_project` separates units in appraisal documents from those in restructuring papers). The word patterns found 7 of the 413 (an emergency call platform never says "early warning"), and the feedback rounds found 137: the Rocchio rounds carry a measure whose vocabulary is wider than its phrases. And results-framework indicator rows read as prose ("campaign launched (Yes/No)") were graded committed: an indicator is a target, not a commitment, and the table-row filter should leave the results framework out, which is a parser rule to add before the box run, not a definition.

## From: Measures and their types

**What the first run on climate risk assessment showed (70 projects, sentence design, top 1,000, no feedback).** 1,341 candidates; 153 graded committed in 50 projects; 47 of those were the Bank's own concept-stage climate and disaster risk screening, which the definition's word "screening" had invited and which is not the measure: a definition is a filter and is tuned like one. Of the remaining 106, 42 were table rows or footnotes the asset map had not scored (fixed above); of the 64 it had scored, 59 linked to an asset at 0.5. Against the workbook, 77 per cent of the flagged activities had a candidate and 54 per cent a committed unit; several of the rest were not assessments of infrastructure at all.

## From: Measures and their types

*First lists, 2026-10-19.* Proposed three times each from the 70-project runs and approved as four types for climate risk assessment (site-specific assessment of financed works; assessment required of contractors or operators; vulnerability assessment of existing infrastructure; hazard mapping and screening to choose sites) and six for early warning (call and dispatch platform; public alerts; monitoring and forecasting; protocols and coordination; communications facilities; training and awareness). Assigned for US$0.02: climate risk assessment 140 units, 34 per cent claimed by no type, which is the signal that the list wants a revision after the box run; early warning 413 units, 10 per cent other, with call-and-dispatch units almost all in one project.

## From: Packages to assets and commitments

*The first full run (2026-10-20/21):* 13,713 packages, 4,530 with an asset at 0.5 (Digital systems and applications 3,218 of them; 479 yes answers at rank 4, the last asked, so a deeper rank would find more); 98,539 commitment-package pairs asked across the 13 measures, of which about one in sixteen links at 0.5, and a committed unit links to as many as 49 packages. That run is most of the 221,000 decision-model requests of that week and is missing from the spend ledger (the stage logged nothing when it failed part-way; it does now). Pairing on a shared asset group rather than a shared asset is what makes the pairs many: narrowing it is the lever if the link is run again at scale.

## The paragraph asset map (until 2026-10-22)

The asset map graded every model paragraph (18,834 on the 70-project corpus; US$1.88 for nine assets, 12 minutes at six requests in flight), with each paragraph's best asset and each component's best of its paragraphs as columns on every measure unit. A cosine gate of 0.25 on the paragraph's best sentence asked 58 per cent of the questions and lost 5.8 per cent of the links at 0.5 (0.30: 39 per cent asked, 9.0 per cent lost); on by default from 2026-10-21. Replaced by the sentence map on 2026-10-22: on 615 graded sentences, the last-asset rule with decay 0.4 and floor 0.3 moved agreement with the person's asset from 0.64 to 0.66. A one-choice asset question (which asset, or none) agreed as often (0.66 against 0.65) at 42 per cent of the cost but gives no grade, so it was not taken. Asset definition notes: early warning v5 and access v5 (2026-10-21: disease surveillance and advisory services are digital systems; access includes the power and climate protection of connected sites); digital systems v3 (named systems only; generic IT equipment is not this asset).

## Measure definitions v4 to v7 (2026-10-20 to 2026-10-21)

- **v4**: each measure defined by the intervention, never the asset, with boundaries between neighbours (assessing a risk is risk assessment, choosing where to build is siting, changing how it is built is hardening). Grade 1 lists context only; grade 3 counts a named intervention wherever printed, since real commitments sit in Paris Alignment paragraphs as list items. Before it, climate governance graded 1,300 units committed and about 262 of 680 renewable-energy units were energy-access programmes.
- **v5**: grade 1 adds existing systems, results indicators, economic-analysis assumptions and other World Bank projects; type lists rewritten to exclude each other; a `boundary` column per measure.
- **v6** (from 477 graded units, sheet s2): grade 2 takes wording the sentence itself makes optional; measure lines for backup power (solar as main supply excluded; precision was about 0.2), adaptation applications (precision about 0.45), early warning, network redundancy, siting and governance.
- **v7** (from the s4 tuning sheet, 331 units and 70 links): how examples, technical assistance and hedges grade; measure lines for every measure (physical hardening needs a named engineering measure on a named asset: s4 precision 0.40). The v7 run (US$1.06) raised committed units from 1,164 to 2,100, mostly generic sentences; the tightened lines that followed tested on 807 graded units at precision 0.65 / 0.70 / 0.74 at recall 0.90 / 0.80 / 0.70 (Gemma 4 31B: 0.70 / 0.74 / 0.78 at about eight times the price). Splitting the question into three yes/no questions gave no gain (0.67 / 0.69 / 0.74).

## Per-measure commit floors (until 2026-10-22)

A unit counted at P(grade 3) at or above `commit_floor` in `analysis/labels/<set>.csv`, else at the most likely grade. Floors read off the v5 sheet: governance, climate information and siting 0.3; risk assessment, disaster recovery, network redundancy and early warning 0.4; energy efficiency, renewable energy and resilient design 0.6; re-read on v6 answers, disaster recovery 0.3, energy efficiency 0.5, network redundancy 0.5. Replaced by one decision on the posterior of grades 2 and 3 (`floor.*` in the control panel). Stored per-measure figures from those floors (grade 3 against grade 3): sheet s2 mean precision 0.64 and recall 0.81 (0.21 to 0.92 by measure); s4 mean 0.61 and 0.69 (0.33 to 0.86).

## Questions removed on 2026-10-22

- **The second pass**: units graded 2 or more were also asked the asset (one choice), how the commitment is delivered (governing, building, sustaining, using, requiring in procurement), whether it is conditional and who carries it out. Nothing decided on them; on the full v7 run they cost US$1.23 against US$0.65 for the other 57,827 units.
- **A paragraph second look** (PR #48, 2026-10-21): on the next independent sheet the sentence alone ranked better (0.76 against 0.69). With the paragraph as context the model graded the paragraph and doubled the false positives on the activities workbook.
- **The scope question** (a rule, one investment, or not a commitment), which capped an investment at its three likeliest packages: US$0.06 a full run, nothing else decided on it.
- **Platt calibration** (`<set>_calibration.json`), read by nothing once the probability layer came in.
- **Measure options** `--context`, `--gate`, `--asset-min`: a measure is asked one way.

## Many items a request (2026-10-21 and 2026-10-22)

Ten items a request against one: the measure question cost 74 per cent less with the same accuracy against the person (precision / recall at the most likely grade 0.77 / 0.80 against 0.77 / 0.83); asset map 61 per cent less, 96 per cent the same grade. Filled requests (up to 80,000 characters) against ten: 90 per cent the same yes/no at 0.5 on the measure question, mean average precision 0.882 against 0.888. An item's neighbours move its first answer about as much as a small rewording (86 to 88 per cent the same grade); the store makes re-runs repeatable. Short per-item questions with grade numbers only saved 44 per cent of an asset request's characters with no measurable change.

## The decision system, exploratory evidence (2026-10-22)

On the graded sentences (742 measure, 555 asset), held-out halves, before the fixes below:

| Measures | ranking (0.5 = chance) | recall at 0.85 precision | at floor 0.5: precision / recall |
|---|---|---|---|
| The model's score alone | 0.874 | 0.78 | 0.75 / 0.92 |
| Score, vote, centroid | 0.885 | 0.81 | 0.82 / 0.86 |
| Prior alone | 0.765 | 0.32 | 0.73 / 0.81 |
| Prior + answer, logistic | 0.890 | 0.83 | 0.82 / 0.87 |
| Prior + answer, strict Bayes | 0.886 | 0.82 | 0.83 / 0.86 |

The pair answer raised recall at 0.90 precision from 0.78 to 0.87 on the 249 sentences it applied to; a softmax across measures added nothing. These figures pool measures, count grade 2 as yes and were fitted on band-drawn sheets without weights, so they overstate the dashboards' precision.

## The method review (2026-10-22)

A review found: the layer was fitted without design weights, chose floors on the rows it was fitted on, let near-copies and same-project sentences vote, and pooled grades from superseded definitions; the skip rule was stated three ways; `./wbg check` failed (a call with removed arguments); `link.asset_yes` and the asset layers were missing from run keys; `./wbg measure` passed `--min-cosine` explicitly, so a fitted gate could never apply; views still read grade 3. On 327 graded sentences of six measures, precision at 0.5 was flat by sentence length (0.83 to 0.88) and recall fell only above 50 words (0.86). All of these were fixed the same day; the sentence splitter gained every enumerator form, comma-separated lists, consecutive footnotes and cell-level splits of long table rows (section 2.5).

## The architecture proposal (2026-10-19)

`docs/architecture-next.md` proposed the measure stages as built in sections 5a to 5d (pool, asset map, funnel, check sheets, types); it was removed on 2026-10-22, the method files being the record of what was built.

**Readiness for the clean run (2026-10-22, later).** The same-project rule in the vote was dropped: with a measure's graded yes in a few projects it left their yes rows voting only among noes, and the out-of-fold check by project already stops leakage between projects; near-copies still do not vote. Grades are now matched to sentences by text checksum, since the re-parse moves unit ids. Both starting gates were set to 0.2 (from 0.25 for assets and 0.45 for measures) so the first run covers the whole range once (about four in five sentences a measure; about US$18 to 22 for the model stages against US$3 to 4); later gates, fitted by the skip rule, ask nothing new. The request spend caps moved to the control panel; view 19 still read the most likely grade, and the loader refused numbers read from JSON manifests: both fixed.

**Assumption audit (2026-10-22, last).** Every setting was checked against the data on the box, a test call or the literature.
- *Batching:* 120 sentences under the climate risk assessment question cost US$0.015 in all. One a request was repeatable: asked twice, 0 crossed 0.5 and the correlation was 0.999. Batches moved first answers: 10 to 12 of 120 crossed 0.5 against one a request, and batches said yes 18 to 23 times against 12. On 131 graded sentences of two measures (US$0.009), batches ranked as well as one a request or better: area under the curve (AUC) 0.90 and 0.90 against 0.88 for risk assessment, 0.98 and 0.97 against 0.96 for disaster recovery. So `ask.batch_items` is 50, inside the measured range.
- *Near-copy votes:* only 6 of 1,383 committed governance sentences had a neighbour in another project above 0.95 (the highest was 0.974), so the rule did almost nothing. It is now off and is compared in the tuning report.
- *Gate:* 22 per cent of the earlier runs' commitments came only from feedback rounds below the 0.45 gate. Their paragraph best cosine was 0.30 or more, and only 3 were under 0.35. The measure gate starts at 0.3. Assets keep 0.2: a 0.30 gate lost 9 per cent of paragraph asset links.
- *Ridge:* the penalty was already a normal prior with standard deviation 1 on each standardised weight. The fit now uses Newton's method in place of fixed gradient steps.
- *Skip rule:* a 1 per cent loss target cannot be vouched for with tens of graded yes. The rule of three gives 30 yes a 10 per cent bound. The rule is unchanged, and its report now gives the bound.

**Assets take the measures' method (2026-10-22, last).** The asset map now has the measures' second look: word patterns (`inputs/taxonomy/assets_patterns.csv`, once a person adds it) and feedback rounds below the gate (`src/feedback.py`, one module for both). The asset gate therefore moves to 0.3, like the measures'. The rounds add at most 39,000 questions (13 assets × 6 rounds × 500) at about US$0.0000026 each, about US$0.10. The asset map's rounds then got their own settings (`asset_feedback.*`), so whether and how deep they run is tuned apart from the measures'.

**The whole loop everywhere (2026-10-22, last).** Types, pair checks and both package links got the measures' loop: a graded tab (the type column; `pair checks`, `package assets`, `package links`), a pooled probability layer keyed on each label's question (`src/judged.py`), a floor on the posterior with a suggested value, and run keys holding the layers. Several winners are allowed for assets and types too (`asset.multiple`, `types.multiple`, `floor.types_also`). `floor.pair_check` became a floor on the posterior of not-this-measure (0.5) instead of the most likely of four answers.

**One question form for every decision (2026-10-22, last).** Types, pair checks and both package links now ask the graded question every asset and measure asks, read as P(2)+P(3) through the same layer and floor. Types first used the measures' scale: on 43 committed risk-assessment sentences and the 7 draft types (US$0.003), 6.1 types a sentence counted at 0.5. A scale grading whether the commitment is of that kind gave 4.0 at 0.5, 2.5 at 0.7 and 1.1 at 0.9, so types start at 0.9 on that scale (`kinds.TYPE_SCALE`) until grades set the floor. The draft wording on 131 graded sentences: risk assessment AUC 0.88 to 0.84, recall 0.72 to 0.98 at precision 0.86 to 0.79; disaster recovery AUC 0.96 to 0.98, recall 0.79 to 0.90 at precision 0.98 to 1.00; the shared rules changed AUC by 0.01 or less; targeted exclusions added to risk assessment changed nothing (US$0.019 in all). The same day the four checks after a classification moved to a yes / unsure / no question (`classify.fit_question`), told what is already decided: types then gave 1.8 a sentence at 0.5 and 1.3 at 0.7, against 6.1 on the commitment scale and 1.1 at 0.9 on a kind scale, so every floor starts at 0.5 again.

**Taxonomy v2 (2026-10-22, last).** Dev's revised definitions (`Green_Digital_Taxonomy_Complete.xlsx`) replace the label files: definitions of at most 100 words, one common 0-3 scale for assets and measures (committed technical assistance is grade 3), types as overlapping subcategories asked by the fit question, phrases and patterns rebuilt with `type_id` links, the inactive green data centres measure removed, and a per-row `flags` column for patterns (`CASE_SENSITIVE` for the bare UPS acronym). On the 131 graded sentences the revised definitions matched the first draft (risk assessment AUC 0.85, precision 0.79, recall 0.98; disaster recovery AUC 0.98, precision 0.98, recall 0.90; US$0.01); the old grades predate the wording, so a regrade under it is the real test.

## From: fetch.md

- Projects interface, 2026-10-20 and 2026-10-21: version 3 held records version 2 lacked for 81 of 251 cohort projects (74 found in version 3; 7 in neither form and not in the documents interface). The two forms disagreed on the commitment for 30 of 131 projects with both; version 3 was higher after additional financing and lower after a cancellation, but not always right (one restructuring paper's component costs matched neither form). Open projects had stayed Active in version 2 past their closing dates.
- On the 70-project cohort run: 65 in the projects interface, 62 with a practice, 66 with a managing unit (65 with a code), 55 with a closing date.

## From: appraisal.md

- On the 70-project cohort run: 147 documents (65 appraisal documents, 13 additional financings, 69 restructuring papers); 1,881 web addresses replaced, 111 empty units dropped, 5,002 units under 5 words rejected; 52,025 paragraphs, 14,723 given to the model (7,718 narrative, 3,117 annex, 3,888 footnotes); 970 component rows covering all 70 projects; 6,115 paragraphs carry a component tag (3,349 among those given to the model).
- A restructuring paper that prints no component numbers put every cost on the wrong component when read by row order; reading numbers by name fixed it. Several restructurings cut components sharply (one from US$56.5 million to US$8.1 million, one from US$200 million to US$100 million), which the projects interface's commitment did not yet show.
- 2026-10-20: a footnote naming a component had re-tagged every paragraph after it; footnotes and table rows now refer to a component without opening it.
- A bulleted table cell whose bullet hung in the margin was split into body and table lines when the whole line had to lie inside the ruling; the 90 per cent width rule fixed it.

## From: procurement.md

- 2,113 plan documents from 55 projects (9 projects published no plan, 6 only plans with no package row); 131,610 package rows; 6,886 packages after grouping, 124,665 older versions; 22 bundles cut to their project, 7 not parsed; 404 renditions with no package (403 empty tables, 1 parse failure).
- Method: INDV 2,048, RFQ 1,191, RFB 1,024, CQS 933, QCBS 792, CDS 305, DIR 247, RFP 151, LCS 68, QBS 32, FBS 4, UN 3, FA 1, unknown 87 (cell 4,990, clipped cell 948, reference 861). Status: Canceled 1,953, Signed 1,783, Under Implementation 974, Pending Implementation 875, Completed 678, Pending 74, Terminated 58, Under Review 52, Planned 2, unknown 437 (source: plan 6,212, award 237, none 437).
- Component: name match 5,173, number only 188, none 1,525. Amount from the newest plan 5,341, none 1,545; 36 packages from a repeated reference with different figures, 59 identical repeats kept once.
- Notices 5,909, linked 4,530 (1,295 with no package of that reference, 46 ambiguous, 38 no reference). Awards 3,485, linked 2,495 (963 no package of that reference, 27 ambiguous); 3,005 name a notice of the same reference.
- Word repair, 120 hand-checked edits: about 5 per cent wrong (for example "prop ice", "seg un do"), about 8 per cent before the fused-pair pass and the join and short-half rules, which changed 77 of 1,500 sampled descriptions, all for the better. Moving the 3-use threshold to 2 or 5 changed about 3.5 per cent of descriptions. "unconsultant" occurred in 149 descriptions, which is why fused pairs are split last.

## Control panel notes

The tuning note behind each setting of `inputs/config/control_panel.csv` as it stood on 2026-10-24, when the panel was cut to one line per setting. Some name modules since replaced (`classify.py`, `measure_stage.py`); the setting names are current.

#### read.grades = 2|3
The model's score for a sentence is the sum of its probabilities for these grades (2 planned, 3 committed): loose first, tightened by the floors.

How to tune: Leave unless the scale changes.

#### ask.batch_items = 50
The most items (sentences, paragraphs, packages) asked in one request. 0 = one a request. $WBG_ASK_BATCH overrides it.

How to tune: Tested 2026-10-22: batches of 10 and of 62 to 66 ranked graded sentences as well as one a request, but moved first answers (1 in 10 crossed 0.5); stored answers never move. Above 120 was never measured. One a request costs about four times as much for measures.

#### ask.batch_max_chars = 80000
A request holds items up to about this many characters.

How to tune: Tested on asset paragraphs 2026-10-22: 80,000 agreed with 30,000 as often.

#### asset.gate_cosine = 0.3
The starting gate: an asset's question goes to a sentence when the sentence scores at least this against the asset's search phrases or a word pattern of the asset matches (inputs/taxonomy/assets_patterns.csv), until the skip rule (skip.max_loss) has fitted the asset's own gate from graded sentences; the feedback rounds then look below it, as for measures. 0 asks every asset of every sentence and ignores fitted gates.

How to tune: A starting value only; the fitted gate replaces it. 0.3 (2026-10-22), the measures' value: the asset map now has the measures' second look below the gate (feedback rounds, and word patterns once the file exists). Raising it later asks nothing; lowering it asks only the sentences it adds.

#### asset.floor = 0.8
A sentence has an asset of its own when that asset's posterior reaches this (level unit; every such asset with asset.multiple). Also what counts as a yes in the asset feedback rounds.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). With asset.multiple false and asset.decay 0: of right commitments, 47% carry the right asset, 0% a wrong one, 53% none stated; joint precision stays at 85%. 0.5 gave 14% wrong.

#### asset.ask_floor = 0.8
What the asset map's feedback rounds count as a sentence having an asset, so which sentences the next round asks (src/classify.py). In the asset map's run key. asset.floor, which decides each sentence's asset, is applied when the map is read and is in no run key.

How to tune: 0.8 (2026-10-23): the floor the v1.0 run's asset rounds were asked at, so that run is reused. At or below asset.floor.

#### asset.decay = 0
A sentence with no asset of its own adds up, for each asset, its own score and every earlier sentence's in its paragraph, each multiplied by this per sentence back (one back x0.4, two back x0.16); every asset whose sum reaches asset.paragraph_floor takes it (the highest only without asset.multiple). 0 turns the rule off.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). The paragraph rule is off: the tested rule reads each sentence's own scores only.

#### asset.paragraph_floor = 0.3
A sentence with no asset of its own takes the asset with the highest decayed sum over its paragraph (asset.decay) when that sum reaches this (level paragraph). Its own line because a sum of several scores is on another scale than one sentence's posterior.

How to tune: Starts at asset.floor's value (2026-10-22), so nothing moves until it is tuned; the tuning report compares it on the grades.

#### asset.multiple = false
A sentence has every asset whose posterior reaches asset.floor (or, at level paragraph, every asset whose decayed sum reaches asset.paragraph_floor), best first; false = the best one only. Each asset is its own question, as each measure is.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). The best asset only (2026-10-23); several at 0.8 were not tested.

#### measure.gate_cosine = 0.3
The starting gate: a sentence goes to a measure's question when its best similarity to the measure's phrases and definition reaches this, or a word pattern matches, until the skip rule (skip.max_loss) has fitted the measure's own gate from graded sentences. 0 asks every sentence and ignores fitted gates, as for assets.

How to tune: A starting value only; the fitted gate replaces it. 0.3 (2026-10-22): of the commitments the earlier runs found in feedback rounds below the old gate, all but 3 sat at a paragraph best of 0.30 to 0.35 or more; about two in five sentences reach 0.3 for a broad measure, one in ten to twenty for a narrow one. Raising it later asks nothing (the answers are stored); lowering it asks only the sentences it adds.

#### measure.top = 0
Also send the measure's best N sentences by similarity, whatever their score. 0 = off.

How to tune: Usually off.

#### skip.max_loss = 0.01
The skip rule: each label's gate is the highest cosine that loses at most this share of the sentences a person graded yes under the current question (at their design weights); word-pattern matches are always asked. Fitted by ./wbg check <set> --evaluate, read by the next run.

How to tune: 

#### skip.min_positives = 10
A label needs at least this many graded yes before its gate is fitted; until then the starting gate above applies.

How to tune: 

#### feedback.batch = 500
Sentences sent per relevance-feedback round, per measure (the ones nearest the measure's committed sentences).

How to tune: 

#### feedback.min_yield = 10
A measure whose round finds fewer committed sentences than this stops.

How to tune: 

#### feedback.max_rounds = 6
At most this many feedback rounds. 0 turns them off.

How to tune: 

#### feedback.negative_weight = 0.5
How much of the rejected sentences' average is subtracted when choosing the next round.

How to tune: 0.5 because the first run's plain centroid pulled in the measure's general neighbourhood. Textbook relevance feedback uses about 0.2 with the original query kept; this one has no query term. Not set from grades: the elusion sheet shows what the rounds miss.

#### asset_feedback.batch = 500
The asset map's own rounds, apart from the measures': sentences sent per round, per asset (the ones nearest the asset's sentences at asset.floor or more).

How to tune: Starts at the measures' value. Not set from grades: an elusion sheet on the asset map shows what the rounds miss.

#### asset_feedback.min_yield = 10
An asset whose round finds fewer sentences at asset.floor than this stops.

How to tune: 

#### asset_feedback.max_rounds = 6
At most this many rounds per asset. 0 turns the asset map's rounds off (the gate and word patterns alone).

How to tune: An option to compare, not a settled choice: run the asset map with 0 and with 6 (the answers are stored, so only the sentences the rounds add cost anything) and compare the assets found.

#### asset_feedback.negative_weight = 0.5
How much of the average of the sentences asked and not at asset.floor is subtracted when choosing the next round.

How to tune: 

#### posterior.on = false
After each model answer, combine the model's score with how similar the sentence is to the sentences a person graded for the same label (src/posterior.py). Off = the model's score alone.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). Every layer tried scored below the raw score at 85% precision; off until a layer beats it out of fold.

#### posterior.sharpness = 20
How sharply similarity counts in the weighted vote of graded sentences: higher = only the closest count.

How to tune: Tested 2026-10-22: 10 to 20 best of 2 to 160 (on an earlier sheet). ./wbg check all --evaluate writes data/reports/tune.md, which compares settings on the grades (5 to 80).

#### posterior.centroid = true
Also use similarity to the average graded commitment minus similarity to the average graded non-commitment.

How to tune: ./wbg check all --evaluate writes data/reports/tune.md, which compares settings on the grades (on, off).

#### posterior.pool = true
Fit each decision's labels together: the measures in one fit, the assets in one, and likewise the types, the pair checks, the package assets and the package links. Each label keeps its own intercept (its base rate) and its own graded sentences for the vote; the weights on the score and the similarities are shared. false = each label fits its own four weights.

How to tune: true from 2026-10-22, for every decision alike: a measure has about 40 graded rows and a type, pair side or asset fewer, few for four weights of its own. ./wbg check all --evaluate writes data/reports/tune.md, which compares pooled and per label on the measures' grades.

#### posterior.ridge = 1.0
How strongly the layer's weights are pulled towards zero: the precision of a normal prior on each standardised weight (1 = a prior standard deviation of 1, 4 = 0.5). More = closer to the model's score alone.

How to tune: ./wbg check all --evaluate writes data/reports/tune.md, which compares settings on the grades (0.25, 1, 4).

#### posterior.min_graded = 20
A label with fewer graded sentences than this (or fewer than posterior.min_each of either kind) gets no layer: its score is the model's.

How to tune: 

#### posterior.min_each = 5
A label needs at least this many graded yes and this many graded no for a layer.

How to tune: 

#### posterior.near_copy = off
Leave out of the vote the graded sentences a sentence nearly copies (embedding similarity at this or more), in fitting, checking and applying alike. off = every graded sentence votes but the sentence itself, near-copies and the same project included.

How to tune: Off (2026-10-22): word-for-word copies are already merged, only 6 of 1,383 committed sentences had a neighbour in another project above 0.95, and a sentence whose graded near-copy is yes usually is yes. ./wbg check all --evaluate writes data/reports/tune.md, which compares settings on the grades (off, 0.97, 0.95).

#### posterior.folds = 5
The layer is checked out of sample in this many folds, whole projects to a fold; the suggested floor is read off the out-of-fold probabilities.

How to tune: 

#### floor.default = 0.73
A sentence counts as committed (or planned) for a measure when its probability reaches this. One floor for every measure unless a line below overrides it.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). Measures: 85% precision, 59% recall on the sheet (75% weighted); the floor was chosen on the same sheet, so these are upper estimates.

#### floor.ask = 0.73
The floor the stages ask at: what a measure's feedback rounds count as a yes, and which sentences the types, pair checks and package links ask about. In those stages' run keys. The decision floors (floor.default, floor.<set>, floor.types, floor.pair_check) are applied when the runs are read and are in no run key, so moving one asks nothing and rebuilds nothing.

How to tune: 0.73 (2026-10-23): the floor the v1.0 run asked at, so its runs are reused and nothing is asked again. Keep it at or below every decision floor: a decision floor below it shows only what was asked at floor.ask (types, pair checks and links for the sentences it adds need a run).

#### floor.target_precision = 0.8
The evaluation report suggests, per measure, the lowest floor whose precision on graded sentences reaches this.

How to tune: 

#### floor.adaptation_applications = 
Override of floor.default for this measure (blank = default).

How to tune: 

#### floor.backup_power = 
Override of floor.default for this measure.

How to tune: 

#### floor.climate_governance = 
Override of floor.default for this measure.

How to tune: 

#### floor.climate_information_systems = 
Override of floor.default for this measure.

How to tune: 

#### floor.climate_informed_siting = 
Override of floor.default for this measure.

How to tune: 

#### floor.cra = 
Override of floor.default for this measure.

How to tune: 

#### floor.disaster_recovery = 
Override of floor.default for this measure.

How to tune: 

#### floor.energy_efficiency = 
Override of floor.default for this measure.

How to tune: 

#### floor.ews = 
Override of floor.default for this measure.

How to tune: 

#### floor.network_redundancy = 
Override of floor.default for this measure.

How to tune: 

#### floor.renewable_energy = 
Override of floor.default for this measure.

How to tune: 

#### floor.resilient_infrastructure_design = 
Override of floor.default for this measure.

How to tune: 

#### floor.types = 0.7
A type counts for a committed sentence when its score (P(yes) of the type's fit question, yes, unsure or no, told the sentence is already committed; through the types' layer once fitted, src/judged.py) reaches this; with types.multiple every such type, best first. None reaching it = no type.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). Types: 85% precision, 42% recall on the sheet (0.5 gave 82% and 54%).

#### types.multiple = true
Every type at floor.types counts, not only the likeliest. false = the likeliest only.

How to tune: The types are written to exclude each other, so a second type that often counts means two definitions overlap: fix the wording.

#### types.gaps = off
on: re-propose OTH types from the sentences no approved type reaches, with a generative call, on the assignment run. off: keep the OTH types already proposed (data/types/<set>_oth_types.csv). Either way every committed sentence is asked the OTH types, so which type counts is a reading.

How to tune: off from 2026-10-23: re-proposing on every run cost new calls and new wording whenever a floor moved. Turn on, run ./wbg run --from measure, turn off.

#### types.gap_min_units = 5
A measure proposes new (OTH) types only from at least this many committed sentences no approved type counts for.

How to tune: Fewer are reported, not proposed from.

#### floor.pair_check = 0.58
A sentence two measures both commit stays in a measure when that side's posterior (P(yes) of the fit question: does the measure apply, the other measure's boundary beside it; through the measure's pair layer once fitted) reaches this.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). Pair checks: 85% precision keeping 74% of the right sides (58% precision with no check).

#### link.asset_top = 4
Assets asked of each procurement package: its best this many by similarity.

How to tune: 

#### link.asset_yes = 0.67
A package has an asset when its posterior (P(yes) of the fit question: does the package buy, build, install, connect, operate or design the asset; through the package-asset layer once fitted, src/judged.py) reaches this.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). Package assets: 85% precision, 73% recall on 65 graded package lines.

#### link.measure_top = 3
Each committed sentence is also paired with its project's nearest this many packages by similarity to the measure.

How to tune: 

#### dashboard.commitment_min_probability = 0.0
A further floor on a sentence's posterior probability over every measure for it to count on the dashboards.

How to tune: 

#### dashboard.commitment_link_levels = unit|none
Where a commitment's asset may come from: unit (the sentence names it), paragraph (earlier sentences of its paragraph do, their scores summed with asset.decay).

How to tune: v1.0 (2026-10-23, provisional): a commitment is project, measure and type; its asset is optional. With asset.floor 0.8 about half of the right commitments on seed 7 name no asset, so 'none' counts them, shown as (no asset). 'paragraph' left out: asset.decay is 0.

#### dashboard.ipf_only = true
Only Investment Project Financing operations count.

How to tune: 

#### dashboard.package_link_min_probability = 0.7
A package counts as linked to a commitment when the link's posterior (P(yes) of the fit question: would carrying out the package deliver the commitment; through the link layer once fitted, src/judged.py) reaches this.

How to tune: v1.0 (2026-10-23, provisional): read off the seed-7 grades on the raw score, the probability layer off (issue #79). Unchanged: package links never reach 85% precision on seed 7 (AUC 0.66, 13 yes of 60); read them as low confidence.

#### dashboard.near_copy_share = 0.5
Another project prints a sentence near-identically when its committed sentences hold this share of the sentence's six-word runs.

How to tune: 

#### text.shingle_words = 6
Words per run when comparing wording: template_share on each search unit and the dashboards' same-wording-across-projects test both cut text into runs of this many words.

How to tune: Six since 2026-10-19; not set from grades. Raising it counts only longer shared runs as the same wording.

#### dashboard.apply_measure_pair_checks = true
A sentence two measures both commit leaves a measure the pair check says it is not.

How to tune: 

#### requests.cap_per_request_usd = 1.0
The most one requested measure may spend on models; a run that reaches it stops and resumes from the stored answers the next time the queue runs.

How to tune: At the 0.3 starting gate a new measure asks one to four in ten sentences, about US$0.3 to 0.8 on the current corpus, so it usually finishes in one run of the queue.

#### requests.cap_per_day_usd = 3.0
The most all requested measures together may spend in a UTC day.

How to tune: 

