# Running the pipeline

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 0. Running it

One command walks the pipeline, and any stretch of it:

```
./wbg run                                  # fetch > prepare > embed > search > classify > measure > link > publish
./wbg run --from search --measures cra     # a changed measure, from its search to Postgres
./wbg run --only measure --measures a b c  # three measures through the funnel, together
./wbg run --from classify                  # the asset definitions changed: the map and everything after it
./wbg run --to prepare --project P180495   # one project's documents and tables
./wbg add cra                              # the same as run --from search --to measure --measures cra
```

| Stage | What it does | Parallel |
|---|---|---|
| fetch | the appraisal documents, procurement records and project records the selected projects lack (`stages.json` says which) | four projects at once |
| prepare | read the PDFs, clean, sentences, components, procurement tables; load the corpus tables into Postgres and cut a release when they changed | PDFs on every core |
| embed | the sentence vectors, only what the cache lacks | four requests in flight |
| search | every label set's phrases against the vectors: the assets and the measures; writes nothing itself (a publish rebuilds every view, so the stage leaves the write to the measure and publish stages) | sets side by side (`--jobs`) |
| classify | the asset map: one graded question per asset per sentence, where the gate lets it through | ten requests in flight |
| measure | each measure through the funnel (section 5c), its types assigned by one fit question per type with OTH types for the rest (5d; proposals only where no person wrote a list), then the cross-measure pair checks over every measure (5c, `src/commit_checks.py`); Postgres written once for every set at the end of the stage | one process for every set (`src/measure_stage.py`, 2026-10-23): the sentence index, paragraphs, texts and answer store read once; each round of every measure is one ask (`--workers` times `--jobs` requests at once) and one ranking pass over the vectors for every measure's feedback query; each measure's run is byte for byte what it would be alone. It was three processes a set (36 for twelve sets), each reading the corpus again |
| link | which asset each package buys and which commitments it carries out (5e) | ten requests in flight |
| publish | every current run into Postgres; the stages do this themselves as they finish, this is the catch-up | |
| audit | the parse and procurement audits, only with `--to audit` or `--only audit` | |

Every stage skips what is up to date: the corpus stages by a fingerprint of their code and inputs (`data/reports/stages.json`), the model stages by a run folder keyed on their inputs (a reader finds the asset map's folder from a fingerprint of the corpus tables, the label files and the code, remembered in `data/classify/current/plans/`, rather than rebuilding all 40,000 requests to name it, which was seconds on each of about ten lookups per measure), and every model answer is stored once per model, state and question and read back on a re-run. **Spend.** `--max-spend X` caps what the whole run may spend on models: every stage's answer store subtracts what the ledger records since the run began and stops before a request that would pass the cap, keeping what it stored (the search stage's sets, run side by side, can each spend what is left, so the cap can be passed by up to one batch per job). With it, no dry run is needed; `--dry-run` skips fetch and prepare (they call no model) and prints the model stages' estimates against the corpus as it stands. A dry run writes nothing anywhere: no scores, and no publish (it used to publish a run already scored for the same version, taking a lock on every view while claiming to change nothing). So `--from` an early stage after a small change costs seconds for the stages nothing changed, and a change in a definition, a phrase, a pattern or a type list re-runs only what reads it. A dry run writes nothing, so a step that keys on a measure run the funnel has not written at that version (a measure's type assignment, its package links) has no estimate to give: it says so and the run carries on, and the cross-measure checks estimate nothing until the funnel has run. The asset map's estimate is gated on the search run, so a dry run that starts before search has scored the new index reports none for it either and the stages after it still get theirs; a real run keeps the error. `--measures` names the sets (default: every set in `analysis/labels/`); the selection filters below name the projects (default: the whole included cohort, which every stage must be given alike, since a run is keyed on its scope).

**No option overrides a threshold.** Every gate, floor, depth and feedback setting is read from the control panel (`inputs/config/control_panel.csv`); the stage commands take a selection, `--dry-run` and the model, nothing else that moves a number.

**Selecting projects.** Filters on the included rows of `inputs/config/cohort.csv`: `--project`; `--country` (country code); `--region` (case-insensitive, part of the name is enough); `--fy` (approval fiscal year); `--practice` (Global Practice name or code, or part of one); `--unit` (managing unit or its code, or part of one); `--set` (`digital` or `digital_contributor`). Each takes several values; different filters combine with "and". A project outside the cohort is refused. `./wbg list` prints a selection, `./wbg summary` where each project stands, `./wbg export` writes it to `data/runs/`.
