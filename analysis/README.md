# analysis/

The part of this repository that changes often. `src/` is the pipeline: it fetches, cleans, embeds, scores and loads, and it does not know what a measure is. This folder holds what is searched for and how the results are read, and it may change every day without touching the pipeline.

## What goes where

| Folder | Holds | Changed |
|---|---|---|
| `inputs/taxonomy/` | The pipeline's own label set: the eight assets, their phrases and grades. | Rarely, deliberately: a changed wording invalidates every grade a person gave under the old one. |
| `analysis/labels/` | Analysis label sets: the measures, and any set being tried (`<set>.csv`, `<set>_queries.csv`, `<set>_scale.csv`, same shape as the assets). | Freely. Each run is keyed on the set's exact wording, so a revised phrase embeds only itself and never disturbs another set's results. |
| `analysis/labels/` (optional, per set) | `<set>_patterns.csv` (whole-word patterns per label: the candidates stage takes every unit one matches) and `<set>_types.csv` (the measure's approved types: `type_id`, `type_name`, `definition`, written by a person from `./wbg types` proposals; a `pattern` column is optional and only tags candidates). | Freely. |
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

Adding a measure is rows, not code: a definition that names the measure and never the digital asset (the asset map supplies that link; the decision model reads a question literally), a few search phrases, one or two whole-word patterns (`\bunderground\b`, not `ducts?`, which also matches "conducted"), and, once the measure has been run, the approved types.

The load (`src/load_pg.py`) puts every set with a current run into the same Postgres tables, keyed by `label_set`, and the `taxonomy` table says which folder each set came from.

## What stays out

- Nothing under `data/`: results live on the box and in Postgres.
- No cut-offs written into code. A cut-off is chosen from `src/evaluate.py`'s report against graded examples; a SQL question may filter on one, and should say which report it came from.
- No document text.
