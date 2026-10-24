# analysis/

The part of this repository that changes often. `src/` is the pipeline: it fetches, cleans, embeds, scores, asks and publishes, and it holds no measure's definition. This folder holds what is searched for and how the results are read, and it may change every day without touching the pipeline.

## What goes where

| Folder | Holds | Changed |
|---|---|---|
| `inputs/taxonomy/` | The pipeline's own label set: the assets, their phrases and patterns. | Rarely, deliberately: a changed wording is a new question, and every grade a person gave under the old one is a grade of another question. |
| `analysis/labels/` | Analysis label sets: the measures, and any set being tried (`<set>.csv`, `<set>_queries.csv`, same shape as the assets), and the shared grade scale `scale.csv` (a `<set>_scale.csv` beside a set overrides it). | Freely. An answer is stored under the exact question sent, so a revised definition re-asks only that set's sentences, and a revised phrase embeds only itself. |
| `analysis/labels/` (optional, per set) | `<set>_patterns.csv` (whole-word patterns per label: the funnel asks every unit one matches) and `<set>_types.csv` (the measure's approved types: `type_id`, `type_name`, `definition`, written by a person from the proposals in `data/types/`; `merge_into` folds a type into another and `display_name` renames it, both at publish with no model call). | Freely. |
| `analysis/sql/` | Questions for Metabase and `psql`, over the `wbg` schema. One file per question, with a comment saying what it answers and what it does not. | Freely. Nothing in `src/` reads them. |

A label set's name is its file name without `.csv`, and the same name must not exist in both folders. Every command takes any set by name:

```
./wbg add cra                                # a new or revised measure: search, funnel, types, pair checks, publish
./wbg run --only measure --measures cra      # the funnel again for one set
./wbg publish                                # the decision tables and views from the stored scores
```

Changing what the dashboards show is rows too, and free: merge two types (`merge_into`), rename one (`display_name`), then `./wbg publish`; nothing is asked again. Dropping a type or changing its definition re-asks that measure's types.

Adding a measure is rows, not code (`docs/method/running.md`, "Adding a measure"): a definition that names the measure and never the digital asset (the asset map supplies that link; the decision model reads a question literally), a few search phrases, one or two whole-word patterns (`\bunderground\b`, not `ducts?`, which also matches "conducted"), and, once the measure has been run, the approved types.

The publish puts every set into the same Postgres tables, keyed by `label_set`, and the `labels` table says which folder each set came from.

## What stays out

- Nothing under `data/`: results live on the box and in Postgres.
- No cut-offs in code: every threshold is a line of `inputs/config/control_panel.csv`. A cut-off is chosen from `./wbg check evaluate`'s report against graded examples; a SQL question may filter on one, and should say which report it came from.
- No document text.
