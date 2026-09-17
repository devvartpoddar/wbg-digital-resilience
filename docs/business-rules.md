# Digital for Resilience: business rules

Two parts.

**Part A** is data constraints — permitted values, null rules, keys, cross-field rules. `src/validate.py` checks these against the tables in the data model.

**Part B** is the detection rule sets for attribution and modality. These produce values rather than constrain them, so they are not machine-checkable. They are here because they are reference detail, not methodology.

---

# Part A — Data constraints

## A1. Enumerated values

### A1.1 `asset_id`

| Value | Meaning |
|---|---|
| `FIB` | Terrestrial fiber optic network |
| `MOB` | Mobile network and towers |
| `SUB` | Submarine cable and landing stations |
| `DAT` | Data centres and hosting |
| `CYB` | Cybersecurity systems |
| `DPI` | Digital public infrastructure — identity, payments, data exchange |
| `DRM` | Disaster risk management and early warning systems |
| `ACC` | Last-mile access and connectivity |

Closed set. A ninth class requires a new classifier and a version bump.

### A1.2 `measures.status`

| Value | Meaning |
|---|---|
| `draft` | Defined but not in use. Detection does not run for it |
| `active` | In use |
| `retired` | No longer detected. Row stays in the file; `superseded_by` is set |

Changing a measure's `definition` invalidates every label drawn under the old definition. Such a change requires a `version` bump; a split or merge additionally requires the old row to be `retired` with `superseded_by` set, and the affected label rows re-drawn. Adding a new measure has no such cost.

### A1.3 `terms_*.match_mode`

| Value | Meaning |
|---|---|
| `lemma` | Match on dictionary form |
| `exact` | Match surface form as written |
| `regex` | Python regular expression |

### A1.4 `terms_measure.role`

| Value | Meaning |
|---|---|
| `trigger` | Names the action |
| `qualifier` | Makes the action resilience-relevant |

### A1.5 `modality_lexicon.class`

| Value | Consumed by rule |
|---|---|
| `enabling` | 1 |
| `modal_firm` | 2 |
| `modal_soft` | 3 |
| `requiring` | 4 |

### A1.6 `samples.purpose`

| Value | Meaning |
|---|---|
| `train` | Fits model coefficients |
| `calibrate` | Sets thresholds |
| `gold` | Scores the system |

### A1.7 `unit_type`

`paragraph` · `clause`

### A1.8 `doc_type`

| Value | Meaning |
|---|---|
| `pad` | Project Appraisal Document |
| `procurement_plan` | Procurement plan |
| `other` | Fetched and retained, not used in detection |

### A1.9 `span_project.link_basis`

| Value | Meaning |
|---|---|
| `document_metadata` | Link asserted by the source interface |
| `manual` | Link asserted by a person |

### A1.10 `paragraphs.block_type`

| Value | Meaning |
|---|---|
| `prose` | Running text |
| `heading` | Section or subsection title |
| `bullet` | Item in a bulleted or numbered list |
| `table_cell` | Text extracted from a table cell |
| `footnote` | Footnote or endnote text |

### A1.11 `clauses.split_rule`

| Value | Boundary |
|---|---|
| `sentence` | Sentence boundary, no further split applied |
| `coord_conj` | Coordinating conjunction |
| `subord_conj` | Subordinating conjunction |
| `relative` | Relative pronoun |
| `semicolon` | Semicolon |
| `verb_subtree` | Verb-headed subtree boundary |

### A1.12 `rejected_units.drop_reason`

| Value | Corpus | Meaning |
|---|---|---|
| `boilerplate_annex` | documents | Standard annex, disclaimer or signature block |
| `acronym_glossary` | documents | Acronym or abbreviation list |
| `running_header` | documents | Repeated header or footer text |
| `page_number` | documents | Page number line |
| `empty_after_clean` | both | Nothing left once normalisation ran |
| `too_short` | documents | Below the minimum token count and typed as heading or fragment |
| `duplicate_package` | procurement | Superseded by a later plan version; `superseded_by` is set |

Procurement rows are never dropped for being short or non-descriptive. A placeholder description is flagged via `is_placeholder`, not removed.

### A1.13 `detector_disagreement.kind`

| Value | Meaning |
|---|---|
| `classifier_only` | Classifier fired, no span located |
| `span_only` | Span located, classifier did not fire |

### A1.14 `locator_method`

| Value | Meaning |
|---|---|
| `term_list` | Matched against a maintained term list |
| `span_model` | Token-classification model |
| `head_extract` | Noun phrase from the parse, classified |

### A1.15 `clause_measure.resolution`

| Value | Meaning |
|---|---|
| `family_and_trigger` | Family fired and a trigger span located |
| `locator_only` | Trigger span located, family classifier did not fire |
| `family_unresolved` | Family fired, no trigger span located |

### A1.16 `attribution.rule_fired` and its tier

| Value | Tier |
|---|---|
| `single_asset_paragraph` | high |
| `pattern_1_amod_compound` | high |
| `pattern_2_nsubj` | high |
| `pattern_3_dobj` | high |
| `pattern_4_prep_pobj` | medium |
| `pattern_5_compound_poss` | medium |
| `pattern_6_nominal_prep` | medium |
| `generic_path` | medium |
| `adjacent_clause_single_asset` | low |
| `paragraph_fallback_single_asset` | low |
| `none` | none |

Fixed mapping. `tier` is derived from `rule_fired` and is never set independently.

### A1.17 `attribution.tier`

`high` · `medium` · `low` · `none`

### A1.18 `modality`

| Value | Meaning |
|---|---|
| `firm` | Commits to the measure |
| `aspiration` | Aims at, considers, or may do the measure |
| `already_done` | Reports the measure as complete |
| `flagged_negation` | Negation on the dependency path; not classified |
| `unclear` | Classifier below confidence, or rules conflicted |

### A1.19 `modality.sentence_shape`

| Value | Meaning |
|---|---|
| `predicate` | Measure has its own verb; modality read from it |
| `modifier` | Measure is a noun-phrase modifier; modality inherited from the governing verb |

### A1.20 `modality.source`

`rule` · `classifier`

### A1.21 `package_asset.match_source`

`term_list` · `classifier`

### A1.22 `term_candidates.review_status`

`new` · `accepted` · `rejected` · `deferred`

### A1.23 Procurement category

Applies to `packages.category`, `awards.procurement_group`, `notices.procurement_category`. Source values are normalised to these four at preparation.

`goods` · `works` · `consultant_services` · `non_consulting_services`

### A1.24 `packages.status` and `intervention_window.status`

| Value | Meaning |
|---|---|
| `planned` | In the plan, no process started |
| `under_preparation` | Specification or bidding documents in progress |
| `under_execution` | Process live — advertised, under evaluation, or awarded but unsigned |
| `signed` | Contract signed |
| `cancelled` | Withdrawn from the plan |
| `unknown` | Source value did not map |

Source values vary across plan versions and are normalised at preparation. Unmapped values go to `unknown` and are counted, never silently assigned.

`Terminated` and its French `Résilié` are deliberately absent from the mapping and so land in `unknown` — 1,209 rows of the eight-country corpus. The contract was signed and then ended early, and neither `signed` nor `cancelled` is true of it. `status_raw` keeps the borrower's own word. Whether the set gains a sixth value is an open decision.

### A1.25 `method`

| Value |
|---|
| `open_international` |
| `open_national` |
| `request_for_quotations` |
| `direct_selection` |
| `consultant_qualification` |
| `quality_cost_based` |
| `least_cost` |
| `other` |

### A1.26 `awards.review_type`

`prior` · `post`

### A1.27 `package_asset.label_quality`

| Value | Meaning |
|---|---|
| `weak` | Produced by term matching without validation |
| `verified` | Hand-checked |

### A1.28 `package_award_link.join_method`

| Value | Meaning |
|---|---|
| `refnum_exact` | Borrower references match character for character |
| `refnum_normalised` | Match after case folding, whitespace and separator normalisation |
| `fuzzy` | Matched on description, amount and date |
| `manual` | Asserted by a person |

### A1.29 `gap_*.scope`

| Value | Meaning |
|---|---|
| `same_span` | Asset and measure co-occur in one paragraph |
| `whole_project` | Both appear anywhere in the project |

### A1.30 `runs.status`

`complete` · `partial` · `failed`

### A1.31 `metrics.metric`

`precision` · `recall` · `f1` · `accuracy` · `unattributed_rate` · `locator_coverage`

---

## A2. Null rules

| Column | Null permitted when |
|---|---|
| `cohort.exclusion_reason` | `included` is true. Required otherwise |
| `document_manifest.project_id` | Document serves several projects; links live in `span_project` |
| `clause_measure.measure_id` | `resolution` is `family_unresolved`. Required otherwise |
| `clause_measure.trigger_char_start`, `trigger_char_end` | `resolution` is `family_unresolved`. Required otherwise |
| `clause_measure.qualifier_term_id` | `qualifier_present` is false. Required otherwise |
| `attribution.path_length` | `rule_fired` is not `generic_path` |
| `modality.classifier_probability` | `source` is `rule`. Required when `classifier` |
| `package_asset.confidence` | `match_source` is `term_list`. Required when `classifier` |
| `package_asset.term_id` | `match_source` is `classifier`. Required when `term_list` |
| `packages.borrower_ref`, `awards.borrower_ref` | Absent in the source |
| `packages.borrower_ref_norm`, `awards.borrower_ref_norm` | The published reference is null |
| `packages.lot`, `phase` | No lot or phase marker in the description |
| `packages.superseded_by` | The row is the latest plan version |
| `rejected_units.doc_id` | The dropped unit is a procurement row |
| `package_award_link.verified_by` | Not yet hand-checked |
| `term_candidates.decided_measure_id`, `decided_by`, `decided_at` | `review_status` is `new`, `rejected` or `deferred`. Required when `accepted` |
| `measures.superseded_by` | `status` is not `retired`. Required when `retired` |
| `intervention_window.days_remaining` | No date on the package |
| `packages.planned_date`, `revised_date`, `estimated_amount`, `actual_amount` | Absent in the source |

Never null: any primary key, any `run_id`, any version stamp including `clean_version`, `metrics.n`, `attribution.asset_id` (use `UNATTRIBUTED`), `attribution.rule_fired` (use `none`), `samples/{sample_id}.text`, `rejected_units.drop_reason`, `packages.description_clean`, `awards.description_clean`.

---

## A3. Keys

### A3.1 Primary keys

| Table | Key |
|---|---|
| `assets`, `measures`, `measure_families`, `cohort` | Single identifier column |
| `terms_asset`, `terms_measure` | `term_id` |
| `thresholds` | `model_id` |
| `modality_lexicon` | `lemma` |
| `samples` | `sample_id` |
| `documents`, `document_manifest` | `doc_id` |
| `paragraphs` | `paragraph_id` |
| `clauses` | `clause_id` |
| `paragraph_asset` | `paragraph_id` + `asset_id` |
| `clause_measure`, `attribution`, `modality` | `clause_id` + `measure_id` |
| `paragraph_labels` | `paragraph_id` + `asset_id` |
| `clause_labels` | `clause_id` + `family_id` |
| `packages` | `package_id` |
| `notices` | `notice_id` |
| `awards` | `contract_id` |
| `package_asset` | `package_id` + `asset_id` |
| `package_award_link` | `package_id` + `contract_id` |
| `commitments` | `commitment_id` |
| `runs` | `run_id` |
| `gold_set` | `gold_id` |

`attribution` and `modality` key on `clause_id` + `measure_id` without `asset_id`: one measure occurrence resolves to at most one asset.

### A3.2 Foreign keys — must resolve

| Column | References |
|---|---|
| `measures.family_id` | `measure_families.family_id` |
| Every `asset_id` in any table | `assets.asset_id` |
| Every `measure_id` in any table | `measures.measure_id` |
| Every `project_id` in any table | `cohort.project_id` |
| Every `run_id` in any table | `runs.run_id` |
| `thresholds.set_on_sample` | `samples.sample_id` |
| `paragraphs.doc_id`, `span_project.doc_id` | `documents.doc_id` |
| `clauses.paragraph_id` | `paragraphs.paragraph_id` |
| `paragraph_asset.paragraph_id`, `paragraph_asset_spans.paragraph_id` | `paragraphs.paragraph_id` |
| `clause_measure.clause_id`, `attribution.clause_id`, `modality.clause_id` | `clauses.clause_id` |
| `paragraph_labels.sample_id`, `clause_labels.sample_id` | `samples.sample_id` |
| `package_asset.package_id`, `package_award_link.package_id` | `packages.package_id` |
| `package_award_link.contract_id` | `awards.contract_id` |
| `commitments.clause_id`, `.paragraph_id`, `.doc_id` | Respective tables |

`attribution.asset_id` resolves to `assets.asset_id` or equals `UNATTRIBUTED`.

---

## A4. Cross-field rules

### A4.1 Reference data

1. `measures.applies_to` contains only values present in `assets.asset_id`.
2. `measure_families.member_count` equals the count of `measures` rows with that `family_id` and `status` of `active`.
3. Every `active` measure has at least one `terms_measure` row with `role` of `trigger`.
4. `thresholds` has exactly one row per asset class and one per active family.
5. `thresholds.set_on_sample` references a sample whose `purpose` is `calibrate`.

### A4.2 Samples and labels

6. A `unit_id` appears in at most one sample file. Training, calibration and gold draws are disjoint.
7. `samples.n_drawn` equals the row count of its sample file.
8. `paragraph_labels` rows reference a sample whose `unit_type` is `paragraph`; `clause_labels` rows, `clause`.
9. Every paragraph in a paragraph sample has exactly eight label rows, one per asset class.
10. `clause_labels` rows exist only for clauses whose paragraph has at least one `paragraph_asset` row with `fired` true.
11. `samples/{sample_id}.text_sha256` matches the corresponding `paragraphs` or `clauses` row.

### A4.3 Cleaning

12. Every unit present in the raw parse appears exactly once: in `paragraphs`, or in `rejected_units` with a reason. The two are disjoint and their union is complete.
13. `rejected_units.drop_reason` values apply to the corpus they are defined for in A1.12. A procurement row never carries `too_short`.
14. `clean_version` is identical across `documents`, `paragraphs`, `clauses` and `rejected_units` within one run.
15. `paragraphs.raw_text_sha256` and `text_sha256` differ only where a cleaning rule fired; where they are equal, no rule fired.
16. `packages.is_placeholder` true does not exclude the row from `package_asset`; placeholders are classified and flagged, not dropped.
17. `packages.superseded_by`, where set, references a `package_id` with a later `plan_version` and the same `borrower_ref_norm`.
17a. Carry-forward across plan versions applies to `estimated_amount` and `method` only. `currency` is taken from the same plan version as the carried amount. `status`, `status_raw`, `category`, `planned_date` and `revised_date` are never carried: a status is a fact at a point in time and inheriting it asserts something no version stated.
17b. `packages.carried_from` names the source plan version of every carried field. A field absent from `carried_from` was stated by the newest version itself.
18. `borrower_ref_norm` is derived from `borrower_ref` by case folding, whitespace collapse and separator normalisation only. No characters are added or removed.
19. `documents.paragraphs_dropped` equals the count of `rejected_units` rows for that `doc_id`.

### A4.4 Preparation

20. `paragraphs.char_end` is greater than `char_start`.
21. `paragraphs.ordinal` is contiguous from 1 within each `doc_id`, with no gaps.
22. `documents.section_ii_found` false implies no `in_scope` paragraphs for that document.
23. `clauses.char_start` and `char_end` fall within the parent paragraph's span.
24. Clause ordinals are contiguous from 1 within each `paragraph_id`.
25. `clauses.splitter_version` matches the version embedded in `clause_id`.
26. On re-parse, every `paragraphs.text_sha256` matches the stored value. A mismatch fails the run.

### A4.5 Detection

27. Every in-scope paragraph has exactly eight `paragraph_asset` rows.
28. `paragraph_asset.fired` is true if and only if `probability` is greater than or equal to `threshold`.
29. `paragraph_asset.threshold` matches the `thresholds` row for that model and `threshold_version`.
30. `paragraph_asset_spans` rows exist only for `asset_id` values with a `fired` row on that paragraph.
31. `clauses` rows exist only for paragraphs with at least one `fired` asset row.
32. `term_candidates` rows exist only where a `clause_measure` row has `resolution` of `family_unresolved`.
33. `term_candidates.example_clause_ids` holds at most five entries, each resolving in `clauses`.

### A4.6 Attribution

34. `tier` matches the A1.15 mapping for its `rule_fired`.
35. `rule_fired` of `none` requires `asset_id` of `UNATTRIBUTED`, and the converse.
36. `rule_fired` of `single_asset_paragraph` requires `competing_asset_count` of 1.
37. `competing_asset_count` greater than 1 forbids `single_asset_paragraph` and `paragraph_fallback_single_asset`.
38. `crossed_boundary` is false on every row where `rule_fired` is not `none`.
39. `path_length` is at most 4 where `rule_fired` is `generic_path`.
40. Every `attribution` row has a matching `clause_measure` row on `clause_id` + `measure_id`.
41. `asset_id`, where not `UNATTRIBUTED`, appears in `measures.applies_to` for that `measure_id`.

### A4.7 Modality

42. `rule_fired` of 7 requires `negation_flag` true and `modality` of `flagged_negation`.
43. `rule_fired` of 8 requires `source` of `classifier`.
44. `rule_fired` of 1 to 6 requires `source` of `rule`.
45. `modality` of `already_done` requires `rule_fired` of 5, 6 or 8.
46. `sentence_shape` of `modifier` requires `governing_token` to sit outside the measure span.
47. Every `modality` row has a matching `clause_measure` row on `clause_id` + `measure_id`.

### A4.8 Procurement

48. `packages.revised_date`, where present, is on or after `planned_date`.
49. `awards.signed_date` is on or after `no_objection_date` where both are present.
50. `awards.supplier_amount`, summed per contract, does not exceed `total_amount`.
51. `package_asset.is_measure_package` true requires a `terms_measure` match recorded on the description.
52. `package_award_link.confidence` is 1.0 where `join_method` is `refnum_exact` or `manual`.
53. A `package_id` links to at most one `contract_id` unless `join_method` is `fuzzy`.
54. `awards.description_lang` is one of `en`, `fr`, `es`, `pt`.

54a. `packages.estimated_amount` is null where the source table printed no Estimated Amount column. On those renditions the single figure on the row is the actual amount and is recorded as `actual_amount`, never promoted to an estimate.
54b. A zero `estimated_amount` in a source version is treated as *not yet costed* and does not block carry-forward from an earlier version. This is a judgement with a cost: a package genuinely revised down to zero keeps its earlier figure. `carried_from` makes the case visible, and `packages_raw` holds every version verbatim.
54c. A rendition whose table columns are scattered contributes `borrower_ref` and `description` only. Its amounts, status and dates are refused, not inferred.

### A4.9 Outputs

55. `commitments.commitment_id` equals `{clause_id}:{measure_id}:{asset_id}`.
56. `commitments.attribution_tier` and `attribution_rule` match the source `attribution` row.
57. `unattributed` contains every `attribution` row with `asset_id` of `UNATTRIBUTED`, and no others.
58. `commitments` and `unattributed` are disjoint.
59. `project_asset_coverage.measures_present` and `measures_absent` are disjoint, and their union equals the `measures` rows whose `applies_to` includes that asset and whose `status` is `active`.
60. `menu.distance_to_complete` equals the count of `measures_to_complete`.
61. `gap_same_span` rows with `present` true have a corresponding `commitments` row.
62. Every `intervention_window.commitment_ids` entry resolves in `commitments`.

### A4.10 Runs and metrics

63. Every output row's `run_id` exists in `runs` with `status` of `complete` or `partial`.
64. `metrics.n` is greater than zero.
65. `metrics` rows reference a `gold_version` present in `gold_set`.
66. `gold_set.unit_id` does not appear in any sample whose `purpose` is `train` or `calibrate`.
67. Version stamps on a row match the values recorded on its `run_id` in `runs`.

---

## A5. Write permissions

| Path | Written by |
|---|---|
| `inputs/taxonomy/**`, `inputs/terms/**`, `inputs/config/**` | People only |
| `inputs/labels/paragraph_labels.csv`, `clause_labels.csv` | People only |
| `inputs/labels/samples.csv`, `inputs/labels/samples/**` | `src/sample.py` |
| `inputs/taxonomy/measure_families.member_count` | `src/validate.py` |
| `meta/gold_set.csv` | People only |
| `meta/runs.csv`, `meta/metrics.csv` | The pipeline |
| Everything under `data/` | The stage that owns it |

No stage writes to a table owned by another stage.

---

## A6. Idempotency

68. Re-running a stage on unchanged input produces byte-identical output, including identifiers.
69. `packages` and `awards` rows are reclassified only where `description_sha256` changed.
70. `paragraphs` and downstream rows are rebuilt only where `content_sha256`, `text_sha256` or `clean_version` changed.
71. Embeddings are regenerated only on a change to `text_sha256` or to `embedding_manifest.model_revision`.

---

# Part B — Detection rule sets

Not machine-checkable. These produce the values Part A constrains.

## B1. Attribution

A dependency parse assigns every word one head word and a labelled arc to it. Attribution asks about arcs rather than word distance, which is what distinguishes "towers sited to avoid flooding" from a coincidental adjacency.

**Use ClearNLP-style labels, not Universal Dependencies.** spaCy's English models emit `dobj` and `nsubjpass`, not `obj` and `nsubj:pass`. Patterns copied from Universal Dependencies documentation match nothing and raise no error.

### B1.1 Named patterns

Implemented with spaCy's `DependencyMatcher`, which matches on arc structure rather than word order.

| # | Shape | Rule | Example | `rule_fired` |
|---|---|---|---|---|
| 1 | Measure modifies asset | `amod` or `compound` from measure to asset | climate-resilient **fiber** | `pattern_1_amod_compound` |
| 2 | Asset is subject of measure verb | asset is `nsubj` or `nsubjpass` | **towers** will be *sited* | `pattern_2_nsubj` |
| 3 | Asset is object of measure verb | asset is `dobj` | *site* the **data centre** | `pattern_3_dobj` |
| 4 | Asset under preposition on measure verb | asset is `pobj` of a `prep` child; preposition in {for, of, on, along, at} | *screening* for **tower** sites | `pattern_4_prep_pobj` |
| 5 | Nominalised measure, asset compounds it | asset is `compound` or `poss` on the measure noun | **route** *selection* | `pattern_5_compound_poss` |
| 6 | Nominalised measure, asset via preposition | measure noun → `prep` → `pobj` = asset | *selection* of **landing stations** | `pattern_6_nominal_prep` |

### B1.2 Generic path rule

Take the shortest undirected path through dependency arcs between the measure token and the asset token. Accept if the path is at most four arcs and crosses no clause-boundary arc. Boundary labels: `advcl`, `ccomp`, `conj`, `mark`.

The boundary check is the load-bearing half. Crossing one of those arcs means the path has entered a different proposition.

### B1.3 Order and fallbacks

72. `single_asset_paragraph` — paragraph names exactly one asset.
73. Patterns 1 to 6, in order.
74. `generic_path`.
75. `adjacent_clause_single_asset` — the adjacent clause names exactly one asset.
76. `paragraph_fallback_single_asset`.
77. `none` → `UNATTRIBUTED`.

---

## B2. Modality

### B2.1 Selecting the governing token

**Predicate shape.** "Sites will be selected to avoid flood-prone areas." The measure has its own verb. Modality is read from that verb.

**Modifier shape.** "The Project will finance climate-resilient fiber." No measure verb. Modality is inherited from the governing verb, and is correctly firm.

### B2.2 Rules, in order

| # | Condition | Class |
|---|---|---|
| 1 | Measure in `xcomp` or `ccomp` of a lemma with `modality_lexicon.class` of `enabling` | `aspiration` |
| 2 | Auxiliary lemma with class `modal_firm` | `firm` |
| 3 | Auxiliary lemma with class `modal_soft` | `aspiration` |
| 4 | Governing verb with class `requiring`, measure in its complement | `firm` |
| 5 | `Tense=Past`, no future auxiliary | `already_done` |
| 6 | `Tense=Pres`, no auxiliary | `already_done` |
| 7 | A `neg` child anywhere on the path | `flagged_negation` |
| 8 | Anything else, or two rules conflicting | Route to classifier |

**Rule 1 runs before rule 2.** Otherwise "the project will aim to select flood-safe sites" reads as firm.

Read auxiliaries and morphological features off the parse; do not enumerate tenses.

Negation flags rather than inverts. "Sites will not be selected on flood exposure grounds alone" is not the negation of the measure.

Rules 5 and 6 are where errors will concentrate.
