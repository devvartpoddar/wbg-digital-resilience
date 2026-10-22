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

Any model that meets all three may be used, including an external one. For an external model that means: pin the exact version, store every response on the box, and treat the stored responses as the record. A provider can change or retire a model without notice; a re-run reads the store instead of asking again. Changing the version is a deliberate step followed by re-measurement.

## Where the work stands

| Stage | State |
|---|---|
| Fetch appraisal documents (PDFs), procurement records and project records | Built |
| Projects table: name, status, dates, practice, managing unit | Built |
| Clean appraisal documents into paragraphs and sentences | Built; under review |
| Collect each project's components and costs; tag paragraphs with their component | Built |
| Clean procurement packages, notices and awards | Built; under review |
| Link notices and awards to their plan package | Built |
| Record a data release (a manifest of checksums); load the corpus tables into Postgres; the model stages write their own rows (section 0) | Built |
| Embed the search units (sentences, whole footnotes and prose table rows; references, fragments and copies left out) | Built; runs as part of `./wbg search <set>` |
| Search sentences for each asset and measure; roll up to paragraphs | Built for any label set in `inputs/taxonomy/` or `analysis/labels/` (`./wbg search assets`, `./wbg search cra`) |
| The pool of a measure: its word patterns plus every unit whose cosine reaches the pool threshold, with firmness wording | Built inside `./wbg measure` (section 5a) in an earlier trial |
| Draw a measure's units for grading, measure the model against the grades, calibrate, and draw what the pool missed | Built (`./wbg check <set>`, `--evaluate`, `--elusion`); the first sheet is graded (section 5c) |
| Grade every sentence against each asset with a decision model, in the same question form as the measures: the asset map (a sentence naming no asset takes the last one named earlier in its paragraph) | Built (`./wbg classify assets`, with a per-asset cosine gate); the sentence map of 2026-10-22 not yet run (section 5b) |
| One control panel for every threshold (`inputs/config/control_panel.csv`), and a probability layer after every graded answer (similarity to the graded sentences, `src/posterior.py`) | Built 2026-10-22; not yet run (section 5c, runbook "The control panel") |
| A measure through the funnel: candidates by cosine, the decision model's grade, the asset answered for the sentence and linked at unit, paragraph or component level, how the commitment is delivered, relevance-feedback rounds (section 5c) | Built (`./wbg measure <set>`); first measure: climate risk assessment (`analysis/labels/cra*`) |
| The funnel measured against a hand-labelled workbook of activities (section 5c) | Built (`./wbg evaluate <set> --column ...`) |
| A measure's types: proposed by a generative model, approved by a person, assigned by the decision model (section 5d) | Built (`./wbg types <set>`, `--assign`) |
| A graded sample of a measure's units: precision and recall with confidence intervals (section 5c) | Built (`./wbg check <set>`, `--evaluate`); the first sheet of 95 units is drawn and awaits grading |
| Every decision-model answer stored per question; what each run asked for in a spend ledger | Built (`src/answers.py`, `data/reports/spend.csv`) |
| The dashboards' layer: commitments (project, asset, measure, type), their evidence and package status, project and measure pages, similar projects, a phrase search (section 5f) | Built 2026-10-21; checked on a 17-project slice of the live schema, not yet loaded on the box. `commitments`, `commitment_units` and `commitment_package_status` (now with `due_date`) are materialised at each publish, so date-based statuses are as of that publish |
| Measures requested from the dashboard, run under a spend cap (section 5f) | Built; the box needs the role, the Metabase connection and the timer (yggdrasil) |
| Labels for measuring it | Drawn by `src/check_units.py` (units); keyed by the SHA-256 of the text, the label and the exact question; nothing but evaluation reads them |

## Where each part is described

The method is split by stage so a change reads only its part. **When you change code, update the file that covers it**, and this table if a file's scope moves. A section number used elsewhere ("section 5c") is the heading inside the file named here.

| Section | File | Covers (code) |
|---|---|---|
| 0 | `docs/method/running.md` | `./wbg run`, its stages, skipping, selections, the run note (`wbg.py`, `run_report.py`, `common.py`) |
| 1 | `docs/method/fetch.md` | what is pulled, the projects table (`fetch.py`, `fetch_projects.py`, `projects.py`) |
| 2 | `docs/method/appraisal.md` | PDF reading, tables, folding, paragraphs, sentences, components (`pdf_layout.py`, `text_rules.py`, `clean.py`, `sentences.py`, `components.py`, `keys.py`) |
| 3 | `docs/method/procurement.md` | plans, notices, awards, word repair, links (`fetch_procurement.py`, `clean_procurement.py`, `glue.py`, `links.py`, `plan_table.py`) |
| 4 | `docs/method/checks-and-postgres.md` | audits, sensitivity, Postgres load and publish, releases (`audit*.py`, `sensitivity.py`, `load_pg.py`, `store.py`, `schema.py`, `release.py`, `sql/views/01-12`) |
| 5, 5a | `docs/method/search.md` | search units, embedding, label sets, scoring, the pool (`units.py`, `embed.py`, `search.py`, `labels.py`) |
| 5b | `docs/method/asset-map.md` | the asset map and its cosine gate (`classify.py`, `answers.py`) |
| 5c, 5d | `docs/method/measures.md` | the funnel, checks against graded sheets, types (`measure.py`, `check_units.py`, `evaluate_activities.py`, `kinds.py`) |
| 5e | `docs/method/links.md` | packages to assets and commitments (`link_packages.py`) |
| 5f | `docs/method/dashboards.md` | the dashboards' views, package status, requested measures (`sql/views/13-19`, `measure_requests.py`) |
| 5g | `docs/method/decisions.md` | one decision system for every question to the decision model: prior, skip, answer, posterior, decision; the prior inputs that need no grading (`prior.py`) |
| 6 | `docs/method/storage.md` | `data/` layout, joins, the cohort (`paths.py`, `cohort_build.py`) |
| 7 | `docs/method/limits.md` | known limits |
| - | `docs/method/history.md` | dated results and trials the method was tuned on; read only when a choice needs its evidence |

`docs/runbook-box.md` is the order of steps on the box; `docs/architecture-next.md` is the proposal the measure stages were built from, kept as a record.
