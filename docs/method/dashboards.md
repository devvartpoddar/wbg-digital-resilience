# What the dashboards read

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 5f. What the dashboards read (`sql/views/13_*` to `19_*`)

The dashboards (Metabase collection "WBG v2": Overview, Measure, Project, Commitment) read views, not tables, and those views are the interface: a column is renamed or removed only after telling Dev (the change of 2026-10-22 is listed in `docs/runbook-box.md`, "After the first load"). Every view carries a comment. Views a pivot reads are flat (no arrays). The views are rebuilt inside the load's transaction (section 4). Four are materialised, built once per load and indexed, because as plain views Postgres misjudged their joins and cards ran for minutes: `commitment_units`, `unit_wording_overlap`, `commitments` and `commitment_package_status`. A status that turns on today's date is therefore as of the last publish.

**A commitment** is one project, one digital asset, one measure and one type. Its sentences are the committed units (`is_committed`: the posterior of grades 2 and 3 at the measure's floor; section 5g) in an Investment Project Financing operation (`project_info.is_ipf`; `dashboard.ipf_only`), whose posterior also reaches `dashboard.commitment_min_probability`, and whose asset is found at a level in `dashboard.commitment_link_levels` (the sentence, or its paragraph by the decayed sum; `asset_from` says which). A sentence counts under its one asset and its one type (`(no type)` when none reaches `floor.types`). A unit two measures commit is dropped from a measure its pair check says it is not (`dashboard.apply_measure_pair_checks`, `floor.pair_check`). `commitment_id` is `md5(project_id|label_set|asset_id|type_id)`, the same on every load. A paragraph in two projects gives its sentences to both. The readings are one row of `wbg.dashboard_settings`, filled from the control panel at publish.

| View or function | One row per | Reads |
|---|---|---|
| `dashboard_settings` | the readings (one row) | control panel |
| `iso_country_code()`, `https()`, `due_date()` | helpers: ISO 3166 alpha-2 (NULL for a regional code, `ZR` read as `CD`); a link made https; the date a package falls due | |
| `project_info`, `project_header`, `project_documents` | project: a name never blank, map-ready country code, `is_regional`; place, status, dates, documents read; each document with its kind, title and PDF link | projects, documents |
| `project_components` | component of the project's latest document that lists any; the cost is that document's, else the most recent one stating it for the same component; `cost_covers` = all funding sources | components |
| `project_assets` | project and asset: sentences naming it, sentences taking it from their paragraph, paragraphs, best probability | unit_assets |
| `asset_map` | sentence the pipeline gives an asset, with its level | unit_assets |
| `package_status`, `upcoming_packages` | package: plan columns, `due_date`, latest notice, signed contract, `package_status`, `next_date`; those still to come | packages, notices, awards |
| `commitment_units` (materialised) | committed unit that counts, per project, with its `commitment_id` and posterior | measure_findings |
| `unit_wording_overlap` (materialised) | committed unit and project: other projects printing it near-identically | measure_findings |
| `unit_dropped_by_restructuring` | committed unit: whether a later paper marks its component for deletion | commitment_units, components |
| `commitments` (materialised) | commitment: project and place; measure, asset, type; `asset_from`; three candidate values (`project_commitment_usd_m`, `component_cost_usd_m`, `linked_package_estimate_usd`); `sentences`, `paragraphs`, `documents`, `strongest_p` (highest posterior), `first_seen_date`, `same_wording_projects`, `dropped_by_restructuring`, `asset_in_asset_map`, `linked_packages`, `status`, `next_date` | the views above |
| `commitment_evidence` | commitment and paragraph, with the commitment's sentences wrapped in 【 】 by character offsets and a PDF link to the page | commitment_units, search_units, paragraphs |
| `commitment_sentences` | commitment and distinct sentence: text, posterior, firmness, section, document, page link, `also_in_documents` | commitment_units |
| `commitment_package_status` (materialised) | commitment and package linked at `dashboard.package_link_min_probability`, with the package's status | commitment_packages, package_status |
| `measure_summary` | measure: definition, version, commitments, projects, countries, types, and a graded sheet's figures (an evaluation sheet when one exists, else the latest training sheet; `graded_sheet` says which) | commitments, measure_quality |
| `measure_asset_matrix`, `measure_type_asset_flow`, `measure_country_type`, `commitment_status_by_quarter`, `type_procurement_profile` | the pivots of the Measure and Overview pages | commitments, commitment_package_status |
| `measure_overlap` | pair of measures: committed units both count (a report; nothing is deduplicated) | measure_findings |
| `measure_findings_in_context`, `measure_findings_by_project`, `measure_finding_lists`, `findings_under()` | every unit a measure read with its context; per project and measure; one row per asset or type a unit carries, for filters; the committed units under a page's filters | measure_findings |
| `filter_*` | a filter's values | |
| `commitments_under(measure, type_, region, country, months)` | rows of `commitments` under the filters | commitments |
| `similar_projects(commitment_id, k)` | other project whose sentences are nearest the commitment's three strongest (stored embeddings, no model call) | sentence_vectors |
| `try_phrase(phrase, k)` | unit and project nearest a typed phrase (one embedding call) | sentence_vectors |
| `label_queries`, `label_patterns`, `label_scale`, `label_pattern_hits` | each set's phrases, patterns and scale as written, and the units each pattern matched and how many count | the label files, measure_findings |

**Value of a commitment: three candidates, none chosen (2026-10-21).** `project_commitment_usd_m` is the project's total commitment from the projects interface, whose two forms disagree on 30 of 131 projects (section 1); `component_cost_usd_m` the cost of the components the sentences sit under, all funding sources, NULL when none is known; `linked_package_estimate_usd` the sum of the linked packages' plan estimates, with `linked_packages_with_estimate` saying how many gave one.

**Package status.** The plan's own status first: Contract completed, Contract signed, Under implementation, Cancelled. Where the plan says pending, a linked award with a signing date makes it Contract signed and a linked notice Tendered; with neither, it is Not in latest plan when the newest plan drops it, else Overdue when its due date has passed and Planned otherwise. `next_date` is kept only while a package is still to come. A completed contract is not delivery.

**The due date** is `due_date(planned, revised)`: the revised date where the plan prints one, else the planned date. The parser keeps a row's last printed date as `revised_date`, which can be a later milestone; `due_date()` is the one place to change the rule.

**A commitment's status** is the first of its packages' statuses in `package_status_order` (Overdue, Planned, Tendered, Under implementation, Contract signed, Contract completed, Not in latest plan, Cancelled), or No package found.

**Same wording across projects.** Each committed unit's text is cut into six-word runs; another project counts when its committed units hold at least `dashboard.near_copy_share` of them (programmes print one climate section with small edits).

**Dropped by a restructuring.** True only where a later restructuring paper marks the component of every sentence with a known component "Marked for Deletion"; NULL when no sentence sits under a known component. Absence from a later paper is not read as dropping.

**Asset map agreement.** `asset_in_asset_map` says whether a sentence of the project names the commitment's asset (`project_assets.sentences_naming_it`): a commitment whose asset no sentence names is worth a second look.

**Requested measures (later, not now).** `src/measure_requests.py` and `./wbg requests` run a queue in `wbg_requests.measure_requests` one at a time under a US$1 cap a request and US$3 a day. Nothing schedules it: the write-only Metabase connection, its role and a timer are yggdrasil changes.
