# AGENTS.md

## What this is

The pipeline reads World Bank Project Appraisal Documents (PADs) and procurement records, finds which digital assets a project finances and which climate resilience measures it commits to for them, and links that to upcoming procurement, so an advisor knows which Task Team Leaders (TTLs) to talk to, and when. Anything outside the four questions at the top of `docs/methodology.md` does not belong here.

`src/` is the pipeline (`./wbg run`: fetch > prepare > embed > search > classify > measure > link > publish); it takes any label set by name and holds no measure's definition. `inputs/taxonomy/` holds the assets, `analysis/labels/` the measures, `analysis/sql/` questions over Postgres, `sql/views/` the views the dashboards read. Keep analysis assumptions out of `src/` and pipeline mechanics out of `analysis/`.

## Read little, spend little

Reading and model calls are what a task costs. So:

- Read `docs/methodology.md` (a short index), then **only** the `docs/method/` file for the code you change. `docs/method/history.md` is evidence, not instructions: open it only when a choice needs its numbers.
- Find code with a search, then read the lines around it. Do not read a whole source file over 300 lines, a whole method file you are not changing, or anything under `data/` beyond the rows you need.
- A measure is files, not code: adding or revising one needs no reading of `src/` (`docs/runbook-box.md`, "Adding a measure from the board").
- Before any stage that calls a model, run it with `--dry-run` and read the estimate. Stop and ask if it is above US$1, or if the stage would re-ask what is already stored. Never run the whole cohort to check a change: use `--project <id>`.
- Report in a few lines: what changed, what was run, the spend from `data/reports/spend.csv`.

## Where it runs

Everything lives in `/ygg/projects/wbg-digital-resilience/`; do not read or write elsewhere on the box. Board cards work in `.worktrees/<task-id>`. Data is always the main checkout's `data/` (`src/paths.py` resolves it; `WBG_DATA` overrides it for tests). Python: `/opt/yggdrasil/venvs/work/bin/python`; do not pip-install, say what is missing. Postgres: schema `wbg` in `postgresql:///work` (`WBG_PG` overrides it). Hardware: four slow cores, 15 GB, no graphics card; stream large files.

A smoke run is `./wbg run --to prepare --project <id>` then `./wbg summary --project <id>`. Every stage skips what is up to date and writes its own rows to Postgres.

## The principle

Every decision is **repeatable** (same text, same answer), **scored** (a probability with a chosen cut-off) and **checkable** (measured against examples a person checked). An external model may be used if its exact version is pinned and every response is stored on the box and re-read on re-runs.

## Rules

1. Never delete data to save space, and never treat a stage's input as disposable. If disk is a problem, say so.
2. No document text in git: tables carry identifiers, offsets and checksums. Test fixtures are invented.
3. `inputs/config/`, `inputs/taxonomy/` and `analysis/labels/` are kept by people: read, never written by code. Changing an asset definition invalidates every label under it: stop and ask. A set in `analysis/labels/` may be added or revised freely.
4. Secrets come from the environment or `.secrets/wbg-openrouter-key` (mode 0600). Never in a committed file, a command line or a log; if you see one anywhere else, stop, say so and recommend rotation.
5. Identifiers are deterministic: a re-run on unchanged input gives byte-identical ids and outputs.
6. Stages are resumable and skip unchanged work, keyed on content checksums.
7. The parse must be reproducible: pin tool versions and check `text_sha256` on re-parse.
8. Reports go to the notes vault, never the repository: run `src/run_report.py`, copy its note from `data/reports/` to `Projects/WBG Digital Resilience/Reports/` and add a line to that folder's `README.md`.
9. Labels are keyed by the SHA-256 of the unit's text plus the label set and question, never by position, and only evaluation reads them.

## Language in anything a person reads

Plain language; expand every abbreviation on first use. No claim beyond the data: documented is not done, nothing is causal, coverage is below 100 per cent. Never characterise a country, government or institution negatively. No staff names in any committed file.

## Done means

- `python -m pytest` passes (locally; the box's venv has no pytest).
- The change was run on real data (at least one project) and the output looked at.
- A re-run on unchanged input gives identical output; an interrupted run restarts without redoing finished work.
- Nothing under `data/` is staged.
- The `docs/method/` file covering the code says what it now does.

## When unsure

Say so and stop. Do not guess at an asset boundary, a measure definition, a threshold or whether a commitment counts: those are judgements for people. A task that halts with a clear question is a good outcome; a plausible table built on a guess is the worst one.
