# analysis/

The part of this repository that changes often. `src/` is the pipeline: it fetches, cleans, embeds, scores, grades and loads, and it holds no measure's definition. This folder holds what is searched for and how the results are read, and it may change every day without touching the pipeline.

## What goes where

| Folder | Holds | Changed |
|---|---|---|
| `inputs/taxonomy/` | The pipeline's own label set: the nine assets, their phrases and grades. | Rarely, deliberately: a changed wording invalidates every grade a person gave under the old one. |
| `analysis/labels/` | Analysis label sets: the measures, and any set being tried (`<set>.csv`, `<set>_queries.csv`, `<set>_scale.csv`, same shape as the assets). | Freely. Each run is keyed on the set's exact wording, so a revised phrase embeds only itself and never disturbs another set's results. |
| `analysis/labels/` (optional, per set) | `<set>_patterns.csv` (whole-word patterns per label: the candidates stage takes every unit one matches) and `<set>_types.csv` (the measure's approved types: `type_id`, `type_name`, `definition`, written by a person from `./wbg types` proposals; a `pattern` column is optional and only tags candidates; `merge_into` folds a type into another and `display_name` renames it, both at load time with no model call). | Freely. |
| `inputs/taxonomy/assets_groups.csv` | The assets' reading groups (`asset_id`, `group_id`, `group_name`): how the dashboards group the 13 assets into about six. A presentation layer: the asset ids, their answers and the graded sheets are untouched, so regrouping is a file edit and a reload. | Freely. |
| `analysis/sql/` | Questions for Metabase and `psql`, over the `wbg` schema. One file per question, with a comment saying what it answers and what it does not. | Freely. Nothing in `src/` reads them. |

A label set's name is its file name without `.csv`, and the same name must not exist in both folders. Every command takes any set by name:

```
./wbg search cra               # score the sentences against analysis/labels/cra*.csv
./wbg measure cra              # the funnel: candidates, the asset map's columns, the decision model's grade
./wbg evaluate cra --column "Resilient telecom"   # the funnel against the hand-labelled activities workbook
./wbg types cra                # propose the measure's types from its committed units (read, then approve)
./wbg types cra --assign       # assign every committed unit to the approved types
./wbg candidates cra           # the candidates alone, with p_commitment, firmness, pattern types, reading order
./wbg words cra cra            # the words that set the measure's candidates apart
```

Changing what the dashboards show is rows too, and free: merge two types (`merge_into`), rename one (`display_name`), regroup the assets (`assets_groups.csv`), then `src/load_pg.py`; nothing is asked again. Dropping a type from the list or changing its definition re-asks the list (US$0.02 per measure on the 70-project runs), because the assignment request carries every question at once.

Adding a measure is rows, not code: a definition that names the measure and never the digital asset (the asset map supplies that link; the decision model reads a question literally), a few search phrases, one or two whole-word patterns (`\bunderground\b`, not `ducts?`, which also matches "conducted"), and, once the measure has been run, the approved types.

The load (`src/load_pg.py`) puts every set with a current run into the same Postgres tables, keyed by `label_set`, and the `labels` table says which folder each set came from.

## What stays out

- Nothing under `data/`: results live on the box and in Postgres.
- No cut-offs written into code beyond the named constants `docs/methodology.md` lists (the pool and link thresholds). A cut-off is chosen from `src/evaluate.py`'s or `./wbg check --evaluate`'s report against graded examples; a SQL question may filter on one, and should say which report it came from.
- No document text.
