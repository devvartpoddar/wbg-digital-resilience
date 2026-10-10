# wbg-digital-resilience

Finds climate resilience commitments for digital infrastructure in World Bank appraisal documents, and links them to upcoming procurement.

```
./run.sh                              # everything: fetch what is new, rebuild what changed, load Postgres, review sheets, run report
./run.sh --update                     # the same, fetching every project again first

./wbg list    --region "South Asia"   # which cohort projects a selection covers
./wbg prepare --project P171528       # fetch what is missing for a selection, rebuild what changed
./wbg summary --country KE            # where each selected project stands (plans, upcoming packages, notices, contracts)
./wbg export  --fy 2024 --tables      # write the selection's results to data/runs/<name>-<date>/
python -m pytest                      # tests (locally)
```

Selections filter the cohort (`inputs/config/cohort.csv`): `--project`, `--country`, `--region` (part of the name is enough) and `--fy`, each repeatable or comma-separated, combined with "and". The same functions are importable: `from wbg import select, prepare, summary, upcoming, open_notices, tables, export`.

Data goes to the main checkout's `data/` folder (never committed), laid out as `src/paths.py` describes. The cleaned tables also go to Postgres, schema `wbg`. The review sheets for reading the cleaning by eye are in `data/review/`.

Start with `AGENTS.md`, then `docs/methodology.md`, which lists every step the pipeline takes.
