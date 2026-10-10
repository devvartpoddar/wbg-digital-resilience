# wbg-digital-resilience

Finds climate resilience commitments for digital infrastructure in World Bank appraisal documents, and links them to upcoming procurement.

```
./run.sh                              # everything: fetch what is new, rebuild what changed, load Postgres, review sheets, run report
./run.sh --update                     # the same, fetching every project again first

./wbg list    --region "South Asia"   # which cohort projects a selection covers
./wbg prepare --project P171528       # fetch what is missing for a selection, rebuild what changed
./wbg summary --country KE            # where each selected project stands (plans, upcoming packages, notices, contracts)
./wbg list    --practice digital      # by Global Practice; --unit IDD04 by managing unit
./wbg export  --fy 2024 --tables      # write the selection's results to data/runs/<name>-<date>/
./wbg sensitivity                     # how much the cleaned text depends on its paragraph, tag and sentence settings
python -m pytest                      # tests (locally)
```

Selections filter the cohort (`inputs/config/cohort.csv`): `--project`, `--country`, `--region` (part of the name is enough), `--fy`, `--practice` and `--unit`, each repeatable or comma-separated, combined with "and". The same functions are importable: `from wbg import select, prepare, summary, upcoming, open_notices, tables, export`.

Data goes to the main checkout's `data/` folder (never committed), laid out as `src/paths.py` describes. The cleaned tables also go to Postgres, schema `wbg`. The review sheets for reading the cleaning by eye are in `data/review/`.

## On the box

From `/ygg/projects/wbg-digital-resilience`, in this order:

```
git pull                      # main
./run.sh --update             # fetch everything again, rebuild every stage that changed, load Postgres,
                              # write review sheets and the run note (re-reads every PDF when the
                              # parser changed: hours)
./wbg sensitivity             # data/reports/sensitivity.md (re-reads 10 PDFs at 5 settings: slow)
```

Then copy the run note from `data/reports/` to `Projects/WBG Digital Resilience/Reports/` in the notes vault and add a line to that folder's `README.md`. An interrupted run restarts where it stopped: run the same command again.

Start with `AGENTS.md`, then `docs/methodology.md`, which lists every step the pipeline takes.
