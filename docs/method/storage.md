# Storage

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 6. Storage

- **Files**: `data/` in the main checkout (`/ygg/projects/wbg-digital-resilience/data/` on the box). Gitignored, never deleted. Every file has one home, defined once in `src/paths.py`:
  - `raw/`: what was fetched, as published (`pdf/`, `pdf_text/`, `plans/`, `notices/`, `awards/`, `projects/`, `documents.csv`, `fetch_log.csv`, and the old `text/` renditions);
  - `appraisal/`: cleaned text (`text/`), per-document caches (`cache/`), `paragraphs.csv`, `sentences.csv`, `rejected.csv`, `components.csv`, `document_parts.csv`;
  - `procurement/`: packages, notices and awards, raw and cleaned, and superseded package versions;
  - `projects/`: `projects.csv`;
  - `embeddings/`: the vector cache, `sentence_emb.npy` and `sentence_index.csv`;
  - `search/`: one folder per label set and version (section 5);
  - `measures/`: a measure's graded pool, one folder per label set and version (section 5c);
  - `types/`: stored generative-model proposals and the decision model's type assignments, one folder per label set and version (section 5d);
  - `links/`: which asset each package buys and which commitments it would carry out, one folder per version (section 5e);
  - `classify/`: every decision-model answer, one file per model, state and question (`answers/`), and one folder per label set and version for the asset map (section 5b);
  - `prior/`: what the prior inputs read (section 5g): the corpus word frequencies they weigh words by (`word_df-<key>.json`, one per state of the sentence index);
  - `labels/`: what a person graded and what was fitted on it: `<set>_units.csv` (a measure's graded units, with stratum and design weight), `package_links.csv` (graded links), `measure_quality.csv` (each evaluation's headline figures), `<set>_posterior.json` and `assets_posterior.json` (the probability layers), `<set>_gate.json` and `assets_gate.json` (the skip rule's gates), `activities.xlsx` (the hand-labelled workbook of activities, never in git). All but the workbook are also in Postgres (section 4);
  - `requested_labels/`: the files of measures requested from the dashboard (section 5f), read as a third label folder;
  - `archive/`: tables an earlier version wrote and nothing reads (`document_projects.csv`, `package_changes.csv`), moved there by `src/paths.py`, never deleted;
  - `releases/`: one folder per data release with its manifest and a copy of the cleaned tables, and `index.csv`;
  - `reports/`: every stage's report, the run notes, and `spend.csv`, one line per model run with calls, cost and seconds (a run that failed part-way is recorded too, with `run` = `failed`, since its answers were paid for; the calls column is a whole number, and the reader also accepts the float a link run wrote before 2026-10-20, since the ledger is never rewritten);
  - `review/`: the grading workbooks and their key files (each row's stratum and the stratum's size);
  - `runs/`: exports for a selection, one folder per selection and date (`./wbg export`).

  `python src/paths.py` moves an older layout into this one. It only moves files, never overwrites, and does nothing the second time; `./wbg prepare` and `run.sh` run it first.
- **How the tables join.** Every link is a column; nothing is joined on a guess.

  | From | To | On |
  |---|---|---|
  | projects | cohort | `project_id` |
  | documents, paragraphs | projects | `document_parts.csv`: which project each stretch of a document belongs to (section 2.5); a paragraph's `project_ids` are its part's |
  | paragraphs, sentences | documents | `doc_id`; sentences also `paragraph_id` |
  | paragraphs | components | `doc_id` and `component_number` / `subcomponent_number` (the component the paragraph sits under) |
  | components | documents | `doc_id`; the latest document's list is the project's current one |
  | components | components | `name_key`: the same component in another document, even when a restructuring renumbered it |
  | packages | projects | `project_id` |
  | packages | components | `component_doc_id` and `component_number` when matched by name (the document whose list gave the number); `component_number` alone when the plan printed only a number |
  | superseded package versions | packages | `package_id` |
  | notices, awards | packages | `package_id`, with `package_link` saying how (step 19) |
  | package_assets | packages, labels | `package_id`; `label_id` (the asset), with the decision model's probability (section 5e) |
  | commitment_packages | measure_findings, packages | `label_set`, `label_id`, `unit_id`; `package_id`; many to many, with the probability (section 5e) |
  | awards | notices | `notice_ids`: the notices issued under the same reference (and, where both are linked, the same package) |
  | notices, awards | projects | `project_id` |
  | search units, search hits | paragraphs, documents, components | `paragraph_id`, `doc_id`, `project_ids`, `component_key` (section 5) |

  So a paragraph about a component, the packages that buy it, the notices that tendered them and the contracts that were signed can be read together.
- **Postgres**: schema `wbg` in database `work`, a queryable copy of the files under the names `src/schema.py` gives them (section 4): the join columns below are `document_id`, `award_id` and so on there.
- **Embeddings**: the vectors live in the content-addressed cache and `sentence_emb.npy`; search reads them from there (section 5). The load copies them into Postgres as `halfvec(3072)` with an HNSW index on cosine distance (section 4); the cache stays the record, and the matrix is rewritten only when a run embeds something new.
- **The cohort**: `inputs/config/cohort.csv`, hand-kept, in two sets named in its `cohort_set` column and carried into `projects.cohort_set` and every findings view: `digital` (operations whose object is a digital asset: the first 70 plus two added on 2026-10-19) and `digital_contributor` (179 operations, added the same day, where a digital asset contributes to another sector's object). Every report filters or splits on it (`./wbg --set digital_contributor`). Of the 251 included rows, 113 have no approval year because the projects interface had no record for them when added; `./wbg prepare` fills what the interface knows and reports the rest. `src/cohort_build.py` lists the board-approved operations of a range of approval fiscal years and lending instruments from the projects interface into `data/reports/cohort_candidates_fy<from>-<to>.csv`, in the cohort's columns, for a person to choose from; it never writes the cohort. `docs/runbook-box.md` is the order of steps, the time and the spend for a run over a new cohort.
- **Analysis**: `analysis/labels/` (label sets that change often) and `analysis/sql/` (questions over the `wbg` schema) are in git, hand-kept, and read by nothing in `src/` except the label-set lookup.
