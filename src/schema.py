"""What the Postgres copy looks like to a person browsing it in Metabase.

The CSV tables are the source and keep their column names; the loader
(src/load_pg.py) renames and types columns on the way in, from this one
specification, and writes the comments Metabase shows as descriptions. The
rules, from the person who runs Metabase:

  - names for people: full words in snake_case, units and currency in the
    name (estimated_amount_usd, cost_usd_millions), booleans as is_/has_/in_/
    was_, ids as <thing>_id with the same name on both sides of a join;
  - real types: date, timestamptz, numeric, boolean, integer, text[] for a
    pipe-joined list; never a date or a flag as text;
  - a PRIMARY KEY on every table and a FOREIGN KEY on every join column
    (declared NOT VALID: an orphan row is reported by the audits, not fatal);
  - COMMENT ON every table and every column a reader could misread;
  - everything lives in one schema, wbg, so a question joins without a
    schema prefix; the three bookkeeping tables (loads, data_release and
    paragraph_projects) say so in their comment and can be hidden in
    Metabase's table settings. Tables nothing in sql/views/ or analysis/sql/
    reads are not loaded at all: the CSV under data/ stays the record.

Every table is recreated on each load, so nothing here survives unless the
loader applies it.
"""

PUBLIC = "wbg"
INTERNAL = PUBLIC   # one schema; the name stays for the loader's bookkeeping tables

# Column renames applied to every table (csv name -> column name).
RENAME = {
    "doc_id": "document_id", "contract_id": "award_id",
    "doc_type": "document_type", "doc_kind": "document_kind", "lang": "language",
    "pdf_bytes": "pdf_size_bytes",
    "unit_doc_id": "managing_unit_document_id", "unit_as_of": "managing_unit_as_of_date",
    "unit_code": "managing_unit_code",
    "approval_fy": "approval_fiscal_year", "commitment_usd_m": "commitment_usd_millions",
    "commitment_v2_usd_m": "commitment_v2_usd_millions",
    "commitment_v3_usd_m": "commitment_v3_usd_millions",
    "cost_usd_m": "cost_usd_millions", "cost_before_usd_m": "cost_before_restructuring_usd_millions",
    "estimated_amount": "estimated_amount_usd", "actual_amount": "actual_amount_usd",
    "total_amount": "total_amount_usd", "supplier_amount": "supplier_amount_usd",
    "n_tokens": "word_count", "n_chars": "character_count", "n_rows": "row_count",
    "char_start": "text_start_offset", "char_end": "text_end_offset",
    "raw_start": "pdf_text_start_offset", "raw_end": "pdf_text_end_offset",
    "for_model": "is_for_model", "list_item": "is_list_item", "active": "is_active",
    "code_dirty": "has_uncommitted_code", "sent": "was_sent_to_model", "asked": "was_asked",
    "committed": "is_committed",
    "part": "part_number",
    "borrower_ref": "borrower_reference", "borrower_ref_norm": "borrower_reference_normalised",
    "description_match": "description_match_key", "description_lang": "description_language",
    "lot_or_phase": "lot_or_phase_label", "component_source": "component_matched_by",
    "component_doc_id": "component_document_id", "method": "method_code",
    "glue_repairs": "word_repair_count", "status_as_of": "status_as_of_date",
    "plan_version": "plan_document_id", "superseded_by": "superseded_by_document_id",
    "package_link": "package_link_kind",
    "run": "run_id", "kind": "unit_kind", "copies": "copy_unit_ids",
    "rank": "embedding_rank", "score": "embedding_score", "percentile": "embedding_percentile",
    "best_score": "best_embedding_score", "best_percentile": "best_embedding_percentile",
    "queries": "query_count", "queries_agreeing": "queries_agreeing_count",
    "units_hit": "units_hit_count",
    "grade": "most_likely_grade", "expected": "expected_grade", "confidence": "grade_confidence",
    "p0": "probability_grade_0", "p1": "probability_grade_1", "p2": "probability_grade_2",
    "p3": "probability_grade_3",
    "p_committed_calibrated": "posterior_probability",
    "types_yes": "type_ids_above_threshold", "best_type": "best_type_id",
    "best_p": "best_type_probability",
    "patterns": "matched_patterns", "types": "matched_types",
    "round": "feedback_round", "cost": "model_cost_usd",
    "asset_para": "paragraph_best_asset_id", "asset_p_para": "paragraph_best_asset_probability",
    "asset_unit": "unit_best_asset_id", "asset_p_unit": "unit_best_asset_probability",
    "assets_yes": "asset_ids_above_threshold",
    "asset_link": "linked_asset_id", "asset_link_level": "linked_asset_level",
    "asset_link_p": "linked_asset_probability",
    "template_projects": "template_project_count", "template_share": "template_wording_share",
}
# Renames that apply in one table only (csv name -> column name).
RENAME_IN = {
    "components": {"number": "component_number", "name": "component_name",
                   "level": "component_level", "source": "read_from", "page": "page_number",
                   "action": "restructuring_action"},
    "packages": {"component": "component_name"},
    "labels": {"name": "label_name"},
    "label_scale": {"grade": "grade"},
    "spend": {"at": "spent_at"},
}

# Types by column name (after renaming). Anything not here is text, except
# *_date -> date and *_at -> timestamptz.
INTEGER = {"ordinal", "text_start_offset", "text_end_offset", "pdf_text_start_offset",
           "pdf_text_end_offset", "word_count", "character_count", "row_count", "page_from",
           "page_to", "page_number", "part_number", "approval_fiscal_year", "pdf_size_bytes",
           "word_repair_count", "embedding_rank", "query_count", "queries_agreeing_count",
           "units_hit_count", "most_likely_grade", "feedback_round",
           "paragraphs", "packages", "notices", "awards", "sentences_embedded",
           "template_project_count", "cosine_rank", "graded_units", "measure_rank", "grade",
           "graded_yes", "graded_no", "positives", "calls", "units_seen", "units_graded",
           "prior_para_assets", "prior_para_measures"}
NUMERIC = {"estimated_amount_usd", "actual_amount_usd", "total_amount_usd", "supplier_amount_usd",
           "cost_usd_millions", "cost_before_restructuring_usd_millions",
           "commitment_usd_millions", "commitment_v2_usd_millions", "commitment_v3_usd_millions",
           "embedding_score", "embedding_percentile",
           "best_embedding_score", "best_embedding_percentile", "best_cosine", "expected_grade",
           "grade_confidence", "probability_grade_0", "probability_grade_1",
           "probability_grade_2", "probability_grade_3",
           "posterior_probability", "best_type_probability", "weight", "gate", "share_lost",
           "cost_usd", "seconds", "stratum_size", "stratum_drawn",
           "model_cost_usd", "precision", "recall", "measure_cosine",
           "paragraph_best_asset_probability",
           "unit_best_asset_probability", "linked_asset_probability",
           "template_wording_share",
           "cosine", "probability", "p_only_a", "p_only_b", "p_both", "p_neither",
           "prior_phrase_cosine", "prior_phrase_match", "prior_probability_a",
           "prior_probability_b", "prior_phrase_cosine_a", "prior_phrase_cosine_b"}
BOOLEAN = {"is_for_model", "is_list_item", "is_rebid", "is_placeholder", "in_latest_plan",
           "in_projects_interface", "has_uncommitted_code", "is_active", "included",
           "was_sent_to_model", "was_asked", "is_committed"}
# Pipe-joined lists in the CSV become arrays, which Metabase renders and
# `= ANY(column)` queries without LIKE.
ARRAY = {"project_ids", "listed_project_ids", "notice_ids", "copy_unit_ids", "matched_patterns",
         "matched_types", "asset_ids_above_threshold", "type_ids_above_threshold",
         "component_mentions",
         "shared_asset_ids"}
INDEXED = {"document_id", "paragraph_id", "sentence_id", "project_id", "package_id", "notice_id",
           "award_id", "unit_id", "borrower_reference_normalised", "label_id", "label_set",
           "type_id"}


def col_type(name):
    if name in INTEGER:
        return "integer"
    if name in NUMERIC:
        return "numeric"
    if name in BOOLEAN:
        return "boolean"
    if name in ARRAY:
        return "text[]"
    if name.endswith(("_date", "_as_of")):
        return "date"
    if name.endswith("_at"):
        return "timestamptz"
    return "text"


def rename(table, csv_name):
    return RENAME_IN.get(table, {}).get(csv_name) or RENAME.get(csv_name, csv_name)


# ---------------------------------------------------------------- the tables
#
# schema, primary key, foreign keys [(column, table, column)], comments.
# Table names are the ones a person sees; the loader maps its sources to them.

# The prior inputs that need no grading (src/prior.py, docs/method/decisions.md):
# stored next to the decision model's answers, read by nothing until a graded
# sheet fits the weights and cut-offs that use them.
PRIOR_INPUTS = {
    "prior_phrase_cosine": "Prior input: best embedding cosine to the label's search phrases.",
    "prior_phrase_match": "Prior input: the best phrase's share of information found in the text, each word weighted by how rare it is in the corpus (0 to 1).",
}
PROBABILITY_GRADES = {
    "grade_confidence": "The model's own confidence in its answer.",
    "probability_grade_0": "Probability of grade 0 (the passage is not about the label).",
    "probability_grade_1": "Probability of grade 1 (mentioned as background or another party's work).",
    "probability_grade_2": "Probability of grade 2 (supported, planned or conditional).",
    "probability_grade_3": "Probability of grade 3 (financed or committed). The four grade probabilities are the model's raw answer, kept as the record: no decision reads them one by one; the decision reads posterior_probability.",
    "posterior_probability": "The probability every decision reads: the model's probability of grades 2 and 3 through the label's probability layer (src/posterior.py), or that score itself until a sheet graded under the current question has been evaluated. A sentence counts at the label's floor (control panel) or above.",
    "model_served": "The exact decision-model snapshot that answered.",
    "run_id": "The run folder under data/, a hash of everything the answers depend on.",
}

TABLES = {
    "projects": dict(
        schema=PUBLIC, key=("project_id",), foreign=[],
        comment="One row per cohort project: the projects interface record plus the managing "
                "unit read from its most recent appraisal document.",
        columns={
            "cohort_set": "Which hand-kept set of the cohort the project is in: digital (its object is a digital asset) or digital_contributor (a digital asset contributes to another sector's object). The filter every report should carry.",
            "in_projects_interface": "Whether the projects interface had a record for this id.",
            "commitment_usd_millions": "Total commitment, US$ millions, from the projects interface: its v3 form where it gives one, else v2. Not always the current Bank financing: the two forms disagree on 30 of 131 projects, and v3 can show a cancelled amount (P171305: US$50m where US$150m remains). See the two columns below.",
            "commitment_v2_usd_millions": "The v2 form's curr_total_commitment, US$ millions: usually the amount at approval, not updated after a cancellation.",
            "commitment_v3_usd_millions": "The v3 form's curr_total_commitment, US$ millions: includes additional financing; after a cancellation it has been seen to show the cancelled amount.",
            "record_source": "Which form of the projects interface gave the record: v2, v3 or v2+v3. v3 is preferred for name, status, dates, place and commitment (the more current); v2 alone gives the fiscal year, lending instrument, Global Practice and sectors, so a v3-only record has those blank (the fiscal year is then computed from the approval date).",
            "approval_fiscal_year": "World Bank fiscal year of Board approval.",
            "sectors": "Top three sectors with their shares, as the interface prints them.",
            "practice": "The Global Practice(s) leading the project.",
            "practice_code": "Global Practice code.",
            "managing_unit": "The owner recorded on the most recent appraisal document that records one, as printed.",
            "managing_unit_code": "The code inside the managing unit (IDD04 from 'Digital Dev - AFR EAST/SOUTH (IDD04)').",
            "managing_unit_document_id": "The document the managing unit was read from.",
            "managing_unit_as_of_date": "That document's disclosure date.",
        }),
    "documents": dict(
        schema=PUBLIC, key=("document_id",), foreign=[],
        comment="One appraisal document (Project Appraisal Document or Project Paper) fetched "
                "as PDF; project_ids lists every operation the interface files it under.",
        columns={
            "document_id": "The documents interface's id.",
            "project_ids": "Every operation the document is filed under (a regional document serves several).",
            "document_type": "The interface's document type.",
            "document_kind": "pad, additional_financing, restructuring or project_paper_other, from the type and title.",
            "disclosure_date": "When the document was disclosed.",
            "pdf_sha256": "SHA-256 of the fetched PDF; the parse is keyed on it.",
            "pdf_size_bytes": "Size of the PDF in bytes.",
            "owner_unit": "The unit the interface records as the document's owner, as printed.",
            "fetched_at": "When the PDF was downloaded.",
        }),
    "paragraphs": dict(
        schema=PUBLIC, key=("paragraph_id",),
        foreign=[("document_id", "documents", "document_id")],
        comment="One cleaned unit of a document (a paragraph, heading, table row or footnote) "
                "with its text and the component heading it sits under.",
        columns={
            "paragraph_id": "Positional: document id and the unit's ordinal.",
            "project_ids": "The project(s) of the document part the paragraph starts in.",
            "ordinal": "Position in the document.",
            "section_path": "Section numbering path, such as II.B.",
            "section_title": "Title of the section the paragraph sits in.",
            "block": "narrative, annex, footnote, table, heading or frontmatter.",
            "is_for_model": "Narrative, annex and non-reference footnotes: what embedding and the decision model read.",
            "is_list_item": "Opened with a bullet or enumerator that was removed.",
            "table_id": "The ruled table this row belongs to, blank otherwise.",
            "text_start_offset": "Start of the text in data/appraisal/text/<document_id>.txt, characters.",
            "text_end_offset": "End of the text in the cleaned text file, characters.",
            "pdf_text_start_offset": "Start of the raw PDF text the paragraph was built from.",
            "pdf_text_end_offset": "End of that raw PDF text.",
            "word_count": "Words in the text.",
            "text_sha256": "SHA-256 of the text; a person's grade is keyed on it, never on position.",
            "page_from": "First PDF page the paragraph spans.",
            "page_to": "Last PDF page the paragraph spans.",
            "parser": "The PDF reader's version; a change can move paragraph boundaries.",
            "component_number": "The component heading the paragraph sits under, in the document's own numbering.",
            "component_name": "That component's name as the heading printed it.",
            "subcomponent_number": "The sub-component heading it sits under, if any.",
            "subcomponent_name": "That sub-component's name.",
            "component_mentions": "Components named in the text itself, not where the paragraph sits.",
            "text": "The cleaned text (resolved from the offsets at load time).",
        }),
    "search_units": dict(
        schema=PUBLIC, key=("unit_id",),
        foreign=[("paragraph_id", "paragraphs", "paragraph_id"),
                 ("document_id", "documents", "document_id")],
        comment="One unit the search embeds and scores: a sentence, a whole footnote or a prose "
                "table row, with its text and the list lead-in it is read with. Every "
                "unit_id elsewhere points here.",
        columns={
            "unit_id": "The sentence id, the footnote's paragraph id, or a table row's paragraph id with a row suffix.",
            "unit_kind": "sentence, footnote or table.",
            "project_ids": "The project(s) of the document part the unit sits in.",
            "block": "The paragraph's block.",
            "component_key": "Name key of the component the unit sits under: the same component across documents even when renumbered.",
            "lead_in": "The sentence that introduces the list the unit is an item of, embedded in front of it; blank otherwise.",
            "left_out_reason": "Why the unit is not searched: reference (a citation or caption), fragment (a scrap or title) or indicator (a results-framework table row); blank when searched.",
            "copy_of_unit_id": "The earlier unit that prints the same text (an additional financing repeats its appraisal document); only that one was embedded.",
            "template_project_count": "How many other projects print a unit with the same words (lead-in included): the Bank's standard text scores high, a project's own commitment 0.",
            "template_wording_share": "Share of the unit's six-word runs that appear in at least two other projects, 0 to 1: template wording with a name or figure filled in. A filter for a reader, not a cut.",
            "text": "The unit's text, without the lead-in.",
        }),
    "components": dict(
        schema=PUBLIC, key=("component_id",),
        foreign=[("document_id", "documents", "document_id")],
        comment="One component or sub-component as one document states it, with its cost; the "
                "most recent document's list is the project's current one.",
        columns={
            "component_id": "Document id, the part's projects, where it was read from, level, number and name key.",
            "project_ids": "The project(s) the document part belongs to.",
            "document_kind": "The document's kind (pad, additional_financing, restructuring).",
            "read_from": "datasheet (the data sheet's table), restructuring (the table of changes) or heading.",
            "component_level": "component or subcomponent.",
            "component_number": "As printed, such as 1 or 2.1.",
            "name_key": "The name lower-cased without spaces or punctuation: matches the same component in another document.",
            "cost_usd_millions": "Cost in US$ millions as the document states it.",
            "cost_before_restructuring_usd_millions": "Cost before a restructuring, when the document is one.",
            "restructuring_action": "What the restructuring did to the component, as printed.",
            "page_number": "PDF page the table was read from.",
        }),
    "packages": dict(
        schema=PUBLIC, key=("package_id",),
        foreign=[("project_id", "projects", "project_id"),
                 ("component_document_id", "documents", "document_id")],
        comment="One procurement-plan package, as its newest plan version states it. A planned "
                "date already past is what the plan records, not evidence of delay.",
        columns={
            "package_id": "project id and the borrower reference; plus a short hash of the description for a generic reference, and ':2' for a duplicate row.",
            "borrower_reference": "The reference the borrower gave the package, as printed.",
            "borrower_reference_normalised": "Upper-cased with every run of separators made one hyphen: the key notices and awards link on.",
            "description": "As printed in the plan.",
            "description_clean": "With other cells, markers and repaired words taken care of (src/clean_procurement.py).",
            "description_match_key": "Lower-cased with spaces removed, for matching only.",
            "description_sha256": "SHA-256 of the cleaned description; a label is keyed on it.",
            "description_language": "en, fr, es or pt, by marker words.",
            "lot_or_phase_label": "'Lot 2', 'Phase II' or similar taken out of the description.",
            "component_number": "The component the plan names, in the appraisal document's numbering.",
            "component_name": "That component's name.",
            "component_matched_by": "name_match (the plan's component cell matched a name), number_only (only a number was printed), or blank.",
            "component_document_id": "The document whose component list gave the number.",
            "word_repair_count": "Words re-joined by the word repair (src/glue.py).",
            "is_rebid": "The description said rebid, re-tender or relance.",
            "is_placeholder": "The description is too generic to match anything (TBD, a bare category, a reference only).",
            "clean_version": "Version of the procurement cleaner that produced the row.",
            "category": "goods, works, consultant_services, non_consulting_services or unknown.",
            "method_code": "The Bank's procurement method code (RFB, RFQ, QCBS, INDV, ...), or unknown.",
            "method_source": "Where the method came from: method_cell, clipped_method_cell or reference.",
            "method_raw": "The method cell as printed.",
            "market_approach": "Open - National, Limited - International, Direct, as printed.",
            "status": "STEP's label (Pending, Under Implementation, Signed, ...), or unknown.",
            "status_raw": "The status as printed.",
            "status_source": "plan (the newest version's status), award (a signed contract), or none.",
            "status_as_of_date": "Date of the plan or contract that stated the status, not today.",
            "planned_date": "What the plan records.",
            "revised_date": "The revised date the plan records.",
            "estimated_amount_usd": "The plan's estimated amount; see amount_source. US$ when currency is USD.",
            "amount_source": "plan (the Estimated Amount cell) or description (a figure inside the description).",
            "actual_amount_usd": "The plan's actual amount, newest version; a blank stays blank and a zero stays zero.",
            "plan_document_id": "The newest plan version that lists the package.",
            "plan_disclosure_date": "That plan's disclosure date.",
            "in_latest_plan": "False when the package is not in the project's most recently disclosed plan: dropped, or the plan was published in parts.",
        }),
    "notices": dict(
        schema=PUBLIC, key=("notice_id",),
        foreign=[("project_id", "projects", "project_id"),
                 ("package_id", "packages", "package_id")],
        comment="One procurement notice from the notices interface, linked to its plan package "
                "where the borrower reference identifies one.",
        columns={
            "notice_type": "As the interface prints it.",
            "notice_status": "As the interface prints it.",
            "deadline_date": "Bid or expression-of-interest deadline.",
            "package_link_kind": "How the package was found: reference, reference_and_description, ambiguous, not_in_plan or no_reference. package_id is set only for the first two.",
            "category": "goods, works, consultant_services, non_consulting_services or unknown.",
            "method_code": "The interface's method code.",
        }),
    "awards": dict(
        schema=PUBLIC, key=("award_id",),
        foreign=[("project_id", "projects", "project_id"),
                 ("package_id", "packages", "package_id")],
        comment="One signed contract from the contract awards interface, linked to its plan "
                "package where the borrower reference identifies one.",
        columns={
            "award_id": "The interface's contract id.",
            "no_objection_date": "Date of the Bank's no-objection.",
            "total_amount_usd": "Contract total; US$ when currency is USD.",
            "supplier_amount_usd": "The named supplier's share of the total.",
            "review_type": "prior or post.",
            "sector": "As the interface prints it (a pipe-joined list).",
            "notice_ids": "Notices of the same project and borrower reference.",
            "package_link_kind": "How the package was found; see notices.",
        }),
    "paragraph_projects": dict(
        schema=INTERNAL, key=("paragraph_id", "project_id"),
        foreign=[("paragraph_id", "paragraphs", "paragraph_id"),
                 ("project_id", "projects", "project_id")],
        comment="Pipeline bookkeeping: one row per paragraph and project, unpacked from "
                "paragraphs.project_ids so a paragraph joins its project by key.",
        columns={}),
    "labels": dict(
        schema=PUBLIC, key=("label_set", "label_id"), foreign=[],
        comment="One label of one label set: the assets (inputs/taxonomy) and each measure "
                "set (analysis/labels), with its definition.",
        columns={
            "label_set": "The set's name: assets, or a measure set such as cra.",
            "label_name": "The label's name.",
            "definition": "The definition the search and the decision model were given.",
            "is_active": "Whether the label is searched and graded.",
            "version": "The hand-kept version of the row.",
            "folder": "Where the set lives in git.",
        }),
    "search_paragraphs": dict(
        schema=PUBLIC, key=("label_set", "label_id", "paragraph_id"),
        foreign=[("paragraph_id", "paragraphs", "paragraph_id")],
        comment="One paragraph per label with its best-scoring search unit: where the search "
                "says the label is talked about. No cut-off is applied.",
        columns={
            "best_embedding_score": "Best cosine over the label's queries and the paragraph's units.",
            "best_embedding_percentile": "That score's percentile within its query.",
            "best_unit_id": "The unit that gave the best score.",
            "best_query_id": "The query that gave it.",
            "units_hit_count": "How many of the paragraph's units were hits.",
            "component_key": "Name key of the component the paragraph sits under.",
            "run_id": "The search run the row comes from.",
        }),
    "paragraph_grades": dict(
        schema=PUBLIC, key=("label_set", "label_id", "unit_id"),
        foreign=[("paragraph_id", "paragraphs", "paragraph_id")],
        comment="The asset map: one row per sentence and asset with the decision model's "
                "probability of each grade (0 not about it, 1 background, 2 supported, "
                "3 financed) and the posterior probability of grades 2 and 3. Which asset a "
                "sentence has is in unit_assets.",
        columns=dict(PROBABILITY_GRADES, **{
            "unit_id": "The sentence graded (search_units.unit_id).",
            "was_asked": "False when the skip rule's gate (the asset's fitted gate, else the control panel's asset.gate_cosine) kept the question from being asked of this sentence; the grades are then NULL.",
            "best_cosine": "The sentence's cosine against the asset's phrases.",
            "route": "Why the asset's question was asked: embedding (the cosine reached the gate), pattern (a word pattern of the asset matched), both, or feedback (a relevance-feedback round below the gate); NULL when not asked.",
            "round": "0 for the first pass; 1 and up for the feedback round that asked it.",
            **PRIOR_INPUTS,
        })),
    "measure_findings": dict(
        schema=PUBLIC, key=("label_set", "label_id", "unit_id"),
        foreign=[("unit_id", "search_units", "unit_id"),
                 ("paragraph_id", "paragraphs", "paragraph_id")],
        comment="One unit seen by a measure run: the decision model's answer (the probability "
                "of each grade: 0 not described, 1 background, 2 planned, 3 committed), the "
                "posterior probability of grades 2 and 3 every decision reads, whether it "
                "counts (is_committed), and the asset it links to.",
        columns=dict(PROBABILITY_GRADES, **{
            "route": "pattern, embedding, both, or feedback (found by a relevance-feedback round).",
            "feedback_round": "0 for the candidate pool; 1 and up for relevance-feedback rounds.",
            "embedding_rank": "Rank for the label by best cosine (round 0) or by closeness to the feedback query.",
            "embedding_score": "Best cosine over the label's queries, or to the feedback query.",
            "matched_patterns": "Pattern ids that matched.",
            "firmness": "activity, outcome or none, from wording lists.",
            "was_sent_to_model": "False when a gate kept the unit from being graded.",
            "is_committed": "The one decision: posterior_probability at or above the measure's floor (control panel inputs/config/control_panel.csv, floor.<set> else floor.default).",
            "paragraph_best_asset_id": "For a sentence whose own best asset is under the control panel's asset.floor: the asset with the highest sum of its own score and the earlier sentences' scores in its paragraph, each weighted by asset.decay per sentence back; empty otherwise.",
            "paragraph_best_asset_probability": "That decayed sum (at most 1).",
            "unit_best_asset_id": "The sentence asset map's best asset for the sentence itself.",
            "unit_best_asset_probability": "Its probability (grades 2 and 3, through the asset's probability layer when fitted).",
            "asset_ids_above_threshold": "The sentence's own asset when it reaches the control panel's asset.floor.",
            "linked_asset_id": "The asset the finding is linked to: the sentence's own (level unit), else the decayed sum over its paragraph's earlier sentences (level paragraph), at the control panel's asset.floor and asset.paragraph_floor.",
            "linked_asset_level": "unit, paragraph or none.",
            "linked_asset_probability": "The probability behind the link.",
            "model_cost_usd": "What the stored answers for this unit cost, US$.",
            "type_ids_above_threshold": "The unit's type (one choice among the measure's types in analysis/labels/<set>_types.csv) when its probability reaches the control panel's floor.types; empty when none does or no list exists yet.",
            "best_type_id": "The likeliest type.",
            "best_type_probability": "Its probability.",
            "template_project_count": "As in search_units: other projects printing the same words.",
            "template_wording_share": "As in search_units: share of six-word runs shared with two or more other projects.",
            "copy_unit_ids": "Units in other documents that print the same text.",
            "text": "The unit's text with its list lead-in.",
            **PRIOR_INPUTS,
        })),
    "measure_types": dict(
        schema=PUBLIC, key=("label_set", "label_id", "type_id"), foreign=[],
        comment="A measure's approved types (its clusters): proposed by a generative model "
                "from the committed units, approved by a person in analysis/labels/"
                "<set>_types.csv, assigned by the decision model one yes/no each.",
        columns={"type_name": "The name shown (display_name in the file when given, else "
                              "the name the model was asked with).",
                 "definition": "The definition the decision model was given.",
                 "merged_from": "Type ids the file merges into this one (merge_into), "
                                "pipe-separated; their yes answers count as this type's."}),
    "measure_quality": dict(
        schema=PUBLIC, key=("label_set", "question_sha256", "sheet"), foreign=[],
        comment="How well the pipeline decides a measure, against a sheet a person graded "
                "(./wbg check <set> --evaluate): one row per measure, exact question wording "
                "and kind of sheet, the latest evaluation. Precision and recall are of the "
                "decision (posterior at the floor) against the person's grade 2 or more, "
                "weighted back to the pool, with recall within the graded pool, not the "
                "corpus. Only an evaluation sheet (a random draw) measures the dashboards; a "
                "training sheet holds the hard cases. Quote graded_units beside them.",
        columns={"version": "The measure's version (its file's version column) when the "
                            "sheet was evaluated.",
                 "question_sha256": "SHA-256 of the exact question the sheet was graded "
                                    "under: a revised definition is a new row.",
                 "run_id": "The measure run the sheet was drawn from.",
                 "sheet": "evaluation (a random draw by the decision, never used to fit) or "
                          "training (drawn near the floor).",
                 "graded_units": "Rows the person graded.",
                 "precision": "Of the units the pipeline counts, the weighted share the person "
                              "graded 2 or more.",
                 "recall": "Of the units the person graded 2 or more, the weighted share the "
                           "pipeline counts (within the graded pool).",
                 "report_path": "The evaluation report under data/reports/."}),
    "label_queries": dict(
        schema=PUBLIC, key=("label_set", "label_id", "query_id"), foreign=[],
        comment="A copy of each set's search phrases (<set>_queries.csv, kept by a person): "
                "the sentences the search ranks units against. Inactive rows are kept.",
        columns={"query_id": "The phrase's id.", "query": "The phrase.",
                 "is_active": "Whether the search uses it.",
                 "version": "The label's version (its set file's version column).",
                 "file_sha256": "SHA-256 of the file the row was copied from."}),
    "label_patterns": dict(
        schema=PUBLIC, key=("label_set", "label_id", "pattern_id"), foreign=[],
        comment="A copy of each set's word patterns (<set>_patterns.csv, kept by a person): "
                "regular expressions that add units to a measure's pool. Inactive rows are "
                "kept. label_pattern_hits counts the units each matched in the current run.",
        columns={"pattern_id": "The pattern's id.",
                 "pattern": "The regular expression, matched without regard to case.",
                 "is_active": "Whether the search uses it.",
                 "version": "The label's version (its set file's version column).",
                 "file_sha256": "SHA-256 of the file the row was copied from."}),
    "label_scale": dict(
        schema=PUBLIC, key=("label_set", "label_id", "grade"), foreign=[],
        comment="A copy of each set's grade scale (<set>_scale.csv, kept by a person), one row "
                "per label and grade with the label's name filled in: the grades the decision "
                "model chooses among.",
        columns={"grade": "The grade, 0 the lowest.",
                 "description": "What the grade means, as the model is shown it.",
                 "version": "The label's version (its set file's version column).",
                 "file_sha256": "SHA-256 of the file the row was copied from."}),
    "package_assets": dict(
        schema=PUBLIC, key=("package_id", "label_id"),
        foreign=[("package_id", "packages", "package_id"),
                 ("project_id", "projects", "project_id")],
        comment="Which digital asset a procurement package buys, builds, installs, connects "
                "or operates: one row per package and asset asked (its best few by cosine "
                "over the asset taxonomy's phrases), with the decision model's yes "
                "probability (src/link_packages.py). Filter on the probability; no cut-off "
                "is built in.",
        columns={
            "label_id": "The asset's id in wbg.labels (label_set assets).",
            "cosine": "The package description's best cosine over the asset's search phrases.",
            "cosine_rank": "The asset's rank among the package's assets by that cosine; 1 is the best.",
            "probability": "The decision model's probability that the package procures this asset.",
            "model_served": "The decision-model snapshot that answered.",
            "prior_phrase_cosine": "Prior input: the cosine above, under the name every question's prior uses.",
            "prior_phrase_match": "Prior input: the best asset phrase's share of information found in the package description, words weighted by rarity.",
        }),
    "unit_paragraph_context": dict(
        schema=PUBLIC, key=("unit_id",),
        foreign=[("unit_id", "search_units", "unit_id"),
                 ("paragraph_id", "paragraphs", "paragraph_id")],
        comment="Prior inputs made once every stage has run (src/prior.py, "
                "docs/method/decisions.md): for each sentence, how many other sentences of its "
                "paragraph the pipeline classified as having an asset and as committed for any "
                "measure. They move when the classification is tuned. Rebuilt whole at every "
                "publish; read by nothing until a graded sheet fits the weights that use them.",
        columns={"prior_para_assets": "Other sentences of the paragraph whose best asset in the asset map reaches the control panel's asset.floor.",
                 "prior_para_measures": "Other sentences of the paragraph committed for any measure (each measure's floor)."}),
    "unit_pair_checks": dict(
        schema=PUBLIC, key=("unit_id", "set_a", "set_b"), foreign=[],
        comment="For a unit two measures both commit: the decision model's choice of only the "
                "first measure, only the second, both or neither, each measure stated by its "
                "boundary (src/commit_checks.py). The commitment views drop a unit from a "
                "measure an answer says it is not (dashboard_settings.apply_measure_pair_checks).",
        columns={"set_a": "The first measure's set (alphabetical).",
                 "set_b": "The second measure's set.",
                 "decision": "only_a, only_b, both or neither: the most likely answer.",
                 "probability": "The probability of that answer.",
                 "p_only_a": "Probability of only the first measure.",
                 "p_only_b": "Probability of only the second measure.",
                 "p_both": "Probability of both.", "p_neither": "Probability of neither.",
                 "prior_probability_a": "Prior input: the first measure's probability for the unit (its probability layer's, else the model's score).",
                 "prior_phrase_cosine_a": "Prior input: the unit's cosine to the first measure's phrases.",
                 "prior_probability_b": "Prior input: the second measure's probability for the unit.",
                 "prior_phrase_cosine_b": "Prior input: the unit's cosine to the second measure's phrases."}),
    "commitment_packages": dict(
        schema=PUBLIC, key=("label_set", "label_id", "unit_id", "package_id"),
        foreign=[("package_id", "packages", "package_id"),
                 ("unit_id", "search_units", "unit_id"),
                 ("project_id", "projects", "project_id")],
        comment="Which procurement packages would carry out a commitment: one row per "
                "committed unit and package of the same project that share an asset, "
                "or that is among the project's packages nearest the "
                "measure itself (route), with the decision model's yes probability "
                "(src/link_packages.py). Many to many: a commitment may link to several "
                "packages and a package to several commitments. Filter on the probability.",
        columns={
            "label_set": "The measure's set.",
            "label_id": "The measure.",
            "unit_id": "The committed unit (wbg.measure_findings).",
            "shared_asset_ids": "Assets both the unit's answers and the package's answers name at 0.5.",
            "route": "How the pair was made: asset (a shared asset) or measure (the package is among the project's nearest to the measure's phrases; no asset needed).",
            "measure_rank": "For the measure route, the package's rank by cosine to the measure among the project's packages not paired by asset.",
            "measure_cosine": "For the measure route, the package description's best cosine to the measure's phrases and definition.",
            "probability": "The decision model's probability that carrying out the package is where the commitment is delivered or applied.",
            "model_served": "The decision-model snapshot that answered.",
            "prior_phrase_cosine": "Prior input: the package description's best cosine to the measure's phrases.",
            "prior_phrase_match": "Prior input: the share of the package description's information found in the commitment sentence, words weighted by rarity.",
        }),
    "unit_assets": dict(
        schema=PUBLIC, key=("unit_id",),
        foreign=[("unit_id", "search_units", "unit_id"),
                 ("paragraph_id", "paragraphs", "paragraph_id")],
        comment="The asset of every sentence the search reads, decided as the measure stage "
                "decides it: the sentence's own best asset when its posterior reaches the "
                "control panel's asset.floor (level unit); else the asset with the highest "
                "sum of its own score and the earlier sentences' scores in its paragraph, each "
                "weighted by asset.decay per sentence back, when that reaches asset.paragraph_floor (level "
                "paragraph); else none. Rebuilt at every publish from the current asset map "
                "and its probability layers.",
        columns={"asset_id": "The sentence's asset; empty at level none.",
                 "probability": "The posterior (level unit) or the decayed sum (level paragraph).",
                 "asset_level": "unit, paragraph or none.",
                 "own_best_asset_id": "The sentence's own best asset, whatever its probability.",
                 "own_best_probability": "Its posterior probability."}),
    "graded_labels": dict(
        schema=PUBLIC, key=("label_set", "label_id", "text_sha256", "question_sha256"),
        foreign=[],
        comment="Every grade a person gave a sentence (data/labels/<set>_units.csv, written "
                "by ./wbg check <set> --evaluate): keyed by the text's SHA-256 and the exact "
                "question, never by position (AGENTS.md rule 9). Only the evaluation reads "
                "them; the layers and gates fit on the rows of training sheets under the "
                "current question. No text is stored here: join search_units on unit_id.",
        columns={"grade": "The person's grade on the measure's scale; 2 or more is yes.",
                 "asset": "The asset the person gave the sentence, if any.",
                 "type": "The type the person gave, if any.",
                 "source": "The sheet the grade came from.",
                 "stratum": "The draw's stratum; eval:* rows are an evaluation sheet's.",
                 "weight": "The design weight: the stratum's size over the rows drawn from it."}),
    "graded_package_links": dict(
        schema=PUBLIC, key=("label_set", "unit_id", "package_id", "question_sha256"),
        foreign=[],
        comment="Every answer a person gave on a package-link sheet (data/labels/"
                "package_links.csv): does carrying out the package deliver the commitment.",
        columns={"applies": "yes, no or unsure."}),
    "check_draws": dict(
        schema=PUBLIC, key=("sheet", "row_id"), foreign=[],
        comment="Every row ever drawn for a person to grade (data/review/*.key.csv), with its "
                "stratum and the stratum's size: what makes a sheet's figures weightable.",
        columns={"sheet": "The key file's name (the sheet and seed).",
                 "measure_dir": "The measure run the row was drawn from."}),
    "posterior_layers": dict(
        schema=PUBLIC, key=("label_set", "label_id"), foreign=[],
        comment="The probability layer fitted for each label (data/labels/<set>_posterior.json, "
                "assets_posterior.json): what turns the model's score into posterior_probability.",
        columns={"question_sha256": "The exact question it was fitted on; it applies to no other.",
                 "graded_yes": "Graded yes it was fitted on.",
                 "graded_no": "Graded no.",
                 "coefficients": "Intercept, then the weights on logit(score), the vote and the "
                                 "centroid difference, on standardised inputs (JSON).",
                 "settings": "The control-panel settings it was fitted with (JSON).",
                 "file_sha256": "SHA-256 of the file."}),
    "skip_gates": dict(
        schema=PUBLIC, key=("label_set", "label_id"), foreign=[],
        comment="The skip rule's gate fitted for each label (data/labels/<set>_gate.json, "
                "src/skip.py): the highest cosine that loses at most skip.max_loss of the "
                "graded yes. A label with no row uses the control panel's starting gate.",
        columns={"gate": "The cosine gate.",
                 "share_lost": "The weighted share of graded yes below it.",
                 "positives": "Graded yes it was fitted on.",
                 "question_sha256": "The question the grades were given under.",
                 "search_run": "The search run whose cosines it read."}),
    "runs": dict(
        schema=PUBLIC, key=("run_id",), foreign=[],
        comment="Every model run's manifest (data/{classify,measures,links,types,checks}/*/"
                "manifest.json): what was asked, of which model, at what cost. Unlike the run "
                "tables, which hold the current run only, this keeps every run written.",
        columns={"stage": "classify, measures, links, types or checks.",
                 "label_set": "The set, where the run is for one.",
                 "created_at": "When the run was written.",
                 "calls": "Requests made by the run.",
                 "cost_usd": "What those requests cost, US$.",
                 "manifest": "The whole manifest (JSON): questions, their SHA-256, settings, "
                             "models served."}),
    "spend": dict(
        schema=PUBLIC, key=None, foreign=[],
        comment="The spend ledger (data/reports/spend.csv): one row per stage run that asked "
                "the decision model or another paid model.",
        columns={"spent_at": "When.", "stage": "Which stage.", "calls": "Requests.",
                 "cost_usd": "US$.", "run": "The run folder written, or 'failed'."}),
    "answers": dict(
        schema=PUBLIC, key=("answer_sha256",), foreign=[],
        comment="Every stored decision-model answer (data/classify/answers/): the record a "
                "re-run reads instead of asking again. Keyed by the SHA-256 of the model, "
                "the state and the question; the state's text is not repeated here.",
        columns={"model": "The model asked (its pinned snapshot).",
                 "model_served": "The snapshot that answered.",
                 "answer": "The answer as returned (JSON): its probabilities.",
                 "cost_usd": "Its share of the request's cost, US$.",
                 "asked_at": "When it was asked.",
                 "call_sha256": "SHA-256 of the whole request it came in."}),
    "sentence_vectors": dict(
        schema=INTERNAL, key=("unit_id",), foreign=[("unit_id", "search_units", "unit_id")],
        comment="Pipeline bookkeeping: one embedding per search unit (3,072 dimensions), used "
                "by wbg.similar_units() and wbg.search_phrase().",
        columns={}),
    "data_release": dict(
        schema=INTERNAL, key=("release_id",), foreign=[],
        comment="Pipeline bookkeeping: the single release this database holds, and when each "
                "kind of record was last fetched. wbg.data_as_of shows it.",
        columns={}),
    "loads": dict(
        schema=INTERNAL, key=None, foreign=[],
        comment="Pipeline bookkeeping: one row per table load, with the source file's SHA-256 "
                "(an unchanged source is not reloaded).",
        columns={}),
}


def qualified(table):
    """schema.table for a table name, as created."""
    return f"{TABLES[table]['schema']}.{table}"
