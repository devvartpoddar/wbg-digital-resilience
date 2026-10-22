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
   | Asset map, 13 assets, cosine gate 0.25 | about US$1.60 | about US$6 | about US$32 |
   | Asset map without the gate | about US$2.70 | about US$10 | about US$55 |
   | One measure (pool plus feedback, two passes) | US$0.13 | about US$0.50 | about US$2.60 |

   At 250 projects the whole run fits well inside the key's cap of US$50
   with or without the gate; the gate is still the setting to use, since
   its loss was measured (section 5b) and the saving is real at every
   later run.

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
(section, sentence, component, paragraph where asked) and the exact question
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
| classify (asset map) | the gate, and every paragraph's request (model, section, passage, component, questions) | a paragraph's text or a question changes |
| measure | the search and asset-map run names, the settings (model, context, gate, top, min_cosine, rounds, projects, `commit_floor` if written), the patterns, the questions (scale, definitions, assets, kinds, facets), the calibration | a question or state changes, or new units enter |
| types | the measure run name, model, and every committed unit's request | a type list changes or new units are committed |
| checks | the measure run names, model, and every pair and scope request | new committed pairs |
| links | `LINK_VERSION`, model, `asset_top`, `MEASURE_TOP`, measure query texts, the bytes of `packages.csv`, the asset and pair questions, the measure run names, projects | new pairs or packages |

Code is in no run folder's name: changing `src/` alone does not make a model
run new (it does count in the prepare stages' fingerprints, below).

**Why a run is "new" with nothing re-asked.** The names chain: anything
that changes `sentence_index.csv` (re-embedding, re-cleaning, a component or
copy change in the unit metadata) gives a new search version, so new measure
folders, so new types, checks and links folders, all rebuilt from stored
answers. A written or changed `commit_floor`, any measure setting, a pattern,
the calibration file or the project scope does the same from the measure
stage down.

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
`similar_projects()` and `try_phrase()`. Their readings (link levels, conditional line, package-link threshold,
near-copy share) are one edit in `sql/views/13_dashboard_settings.sql`, and
the due-date rule one edit in `wbg.due_date()` (`03_procurement_links.sql`). Earlier cards read:

- `measure_findings_by_project`: one row per project and measure, with the
  assets linked, how delivered, by whom (the project list and map);
- `measure_findings_in_context`: every unit a measure read, with its grade,
  calibrated probability, asset, kind, actor, conditionality, type (the
  measure's cluster) and the text around it (filter `most_likely_grade = 3`);
- `measure_finding_lists`: one row per unit and asset, kind, actor or type,
  for dashboard filters on what the array columns hold;
- `packages_with_commitments`: one row per package with the assets it buys,
  the measures linked to it and how many commitments (at 0.5); the pairs
  themselves are `commitment_packages_in_context`, the package-asset answers
  `package_assets_in_context`;
- `asset_map`, `search_paragraphs_with_text`, `paragraphs_with_document`,
  `packages_with_project`, `upcoming_packages`, `notices_with_package`,
  `awards_with_package`, `data_as_of`.

Hide `loads`, `data_release` and `paragraph_projects` in Metabase's table
settings; they are bookkeeping. Every count in the findings is the decision
model's grade, not a person's: the graded sheet's precision and recall
(`data/reports/evaluate_units_<set>.md`) is the number to quote beside it.

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

- **A floor, `asset.floor`, `asset.decay`, `posterior.*`, `link.asset_yes`**: nothing. The
  stage reads the stored answers again under the new number: `./wbg run --from measure`
  (or `--from link`); `./wbg status` shows those runs as "would be new" until then.
- **`dashboard.*`**: nothing; `./wbg publish` rebuilds the views with the new value.
- **A gate (`asset.gate_cosine`, `measure.gate_cosine`) or the feedback settings**: the
  sentences newly let in are asked; the rest come from the store. Dry-run first.
- **`measure.second_pass_context`**: the facet questions are asked again.

Tuning the floors: after a run, `./wbg check all --seed <n> --evaluate` fits each
measure's probability layer (`data/labels/<set>_posterior.json`) and each asset's
(`data/labels/assets_posterior.json`) from the graded sentences, and writes into each
`data/reports/evaluate_units_<set>.md` the floor whose precision on those sentences
reaches `floor.target_precision`. Put the floors you accept into the panel, then re-run
the measures (from the store, free).

**First run after the change of 2026-10-22 (not yet run).** The sentence asset map is
new, so `./wbg classify assets --dry-run` first: it asks every sentence again, one choice
question each (about US$1 to 2 at the test rate, near the paragraph map's US$1.0 to 1.2). The measure questions are unchanged and come
from the store; the facet questions (conditional, who carries it out) are asked again (now on
the sentence). The delivery question (govern, build, sustain, use) is no longer asked.
Every floor is `floor.default` 0.5 until the evaluation suggests better ones, so the
published counts will move.

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

- Grade one check sheet per measure (`./wbg check <set>`, then
  `--evaluate`): that is the only precision and recall figure there is for
  the new cohort, and it refits the calibration.
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

