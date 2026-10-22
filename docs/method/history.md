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
