# Next architecture: assets once, measures on top

Status (2026-10-19): built in a different shape than proposed, and `docs/methodology.md` sections 5a to 5d describe what the code does now. What became of each stage: A (enriched units) is not built, pending a placebo test; B (the asset map) exists as `./wbg classify assets` at paragraph level over prose, footnotes and table rows, with a per-asset cosine gate for scale; C exists as `./wbg measure`, a funnel with relevance-feedback rounds and a yield rule, not a learned classifier with a stopping guarantee, and the asset is answered for the sentence itself in the same stage; D exists as `./wbg types` (one yes/no per approved type, not a choice question); E (procurement) is not built. Of the open decisions, 1 is answered by the combined link (unit, else paragraph, else component), 2 by the sentence as the unit, 3 by a 95-unit graded sheet per measure. The rest of this note is the proposal as written, kept as the record of why.

## Why change

The trials (six measures, early warning, climate risk assessment, solar, a draft disaster risk management theme) showed:

- **Candidates work.** Patterns plus embedding top 1,000 plus BM25 (keyword ranking) found the commitments; BM25 alone added 2 to 605 per measure.
- **The loop works as a learner, but it was run the slow way.** Reading 25 at a time in sequence is a protocol for human readers. With a decision model at about US$0.15 per 1,000 passages, each round's batch should be read in parallel.
- **A broad theme never stops.** For the draft disaster risk management theme, 659 of 800 reads were positive and the yield stayed near 20 per 25. A theme must be split into measures, and the loop should say so.
- **The asset link is missing.** Many false positives (roads, health, the contingent emergency response component) had no digital asset in the sentence, paragraph or component. Every measure re-derived this link; it should be computed once.
- **Unsupervised clustering does not recover types** (adjusted Rand index 0.04 to 0.20 against hand types over nine methods). Types need a reader.

## Stages

| Stage | Runs | What it does | Model use |
|---|---|---|---|
| A. Enrich units | once per corpus | context header per unit, carry-over for sentences opening "This/It/These/Such", extracted fields; embed the enriched text | embedding only |
| B. Asset map | once, and when `inputs/taxonomy/` changes | tag each sentence, paragraph and component with the assets it concerns; record the level | patterns, embeddings; decision model on paragraphs once (`src/classify.py`) |
| C. Measure loop | per measure | asset-gated candidates; a classifier learns the measure with the decision model as teacher; stopping rule | decision model on a few hundred chosen units |
| D. Types | per measure, pinned version | one call groups the positives into named types; a person approves; saved as a numbered version | one generative model call (Haiku) |
| E. Procurement | every run | positive to component to packages; implementation and learning countries | SQL; decision model only for unclear package links |

### A. Enrich units

A sentence keeps its own offsets as evidence. The text that is embedded and graded gets a header from the document's structure, not from a model: document title, section path, component number and name, and the paragraph's first sentence. A sentence opening with a reference word gets the previous sentence attached. Extracted fields become columns: asset pattern hits, hazard terms, action verb class (finance, require, study, train), amounts, `p_commitment`, firmness.

This is the deterministic form of contextual retrieval (Anthropic reported 35 per cent fewer top-20 retrieval failures from contextual embeddings and 49 per cent with contextual BM25, using a model to write the context). Whether it helps here is to be tested before it is relied on.

### B. Asset map

Each unit gets `asset_id` and `asset_level` (sentence, paragraph, component, none). Measures only consider units with a level other than none. The level is a filter in Metabase: sentence-level links are the precise view.

### C. Measure loop

- Each round the classifier (logistic regression on the enriched embedding plus the extracted fields, numpy, as in `src/commitment.py`) picks 50 to 100 units: half its likeliest positives (recall), half its least certain (the boundary). The decision model reads them in parallel; the classifier is refitted.
- Stop when fewer than two new positives come in over the last three rounds **and** the classifier's verdicts on the pool barely change between rounds.
- If more than about 30 per cent of the pool is positive, stop and report that the measure is a theme to be split.
- After stopping, the classifier scores the whole corpus, and the decision model reads a random sample of what it rejects (an elusion check).
- Output: the graded positives and a versioned classifier per measure. New documents are scored by the classifier without model calls; the decision model is called again only when a definition changes or for a spot check.
- Target: under 10 minutes and a few hundred decision-model calls per measure (an estimate to be measured).

### D. Types

The positives, with their headers and asset links, go to one generative call that proposes named types with one-line definitions. A person edits and approves; the list is saved as `analysis/labels/<set>_types.csv` with a version. Assignment of units to types uses the decision model (choice question with an "other" option, several types allowed). A new version is made only when a person chooses, for instance when "other" passes about 10 per cent.

### E. Procurement

`packages.component_number` (filled on 5,369 of 6,894 packages in the trial corpus) joins packages to the component of each positive. Implementation countries: a linked package not yet signed with a planned or revised date in the next 18 months. Learning countries: linked packages signed or completed. Where a component holds many packages, the decision model answers "does this package buy this measure?".

## Where models are used, and only there

1. The decision model (TypeSafe Jev via OpenRouter, pinned snapshot): yes/no and graded decisions on measures, assets and package links.
2. One generative model call per measure to propose types.

Everything else is code: patterns, embeddings, BM25, classifiers, SQL. Every model response is stored and re-read on re-runs.

## Test plan before any new measure

1. Build A and B; re-run solar, early warning and climate risk assessment.
2. Compare the decision model with the earlier role-play grades (identifiers and checksums only; kept outside git) and report agreement by grade.
3. Measure the loop's time and calls per measure.

## Open decisions

1. Does a component-level asset link count by default, or only sentence and paragraph links with component links shown separately?
2. Unit of analysis: sentence with a context header (proposed) or paragraph?
3. Calibration budget: about 200 graded sentences per measure family, by a person.
