# Postgres: the record and the copy the app reads

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 4. The two schemas

Database `work` on the box (`$WBG_PG` overrides; `$WBG_SCHEMA` names the presentation schema, default `wbg`, and the record schema is `<schema>_store`).

**`wbg_store`, the record (`src/wbg_portfolio/db.py`), never dropped:**

| Table | One row per | Key |
|---|---|---|
| `answers` | model answer | model, the state's SHA-256, the question's SHA-256; holds the raw answer, the snapshot served, the cost share, the call and the time |
| `states`, `questions` | exact state or question sent | its SHA-256 (so an answer can be read back in words) |
| `grades` | grade a person or an agent gave | kind, label set, label, the item's text checksum, design (train, eval, elusion); holds the grade, grader, source, question hash, stratum, weight, rationale |
| `fits` | fitted layer or gate | kind, label set, label; holds the spec and the question it was fitted for |
| `spend` | step that cost money | time; stage, label set, model, calls, US$, seconds |

**`wbg`, the copy the dashboards read (`src/wbg_portfolio/load_pg.py`, `src/wbg_portfolio/decide.py`, `src/wbg_portfolio/schema.py`), rebuilt on load and publish:**

- *Corpus* (`./wbg_portfolio prepare`, when a file's checksum changed): `projects`, `documents`, `paragraphs` (text resolved from offsets), `components`, `search_units`, `search_paragraphs`, `packages`, `notices`, `awards`, `paragraph_projects`, `sentence_vectors` (`halfvec` with an HNSW index where pgvector 0.7 is installed), `data_release`, `loads`.
- *Scores* (the model stages): `scores`, one row per kind, label set, label and item, with the score, the raw answer, route, round, cosine and rank.
- *Decisions* (`./wbg_portfolio publish`): `measure_findings`, `paragraph_grades`, `unit_assets`, `unit_pair_checks`, `package_assets`, `commitment_packages`, `labels`, `measure_types`, `label_queries`, `label_patterns`, `label_scale`, `measure_quality` (the evaluation's headline figures).

**Names for people.** `src/wbg_portfolio/schema.py` renames on the way in: full words (`document_id`, `borrower_reference_normalised`), units in the name (`estimated_amount_usd`, `word_count`), booleans as `is_`, `in_`, `was_`, the grade probabilities as `probability_grade_0` to `_3`, the decision's probability as `posterior_probability`, the same name on both sides of every join. Pipe-joined lists are arrays; `*_date` columns are dates, `*_at` timestamps; a value that does not parse is NULL and counted. A primary key on every table; a comment on every table and every column a reader could misread.

**Loading.** Each table is replaced whole (`DROP`, `CREATE`, `COPY`) inside one transaction with a two-minute lock timeout, so a reader sees the old state or the new and a load behind a dashboard query says who holds the lock rather than hanging. The decision tables are created empty where missing, so every view builds before the first model run. `sql/views/*.sql` run in file order after every load or publish; a file that fails stops the load with its error (a view missing silently is worse). Files that need pgvector or the box's `openrouter()` function are skipped where those are absent.

**Views** (`sql/views/`): the corpus views (`packages_with_project`, `notices_with_package`, `awards_with_package`, `paragraphs_with_document`, `paragraph_grades_with_text`, `asset_map`, `search_paragraphs_with_text`, `measure_findings_in_context`, `data_as_of`), the function `similar_units(unit_id, k)`, and the app's views (section 5f). `analysis/sql/` holds questions written on the views.

**Automated checks** (`src/wbg_portfolio/audit.py`, `./wbg_portfolio audit`) count known parse and procurement defects (sections 2 and 3) into `data/reports/audit.txt` and `audit_procurement.txt`; each check has a gate in the tests set just above the measured rate, so a regression fails. **Sensitivity** (`./wbg_portfolio sensitivity`) reports how much the cleaned text depends on the paragraph, tag and sentence settings (`data/reports/sensitivity.md`); it re-reads PDFs and is slow.
