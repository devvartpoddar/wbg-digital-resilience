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
./wbg prepare                                    # fetch what is missing; read, clean, components, procurement
./wbg classify assets --min-cosine 0.25 --dry-run
./wbg classify assets --min-cosine 0.25          # the asset map (reads the store first; stops on a refusal)
./wbg search cra && ./wbg measure cra            # each measure: search, then the funnel
./wbg search ews && ./wbg measure ews
./wbg types cra --assign --set digital           # the approved types (analysis/labels/cra_types.csv) on the committed units
./wbg types ews --assign --set digital
python3 src/load_pg.py --set digital             # Postgres: tables, keys, comments, views; the digital set's runs
python3 src/run_report.py                        # the run note for the vault
```

`./wbg prepare` is the long step and can be left overnight; it writes
`data/reports/fetch_status/` so a project that failed is tried again next
time. Everything else is minutes to an hour. `./wbg status` says what is out
of date.

## What Metabase sees

Schema `wbg` only. Start cards from the views, not the tables:

- `measure_findings_by_project`: one row per project and measure, with the
  assets linked, how delivered, by whom (the project list and map);
- `measure_findings_in_context`: every unit a measure read, with its grade,
  calibrated probability, asset, kind, actor, conditionality, type (the
  measure's cluster) and the text around it (filter `most_likely_grade = 3`);
- `measure_finding_lists`: one row per unit and asset, kind, actor or type,
  for dashboard filters on what the array columns hold;
- `asset_map`, `search_paragraphs_with_text`, `paragraphs_with_document`,
  `packages_with_project`, `upcoming_packages`, `notices_with_package`,
  `awards_with_package`, `data_as_of`.

Hide `loads`, `data_release` and `paragraph_projects` in Metabase's table
settings; they are bookkeeping. Every count in the findings is the decision
model's grade, not a person's: the graded sheet's precision and recall
(`data/reports/evaluate_units_<set>.md`) is the number to quote beside it.

## Changing what the dashboards show, without a model call

A person decides the clusters and the grouping; the files carry the
decision and a reload applies it. None of these asks the model anything.

- **Merge two types**: in `analysis/labels/<set>_types.csv` set
  `merge_into` on the type to fold (to the id of the type it joins). Its
  yes answers count as the target's from the next load.
- **Rename a type**: set `display_name`. `type_name` stays as the model was
  asked, which is what keeps the stored answers valid.
- **Regroup the assets**: edit `inputs/taxonomy/assets_groups.csv`
  (`asset_id, group_id, group_name`). The views show the group beside the
  asset; the asset ids underneath do not move.
- Then `python3 src/load_pg.py --set digital --set digital_contributor`
  (or the sets the runs were made over). The load is keyed on the file, so
  it reloads `measure_findings`, `measure_types` and `label_groups` and
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
      <id>_patterns.csv exist following ews's shape; ./wbg search <id>,
      ./wbg measure <id> --set digital and ./wbg types <id> --set digital
      complete with 0 failed requests; src/load_pg.py --set digital
      completes; the summary quotes the measure report's "committed units"
      line, the spend, and the path of the proposals
      (data/types/<id>-<version>/proposals.md).
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
