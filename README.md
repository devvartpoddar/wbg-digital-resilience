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
./wbg status                          # which code and which data release the store is at; what is out of date
./wbg release                         # record the current data as a release (the pipeline does this itself)
./wbg sensitivity                     # how much the cleaned text depends on its paragraph, tag and sentence settings
./wbg search assets                   # embed the units (only what is not cached) and score them against
                                      # every asset definition and phrase; --dry-run shows the cost
./wbg search cra                      # the same for any label set in analysis/labels/ (see analysis/README.md)
./wbg classify assets                 # the asset map: grade every paragraph against each asset with the
                                      # decision model (only what is not stored); --dry-run shows the cost;
                                      # --min-cosine 0.3 asks only where the search sees the asset
./wbg measure cra                     # a measure through the funnel: candidates by cosine, the decision
                                      # model's grade, the asset and activity kind per unit, feedback rounds
./wbg evaluate cra --column "Adaptation" --text-regex "risk assessment"   # the funnel against the
                                      # hand-labelled activities workbook (data/labels/activities.xlsx)
./wbg check cra                       # draw a measure's units for a person to grade; --evaluate reads
                                      # the grades back: precision, recall, calibration; --elusion
                                      # draws (and evaluates) what the pool never read
./wbg types cra                       # propose the measure's types from its committed units; --assign
                                      # puts every committed unit to the approved list
./wbg words cra cra                   # the words that set the measure's candidates apart
$PY src/check_sheet.py assets         # a sheet of paragraphs for a person to grade the asset map
$PY src/evaluate.py assets            # store the grades as labels; precision and recall by cut-off
python -m pytest                      # tests (locally)
```

`$PY` is the box's Python, `/opt/yggdrasil/venvs/work/bin/python` (`./wbg` finds it on its own). Review sheets already written are kept, since they may hold notes; `--seed` draws a fresh one.

Selections filter the cohort (`inputs/config/cohort.csv`): `--project`, `--country`, `--region` (part of the name is enough), `--fy` (2024, FY24 or FY2024), `--practice` and `--unit`, each repeatable or comma-separated, combined with "and". The same functions are importable: `from wbg import select, prepare, summary, upcoming, open_notices, tables, export`.

Data goes to the main checkout's `data/` folder (never committed), laid out as `src/paths.py` describes. The cleaned tables and the model runs also go to Postgres, schema `wbg`, named, typed, keyed and commented for Metabase (`src/schema.py` (`docs/methodology.md`, section 4). The review sheets for reading the cleaning by eye are in `data/review/`.

The repository is two things. `src/` is the pipeline: fetch, clean, embed, score, grade, load; it takes any label set by name and holds no measure's definition. `inputs/` and `analysis/` are what is searched for: `inputs/taxonomy/` the assets (changed rarely), `analysis/labels/` the measures and any set being tried (changed freely), `analysis/sql/` questions for Metabase. `analysis/README.md` says what goes where.

## On the box

From `/ygg/projects/wbg-digital-resilience`, in this order:

```
git pull                      # main
./run.sh --update             # fetch everything again, rebuild every stage that changed, load Postgres,
                              # write review sheets and the run note (re-reads every PDF when the
                              # parser changed: hours)
./wbg sensitivity             # data/reports/sensitivity.md (re-reads 10 PDFs at 5 settings: slow)
```

The embedding key lives in `.secrets/wbg-openrouter-key` in this folder, readable only by the account that runs the pipeline:

```
sudo mkdir -p .secrets && sudo chown svc-hermes .secrets && sudo chmod 700 .secrets
sudo install -m 600 -o svc-hermes /dev/stdin .secrets/wbg-openrouter-key    # paste the key, then Ctrl-D
```

Then copy the run note from `data/reports/` to `Projects/WBG Digital Resilience/Reports/` in the notes vault and add a line to that folder's `README.md`. An interrupted run restarts where it stopped: run the same command again.

Start with `AGENTS.md`, then `docs/methodology.md`, which lists every step the pipeline takes.
