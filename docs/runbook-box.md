# Running the cohort on the box

What to do, in order, to run the pipeline over a new cohort on the box and
have Postgres ready for Metabase. Every step skips work already done, so a
step that stops can be run again. Model spend is only in steps 5 to 7, and
each prints its estimate before asking anything (`--dry-run`).

## Before starting

1. **The key.** `.secrets/wbg-openrouter-key` in the main checkout, mode 0600,
   owned by the account that runs the pipeline (`svc-hermes`); or
   `$WBG_OPENROUTER_KEY`. The box has no key yet. The key's own spend cap is
   the only guard: there is none in the code, by design.
2. **The cohort.** `inputs/config/cohort.csv` is hand-kept. To list what a
   cohort could hold:

   ```
   python3 src/cohort_build.py --from-fy 2020 --count          # totals only
   python3 src/cohort_build.py --from-fy 2020                  # writes data/reports/cohort_candidates_fy2020-2026.csv
   ```

   then copy the rows wanted into `cohort.csv` (same columns; extra columns
   are for choosing). On 2026-10-19 the interface listed 1,402 board-approved
   Investment Project Financing operations for FY2020 to FY2025, but only 75
   for FY2025: the interface lags on recent approvals, so FY2025 and FY2026
   need a second pass later.
   The cohort as committed on 2026-10-19 holds 251 included operations in
   two sets, `digital` (72) and `digital_contributor` (179); `./wbg list
   --set digital_contributor` shows one. 113 of them had no record in the
   projects interface when added, so expect `./wbg prepare` to report
   projects with no documents; those stay in the cohort and are tried again
   on the next run.
3. **Time and money at 250 projects**, scaled from the 70-project corpus
   (`data/reports/spend.csv`), so roughly three and a half times (the
   1,400-project column is what a whole-portfolio run would cost):

   | Step | 70 projects | 250 projects | 1,400 projects |
   |---|---|---|---|
   | Fetch PDFs, plans, notices, awards, records | hours | overnight (the interface is rate-limited; `./wbg prepare` resumes) | a day or more |
   | Read PDFs (0.3 s a page on a fast core; three workers) | minutes | about 3 hours on the N150 | about 15 hours |
   | Embed the units | US$0.30 | about US$1 | about US$6 |
   | Asset map, 13 assets, every sentence (starting gate) | about US$2 to 3 (estimate) | about US$8 to 11 | about US$45 to 60 |
   | One measure (pool plus feedback) | about US$0.10 | about US$0.40 | about US$2 |

   The asset-map row is an estimate from the test rate on graded sentences;
   the dry run gives the figure. At 250 projects the whole run fits well
   inside the key's cap of US$50.

## The run

```
./wbg run                                        # everything, fetch to publish; every stage skips what is up to date
./wbg run --from search --measures <set>         # a new or changed measure, in Postgres when it returns (./wbg add <set> is the same)
./wbg run --from search                           # after an asset definition changed: a definition is a search query, so the asset search re-scores and the map follows
./wbg run --only link                            # the package links alone
./wbg status                                     # what is out of date
```

Every stage writes Postgres itself as it finishes; `prepare` loads the corpus
tables and cuts the release when the corpus changed. The whole included
cohort is the default scope; give `--set` only for a deliberate subset, and
then to every stage alike. The fetch is the long step on a new cohort and can
be left overnight; everything else is minutes.

## How runs and caches are keyed, and why status says "stale"

Read this instead of re-deriving keys from `src/`. Two separate things are
keyed: **answers** (what costs money) and **run folders** (what is current).

**Answers cost money; folders do not.** Every model answer is stored under
`sha256(model, state, question)` (`answers.key`): the exact state dict sent
(section, sentence, component) and the exact question
(its text, criteria and options, not its id), in
`data/classify/answers/<model>/`. A stage asks only the keys that are
missing. Batching does not change keys. So a new run folder is usually
rebuilt from stored answers at no cost; money is spent only when a
question's wording, the model, or the text sent changes, or when new units
enter (a lower floor, a wider gate, new documents).

**Run folders** are `data/<stage>/<name>-<12 hex>`, where the hex is a
SHA-256 over the stage's inputs. A folder with `manifest.json` is complete
and is skipped. The current run is the folder whose name the inputs hash to
now.

| Stage | Folder hash covers | Asks the model when |
|---|---|---|
| embed | one vector per text: `cache/<model>.<dim>/<sha of text>.f32`; `sentence_index.csv` is rebuilt every run | the text, model or dim changes |
| search | query ids and texts (definitions and phrases), model:dim, and the **bytes of `sentence_index.csv`** | never (query vectors are cached) |
| classify (asset map) | the starting gate, the fitted gates that apply (`data/labels/assets_gate.json`), the prior-input version (`prior.VERSION`), and every sentence's request (model, section, sentence, component, questions) | a sentence's text or a question changes, or a gate lets new sentences in |
| measure | the search and asset-map run names, the asset layers that apply, the settings (model, gate (fitted or starting), top, feedback, floor, projects, the prior-input version, the `read.`, `asset.` and `posterior.` panel settings), the patterns, the question (scale, definitions), the measure's probability layer | a question or state changes, or new units enter |
| types | the measure run name, model, the prior-input version, and every committed unit's request | a type list changes or new units are committed |
| checks | the measure run names, model, the prior-input version, and every pair request | new committed pairs |
| links | `LINK_VERSION`, model, `link.asset_top`, `link.measure_top`, `link.asset_yes`, measure query texts, the bytes of `packages.csv`, the asset and pair questions, the measure run names, projects | new pairs or packages |

The prior inputs (section 5g, `src/prior.py`) are stored with every stage's
answers; their version is in each stage's key (`prior.VERSION`, and
`LINK_VERSION` 4 for the links), so the first run after they were added is a
new folder at every stage, rebuilt from the store at no cost. The word
frequencies they weigh words by are counted once per sentence index
(`data/prior/word_df-<key>.json`: a pass over the corpus text, no model call).
The paragraph counts (`wbg.unit_paragraph_context`) are made at every
publish from the finished classification; they are in no run key.

Code is in no run folder's name: changing `src/` alone does not make a model
run new (it does count in the prepare stages' fingerprints, below).

**Why a run is "new" with nothing re-asked.** The names chain: anything
that changes `sentence_index.csv` (re-embedding, re-cleaning, a component or
copy change in the unit metadata) gives a new search version, so new measure
folders, so new types, checks and links folders, all rebuilt from stored
answers. A changed floor, any measure setting, a pattern, a refitted
probability layer or gate, or the project scope does the same from the
measure stage down.

**What `./wbg status` prints.**

- `code:` HEAD against origin/main as last fetched, and "(uncommitted
  changes)" when `src`, `inputs`, `sql`, `run.sh` or `wbg` differ.
- `data:` `r-<10 hex>` is a SHA-256 over the corpus tables (rows and bytes,
  `sentence_index.csv` included), the cohort and every label CSV, and the
  names of **every** run folder present under search, classify, measures
  and links (not only the current ones). So any new run folder, or any label
  edit, makes "r-… would be new": it means no release has been cut since,
  not that something is out of date. A release is cut only by `./wbg
  release` (or `prepare` when the corpus changed); the commit is not in the
  id.
- `stages:` only the prepare stages (projects, clean, components,
  clean_procurement). One is out of date when it has no record in
  `data/reports/stages.json`, or its fingerprint (its code files' bytes, its
  inputs' bytes, the pdfplumber version) differs from the recorded one, or
  an output is missing or changed. The model stages are not listed here;
  each one skips by its own folder manifest, so `./wbg run --dry-run` is the
  way to see what they would ask.

**Postgres** skips a table whose source key equals the last load's
(`wbg.loads.source_sha256`): a file's SHA-256 for corpus tables, the
`|`-joined current run names for run tables. A publish replaces only the
sets that have a current run; a run over named projects is not published
(`WBG_PUBLISH_SCOPED=1` overrides, for a scratch database).

## What Metabase sees

Schema `wbg` only. Start cards from the views, not the tables. The
dashboards built from 2026-10-21 read the views of `docs/methodology.md`
section 5f: `commitments` (the grain of every page), `commitment_evidence`,
`commitment_sentences`, `commitment_package_status`, `package_status`,
`project_header`, `project_documents`, `project_components`,
`project_assets`, `measure_summary`, `measure_asset_matrix`,
`measure_type_asset_flow`, `measure_country_type`,
`commitment_status_by_quarter`, `type_procurement_profile`,
`measure_overlap`, the `filter_*` views, and the functions `commitments_under()`,
`similar_projects()` and `try_phrase()`. Their readings (link levels, package-link threshold,
near-copy share) are `dashboard.*` lines of the control panel, filled in at publish, and
the due-date rule one edit in `wbg.due_date()` (`03_procurement_links.sql`). Earlier cards read:

- `measure_findings_by_project`: one row per project and measure, with the
  units that count, the assets linked and the types (the project list and map);
- `measure_findings_in_context`: every unit a measure read, with
  `is_committed`, `posterior_probability`, asset, type and the text around
  it (filter `is_committed`);
- `measure_finding_lists`: one row per unit and asset or type, for
  dashboard filters on what the array columns hold;
- `packages_with_commitments`: one row per package with the assets it buys,
  the measures linked to it and how many commitments (at the panel's
  thresholds); the pairs themselves are `commitment_packages_in_context`,
  the package-asset answers `package_assets_in_context`;
- `asset_map`, `search_paragraphs_with_text`, `paragraphs_with_document`,
  `packages_with_project`, `upcoming_packages`, `notices_with_package`,
  `awards_with_package`, `data_as_of`.

Hide `loads`, `data_release` and `paragraph_projects` in Metabase's table
settings; they are bookkeeping. Every count in the findings is the
pipeline's decision, not a person's: an evaluation sheet's weighted precision
and recall (`data/reports/evaluate_units_<set>.md`, `wbg.measure_quality`
with `sheet` = evaluation) is the number to quote beside it.

## The control panel

Every threshold a person tunes is one line of `inputs/config/control_panel.csv`
(setting, value, stage, what it does, how to tune it): the gates (`asset.gate_cosine`,
`measure.gate_cosine`), the feedback rounds, how a sentence gets its asset
(`asset.floor`, `asset.decay`), the probability layer (`posterior.*`), the floor
per measure (`floor.default`, `floor.<set>`), the package links (`link.*`) and the
dashboard readings (`dashboard.*`, filled into `sql/views/13_dashboard_settings.sql`
at publish). Code only reads it (`src/panel.py`); a change is a pull request a
person merges.

What a change costs:

- **A floor, `asset.floor`, `asset.decay`, `posterior.*`**: nothing. The
  stage reads the stored answers again under the new number: `./wbg run --from measure`;
  `./wbg status` shows those runs as "would be new" until then.
- **`dashboard.*`**: nothing; `./wbg publish` rebuilds the views with the new value.
- **A gate (`asset.gate_cosine`, `measure.gate_cosine`, `skip.*`), the feedback settings,
  `link.*`**: the sentences or pairs newly let in are asked; the rest come from the
  store. Dry-run first.

No command-line option overrides a panel setting.

## Clean run from scratch

The order once this branch is on the box, with nothing graded yet under the
current questions:

1. `./wbg run --to search` (fetch, prepare, embed, search): the parser and the
   sentence splitter changed, so every PDF is read again (hours) and the sentence
   index is new; embeddings come from the cache where the text is unchanged.
2. `./wbg run --from classify --dry-run`, read the estimates, then without
   `--dry-run`. The first run has **no probability layer and no fitted gate**: the
   floors read the model's score and the pools use the panel's starting gates
   (`asset.gate_cosine`, `measure.gate_cosine`). Stored answers are reused wherever
   the exact question and state were asked before.
3. Grade a training sheet: `./wbg check all --design train --seed <n>`
   (about 40 rows a measure, near each floor), then `./wbg check all --seed <n>
   --evaluate`. That stores the grades, fits each measure's layer and gate (and the
   assets', from the assets given on the sheet), and reports each measure out of
   sample with a suggested floor.
4. `./wbg run --from classify` again: the runs are new (their keys hold the layers
   and gates); answers come from the store, and only sentences a gate newly lets in
   are asked. Repeat 3 and 4 while definitions are being tuned.
5. When definitions are settled: an evaluation sheet, drawn fresh,
   `./wbg check all --design eval --seed <m>` (90 rows a measure: 60 that count, 30
   that do not), graded and evaluated the same way. Its weighted precision and recall
   are the dashboards' figures; its rows never fit anything. Add elusion sheets
   (`./wbg check <set> --elusion`) for recall outside the pool.

## After the first load

The load of 2026-10-22's schema drops or renames columns some Metabase cards may
read. Update any card that uses:

- `most_likely_grade`, `expected_grade`, or `probability_grade_3` as a decision
  (read `is_committed` and `posterior_probability`);
- `committed_probability_calibrated` (now `posterior_probability`);
- the kind, actor and conditional columns (`activity_kinds_above_threshold`,
  `best_activity_kind`, `conditional_probability`, `actor_ids_above_threshold`,
  `best_actor_id`, and their probabilities), `component_best_asset_*`,
  `probability_grade_4`;
- `wbg.unit_scope`, and `scope`, `flagged_not_commitment`, `share_not_conditional`
  in `commitments` and `commitment_sentences`;
- `project_assets.paragraphs_financed`, `paragraphs_supported`, `how` (now
  `sentences_naming_it`, `sentences_from_paragraph`, `paragraphs`), and the
  paragraph-level `asset_map` (now one row per sentence with an asset);
- `measure_findings_by_project.planned_units`, `activity_kinds`, `actors`,
  `conditional_committed_units`, `best_committed_probability_calibrated`;
- the `kind` and `actor` arguments of `findings_under()`.

## Changing what the dashboards show, without a model call

A person decides the clusters; the files carry the
decision and a reload applies it. None of these asks the model anything.

- **Merge two types**: in `analysis/labels/<set>_types.csv` set
  `merge_into` on the type to fold (to the id of the type it joins). Its
  yes answers count as the target's from the next load.
- **Rename a type**: set `display_name`. `type_name` stays as the model was
  asked, which is what keeps the stored answers valid.
- Then `./wbg publish` (the stages write Postgres themselves; this is the catch-up)
  (or the sets the runs were made over). The load is keyed on the file, so
  it reloads `measure_findings` and `measure_types` and
  nothing else.

What does cost a call: a new type or a changed definition (`./wbg types
<set> --assign`, about US$0.02 per measure on the 70-project runs); a
changed asset definition (the asset map, about US$0.35 per asset);
a new measure (below).

## Adding a measure from the board

A measure is four small files and three stages, and the only part a person
must write is the definition. A board card for Hephaestus carries it:

```
Goal: the measure <id> ("<name>") is run over the digital set and loaded,
      with its types proposed for approval.
Why:  <the definition, three to six sentences: what counts, what does
      not; name the measure, never the digital asset, the asset map
      supplies that link>
      <six to ten search phrases, each a sentence a document might write>
      <optional: one or two whole-word patterns>
Done when: analysis/labels/<id>.csv, <id>_queries.csv, <id>_scale.csv
      (copied from cra_scale.csv with the name substituted) and, if given,
      <id>_patterns.csv exist following ews's shape; one command,
      ./wbg add <id>, completes with 0 failed requests (it runs the
      search, the funnel, the type proposals and the assignment, each
      writing Postgres as it finishes, and prints the time per set and
      overall); the summary
      quotes the measure report's "committed units"
      line, the spend, the path of the proposals
      (data/types/<id>-<version>/proposals.md) and the "everything ...s"
      line. Several measures go in one command: ./wbg add a b c.
Stop and ask if: the measure stage's estimate is above US$1; the search
      finds under 20 candidates.
```

Cost on the digital set: the search is embeddings only (cents); the
measure about US$0.15; the proposals about US$0.05. The types are then
approved by a person into `analysis/labels/<id>_types.csv` and assigned
with a second card (`./wbg types <id> --assign --set digital`, about
US$0.02), after which the measure appears in every dashboard filter.

## After the run

- Grade a training sheet per measure (`./wbg check <set>`, then
  `--evaluate`): it refits the probability layer and the gate. Only an
  evaluation sheet (`--design eval`) gives the precision and recall to quote.
- The elusion sheet (`./wbg check <set> --elusion`) when there is time.
- Copy the run note to the vault (AGENTS.md rule 8).

## New measures from the dashboard

`./wbg requests` runs what the New measure form queued in
`wbg_requests.measure_requests`, one request at a time, US$1 each and US$3 a
day at most (`src/measure_requests.py`; `--schema` creates the table, `--once`
runs one). Three things live in yggdrasil, not here, and are a card of their
own: a Postgres role that can only `INSERT` and `SELECT` on
`wbg_requests.measure_requests`; a second Metabase database connection using
that role, with model actions on (the existing connection stays read-only);
and a timer that runs `./wbg requests` (every 15 minutes is plenty). A
requested measure's files sit in `data/requested_labels/` until a person
copies them into `analysis/labels/` and deletes them there.

