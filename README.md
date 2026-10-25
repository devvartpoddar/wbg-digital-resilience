# wbg-digital-resilience

Finds climate resilience commitments for digital infrastructure in World Bank appraisal documents, and links them to upcoming procurement.

```
./run.sh                                   # everything: fetch what is new, rebuild what changed, publish
./run.sh --update                          # the same, fetching every project again first

./wbg_portfolio run --to prepare --project P171528   # one project's documents and tables
./wbg_portfolio run --from classify --max-spend 5    # the asset map, the measures, the links, the publish; stop at US$5
./wbg_portfolio run --from search --measures cra     # a changed measure, from its search to Postgres
./wbg_portfolio run --dry-run --from classify        # estimates only; nothing asked, nothing written
./wbg_portfolio add cra                              # a new measure from its files (analysis/README.md)
./wbg_portfolio publish                              # every decision read again from the scores, then the views

./wbg_portfolio check draw --seed 3                  # grading tasks for a person or an agent (data/review/tasks_s3.jsonl)
./wbg_portfolio check fill --seed 3 --file fills_s3.jsonl --grader <who>   # read the grades into the store
./wbg_portfolio check evaluate                       # precision and recall of every decision kind; fit the layers and gates
./wbg_portfolio check import                         # once: the grades of the old file sheets into the store

./wbg_portfolio list    --region "South Asia"        # which cohort projects a selection covers
./wbg_portfolio summary --country KE                 # where each selected project stands
./wbg_portfolio status                               # which code and data release the store is at
./wbg_portfolio audit                                # the parse and procurement audits (data/reports/)
python -m pytest                           # tests (not on the box)
```

`./wbg_portfolio` finds the box's Python (`/opt/yggdrasil/venvs/work/bin/python`; `WBG_PY` overrides it). Every threshold is a line of `inputs/config/control_panel.csv`; no command-line option overrides one.

Selections filter the cohort (`inputs/config/cohort.csv`): `--set` (`digital` or `digital_contributor`), `--project`, `--country`, `--region` (part of the name is enough), `--fy` (2024, FY24 or FY2024), `--practice` and `--unit`, each repeatable or comma-separated, combined with "and".

Two stores of record: files under the main checkout's `data/` (what was fetched, parsed and embedded; never committed, laid out as `src/wbg_portfolio/paths.py` describes) and Postgres schema `wbg_store` (every model answer, grade, fit and payment). Everything else is derived: a `scores` table and the decision tables and views in schema `wbg`, which the Shiny app reads (`docs/method/postgres.md`, `docs/method/dashboards.md`).

The repository is two things. `src/wbg_portfolio/` is the pipeline (a Python package, `pyproject.toml`) and the functions the app reads through (`app.py`); it takes any label set by name and holds no measure's definition. `inputs/` and `analysis/` are what is searched for: `inputs/taxonomy/` the assets (changed rarely), `analysis/labels/` the measures and any set being tried (changed freely), `analysis/sql/` questions over Postgres. `analysis/README.md` says what goes where.

## On the box

From `/ygg/projects/wbg-digital-resilience`, the order in `docs/method/running.md` ("Clean run on the box"). The key lives in `.secrets/wbg-openrouter-key`, readable only by the account that runs the pipeline:

```
sudo mkdir -p .secrets && sudo chown svc-hermes .secrets && sudo chmod 700 .secrets
sudo install -m 600 -o svc-hermes /dev/stdin .secrets/wbg-openrouter-key    # paste the key, then Ctrl-D
```

An interrupted run restarts where it stopped: run the same command again. Reports go to the notes vault (AGENTS.md rule 8).

Start with `AGENTS.md`, then `docs/methodology.md`: an index to `docs/method/`, one file per stage.
