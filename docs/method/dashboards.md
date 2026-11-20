# What the app reads

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 5f. What the app reads (`sql/views/13_*` to `18_*`)

The Shiny app (`apps.amduat.dev/wbg`, repository `devvartpoddar/shiny`, `apps/wbg`) reads views, not tables, through `src/wbg_portfolio/app.py`, and those views are the interface: a column is renamed or removed only with the app changed in step. Every view carries a comment. The views are rebuilt inside the publish's transaction (section 4). Four are materialised, built once per publish and indexed, because as plain views Postgres misjudged their joins: `commitment_units`, `unit_wording_overlap`, `commitments` and `commitment_package_status`. A status that turns on today's date is therefore as of the last publish.

**A commitment** is one project, one digital asset, one measure and one type. Its sentences are the committed units (`is_committed`: the band rule; section 5g) in an Investment Project Financing operation (`project_info.is_ipf`; `dashboard.ipf_only`), whose posterior also reaches `dashboard.commitment_min_probability`, and whose asset is found at a level in `dashboard.commitment_link_levels` (the sentence, or its paragraph by the decayed sum; `asset_from` says which). A sentence counts under each of its assets and each of its types (`asset.multiple`, `types.multiple`; `(no type)` when none reaches `floor.types`), so one sentence can hold up several commitments and a sentence count summed across commitments counts it more than once. `commitment_id` is `md5(project_id|label_set|asset_id|type_id)`, the same on every publish. A paragraph in two projects gives its sentences to both. The readings are one row of `wbg.dashboard_settings`, filled from the control panel at publish.

| View or function | One row per | Reads |
|---|---|---|
| `dashboard_settings` | the readings (one row) | control panel |
| `iso_country_code()`, `https()`, `due_date()`, `is_upcoming()`, `same_component()` | helpers: ISO 3166 alpha-2 (NULL for a regional code, `ZR` read as `CD`); a link made https; the date a package falls due; whether a status is still to come; whether two component numbers name the same component | |
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
| `commitment_package_status` (materialised) | commitment and package linked (strong, or by a shared asset: `strength`; `link_probability` is the strong link's, null for a shared-asset link; section 5e), with the package's status | commitment_package_links, package_status |
| `measure_summary` | measure: family, definition, version, commitments, projects, countries, types, and the evaluation's figures (an evaluation draw when one is graded, else the latest training draw; `graded_sheet` says which) | commitments, measure_quality |
| `measure_findings_in_context` | every unit a measure read, with its context | measure_findings |
| `measure_overlap` | pair of measures: committed units both count (a report; nothing is deduplicated) | measure_findings |
| `similar_projects(commitment_id, k)` | other project whose sentences are nearest the commitment's three strongest (stored embeddings, no model call) | sentence_vectors |

**Value of a commitment: three candidates, none chosen.** `project_commitment_usd_m` is the project's total commitment from the projects interface, whose two forms disagree on some projects (section 1); `component_cost_usd_m` the cost of the components the sentences sit under, all funding sources, NULL when none is known; `linked_package_estimate_usd` the sum of the linked packages' plan estimates, with `linked_packages_with_estimate` saying how many gave one.

**Package status.** The plan's own status first: Contract completed, Contract signed, Under implementation, Cancelled. Where the plan says pending, a linked award with a signing date makes it Contract signed and a linked notice Tendered; with neither, it is Not in latest plan when the newest plan drops it, else Overdue when its due date has passed and Planned otherwise. `is_upcoming()` is the one place that says which statuses are still to come. `next_date` is kept only while a package is still to come. A completed contract is not delivery.

**The due date** is `due_date(planned, revised)`: the revised date where the plan prints one, else the planned date. The parser keeps a row's last printed date as `revised_date`, which can be a later milestone; `due_date()` is the one place to change the rule.

**A commitment's status** is the first of its packages' statuses in `package_status_order` (Overdue, Planned, Tendered, Under implementation, Contract signed, Contract completed, Not in latest plan, Cancelled), or No package found.

**Same wording across projects.** Each committed unit's text is cut into runs of `text.shingle_words` words; another project counts when its committed units hold at least `dashboard.near_copy_share` of them (programmes print one climate section with small edits).

**Dropped by a restructuring.** True only where a later restructuring paper marks the component of every sentence with a known component "Marked for Deletion"; NULL when no sentence sits under a known component. Absence from a later paper is not read as dropping.

**Asset map agreement.** `asset_in_asset_map` says whether a sentence of the project names the commitment's asset (`project_assets.sentences_naming_it`): a commitment whose asset no sentence names is worth a second look.

## The app

`src/wbg_portfolio/app.py` holds every query the app runs and what it computes beyond the views; the app (`apps/wbg/app.py` in the shiny repository) only lays them out. It is read only: no write, no model call, no embedding. It reads one frame of commitments (`app.commitments`, a few thousand rows) and filters it in memory, so every chart, board and drill on a page counts the same rows; the sentences are read only for the rows a drill shows (`app.sentences`, every sentence of a commitment; `app.evidence`, its strongest). A switch counts every chart in commitments or in projects (`app.tally`), and the projects a share is taken of come from `app.projects`.

| View | What it answers |
|---|---|
| Overview | How many commitments, the linked packages still to come soonest first, where (a world map; regional projects listed apart), by measure, by asset, measure by asset, by approval year, and how far linked procurement has gone |
| Explore, one country | Its projects, which project commits to what (measure by project), its assets, where each project stands |
| Explore, one project | What it commits to, measure by measure, with the strongest sentence; the assets its sentences name; its components and their cost; its documents; its linked packages on a timeline |
| Explore, one asset | Its definition, the measures committed for it, measure by region, where each project stands, a world map |
| Explore, one measure | Its definition and precision on the graded sample, its types (definition, commitments, projects), where each project stands, a world map, type by region and type by asset |
| About | The definitions, which projects are counted, the pipeline as one figure with the count each step passes on (`app.about`), the models and the limitations |

Every bar, cell, country and card opens what is behind it in a side panel: the projects, each opening to its sentences. A sentence behind several commitments of a project (two assets, two types) is shown once, with every measure, type and asset it carries, its stage and its page.

`not_digital_findings` holds the committed sentences the digital question says are not a digital asset or the digital sector: they stay in `measure_findings` (`is_committed` true, `is_digital` false), count nowhere on the dashboards, and are there for a person to look at what the rule leaves out. `activities` and `activity_summary` group a project's committed, digital sentences for a measure into the distinct things it commits to do: the chat model is shown the whole group (numbered, with each sentence's section, block and document kind; a group over `activities.max_sentences` is split by document, appraisal document first, and each part grouped on its own), and names the activities. A reply that drops or repeats a sentence is repaired (a dropped sentence becomes an activity of its own, a repeated one stays where it was first placed) and the repair counts are in the run log. `activity_id` is the MD5 of project, measure and the activity's sorted unit ids, so a rerun gives the same ids. `activity_summary` gives one row per activity with its sentences' assets, kinds, instruments, types and link strengths. A group of one sentence is one activity with no call. Trial: shown a whole group, the chat model's pairs of sentences put together were right 0.93 of the time for groups up to 8 sentences, 0.97 for 12 to 20, and 0.78 above 20 (model-graded). `commitment_package_links_in_context` carries the link strengths (section 5e). The redesigned dashboard reads them through the functions below ("The reader for the redesigned dashboard").

**Computed in `app.py`, not in the views:**

- **Stage** (`app.STAGES`, `app.stage`): a commitment's `status` grouped into Completed (Contract completed), Under way (Under implementation, Contract signed), Coming up (Overdue, Planned, Tendered), Package dropped (Not in latest plan, Cancelled: the package, not the commitment) and No package linked. A sentence behind commitments at several stages shows the first in `app.STAGE_URGENCY` (Coming up before Under way before Completed), the order `wbg.package_status_order` gives a commitment with several packages. Package links are found for about a third of commitments, so No package linked is the common case and says nothing about the project.
- **Place**: the country, or the region for a regional project.
- **Counting projects** (`app.tally` with unit `projects`): a project counts once in each group it has a commitment in, however many, so the groups do not add up to the number of projects. Its share is of a base of projects (`app.projects`: the cohort's projects with a document read, of the instruments `dashboard.ipf_only` admits, with or without a commitment). Grouped by something about the project (country, region, approval year) the base is the projects sharing it; grouped by measure, asset, type or stage it is the whole base. Inside one measure or asset the app passes that measure's or asset's projects as the base, so a type's share is of the measure's projects; inside one country, the country's projects.
- **Where each project stands** (`app.board`): one card per project and stage, with its commitments and types; a project with commitments at two stages has two cards.
- **A package's date** (`app.packages`): the next date while it is still to come, else the signing date, else the due date; `date_kind` says which (planned, signed, was due).
- **Coming up** (`app.coming_up`): one row per linked package whose status is Overdue, Planned or Tendered, with its project, the measures of the commitments it is linked to, its date (the next date, else the date it was due) and the plan's estimate, soonest first.
- **The About page's counts** (`app.about`): one statement per group (projects, text, how sentences reached the model, measures, assets, procurement, definitions, grades), all for the operations the dashboards count and their documents; a group whose statement fails is left out and the page still draws. The thresholds it quotes are read from the control panel.

Countries are mapped from `country_code` (ISO 3166 alpha-2); a regional project has none and is counted beside the map, not on it.

## The reader for the redesigned dashboard

`app.py` also holds the functions the redesigned pages read, each one query over the published tables and views (the existing functions and their columns are unchanged). Filters are keyword arguments, a value or a list; a misspelt one raises `TypeError`. Frames carry plain text in place of lists: several ids or names are joined with `"; "`. Only the operations the dashboards count are read (`dashboard.ipf_only`).

| Function | Returns |
|---|---|
| `families(q)` | one row per family and measure (a measure in "a\|b" is under both), with project and activity counts for the measure and for the family |
| `activities(q, family=None, **filters)` | one row per activity: place, measure, label, types (and whether one is a proposed `OTH.` type), up to two assets with their asset type, instrument, component, sentence count, first document date and procurement summary. `asset_column` is the asset when there is one, else `none` (policy is the instrument, not an asset) |
| `activity_sentences(q, activity_ids=None, family=None, **filters)` | the sentences of those activities (or of the filtered ones) with document kind, date, page, link, section, component and the strength inputs (`score_first`, `score_context`, `second_grade`, `decided_by`) |
| `packages_detail(q, family=None, **filters)`, `package(q, package_id)` | one row per linked package with every procurement field, its asset(s), link strength, the measures its strong links name and the activities it serves; `package` adds its notices, awards and activities |
| `not_digital(q, project_id=None, measure=None)` | the committed sentences the digital question leaves out |
| `asset_types(q)`, `type_definitions(q)` | names and definitions of assets, asset types and measure types, for hover and tables |
| `download(q, level, family=None, **filters)`, `download_notes(q, level, family, filters)` | a sheet per level (`project_flags`, `project`, `activity`, `sentence`, `package`) with fixed columns (`app.DOWNLOAD_COLUMNS`), and the rows of its notes sheet: what each column means, each measure's definition, the data date, the filters, and the three standing statements |

An activity's instrument, asset and types are read from its sentences (the instrument most of them carry; the assets most of them name, at most two shown; every type any of them has). Its procurement summary counts the packages linked to any of its sentences, `strong` where the package, asked alone, is about the measure and `asset` where only an asset is shared. `cohort` (a keyword argument of `families`, `activities`, `activity_sentences`, `packages_detail`, `not_digital` and `download`, as `cohort` of the older functions: 'digital' or 'digital_contributor', or none for every project) keeps the projects of that `cohort_set`; the demo seed carries both values. A package filter on `type`, `asset_type` or `instrument` is of the activities the package serves. In `download`, filters choose rows and never columns; `status`, `strength` and `package_id` belong to level `package` only. At `project_flags` a measure column is 1 where the project has an activity of the measure within the filters.

`tests/demo_seed.py` publishes an invented dataset (about 60 projects, no real text) into the schema `wbg_demo` through the pipeline's own loader and views, to build and try a page without the real data: `WBG_PG=... python3 tests/demo_seed.py --schema wbg_demo`, then run the app with `WBG_SCHEMA=wbg_demo WBG_STORE=wbg_demo_store`.
