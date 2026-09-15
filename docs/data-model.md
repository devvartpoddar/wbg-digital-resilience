# Digital for Resilience: data model

Tables, schemas, lineage and the version control boundary.

---

## 1. Commit boundary

| Path | Contents | In git |
|---|---|---|
| `data/raw/` | Fetched documents and interface responses, by source | No |
| `data/intermediate/` | Prepared spine, embeddings, detections, procurement, model coefficients | Partly — see below |
| `data/outputs/` | Commitments, menu, gaps, windows, cases | Yes |
| `data/scratch/` | Working files | No |
| `meta/` | Manifests, run register, gold set, metrics | Yes |
| `inputs/` | Taxonomy, term lists, config, labels | Yes |

Within `data/intermediate/`, embedding matrices (`*.npy`) are not committed. Their row index and manifest are.

**No document text is committed.** Paragraph and clause tables carry the spine — identifiers, offsets, checksums — and no text column. Text appears in exactly one committed place: the sample files under `inputs/labels/samples/`, so a draw is self-contained for hand-labelling without the corpus present.

Everything stays on the server. Gitignored does not mean deletable.

Committed tables approaching 20 MB are split by approval fiscal year. Compression is not used — a compressed file is not diffable.

No World Bank staff names appear in any committed file. Supplier organisation name and country are retained.

---

## 2. Identifiers

All identifiers are deterministic functions of content or position. A re-run on unchanged input produces byte-identical identifiers.

| Identifier | Form |
|---|---|
| `doc_id` | Document identifier from the documents interface |
| `project_id` | `P` plus six digits |
| `paragraph_id` | `{doc_id}:p{ordinal:05d}` |
| `clause_id` | `{paragraph_id}:c{ordinal:02d}:{splitter_version}` |
| `asset_id` | Three-letter uppercase code |
| `measure_id` | `MEA` plus three digits |
| `family_id` | `FAM` plus two digits |
| `package_id` | Borrower reference where present, else `{project_id}:pkg{n}` |
| `contract_id` | Contract identifier from the awards interface |
| `commitment_id` | `{clause_id}:{measure_id}:{asset_id}` |
| `sample_id` | `{purpose}-{YYYYMMDD}-{nn}` |
| `run_id` | `{YYYYMMDD}T{HHMM}-{short git sha}` |

Paragraph ordinals are assigned in document order after parsing and before filtering. Filtering does not renumber.

`clause_id` carries the splitter version because a splitter change moves clause boundaries. Without it, existing clause labels would silently repoint to different text.

Since paragraph text is not committed, `paragraph_id` stability depends on the parse being reproducible. `documents` records the parse tool, version and settings, and `paragraphs.text_sha256` is verified on every re-parse.

---

## 3. Interfaces

| Interface | Endpoint | Auth | Populates |
|---|---|---|---|
| Documents | `search.worldbank.org/api/v3/wds` | None | Appraisal documents, procurement plans |
| Projects | `search.worldbank.org/api/v2/projects` | None | Cohort metadata |
| Solicitation notices | `search.worldbank.org/api/v2/procnotices` | None | `notices` |
| Contract awards | `search.worldbank.org/api/contractdata` | None | `awards` |

**Projects interface: use version 2.** Version 3 drops fields without warning.

### 3.1 Contract awards fields

`projectid`, `project_name`, `contr_id`, `contr_desc`, `contr_refnum`, `contr_sgn_date`, `total_contr_amnt`, `procurement_group`, `procurement_group_desc`, `procu_meth_text`, `suppinfo` (supplier name, country, per-supplier amount), `contr_no_obj_dat`, `sector`, `rvw_type`, `regionname`, `teammemfullname`.

`teammemfullname` is read and discarded at preparation. It is never written to a committed file.

Record count grows continuously. Do not quote it from a document; query it.

Descriptions arrive in English, French, Spanish and Portuguese.

### 3.2 Solicitation notices fields

`id`, `url`, `notice_type`, `publication_date`, `project_id`, `bid_description`, `procurement_category`, `deadline_date`, `country_code`, `country_name`, `sector`.

---

## 4. Inputs

Hand-maintained. No script writes here, except `sample.py` to `labels/samples/` and `validate.py` to `measure_families.member_count`.

### `inputs/taxonomy/assets.csv`

| Column | Type |
|---|---|
| `asset_id` | string, primary key |
| `asset_name` | string |
| `definition` | string |
| `active` | boolean |
| `version` | string |

### `inputs/taxonomy/measures.csv`

| Column | Type |
|---|---|
| `measure_id` | string, primary key |
| `measure_name` | string |
| `definition` | string |
| `family_id` | string, foreign key |
| `applies_to` | string, pipe-delimited `asset_id` list |
| `status` | enum |
| `version` | string |
| `superseded_by` | string, nullable |

### `inputs/taxonomy/measure_families.csv`

| Column | Type |
|---|---|
| `family_id` | string, primary key |
| `family_name` | string |
| `grouping_rationale` | string |
| `member_count` | integer, derived |

### `inputs/terms/terms_asset.csv`

| Column | Type |
|---|---|
| `term_id` | string, primary key |
| `asset_id` | string, foreign key |
| `term` | string |
| `match_mode` | enum |
| `lang` | string, ISO 639-1 |
| `active` | boolean |
| `added_run_id` | string, nullable |

### `inputs/terms/terms_measure.csv`

Same shape, plus:

| Column | Type |
|---|---|
| `measure_id` | string, foreign key |
| `role` | enum |

### `inputs/terms/modality_lexicon.csv`

| Column | Type |
|---|---|
| `lemma` | string, primary key |
| `class` | enum |
| `rule_ref` | integer, 1–8 |

### `inputs/config/cohort.csv`

| Column | Type |
|---|---|
| `project_id` | string, primary key |
| `country_code` | string, ISO 3166-1 alpha-3 |
| `region` | string |
| `approval_fy` | integer |
| `practice` | string |
| `included` | boolean |
| `exclusion_reason` | string, nullable |
| `cohort_version` | string |

### `inputs/config/thresholds.csv`

| Column | Type |
|---|---|
| `model_id` | string, primary key |
| `threshold` | float |
| `set_on_sample` | string, foreign key |
| `target_precision` | float |
| `observed_recall` | float |
| `set_by` | string |
| `set_at` | date |
| `threshold_version` | string |

### `inputs/labels/samples.csv`

| Column | Type |
|---|---|
| `sample_id` | string, primary key |
| `purpose` | enum |
| `unit_type` | enum |
| `drawn_at` | timestamp |
| `stratification` | string |
| `seed` | integer |
| `n_drawn` | integer |
| `source_run_id` | string |

### `inputs/labels/samples/{sample_id}.csv`

The drawn units, with text. The only committed table carrying document text.

| Column | Type |
|---|---|
| `sample_id` | string |
| `unit_id` | string |
| `unit_type` | enum |
| `stratum` | string |
| `text` | string |
| `text_sha256` | string |

### `inputs/labels/paragraph_labels.csv`

| Column | Type |
|---|---|
| `paragraph_id` | string, composite key with `asset_id` |
| `asset_id` | string |
| `label` | boolean |
| `labeller` | string |
| `labelled_at` | date |
| `sample_id` | string, foreign key |

### `inputs/labels/clause_labels.csv`

| Column | Type |
|---|---|
| `clause_id` | string, composite key with `family_id` |
| `family_id` | string |
| `measure_id` | string, nullable |
| `asset_id` | string, nullable |
| `modality` | enum |
| `labeller` | string |
| `labelled_at` | date |
| `sample_id` | string, foreign key |

Purpose is carried on the sample, not the label row. A unit appears in exactly one sample.

---

## 5. Meta

### `meta/document_manifest.csv`

| Column | Type |
|---|---|
| `doc_id` | string, primary key |
| `project_id` | string, nullable |
| `doc_type` | enum |
| `source_url` | string |
| `content_sha256` | string |
| `bytes` | integer |
| `fetched_at` | timestamp |
| `api_source` | string |
| `disclosure_date` | date |

### `meta/api_fetch_log.csv`

| Column | Type |
|---|---|
| `fetch_id` | string, primary key |
| `source` | string |
| `endpoint` | string |
| `query_params` | string |
| `fetched_at` | timestamp |
| `record_count` | integer |
| `response_sha256` | string |

### `meta/runs.csv`

| Column | Type |
|---|---|
| `run_id` | string, primary key |
| `started_at` | timestamp |
| `finished_at` | timestamp, nullable |
| `git_sha` | string |
| `ruleset_version` | string |
| `threshold_version` | string |
| `model_versions` | string, JSON |
| `cohort_version` | string |
| `input_row_counts` | string, JSON |
| `output_row_counts` | string, JSON |
| `status` | enum |

### `meta/gold_set.csv`

| Column | Type |
|---|---|
| `gold_id` | string, primary key |
| `unit_type` | enum |
| `unit_id` | string |
| `stage` | integer, 1–9 |
| `expected_value` | string |
| `verified_by` | string |
| `verified_at` | date |
| `gold_version` | string |

### `meta/metrics.csv`

| Column | Type |
|---|---|
| `run_id` | string |
| `gold_version` | string |
| `stage` | integer, 1–9 |
| `scope` | string |
| `metric` | enum |
| `value` | float |
| `n` | integer, required |

---

## 6. Raw

Gitignored. One directory per source.

```
data/raw/
├── documents/     # {doc_id}.pdf and .txt
├── projects/      # project metadata responses
├── plans/         # procurement plan responses
├── notices/       # solicitation notice responses
└── awards/        # contract award responses
```

Each fetch writes a row to `meta/api_fetch_log.csv` and, for documents, `meta/document_manifest.csv`.

---

## 7. Intermediate — prepared

### `data/intermediate/prepared/documents.csv`

| Column | Type |
|---|---|
| `doc_id` | string, primary key |
| `doc_type` | enum |
| `title` | string |
| `disclosure_date` | date |
| `page_count` | integer |
| `parse_tool` | string |
| `parse_version` | string |
| `parse_settings` | string, JSON |
| `clean_version` | string |
| `content_sha256` | string |
| `section_ii_found` | boolean |
| `pages_with_header_removed` | integer |
| `paragraphs_dropped` | integer |

### `data/intermediate/prepared/span_project.csv`

| Column | Type |
|---|---|
| `doc_id` | string |
| `project_id` | string |
| `link_basis` | enum |

### `data/intermediate/prepared/paragraphs.csv`

No text column.

| Column | Type |
|---|---|
| `paragraph_id` | string, primary key |
| `doc_id` | string, foreign key |
| `ordinal` | integer |
| `section_label` | string |
| `in_scope` | boolean |
| `block_type` | enum |
| `char_start` | integer |
| `char_end` | integer |
| `raw_text_sha256` | string |
| `text_sha256` | string, of cleaned text |
| `clean_version` | string |
| `token_count` | integer |

### `data/intermediate/prepared/rejected_units.csv`

Everything dropped at cleaning, with a reason. Counted, never silent.

| Column | Type |
|---|---|
| `unit_id` | string, primary key |
| `unit_type` | enum |
| `doc_id` | string, nullable |
| `drop_reason` | enum |
| `raw_text_sha256` | string |
| `clean_version` | string |
| `run_id` | string |

### `data/intermediate/prepared/clauses.csv`

No text column.

| Column | Type |
|---|---|
| `clause_id` | string, primary key |
| `paragraph_id` | string, foreign key |
| `ordinal` | integer |
| `char_start` | integer |
| `char_end` | integer |
| `text_sha256` | string |
| `clean_version` | string |
| `split_rule` | enum |
| `splitter_version` | string |
| `parser_version` | string |

---

## 8. Intermediate — features and models

`data/intermediate/features/paragraph_emb.npy`, `clause_emb.npy` — gitignored.

### `data/intermediate/features/{unit}_emb_index.csv`

| Column | Type |
|---|---|
| `row` | integer, primary key |
| `unit_id` | string |
| `text_sha256` | string |

### `data/intermediate/features/embedding_manifest.json`

| Field | Notes |
|---|---|
| `model_name` | |
| `model_revision` | Pinned. External providers change models without notice |
| `provider` | `local` or the service name |
| `dimensions` | integer |
| `normalised` | boolean |
| `rows` | integer |
| `input_table` | path |
| `input_sha256` | string |
| `created_at` | timestamp |
| `run_id` | string |

### `data/intermediate/models/*.csv`

Classifier coefficients as text, one file per model, with a training manifest beside them. Never pickles.

---

## 9. Intermediate — detections

### `data/intermediate/detect/paragraph_asset.csv`

All eight rows written per paragraph, including those below threshold.

| Column | Type |
|---|---|
| `paragraph_id` | string, composite key with `asset_id` |
| `asset_id` | string |
| `probability` | float, 0–1 |
| `threshold` | float |
| `fired` | boolean |
| `model_version` | string |
| `threshold_version` | string |
| `run_id` | string |

### `data/intermediate/detect/paragraph_asset_spans.csv`

| Column | Type |
|---|---|
| `paragraph_id` | string |
| `asset_id` | string |
| `char_start` | integer |
| `char_end` | integer |
| `locator_method` | enum |
| `term_id` | string, nullable |
| `confidence` | float, nullable |

### `data/intermediate/detect/detector_disagreement.csv`

| Column | Type |
|---|---|
| `paragraph_id` | string |
| `asset_id` | string |
| `kind` | enum |

### `data/intermediate/detect/clause_measure.csv`

| Column | Type |
|---|---|
| `clause_id` | string, composite key with `measure_id` |
| `family_id` | string |
| `family_probability` | float |
| `measure_id` | string, nullable |
| `resolution` | enum |
| `trigger_char_start` | integer, nullable |
| `trigger_char_end` | integer, nullable |
| `locator_method` | enum |
| `qualifier_present` | boolean |
| `qualifier_term_id` | string, nullable |
| `model_version` | string |
| `ruleset_version` | string |
| `run_id` | string |

### `data/intermediate/detect/attribution.csv`

| Column | Type |
|---|---|
| `clause_id` | string, composite key with `measure_id` |
| `measure_id` | string |
| `asset_id` | string, `UNATTRIBUTED` permitted |
| `rule_fired` | enum |
| `tier` | enum |
| `competing_asset_count` | integer |
| `path_length` | integer, nullable |
| `crossed_boundary` | boolean |
| `parser_version` | string |
| `ruleset_version` | string |
| `run_id` | string |

### `data/intermediate/detect/modality.csv`

| Column | Type |
|---|---|
| `clause_id` | string, composite key with `measure_id` |
| `measure_id` | string |
| `modality` | enum |
| `sentence_shape` | enum |
| `governing_token` | string |
| `governing_verb_lemma` | string |
| `rule_fired` | integer, 1–8 |
| `source` | enum |
| `classifier_probability` | float, nullable |
| `negation_flag` | boolean |
| `ruleset_version` | string |
| `run_id` | string |

### `data/intermediate/detect/term_candidates.csv`

| Column | Type |
|---|---|
| `candidate_id` | string, primary key |
| `head_phrase` | string |
| `family_id` | string |
| `corpus_frequency` | integer |
| `example_clause_ids` | string, pipe-delimited, max five |
| `review_status` | enum |
| `decided_measure_id` | string, nullable |
| `decided_by` | string, nullable |
| `decided_at` | date, nullable |
| `first_seen_run_id` | string |

---

## 10. Intermediate — procurement

### `data/intermediate/procurement/packages.csv`

| Column | Type |
|---|---|
| `package_id` | string, primary key |
| `project_id` | string |
| `borrower_ref` | string, nullable, as published |
| `borrower_ref_norm` | string, nullable |
| `description` | string, as published |
| `description_clean` | string |
| `description_sha256` | string, of cleaned text |
| `description_lang` | string |
| `lot` | string, nullable |
| `phase` | string, nullable |
| `is_rebid` | boolean |
| `is_placeholder` | boolean |
| `superseded_by` | string, nullable |
| `clean_version` | string |
| `category` | enum |
| `method` | enum |
| `status` | enum |
| `status_raw` | string |
| `planned_date` | date, nullable |
| `revised_date` | date, nullable |
| `estimated_amount` | float, nullable |
| `currency` | string |
| `actual_amount` | float, nullable |
| `plan_version` | string |
| `fetched_at` | timestamp |

Package and award descriptions are short metadata fields, not document text, and are committed. Both the published string and the cleaned string are kept — the first for display, the second for matching.

`actual_amount` is unreliable and frequently zero for signed packages. Signed amounts come from `awards`.

### `data/intermediate/procurement/notices.csv`

| Column | Type |
|---|---|
| `notice_id` | string, primary key |
| `project_id` | string |
| `notice_type` | string |
| `publication_date` | date |
| `deadline_date` | date, nullable |
| `bid_description` | string, as published |
| `bid_description_clean` | string |
| `description_lang` | string |
| `is_placeholder` | boolean |
| `clean_version` | string |
| `procurement_category` | enum |
| `country_code` | string |
| `sector` | string |
| `url` | string |

### `data/intermediate/procurement/awards.csv`

| Column | Type |
|---|---|
| `contract_id` | string, primary key |
| `project_id` | string |
| `borrower_ref` | string, nullable, as published |
| `borrower_ref_norm` | string, nullable |
| `description` | string, as published |
| `description_clean` | string |
| `description_lang` | string |
| `clean_version` | string |
| `signed_date` | date |
| `no_objection_date` | date, nullable |
| `total_amount` | float |
| `currency` | string |
| `procurement_group` | enum |
| `method` | enum |
| `review_type` | enum |
| `supplier_name` | string |
| `supplier_country` | string |
| `supplier_amount` | float, nullable |
| `region` | string |

### `data/intermediate/procurement/package_asset.csv`

| Column | Type |
|---|---|
| `package_id` | string, composite key with `asset_id` |
| `asset_id` | string |
| `is_measure_package` | boolean |
| `match_source` | enum |
| `term_id` | string, nullable |
| `lang_matched` | string |
| `confidence` | float, nullable |
| `label_quality` | enum |
| `ruleset_version` | string |
| `run_id` | string |

### `data/intermediate/procurement/package_award_link.csv`

| Column | Type |
|---|---|
| `package_id` | string, composite key with `contract_id` |
| `contract_id` | string |
| `join_method` | enum |
| `confidence` | float |
| `verified_by` | string, nullable |

---

## 11. Outputs

### `data/outputs/commitments.csv`

No text column. Evidence is resolved from the corpus at read time via `clause_id`.

| Column | Type |
|---|---|
| `commitment_id` | string, primary key |
| `project_id` | string |
| `asset_id` | string |
| `measure_id` | string |
| `modality` | enum |
| `clause_id` | string |
| `paragraph_id` | string |
| `doc_id` | string |
| `attribution_tier` | enum |
| `attribution_rule` | enum |
| `modality_source` | enum |
| `ruleset_version` | string |
| `run_id` | string |

### `data/outputs/unattributed.csv`

Same schema, `asset_id` fixed to `UNATTRIBUTED`.

### `data/outputs/project_asset_coverage.csv`

| Column | Type |
|---|---|
| `project_id` | string, composite key with `asset_id` |
| `asset_id` | string |
| `measures_present` | string, pipe-delimited |
| `measures_absent` | string, pipe-delimited |
| `firm_count` | integer |
| `aspiration_count` | integer |
| `already_done_count` | integer |
| `package_count` | integer |
| `uncontracted_amount` | float |

### `data/outputs/gap_same_span.csv` and `gap_whole_project.csv`

| Column | Type |
|---|---|
| `project_id` | string |
| `asset_id` | string |
| `measure_id` | string |
| `present` | boolean |
| `scope` | enum |

### `data/outputs/menu.csv`

| Column | Type |
|---|---|
| `project_id` | string |
| `asset_id` | string |
| `package_definition_id` | string |
| `measures_present` | string, pipe-delimited |
| `measures_to_complete` | string, pipe-delimited |
| `distance_to_complete` | integer |
| `cost_band` | string |

### `data/outputs/intervention_window.csv`

| Column | Type |
|---|---|
| `project_id` | string |
| `asset_id` | string |
| `package_id` | string |
| `commitment_ids` | string, pipe-delimited |
| `planned_date` | date, nullable |
| `deadline_date` | date, nullable |
| `days_remaining` | integer, nullable |
| `status` | enum |
| `amount` | float, nullable |

`days_remaining` is computed at run time and is stale the following day.

### `data/outputs/learning_cases.csv`

| Column | Type |
|---|---|
| `project_id` | string |
| `asset_id` | string |
| `commitment_id` | string |
| `contract_id` | string |
| `signed_date` | date |
| `amount` | float |
| `supplier_country` | string |
| `selection_reason` | string |

---

## 12. Lineage

| Output | Built from | Rebuilt when |
|---|---|---|
| `document_manifest` | Documents interface | New fetch |
| `documents`, `paragraphs`, `rejected_units` | Raw documents | Manifest checksum or `clean_version` changes |
| `packages`, `notices`, `awards` | Raw interface responses | New fetch or `clean_version` changes |
| `paragraph_emb` | `paragraphs` | Paragraph text hash or embedding model changes |
| `paragraph_asset` | `paragraph_emb` + classifiers | Model or threshold version changes |
| `paragraph_asset_spans` | Corpus text + locator | Locator method or term list changes |
| `clauses`, `clause_emb` | Paragraphs where an asset fired | Paragraph text hash or splitter version changes |
| `clause_measure` | `clause_emb` + family classifiers + locator | Locator, family or model changes |
| `attribution` | `clause_measure` + `paragraph_asset_spans` + parse | Ruleset or parser version changes |
| `modality` | `clause_measure` + parse + classifier | Ruleset or classifier changes |
| `package_asset` | `packages`, `notices` + locator | Description hash changes |
| `package_award_link` | `packages` + `awards` | Either side changes |
| `commitments` | `attribution` + `modality` | Any upstream change |
| `menu`, gaps, `intervention_window`, `learning_cases` | `commitments` + `package_asset` | Every run |

Nothing is edited in place. A correction is a new run with a bumped version.

---

## 13. Repository layout

```
wbg-digital-resilience/
├── AGENTS.md
├── README.md
├── LICENSE
├── .gitignore
├── docs/
│   ├── methodology.md
│   ├── data-model.md
│   └── business-rules.md
├── inputs/
│   ├── taxonomy/        # assets, measures, measure_families
│   ├── terms/           # terms_asset, terms_measure, modality_lexicon
│   ├── config/          # cohort, thresholds
│   └── labels/          # samples.csv, samples/, paragraph_labels, clause_labels
├── meta/                # manifests, runs, gold_set, metrics
├── src/
│   ├── 01_fetch.py
│   ├── 02_prepare.py
│   ├── 03_detect_assets.py
│   ├── 04_segment.py
│   ├── 05_detect_measures.py
│   ├── 06_attribute.py
│   ├── 07_modality.py
│   ├── 08_procurement.py
│   ├── 09_join.py
│   ├── sample.py        # on demand
│   └── validate.py      # on demand
├── analysis/            # named investigations, script and result table
├── data/
│   ├── raw/             # gitignored, by source
│   ├── intermediate/    # prepared, features, models, detect, procurement
│   ├── outputs/
│   └── scratch/         # gitignored
└── tests/
```

`analysis/` holds V1 to V9 and any other one-off investigation. Working files go to `data/scratch/`.

### `.gitignore`

```
data/raw/
data/scratch/
data/intermediate/features/*.npy
data/intermediate/features/*.bin
*.pdf
*.pkl
__pycache__/
.venv/
```
