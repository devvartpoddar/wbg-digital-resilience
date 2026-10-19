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
./wbg types cra                                  # proposals to read and approve into analysis/labels/cra_types.csv
python3 src/load_pg.py                           # Postgres: tables, keys, comments, views
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
  calibrated probability, asset, kind, actor, conditionality and the text
  around it (filter `most_likely_grade = 3`);
- `asset_map`, `search_paragraphs_with_text`, `paragraphs_with_document`,
  `packages_with_project`, `upcoming_packages`, `notices_with_package`,
  `awards_with_package`, `data_as_of`.

Hide `loads`, `data_release` and `paragraph_projects` in Metabase's table
settings; they are bookkeeping. Every count in the findings is the decision
model's grade, not a person's: the graded sheet's precision and recall
(`data/reports/evaluate_units_<set>.md`) is the number to quote beside it.

## After the run

- Grade one check sheet per measure (`./wbg check <set>`, then
  `--evaluate`): that is the only precision and recall figure there is for
  the new cohort, and it refits the calibration.
- The elusion sheet (`./wbg check <set> --elusion`) when there is time.
- Copy the run note to the vault (AGENTS.md rule 8).
