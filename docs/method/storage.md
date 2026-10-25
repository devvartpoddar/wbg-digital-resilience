# Storage

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 6. Storage

**Files**: `data/` in the main checkout (`/ygg/projects/wbg-digital-resilience/data/` on the box; `$WBG_DATA` overrides, for tests). Gitignored, never deleted. Every file has one home, named once in `src/wbg_portfolio/paths.py`:

- `raw/`: what was fetched, as published (`pdf/`, `pdf_text/`, `plans/`, `notices/`, `awards/`, `projects/`, `documents.csv`, `fetch_log.csv`);
- `appraisal/`: cleaned text (`text/`), per-document caches (`cache/`), `paragraphs.csv`, `sentences.csv`, `rejected.csv`, `components.csv`, `document_parts.csv`;
- `procurement/`: packages, notices and awards, raw and cleaned, and superseded package versions;
- `projects/projects.csv`;
- `embeddings/`: the content-addressed vector cache, `sentence_emb.npy` and `sentence_index.csv`;
- `search/`: one folder per label set and version of the cosines (section 5);
- `types/`: proposed type lists (`<set>_types.csv`, `<set>_oth_types.csv`);
- `review/`: the grading tasks and fills (`tasks_s<seed>.jsonl`, `fills_s<seed>.jsonl`, `spotcheck_s<seed>.xlsx`);
- `releases/`: one folder per data release with its manifest and a copy of the cleaned tables, and `index.csv`;
- `reports/`: every stage's report, the evaluation (`evaluate.md`), the run notes;
- `classify/answers/`, `labels/`, `measures/`, `links/`, `checks/`, `archive/`: what the file-based pipeline wrote before 2026-10-24. The answers are read once into `wbg_store.answers` as they are needed; the grades are imported by `./wbg_portfolio check import`. Nothing is deleted.

The record of every model answer, grade, fit and payment is Postgres (`docs/method/postgres.md`), not a file.

**How the tables join.** Every link is a column; nothing is joined on a guess.

| From | To | On |
|---|---|---|
| projects | cohort | `project_id` |
| documents, paragraphs | projects | `document_parts.csv`: which project each stretch of a document belongs to; a paragraph's `project_ids` are its part's |
| paragraphs, sentences | documents | `doc_id`; sentences also `paragraph_id` |
| paragraphs | components | `doc_id` and `component_number` / `subcomponent_number` |
| components | components | `name_key`: the same component in another document, even when a restructuring renumbered it |
| packages | projects, components | `project_id`; `component_doc_id` and `component_number` when matched by name, `component_number` alone when the plan printed only a number |
| notices, awards | packages | `package_id`, with `package_link` saying how |
| awards | notices | `notice_ids` |
| scores, decisions | search units, labels | `item_id` (a unit, a package, or `unit|package`), `label_set`, `label_id` |
| grades | search units, packages | the text's SHA-256 (`item_sha`), never a position: a re-parse moves unit ids |

**The cohort**: `inputs/config/cohort.csv`, hand-kept, in two sets named in its `cohort_set` column and carried into `projects.cohort_set` and every findings view: `digital` (operations whose object is a digital asset) and `digital_contributor` (where a digital asset contributes to another sector's object). `src/wbg_portfolio/cohort_build.py` lists the board-approved operations of a range of fiscal years from the projects interface into `data/reports/cohort_candidates_fy<from>-<to>.csv` for a person to choose from; it never writes the cohort.

**Analysis**: `analysis/labels/` (label sets that change often) and `analysis/sql/` (questions over the `wbg` schema) are in git, hand-kept, and read by nothing in `src/` except the label-set lookup.
