---
name: wbg
description: Start here for any work on the wbg-digital-resilience repository (the World Bank digital resilience pipeline): how to learn the store's state in one command, where the method is written, how to answer "what is there on X" with a scratch measure, and what a run may spend. For Claude, Hephaestus or any agent working in this checkout.
---

# Working in wbg-digital-resilience

## First minute of every session

1. `./wbg status` prints the brief: code and data release, active measures, scores per kind, stored answers, grades, spend in all and this week, last publish, scratch measures tried, waiting requests, whether a key is present. Read it before anything else; do not re-derive it from `data/` or Postgres.
2. Read `AGENTS.md` (rules) and `docs/methodology.md` (an index). Open only the `docs/method/` file that covers the code you touch. `docs/method/history.md` is evidence, not instructions.
3. Nothing under `data/`, `inputs/`, `analysis/labels/` or `wbg_store` is written by hand or by a script you improvise. Code changes go through a branch and a pull request Dev merges.

## Answering "what is there on X?" (no measure exists for X)

Three tiers, cheapest first. Say which tier you used and what it cost.

| Tier | Command | Time, cost | Gives |
|---|---|---|---|
| 1. Phrase | `SELECT * FROM wbg.try_phrase('<phrase>', 50)` in Postgres or Metabase | seconds, one embedding | the 50 nearest sentences with project, country and the measures that already count them |
| 2. Scratch measure | `./wbg try <slug> --expand "<what to look for>" --max-spend 0.50` | minutes, cents | a definition, phrases and patterns written by one cheap generative call, then the real funnel: embeddings, gate, patterns, the decision model's grade per sentence, feedback rounds. Scores in `wbg.scores` kind `scratch`; the Explore dashboard and `wbg.scratch_findings` show them. The commitments pages do not move. |
| 3. Measure | three files in `analysis/labels/` by pull request, then `./wbg add <slug>` | an hour, up to US$1 | a real measure with types, pair checks, links and a place on every dashboard |

Tier 2 is the default when Dev asks to "check what is there on X". Write the definition yourself when he gives one (`--definition ... --phrase ... --phrase ...`); use `--expand` when he gives a topic. Re-running `./wbg try` with a sharper definition re-asks only the sentences whose question changed. `./wbg try --list` shows what has been tried. Report: sentences asked, sentences at the floor, projects, the five strongest sentences, the spend.

## Spend

Every stage that calls a model takes `--max-spend <US$>`; set it to what you expect (a run rebuilt from stored answers costs cents; a scratch measure under US$0.50; a new measure under US$1). Report the spend from the ledger (`wbg_store.spend`) afterwards. Never run the whole cohort to check a change: `--project <id>`. If a cap stops a run, say what it would have asked.

## What the pieces are

- `src/`: the pipeline (`./wbg run`: fetch > prepare > embed > search > classify > measure > link > publish). Holds no measure's definition.
- `inputs/taxonomy/`: the assets (changed rarely; a changed definition is a new question and invalidates grades under it: stop and ask).
- `analysis/labels/`: the measures, the shared grade scale, any set being tried. `analysis/sql/`: questions over Postgres.
- `sql/views/`: what Metabase reads. `inputs/config/control_panel.csv`: every threshold, one line each; never a number in code.
- Postgres `wbg_store`: the record (answers, grades, fits, spend), never dropped. `wbg`: the copy the dashboards read, rebuilt by `./wbg publish`.

## Never

- Print, log or commit a key. Delete anything under `data/`. Drop or truncate `wbg_store`. Put a staff name in a committed file. Write document text into git.
- Guess at an asset boundary, a measure definition, a threshold or whether a commitment counts: stop and ask.
