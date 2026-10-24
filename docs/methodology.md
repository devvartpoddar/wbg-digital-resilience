# Digital for Resilience: method

## What this is for

For a lending project, the pipeline answers four questions:

1. Which digital assets does it finance?
2. Which resilience measures does the appraisal document commit to for those assets?
3. How firm is each commitment?
4. What procurement is coming up against those same assets?

**Terms.** Project Appraisal Document (PAD): the document the Board approves, describing what a lending operation finances. Task Team Leader (TTL): the World Bank staff member leading that operation; the output helps an advisor decide which TTLs to talk to, and when. Systematic Tracking of Exchanges in Procurement (STEP): the system through which borrowers publish procurement plans.

**Three statements go with every output.** A commitment in an appraisal document is design intent, not delivery. Nothing here is causal. The pipeline misses real commitments and the miss rate is not known.

## The principle

Every decision the pipeline makes must be:

1. **Repeatable.** The same text gets the same answer on every run, so a figure shown to a task team does not move next week.
2. **Scored.** Every decision is a probability with a cut-off someone chose, so precision can be traded for recall deliberately. Precision is preferred: a false commitment costs credibility with a task team; a missed one costs an opportunity.
3. **Checkable.** It is measured against examples a person has checked, and the measurement is reported with its sample size.

Any model that meets all three may be used, including an external one. For an external model that means: pin the exact version, store every response, and treat the stored responses as the record. A provider can change or retire a model without notice; a re-run reads the store instead of asking again. Changing the version is a deliberate step followed by re-measurement.

## How it is built

Two stores of record, everything else derived from them:

- **Files under `data/`** hold what was fetched and parsed (documents, paragraphs, sentences, components, procurement tables) and the sentence vectors. These are slow to make and never deleted.
- **Postgres schema `wbg_store`** holds every model answer (keyed on the exact text and question sent), every grade a person or an agent gave (keyed on the item's text and the label), every fitted layer and gate, and every payment. It is never dropped.

From those two, every run derives the same things: a `scores` table (the model's answer to each question about each item), the decision tables the dashboards read (schema `wbg`, rebuilt by `./wbg publish`), and the views over them. A moved threshold is a publish, not a run; a changed question re-asks only the items whose question changed; a new document asks only its own sentences. There are no run folders and no run keys.

## Where the work stands

| Stage | State |
|---|---|
| Fetch appraisal documents, procurement records and project records; the projects table | Built |
| Clean appraisal documents into paragraphs and sentences; components and costs | Built |
| Clean procurement packages, notices and awards; link notices and awards to their package | Built |
| Embed the search units; score every label set's phrases (sections 5, 5a) | Built |
| The funnel, the same for the asset map and every measure: gate, word patterns, stored answers, feedback rounds (5b, 5c) | Built, rewritten 2026-10-24; not yet run on the box in this form |
| The checks: types, pair checks, package assets, commitment links (5d, 5e) | Built, rewritten 2026-10-24 |
| One decision function and the dashboards' views (5f, 5g) | Built, rewritten 2026-10-24 |
| Grading as self-contained tasks a person or an agent fills; one evaluation for every decision kind (5g) | Built 2026-10-24; the file-based grades import once |
| Release v1.0 (provisional, 2026-10-23): decisions on the raw score, the layer off, floors read off the seed-7 grades | Settings unchanged; figures are upper estimates until an evaluation draw is graded |

**Next on the box:** `./wbg check import` (the grades of the file sheets into the store), then a run from `classify` (every stored answer is reused; the types and pair checks are re-asked because their state now carries the section and component), then `./wbg check evaluate`. `docs/method/running.md` has the order and the costs.

## Where each part is described

The method is split by stage so a change reads only its part. **When you change code, update the file that covers it**, and this table if a file's scope moves. A section number used elsewhere ("section 5c") is the heading inside the file named here.

| Section | File | Covers (code) |
|---|---|---|
| 0 | `docs/method/running.md` | `./wbg run`, its stages, what each costs, the clean run, adding a measure, what Metabase sees (`wbg.py`) |
| 1 | `docs/method/fetch.md` | what is pulled, the projects table (`fetch.py`, `fetch_projects.py`, `projects.py`) |
| 2 | `docs/method/appraisal.md` | PDF reading, folding, paragraphs, sentences, components (`pdf_layout.py`, `text_rules.py`, `clean.py`, `sentences.py`, `components.py`, `keys.py`) |
| 3 | `docs/method/procurement.md` | plans, notices, awards, word repair, links (`fetch_procurement.py`, `clean_procurement.py`, `plan_table.py`, `glue.py`, `links.py`, `procurement_vocab.json`) |
| 4 | `docs/method/postgres.md` | the two stores, the loader, the tables, the views (`db.py`, `load_pg.py`, `schema.py`, `sql/views/`) |
| 5, 5a | `docs/method/search.md` | search units, embedding, label sets, scoring (`units.py`, `embed.py`, `search.py`, `labels.py`) |
| 5b | `docs/method/asset-map.md` | the asset map and how a sentence gets its asset |
| 5c, 5d | `docs/method/measures.md` | a measure through the funnel, types, pair checks (`checks.py`) |
| 5e | `docs/method/links.md` | packages to assets and to commitments |
| 5f | `docs/method/dashboards.md` | the dashboards' views, package status, requested measures (`sql/views/13-19`, `measure_requests.py`) |
| 5g | `docs/method/decisions.md` | one decision system: the asker, the funnel, the floors, the probability layer, the skip rule, grading and evaluation, the control panel (`api.py`, `ask.py`, `questions.py`, `funnel.py`, `decide.py`, `grading.py`, `posterior.py`, `skip.py`, `fits.py`, `panel.py`) |
| 6 | `docs/method/storage.md` | `data/` layout, joins, the cohort (`paths.py`, `cohort_build.py`) |
| 7 | `docs/method/limits.md` | known limits |
| - | `docs/method/history.md` | dated results and trials the method was tuned on; read only when a choice needs its evidence |
