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
| Fetch appraisal documents (PDFs), procurement records and project records; the projects table | Built |
| Clean appraisal documents into paragraphs and sentences; components and costs | Built; the sentence splitter fixed on 2026-10-22 (every enumerator form, comma-separated lists, consecutive footnotes, long table rows cut at cells) |
| Clean procurement packages, notices and awards; link notices and awards to their package | Built |
| Releases, Postgres loads, each stage writing its own rows (section 4) | Built |
| Embed the search units; search every label set; the pool of a measure (sections 5, 5a) | Built |
| The asset map: every sentence graded against each asset; each sentence's asset by its own posterior or the decayed paragraph sum (5b) | Built; the sentence map not yet run on the box |
| A measure through the funnel; cross-measure pair checks (5c) | Built for 12 measures |
| Types: one choice per committed sentence, with gap types (5d) | Built |
| Packages to assets and to commitments (5e) | Built |
| The dashboards' views, all reading one decision (5f) | Built; Metabase cards that read dropped or renamed columns need an edit after the next load (runbook) |
| One decision system: the control panel, the skip rule, the probability layer (weighted, out of sample, no same-project or near-copy votes, current-question grades only), the floor (5g) | Built 2026-10-22; no layer or fitted gate exists yet, because no sheet has been graded under the current questions |
| Training and evaluation sheets (`./wbg check --design train`, `--design eval`), elusion sheets | Built; the next sheets to grade |
| The record of judgement in Postgres: grades, draws, layers, gates, runs, spend, answers (4) | Built 2026-10-22, not yet loaded |

**Next on the box: a clean run from scratch** (`docs/runbook-box.md`, "Clean run from scratch"). The box's checkout is at #56; #57, #58 and the changes of 2026-10-22 have not run there.

## Where each part is described

The method is split by stage so a change reads only its part. **When you change code, update the file that covers it**, and this table if a file's scope moves. A section number used elsewhere ("section 5c") is the heading inside the file named here.

| Section | File | Covers (code) |
|---|---|---|
| 0 | `docs/method/running.md` | `./wbg run`, its stages, skipping, selections, the run note (`wbg.py`, `run_report.py`, `common.py`) |
| 1 | `docs/method/fetch.md` | what is pulled, the projects table (`fetch.py`, `fetch_projects.py`, `projects.py`) |
| 2 | `docs/method/appraisal.md` | PDF reading, tables, folding, paragraphs, sentences, components (`pdf_layout.py`, `text_rules.py`, `clean.py`, `sentences.py`, `components.py`, `keys.py`) |
| 3 | `docs/method/procurement.md` | plans, notices, awards, word repair, links (`fetch_procurement.py`, `clean_procurement.py`, `glue.py`, `links.py`, `plan_table.py`) |
| 4 | `docs/method/checks-and-postgres.md` | audits, sensitivity, Postgres load and publish, the record of judgement, releases (`audit*.py`, `sensitivity.py`, `load_pg.py`, `store.py`, `schema.py`, `release.py`, `sql/views/01-12`) |
| 5, 5a | `docs/method/search.md` | search units, embedding, label sets, scoring, the pool (`units.py`, `embed.py`, `search.py`, `labels.py`) |
| 5b | `docs/method/asset-map.md` | the asset map and how a sentence gets its asset (`classify.py`, `answers.py`) |
| 5c, 5d | `docs/method/measures.md` | the funnel, pair checks, training and evaluation sheets, types (`measure.py`, `commit_checks.py`, `check_units.py`, `evaluate_activities.py`, `kinds.py`) |
| 5e | `docs/method/links.md` | packages to assets and commitments (`link_packages.py`) |
| 5f | `docs/method/dashboards.md` | the dashboards' views, package status, requested measures (`sql/views/13-19`, `measure_requests.py`) |
| 5g | `docs/method/decisions.md` | one decision system for every question: the control panel, the skip rule, the probability layer, the floor; the prior inputs, stored and not yet read (`panel.py`, `skip.py`, `posterior.py`, `prior.py`) |
| 6 | `docs/method/storage.md` | `data/` layout, joins, the cohort (`paths.py`, `cohort_build.py`) |
| 7 | `docs/method/limits.md` | known limits |
| - | `docs/method/history.md` | dated results and trials the method was tuned on; read only when a choice needs its evidence |

`docs/runbook-box.md` is the order of steps on the box.
