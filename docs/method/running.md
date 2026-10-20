# Running the pipeline

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 0. Running it

One command walks the pipeline, and any stretch of it:

```
./wbg run                                  # fetch > prepare > embed > search > classify > measure > link > publish
./wbg run --from search --measures cra     # a changed measure, from its search to Postgres
./wbg run --only measure --measures a b c  # three measures through the funnel, side by side
./wbg run --from classify                  # the asset definitions changed: the map and everything after it
./wbg run --to prepare --project P180495   # one project's documents and tables
./wbg add cra                              # the same as run --from search --to measure --measures cra
```

| Stage | What it does | Parallel |
|---|---|---|
| fetch | the appraisal documents, procurement records and project records the selected projects lack (`stages.json` says which) | four projects at once |
| prepare | read the PDFs, clean, sentences, components, procurement tables; load the corpus tables into Postgres and cut a release when they changed | PDFs on every core |
| embed | the sentence vectors, only what the cache lacks | four requests in flight |
| search | every label set's phrases against the vectors: the assets and the measures | sets side by side (`--jobs`) |
| classify | the asset map: one graded question per asset per paragraph | ten requests in flight |
| measure | each measure through the funnel (section 5c), its types proposed and assigned (5d); Postgres written once for every set at the end of the stage | sets side by side; within a set the second-pass questions run behind the next round; the three type proposals side by side |
| link | which asset each package buys and which commitments it carries out (5e) | ten requests in flight |
| publish | every current run into Postgres; the stages do this themselves as they finish, this is the catch-up | |
| audit | the parse and procurement audits, only with `--to audit` or `--only audit` | |

Every stage skips what is up to date: the corpus stages by a fingerprint of their code and inputs (`data/reports/stages.json`), the model stages by a run folder keyed on their inputs (a reader finds the asset map's folder from a fingerprint of the corpus tables, the label files and the code, remembered in `data/classify/current/plans/`, rather than rebuilding all 40,000 requests to name it, which was seconds on each of about ten lookups per measure), and every model answer is stored once per model, state and question and read back on a re-run. So `--from` an early stage after a small change costs seconds for the stages nothing changed, and a change in a definition, a phrase, a pattern or a type list re-runs only what reads it. `--measures` names the sets (default: every set in `analysis/labels/`); the selection filters below name the projects (default: the whole included cohort, which every stage must be given alike, since a run is keyed on its scope).

**Selecting projects.** Filters on the included rows of `inputs/config/cohort.csv`: `--project`; `--country` (country code); `--region` (case-insensitive, part of the name is enough); `--fy` (approval fiscal year); `--practice` (Global Practice name or code, or part of one); `--unit` (managing unit or its code, or part of one); `--set` (`digital` or `digital_contributor`). Each takes several values; different filters combine with "and". A project outside the cohort is refused. `./wbg list` prints a selection, `./wbg summary` where each project stands, `./wbg export` writes it to `data/runs/`.
