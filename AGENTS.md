# AGENTS.md

## What this is

The pipeline reads World Bank Project Appraisal Documents (PADs) and procurement records, finds which digital assets a project finances and which climate resilience measures it commits to for them, and links that to upcoming procurement, so an advisor knows which Task Team Leaders (TTLs) to talk to, and when. Anything outside the four questions at the top of `docs/methodology.md` does not belong here.

`src/` is the pipeline (`./wbg run`: fetch > prepare > embed > search > classify > measure > link > publish); it takes any label set by name and holds no measure's definition. `inputs/taxonomy/` holds the assets, `analysis/labels/` the measures, `analysis/sql/` questions over Postgres, `sql/views/` the views the dashboards read. Keep analysis assumptions out of `src/` and pipeline mechanics out of `analysis/`.

## Read little, spend little

Reading and model calls are what a task costs. So:

- Read `docs/methodology.md` (a short index), then **only** the `docs/method/` file for the code you change. `docs/method/history.md` is evidence, not instructions: open it only when a choice needs its numbers.
- Find code with a search, then read the lines around it. Do not read a whole source file over 300 lines, a whole method file you are not changing, or anything under `data/` beyond the rows you need.
- Every threshold is one line of `inputs/config/control_panel.csv` (read by `src/panel.py`; a missing line fails loudly); never put a number a person tunes back into code.
- A measure is files, not code: adding or revising one needs no reading of `src/` (`docs/method/running.md`, "Adding a measure").
- What a change costs and why a stage asks again: `docs/method/running.md`, "What a change costs". An answer is stored under the model, the exact state and the exact question; a stage asks only what the store lacks. Do not re-derive this from `src/`.
- Run any stage that calls a model with `--max-spend <US$>`, set to what you expect it to cost (a run rebuilt from stored answers: a few cents). The run reads the ledger before each step and stops before any request that would pass the cap; `--dry-run` prints the estimates without running. Report the spend afterwards. If the cap stops a run, report what it would ask; stop and ask if it would re-ask what is already stored (a whole measure or the whole asset map). Never run the whole cohort to check a change: use `--project <id>`.
- Report in a few lines: what changed, what was run, the spend (`wbg_store.spend` in Postgres).

## Where it runs

Everything lives in `/ygg/projects/wbg-digital-resilience/`; do not read or write elsewhere on the box. **Code is edited in the card's worktree, `.worktrees/<task-id>`, never in the main checkout; data is always the main checkout's `data/`**, whichever checkout runs (`src/paths.py` resolves it; `WBG_DATA` overrides it for tests). `STREAM.md` in the main checkout is untracked on purpose: it is never a reason to call a checkout dirty. Python: `/opt/yggdrasil/venvs/work/bin/python`; do not pip-install, say what is missing. It has psycopg 3 (`import psycopg`, not psycopg2), pdfplumber, pypdfium2, pikepdf, pgvector, numpy, pandas, openpyxl, xlsxwriter, matplotlib, python-docx and requests; it has no pytest. Postgres: schema `wbg` (the copy the dashboards read, rebuilt on publish) and `wbg_store` (the record: answers, grades, fits, spend; never dropped) in `postgresql:///work` (`WBG_PG` overrides it). Hardware: four slow cores, 15 GB, no graphics card; stream large files.

A smoke run is `./wbg run --to prepare --project <id>` then `./wbg summary --project <id>`. Every stage skips what is up to date.

## The principle

Every decision is **repeatable** (same text, same answer), **scored** (a probability with a chosen cut-off) and **checkable** (measured against examples a person checked). An external model may be used if its exact version is pinned and every response is stored on the box and re-read on re-runs.

## Rules

1. Never delete data to save space, and never treat a stage's input as disposable. Never drop or truncate `wbg_store`. If disk is a problem, say so.
2. No document text in git: tables carry identifiers, offsets and checksums. Test fixtures are invented.
3. `inputs/config/`, `inputs/taxonomy/` and `analysis/labels/` are kept by people: read, never written by code. Changing an asset definition invalidates every grade under it: stop and ask. A set in `analysis/labels/` may be added or revised freely. These files change only through a pull request that a card asks for and a person merges; the pipeline never writes them.
4. Secrets come from the environment or `.secrets/wbg-openrouter-key` (mode 0600). Never in a committed file, a command line or a log; if you see one anywhere else, stop, say so and recommend rotation.
5. Identifiers are deterministic: a re-run on unchanged input gives byte-identical ids and outputs.
6. Stages are resumable and skip unchanged work, keyed on content checksums.
7. The parse must be reproducible: pin tool versions and check `text_sha256` on re-parse.
8. Reports go to the notes vault, never the repository: run `src/run_report.py`, copy its note from `data/reports/` to `Projects/WBG Digital Resilience/Reports/` and add a line to the "Report index" table in `Projects/WBG Digital Resilience/README.md` (there is no README inside `Reports/`).
9. Grades are keyed by the SHA-256 of the item's text plus the label set and label, never by position; the question's hash is kept as provenance. Only the evaluation reads them.

## Language in anything a person reads

Plain language; expand every abbreviation on first use. No claim beyond the data: documented is not done, nothing is causal, coverage is below 100 per cent. Never characterise a country, government or institution negatively. No staff names in any committed file.

## Done means

- On the box there is no pytest: check a change with `python -m py_compile` on the files touched, then a real run on one project with `--max-spend`. `python -m pytest` passes where pytest is installed (a laptop, a cloud session); it is not run on the box.
- The change was run on real data (at least one project) and the output looked at.
- A re-run on unchanged input gives identical output; an interrupted run restarts without redoing finished work.
- Nothing under `data/` is staged.
- The `docs/method/` file covering the code says what it now does.

## When unsure

Say so and stop. Do not guess at an asset boundary, a measure definition, a threshold or whether a commitment counts: those are judgements for people. A task that halts with a clear question is a good outcome; a plausible table built on a guess is the worst one.
