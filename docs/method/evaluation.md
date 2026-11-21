# 5h. The evaluation sample: draw, grade by three models, tune and report

Part of the method (`docs/methodology.md` is the index). Code: `src/wbg_portfolio/sampling.py` (draw), `src/wbg_portfolio/graders.py` (grade), `src/wbg_portfolio/tuning.py` (tune and report); wording in `questions.grader_*`.

## Why and what

Every threshold is read at publish from stored scores, so any of them can move with no model call. What has been missing is a sample large and fair enough to choose them on and to say how good the result is. The earlier test (260 pairs, 71 in the band) was drawn from an older run, and the thresholds were tuned and measured on the same items. This sample is drawn from what is published now, graded by three paid models, split into a **tuning set** (about 60 per cent) on which the thresholds are chosen and a **holdout set** (the rest) on which they are reported.

Three commands, beside the pipeline's own grading cycle (`check draw|fill|evaluate`, which serves the person-graded sheets that fit the probability layer):

```
./wbg_portfolio check sample --seed 1 --dry-run              # expected counts per region; nothing written
./wbg_portfolio check sample --seed 1                        # draw: data/samples/s1/*.jsonl
./wbg_portfolio check grade  --seed 1 --dry-run              # what the graders would be asked, and the estimate
./wbg_portfolio check grade  --seed 1 --max-spend 4          # three models grade every item (paid)
./wbg_portfolio check grade  --seed 1 --export-for-person 100   # data/samples/s1/person.csv, for the owner
./wbg_portfolio check grade  --seed 1 --read-person data/samples/s1/person.csv   # how the graders agree with the owner
./wbg_portfolio check tune   --seed 1 [--setting band.keep=0.85 --setting band.context=0.5]
```

Beside the live data: `WBG_SCHEMA=wbg_v2 WBG_STORE=wbg_store WBG_DATA=<the data folder of that run> ./wbg_portfolio check ...`. The draw reads the published tables of `WBG_SCHEMA`, the stored scores and the data folder's corpus (for the text); grading stores its answers in `WBG_STORE`; the tune step writes `data/reports/evaluation_s<seed>.md`, `data/reports/holdout_s<seed>.json` and the holdout rows of `<schema>.measure_quality`.

## Poisson sampling with unequal chances

Each unit of a population (a sentence x measure pair, a package x measure pair, a link, a kept sentence) gets its own chance pi of being in the sample, and is in it when a hash of the seed, the kind and its ids, read as a number between 0 and 1, is below pi. So the same seed always draws the same sample, and each unit's chance is known exactly. Every estimate weighs a graded item by 1 / pi (the Horvitz-Thompson weight): an item drawn with a chance of 1 in 100 stands for 100 units. Because the chances can differ, the sample can spend its items where a grade can change a decision (the band, the digital cut, the second looks) and very few where the outcome is near certain (a first score of 0.95, or under 0.3), and still estimate the whole population without bias. An equal-chance sample would need several times as many items to put as many in the band.

A second, independent hash puts each drawn unit in the tuning or the holdout set, so the split does not depend on the chance of being drawn.

**The regions and their targets** (`sampling.TARGETS`, expected items). A unit's chance is its region's target over the region's size, at most 1. Regions are fixed bins of the stored scores, not today's thresholds:

| Kind | Regions (target) |
|---|---|
| measure pairs | first 0.95 or more (130); 0.85 to 0.95 (150); the band 0.5 to 0.85 by context score (under 0.3, 0.3-0.5, 0.5-0.7, 0.7-0.85, 0.85 or more) and chat grade (under 2, 2 or more): 25 to 70 each, 420 in all; band with no second look (20); digital P(yes) under 0.5 in three bins (under 0.1, 0.1-0.3, 0.3-0.5) for first 0.85 or more and for the band (215 in all); no digital answer (20); first 0.3 to 0.5 (100); first under 0.3 (60, only to bound recall) |
| package x measure | the package_measure score: 0.9 or more (40), 0.7-0.9 (60), 0.5-0.7 (60), 0.3-0.5 (50), under 0.3 (40); packages never asked (no score for any measure), paired with each kept measure of their project whose word pattern their description matches (70) or, with no match, one such measure chosen by the seed (30) |
| links | the ladder: 1 strong (50); 2 same asset type and same top-level component (45); 3 same asset type (45); 4 same asset and component (40); 5 same asset, component unknown (35); 6 same asset, component differs (35) |
| kept sentences' assets | the published asset level (unit, second_look, none, paragraph) by the best first-pass asset score (under 0.5, 0.5-0.8, 0.8-0.9, 0.9 or more), each cell split equally between sentences whose type is unspecified and the rest: about 300 |

Then two adjustments to the measure pairs: a measure whose expected count would be under 25 has the chances of its pairs from 0.3 up raised until it reaches 25 (or all its pairs), and in every kind a project whose expected count would be over 4 per cent of the kind's total (never less than twice an average project's share) has its chances scaled down to that. The project of every unit is recorded, so the ranges come from resampling projects. Of the kept measure pairs drawn, about 300 are also asked the measure's types (each with a further chance, recorded in `types_pi`).

On a synthetic population shaped like the published run (about 980,000 scored pairs, 940,000 of them under 0.3, 28 measures, 280 projects of very unequal size) the measure kind expects about 1,250 pairs: 154 at 0.95 or more, 177 at 0.85-0.95, about 490 in the band, about 250 under 0.5 digital, 118 at 0.3-0.5 and 56 under 0.3. The real counts depend on the published run; `--dry-run` prints them before anything is drawn.

**What the link ladder needs.** A sentence's asset type is read from `unit_assets`. A package's asset type is read from the published data when the pipeline stores one (scores kind `package_asset_subtype`, or a `subtype_id` column in `package_assets`); otherwise from the asset word patterns of `inputs/taxonomy/assets_patterns.csv`, whose ids name the kind they look for (`sampling.PATTERN_KIND` reads those names as the kinds of `assets_subtypes.csv`; the data hosting patterns name no single kind). Each row says which source was used (`package_type_source`). The patterns are mostly English, so a package in French, Spanish or Portuguese is often untyped and falls to the levels without a type. Components are compared at the top level (`Component 2.1`, `2b` and `II` are all 2). Cancelled packages are kept and flagged.

## The graders and the truth

Three models (default `openai/gpt-5.6-luna`, `openai/gpt-5.4-mini`, `anthropic/claude-haiku-5.5`; `--graders` changes them; they are an option, not a control panel line, because the panel is kept by people) are asked every item through the chat asker, so each answer is stored once under the model, the item's state and the question, the ledger records the cost, a restart never pays twice and an unreadable reply is asked again up to `second.max_attempts` times and never counted. The wording is the earlier trial ruler's: a system line, the item laid out in the prompt, the paragraph with the sentence marked `>>> <<<` (at most 4,000 characters around it), and for a measure the shared 0-3 scale with the measure's current definition. The same request also asks whether the sentence is about something digital (the pipeline's digital question and definition) and whether it is only a general aim with no specific thing to be done, which is cheaper than a second request but means a grade here is not asked exactly as the trial asked it. Packages are asked the trial's package question about the measure (yes, unsure, no); links the trial's "does carrying out this package deliver the commitment"; assets which one of the four assets, or none, and which type; types which of the measure's types apply. The request is the trial's: replies of up to 1,500 tokens and the model's own reasoning setting.

The truth is computed when it is read, never stored: **lenient** when at least two of the three give 2 or 3 (or yes); **strict** when at least two give 3 (or yes, and none says unsure); unanimous when all three give the same answer; each grader's answer is kept. An item with fewer than two readable answers has no truth and is counted in the report.

**What the estimates mean.** They are the models' reading, not a person's. The graders are known to be generous on sentences that say the project *may* do something (grade 2), so the strict reading is reported beside the lenient one. The owner can grade 100 items of the measure sample (`--export-for-person`; the file has blank grade columns and shows no model's grade) and `--read-person` reports how often each grader agrees and how often it calls right what the owner does not. Recall is among the pairs the pipeline asked about (first score 0.3 or more), with a second denominator of the right pairs the graders also call digital, since the digital rule's job is scope; the right pairs under 0.3 are estimated separately and are uncertain, because each such item stands for tens of thousands of pairs. Nothing here says a commitment is delivered.

## Tuning and the report

`check tune` asks nothing. On the tuning set it sweeps the one rule for all measures (band.keep 0.80 to 1.01, band.low 0.5 to 0.7, band.context 0 to 0.8, band.second_grade 0 to 3, digital.floor 0.3 to 0.6, and the mean-of-first-and-context shape), prints the frontier of lenient precision against recall, and names two operating points: **precision first** (the most recall with lenient precision not below the current rule's) and **balanced** (the most recall with lenient precision within 3 points of it). It sweeps link.strong_yes, floor.types and asset.floor the same way. Then it reports the current settings, the two operating points and any `--setting` on the **holdout set only**, with 90 per cent ranges from 500 bootstrap resamples of projects: precision and both recalls, lenient and strict; for links the share judged to deliver the commitment at each ladder level (and cumulatively), for the strong question precision and recall per threshold and the estimated number of truly-about pairs among packages never asked (the known gap: packages with no asset, such as many cybersecurity packages, are never asked the measure question); for assets and types precision and coverage, the share unspecified, and how often a second-look asset is right. The report holds counts and rates only, never document text.

The holdout figures of the current rule feed the app's measure page: one `measure_quality` row per measure with sheet `holdout` (lenient precision; recall among pairs asked), with the measure's own figures where it has 30 holdout pairs or more and the overall figures, marked `scope` = overall, otherwise. `measure_summary` prefers a holdout sheet to an evaluation sheet.

## Cost and reruns

About US$0.001 an item for the three graders together in the trials: about 2,200 items (1,250 measure pairs, 300 types, 350 package x measure, 250 links, 300 assets) is about US$2 to 3. `check grade` prints the estimate first (items x graders x the measured price per answer, from the grader's stored answers) and refuses to start when it is more than `--max-spend` leaves; a grader with no stored answers yet is asked 30 items first to measure its price. Drawing and tuning are free and can be repeated at any time; grading again pays only for items not yet graded. A new seed is a new sample; a drawn seed is never drawn over. A definition changed after grading is a new question, so its items have no answers until graded again.
