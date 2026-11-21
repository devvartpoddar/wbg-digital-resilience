"""What the Postgres copy looks like to a person or an app reading it.

The CSV tables are the source and keep their column names; the loader
(src/wbg_portfolio/load_pg.py) renames and types columns on the way in, from this one
specification, and writes a comment on every table and column. The
rules:

  - names for people: full words in snake_case, units and currency in the
    name (estimated_amount_usd, cost_usd_millions), booleans as is_/has_/in_/
    was_, ids as <thing>_id with the same name on both sides of a join;
  - real types: date, timestamptz, numeric, boolean, integer, text[] for a
    pipe-joined list; never a date or a flag as text;
  - a PRIMARY KEY on every table and a FOREIGN KEY on every join column
    (declared NOT VALID: an orphan row is reported by the audits, not fatal);
  - COMMENT ON every table and every column a reader could misread;
  - everything a dashboard reads lives in one schema, wbg; the record
    (answers, grades, fits, spend) lives in wbg_store (src/wbg_portfolio/db.py) and is
    never dropped. loads, data_release and paragraph_projects are
    bookkeeping.

Every wbg table is recreated on load or publish, so nothing here survives
unless the loader applies it.
"""


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
    "committed": "is_committed", "measure_rule": "passes_measure_rule",
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
    "p_committed_calibrated": "posterior_probability", "p_calibrated": "posterior_probability",
    "types_yes": "type_ids_above_threshold", "best_type": "best_type_id",
    "best_p": "best_type_probability",
    "patterns": "matched_patterns", "types": "matched_types",
    "round": "feedback_round", "cost": "model_cost_usd",
    "asset_para": "paragraph_best_asset_id", "asset_p_para": "paragraph_best_asset_probability",
    "asset_unit": "unit_best_asset_id", "asset_p_unit": "unit_best_asset_probability",
    "assets_yes": "asset_ids_above_threshold",
    "asset_link": "linked_asset_id", "asset_link_level": "linked_asset_level",
    "asset_link_p": "linked_asset_probability", "asset_links": "linked_asset_ids",
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
INTEGER = {"ordinal", "asset_rank", "level_rank", "best_link_rank", "sentences", "documents",
           "linked_packages", "strong_packages", "text_start_offset", "text_end_offset", "pdf_text_start_offset",
           "pdf_text_end_offset", "word_count", "character_count", "row_count", "page_from",
           "page_to", "page_number", "part_number", "approval_fiscal_year", "pdf_size_bytes",
           "word_repair_count", "embedding_rank", "query_count", "queries_agreeing_count",
           "units_hit_count", "most_likely_grade", "feedback_round", "second_grade",
           "paragraphs", "packages", "notices", "awards", "sentences_embedded",
           "template_project_count", "cosine_rank", "graded_units", "grade",
           "graded_yes", "graded_no", "positives", "calls", "units_seen", "units_graded",
           "n_sentences"}
NUMERIC = {"estimated_amount_usd", "actual_amount_usd", "total_amount_usd", "supplier_amount_usd",
           "cost_usd_millions", "cost_before_restructuring_usd_millions",
           "commitment_usd_millions", "commitment_v2_usd_millions", "commitment_v3_usd_millions",
           "embedding_score", "embedding_percentile",
           "best_embedding_score", "best_embedding_percentile", "best_cosine", "expected_grade",
           "grade_confidence", "probability_grade_0", "probability_grade_1",
           "probability_grade_2", "probability_grade_3",
           "posterior_probability", "best_type_probability", "weight", "gate", "share_lost",
           "cost_usd", "seconds", "stratum_size", "stratum_drawn",
           "model_cost_usd", "precision", "recall",
           "paragraph_best_asset_probability",
           "unit_best_asset_probability", "linked_asset_probability",
           "template_wording_share", "score_first", "score_context", "digital_p", "strong_p",
           "cosine", "probability"}
BOOLEAN = {"is_for_model", "is_list_item", "is_rebid", "is_placeholder", "in_latest_plan",
           "in_projects_interface", "has_uncommitted_code", "is_active", "included",
           "was_sent_to_model", "was_asked", "is_committed", "passes_measure_rule", "is_digital",
           "has_asset", "same_type", "package_cancelled", "package_generic"}
# Pipe-joined lists in the CSV become arrays, which `= ANY(column)` queries without LIKE.
ARRAY = {"project_ids", "listed_project_ids", "notice_ids", "copy_unit_ids", "matched_patterns",
         "matched_types", "asset_ids_above_threshold", "type_ids_above_threshold",
         "linked_asset_ids",
         "component_mentions",
         "shared_asset_ids", "type_ids", "components"}
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

PROBABILITY_GRADES = {
    "grade_confidence": "The model's own confidence in its answer.",
    "probability_grade_0": "Probability of grade 0 (the passage is not about the label).",
    "probability_grade_1": "Probability of grade 1 (mentioned as background or another party's work).",
    "probability_grade_2": "Probability of grade 2 (supported, planned or conditional).",
    "probability_grade_3": "Probability of grade 3 (financed or committed). The four grade probabilities are the model's raw answer, kept as the record: no decision reads them one by one; the decision reads posterior_probability.",
    "posterior_probability": "The probability every decision reads: the model's probability of grades 2 and 3 through the label's probability layer (src/wbg_portfolio/posterior.py), or that score itself until a sheet graded under the current question has been evaluated. A sentence counts at the label's floor (control panel) or above.",
    "model_served": "The exact decision-model snapshot that answered.",
}

TABLES = {
    "projects": dict(
        key=("project_id",), foreign=[],
        comment="One row per cohort project: the projects interface record plus the managing "
                "unit read from its most recent appraisal document.",
        columns={
            "cohort_set": "Which hand-kept set of the cohort the project is in: digital (its object is a digital asset) or digital_contributor (a digital asset contributes to another sector's object). The filter every report should carry.",
            "in_projects_interface": "Whether the projects interface had a record for this id.",
            "commitment_usd_millions": "Total commitment, US$ millions, from the projects interface: its v3 form where it gives one, else v2. Not always the current Bank financing: the two forms can disagree, and v3 can show a cancelled amount. See the two columns below.",
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
        key=("document_id",), foreign=[],
        comment="One appraisal document (Project Appraisal Document or Project Paper) fetched "
                "as PDF; project_ids lists every operation it belongs to.",
        columns={
            "document_id": "The documents interface's id.",
            "project_ids": "Every operation the document belongs to (a regional document serves several): the interface's listing, or the operation its data sheet names when the interface files it under another.",
            "document_type": "The interface's document type.",
            "document_kind": "pad, additional_financing, restructuring or project_paper_other, from the type and title.",
            "disclosure_date": "When the document was disclosed.",
            "pdf_sha256": "SHA-256 of the fetched PDF; the parse is keyed on it.",
            "pdf_size_bytes": "Size of the PDF in bytes.",
            "owner_unit": "The unit the interface records as the document's owner, as printed.",
            "fetched_at": "When the PDF was downloaded.",
        }),
    "paragraphs": dict(
        key=("paragraph_id",),
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
        key=("unit_id",),
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
        key=("component_id",),
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
        key=("package_id",),
        foreign=[("project_id", "projects", "project_id"),
                 ("component_document_id", "documents", "document_id")],
        comment="One procurement-plan package, as its newest plan version states it. A planned "
                "date already past is what the plan records, not evidence of delay.",
        columns={
            "package_id": "project id and the borrower reference; plus a short hash of the description for a generic reference, and ':2' for a duplicate row.",
            "borrower_reference": "The reference the borrower gave the package, as printed.",
            "borrower_reference_normalised": "Upper-cased with every run of separators made one hyphen: the key notices and awards link on.",
            "description": "As printed in the plan.",
            "description_clean": "With other cells, markers and repaired words taken care of (src/wbg_portfolio/clean_procurement.py).",
            "description_match_key": "Lower-cased with spaces removed, for matching only.",
            "description_sha256": "SHA-256 of the cleaned description; a label is keyed on it.",
            "description_language": "en, fr, es or pt, by marker words.",
            "lot_or_phase_label": "'Lot 2', 'Phase II' or similar taken out of the description.",
            "component_number": "The component the plan names, in the appraisal document's numbering.",
            "component_name": "That component's name.",
            "component_matched_by": "name_match (the plan's component cell matched a name), number_only (only a number was printed), plan_pdf (the text reading found none; the plan PDF's own Component column gave it), or blank.",
            "component_document_id": "The document whose component list gave the number.",
            "word_repair_count": "Words re-joined by the word repair (src/wbg_portfolio/glue.py).",
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
            "amount_source": "plan (the Estimated Amount cell), description (a figure inside the description) or plan_pdf (neither of those; the plan PDF's Estimated Amount column).",
            "actual_amount_usd": "The plan's actual amount, newest version; a blank stays blank and a zero stays zero.",
            "plan_document_id": "The newest plan version that lists the package.",
            "plan_disclosure_date": "That plan's disclosure date.",
            "in_latest_plan": "False when the package is not in the project's most recently disclosed plan: dropped, or the plan was published in parts.",
        }),
    "notices": dict(
        key=("notice_id",),
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
        key=("award_id",),
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
        key=("paragraph_id", "project_id"),
        foreign=[("paragraph_id", "paragraphs", "paragraph_id"),
                 ("project_id", "projects", "project_id")],
        comment="Pipeline bookkeeping: one row per paragraph and project, unpacked from "
                "paragraphs.project_ids so a paragraph joins its project by key.",
        columns={}),
    "labels": dict(
        key=("label_set", "label_id"), foreign=[],
        comment="One label of one label set: the assets (inputs/taxonomy) and each measure "
                "set (analysis/labels), with its definition.",
        columns={
            "label_set": "The set's name: assets, or a measure set such as cra.",
            "label_name": "The label's name.",
            "definition": "The definition the search and the decision model were given.",
            "is_active": "Whether the label is searched and graded.",
            "version": "The hand-kept version of the row.",
            "family": "The family the measure belongs to (climate, cloud_data): measures of different "
                      "the app shows measures of one family together. Empty for the assets.",
            "folder": "Where the set lives in git.",
        }),
    "search_paragraphs": dict(
        key=("label_set", "label_id", "paragraph_id"),
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
        key=("label_set", "label_id", "unit_id"),
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
        })),
    "measure_findings": dict(
        key=("label_set", "label_id", "unit_id"),
        foreign=[("unit_id", "search_units", "unit_id"),
                 ("paragraph_id", "paragraphs", "paragraph_id")],
        comment="One unit seen by a measure run: the decision model's answer (the probability "
                "of each grade: 0 not described, 1 background, 2 planned, 3 committed), the "
                "posterior probability of grades 2 and 3 every decision reads, whether it "
                "is kept (is_committed: it passes the measure rule and is digital), and the asset it links to.",
        columns=dict(PROBABILITY_GRADES, **{
            "route": "pattern, embedding, both, or feedback (found by a relevance-feedback round).",
            "feedback_round": "0 for the candidate pool; 1 and up for relevance-feedback rounds.",
            "embedding_rank": "Rank for the label by best cosine (round 0) or by closeness to the feedback query.",
            "embedding_score": "Best cosine over the label's queries, or to the feedback query.",
            "matched_patterns": "Pattern ids that matched.",
            "firmness": "activity, outcome or none, from wording lists.",
            "was_sent_to_model": "False when a gate kept the unit from being graded.",
            "is_committed": "Kept: the sentence passes the measure rule (passes_measure_rule) and is digital (is_digital). This is what every table, view, download and count of commitments reads.",
            "passes_measure_rule": "The band rule alone: a first-pass score at band.keep or more, or in the band with both second looks agreeing (decided_by says which; control panel inputs/config/control_panel.csv, band.*). True for a sentence that is not digital too; those are listed in not_digital_findings and are not kept.",
            "paragraph_best_asset_id": "For a sentence whose own best asset is under the control panel's asset.floor: the asset with the highest sum of its own score and the earlier sentences' scores in its paragraph, each weighted by asset.decay per sentence back; empty otherwise.",
            "paragraph_best_asset_probability": "That decayed sum (at most 1).",
            "unit_best_asset_id": "The sentence asset map's best asset for the sentence itself.",
            "unit_best_asset_probability": "Its probability (grades 2 and 3, through the asset's probability layer when fitted).",
            "asset_ids_above_threshold": "The sentence's own assets at the control panel's asset.floor, best first (every one with asset.multiple, else the best).",
            "linked_asset_ids": "Every asset the finding is linked to at its level (asset.multiple), best first; linked_asset_id is the first.",
            "linked_asset_id": "The asset the finding is linked to: the sentence's own (level unit), else the decayed sum over its paragraph's earlier sentences (level paragraph), at the control panel's asset.floor and asset.paragraph_floor.",
            "linked_asset_level": "unit, second_look (the chat model's second look), paragraph or none.",
            "linked_asset_probability": "The probability behind the link.",
            "score_first": "The first-pass score the band rule reads: the posterior of grades 2 and 3 from the sentence asked alone.",
            "score_context": "The decision model's score for the same question asked with the paragraph beside the sentence; empty unless the pair's first-pass score was asked about (band.ask_low to band.ask_high; a look above band.keep decides nothing). Model-graded, not person-graded.",
            "second_grade": "The chat model's grade 0 to 3 for the pair, asked in the band; empty otherwise. Reading is the model's: a missing second look never counts as yes.",
            "decided_by": "score (first-pass score at band.keep or more), second_look (in the band, and both looks reach band.context and band.second_grade) or empty (not committed).",
            "is_digital": "True when the digital question (the sentence with its paragraph beside it, asked once whatever the measure) scores digital.floor or more. A sentence is kept (is_committed) only when this is true.",
            "digital_p": "P(yes) of the digital question; empty when not asked (it is asked of committed sentences only).",
            "instrument": "investment or policy, the chat model's reading of the committed sentence (flags.csv, policy); empty when not asked.",
            "model_cost_usd": "What the stored answers for this unit cost, US$.",
            "type_ids_above_threshold": "The unit's types (one fit question per type in analysis/labels/<set>_types.csv: yes, unsure or no): every type whose P(yes), through the types' layer once fitted, reaches the control panel's floor.types (the likeliest only without types.multiple); empty when none does or no list exists yet.",
            "best_type_id": "The likeliest type.",
            "best_type_probability": "Its probability.",
            "template_project_count": "As in search_units: other projects printing the same words.",
            "template_wording_share": "As in search_units: share of six-word runs shared with two or more other projects.",
            "copy_unit_ids": "Units in other documents that print the same text.",
            "text": "The unit's text with its list lead-in.",
        })),
    "measure_types": dict(
        key=("label_set", "label_id", "type_id"), foreign=[],
        comment="A measure's approved types (its clusters): proposed by a generative model "
                "from the committed units, approved by a person in analysis/labels/"
                "<set>_types.csv, assigned by the decision model with one fit question per type, yes, unsure or no (src/wbg_portfolio/kinds.py); a unit has every type whose probability reaches floor.types.",
        columns={"type_name": "The name shown (display_name in the file when given, else "
                              "the name the model was asked with).",
                 "definition": "The definition the decision model was given.",
                 "merged_from": "Type ids the file merges into this one (merge_into), "
                                "pipe-separated; their yes answers count as this type's."}),
    "asset_subtypes": dict(
        key=("asset_id", "subtype_id"), foreign=[],
        comment="The types of each asset (inputs/taxonomy/assets_subtypes.csv, kept by a person), "
                "one row per asset and type with its name and definition, so unit_assets.subtype_id "
                "can be read by name. Inactive types are listed too, marked.",
        columns={"asset_id": "The asset (a label of the assets set) the type belongs to.",
                 "subtype_id": "The type's id, as unit_assets.subtype_id carries it.",
                 "subtype_name": "The type's name.",
                 "definition": "The definition the chat model was shown for the type.",
                 "is_active": "Whether the type is offered to the chat model.",
                 "version": "The hand-kept version of the row."}),
    "flags": dict(
        key=("flag_id",), foreign=[],
        comment="The yes-or-no flags asked of a committed sentence whatever its measure "
                "(inputs/taxonomy/flags.csv, kept by a person): digital and policy, with the definitions "
                "the chat model was given. They are definitions, not label sets.",
        columns={"flag_name": "What the flag says of a sentence.",
                 "definition": "The definition the chat model was given.",
                 "version": "The hand-kept version of the row."}),
    "measure_quality": dict(
        key=("label_set", "question_sha256", "sheet"), foreign=[],
        comment="How well the pipeline decides a measure, against a sheet a person graded "
                "(./wbg_portfolio check evaluate): one row per measure, exact question wording "
                "and kind of sheet, the latest evaluation. Precision and recall are of the "
                "decision (posterior at the floor) against the person's grade 2 or more, "
                "weighted back to the pool, with recall within the graded pool, not the "
                "corpus. Only an evaluation sheet (a random draw) measures the dashboards; a "
                "training sheet holds the hard cases. Quote graded_units beside them. A holdout "
                "sheet (./wbg_portfolio check tune) is the holdout half of the evaluation sample, graded "
                "by three models, for the current settings: the band rule and the digital rule against "
                "two of three graders giving 2 or 3; recall among the pairs asked (first score 0.3 or more).",
        columns={"version": "The measure's version (its file's version column) when the "
                            "sheet was evaluated.",
                 "question_sha256": "SHA-256 of the exact question the sheet was graded "
                                    "under: a revised definition is a new row.",
                 "sheet": "holdout (the evaluation sample's holdout half, graded by three models), "
                          "evaluation (a random draw by the decision, which fits nothing) or "
                          "training (drawn near the floor).",
                 "graded_units": "Rows graded (scope overall: every holdout pair).",
                 "scope": "measure (the measure's own graded rows) or overall (a holdout sheet's "
                          "figures over every measure, for a measure with too few holdout rows of its own).",
                 "precision": "Of the units the pipeline counts, the weighted share the person "
                              "graded 2 or more.",
                 "recall": "Of the units the person graded 2 or more, the weighted share the "
                           "pipeline counts (within the graded pool).",
                 "report_path": "The evaluation report under data/reports/."}),
    "label_queries": dict(
        key=("label_set", "label_id", "query_id"), foreign=[],
        comment="A copy of each set's search phrases (<set>_queries.csv, kept by a person): "
                "the sentences the search ranks units against. Inactive rows are kept.",
        columns={"query_id": "The phrase's id.", "query": "The phrase.",
                 "is_active": "Whether the search uses it.",
                 "version": "The label's version (its set file's version column).",
                 "file_sha256": "SHA-256 of the file the row was copied from."}),
    "label_patterns": dict(
        key=("label_set", "label_id", "pattern_id"), foreign=[],
        comment="A copy of each set's word patterns (<set>_patterns.csv, kept by a person): "
                "regular expressions that add units to a measure's pool. Inactive rows are "
                "kept.",
        columns={"pattern_id": "The pattern's id.",
                 "pattern": "The regular expression, matched without regard to case.",
                 "is_active": "Whether the search uses it.",
                 "version": "The label's version (its set file's version column).",
                 "file_sha256": "SHA-256 of the file the row was copied from."}),
    "label_scale": dict(
        key=("label_set", "label_id", "grade"), foreign=[],
        comment="A copy of the grade scale each set uses (analysis/labels/scale.csv, kept by a person), one row "
                "per label and grade with the label's name filled in: the grades the decision "
                "model chooses among.",
        columns={"grade": "The grade, 0 the lowest.",
                 "description": "What the grade means, as the model is shown it.",
                 "version": "The label's version (its set file's version column).",
                 "file_sha256": "SHA-256 of the file the row was copied from."}),
    "package_assets": dict(
        key=("package_id", "label_id"),
        foreign=[("package_id", "packages", "package_id"),
                 ("project_id", "projects", "project_id")],
        comment="Which digital asset a procurement package is for: one row per package and "
                "asset asked (its best few by cosine over the asset taxonomy's phrases), with the "
                "decision model's score (how far the package is for the asset, P(grades 2, 3)) and "
                "its posterior (src/wbg_portfolio/checks.py). A package has the asset (has_asset) "
                "when posterior_probability reaches the control panel's link.asset_yes, or from "
                "link.asset_band_low when the chat model's second look says yes; a package may have "
                "several. subtype_id is the type of an asset it has, by the chat model.",
        columns={
            "label_id": "The asset's id in wbg.labels (label_set assets).",
            "cosine": "The package description's best cosine over the asset's search phrases.",
            "cosine_rank": "The asset's rank among the package's assets by that cosine; 1 is the best.",
            "probability": "The decision model's score: the probability of grades 2 and 3 on how far the package is for this asset.",
            "has_asset": "True when the package has the asset (see the table comment).",
            "subtype_id": "The asset's type for this package (assets_subtypes.csv), by the chat model; empty where the package does not have the asset or the type was not asked yet.",
            "model_served": "The decision-model snapshot that answered.",
        }),
    "unit_assets": dict(
        key=("unit_id", "asset_rank"),
        foreign=[("unit_id", "search_units", "unit_id"),
                 ("paragraph_id", "paragraphs", "paragraph_id")],
        comment="The assets of every sentence the search reads, one row per asset (asset_rank "
                "1 the best; with asset.multiple every asset at its floor; rank 0 a sentence "
                "with none), decided as the measure stage "
                "decides it: the sentence's own assets whose posterior reaches the "
                "control panel's asset.floor (level unit); else the assets whose sum of their "
                "own score and the earlier sentences' scores in its paragraph, each weighted by "
                "asset.decay per sentence back, reaches asset.paragraph_floor (level "
                "paragraph); else none. A committed, digital sentence with no asset at the floor is shown to the "
                "chat model, which names up to two assets or none (level second_look, no probability). Rebuilt at every publish from the current asset map "
                "and its probability layers.",
        columns={"asset_id": "The sentence's asset; empty at level none.",
                 "asset_rank": "1 for the sentence's best asset, 2 the next, and so on; 0 for a sentence with none.",
                 "probability": "The posterior (level unit) or the decayed sum (level paragraph).",
                 "subtype_id": "The type of the asset (assets_subtypes.csv), chosen by the chat model; empty where it was not asked or its answer was not one of the asset's types.",
                 "asset_level": "unit (the first pass), second_look (the chat model's second look at a committed, digital sentence the first pass gave no asset), paragraph or none.",
                 "own_best_asset_id": "The sentence's own best asset, whatever its probability.",
                 "own_best_probability": "Its posterior probability.",
                 "second_look_asset_id": "The assets the chat model named for this sentence (asset.ask_high: asked also where the first pass has an asset), joined by |, in the order it named them; empty where it was not asked or named none. Read it against asset_id to measure how often the two agree; it decides nothing where the first pass has an asset."}),
    "commitment_package_links": dict(
        key=("label_set", "unit_id", "package_id"),
        foreign=[("package_id", "packages", "package_id"),
                 ("unit_id", "search_units", "unit_id"),
                 ("project_id", "projects", "project_id")],
        comment="Which procurement packages a kept sentence is linked to, and how firmly: one row per "
                "sentence, measure and package of the same project. strength strong: the package, asked "
                "on its own, is about the sentence's measure (strong_p reaches link.strong_yes). strength "
                "asset: the package and the sentence share a top-level asset. level orders the links from "
                "most to least telling: strong (the package names the measure), type_and_component (same "
                "asset type and same top-level component), type (same asset type), component (same asset "
                "and same component), asset (same asset only); level_rank is 1 to 5 in that order. The "
                "levels add nothing and remove nothing: they only order the same links. A same-asset link "
                "at any level says the package and the sentence concern the same kind of asset, not that "
                "the package delivers the commitment. Built from the model readings at every publish. The "
                "models' reading, not a person's; documented is not done.",
        columns={
            "strength": "strong or asset: see the table comment.",
            "shared_asset_ids": "The top-level assets the sentence and the package share (empty for a strong link with none).",
            "strong_p": "The decision model's P(yes) that the measure applies to the package alone; empty when the package was not asked about this measure (each package is asked only its likeliest measures).",
            "package_category": "The package's procurement category (Goods, Works, Consulting and so on).",
            "level": "strong, type_and_component, type, component or asset: see the table comment.",
            "level_rank": "1 (strong) to 5 (asset): order links by it.",
            "same_type": "True when the sentence and the package have the same type of a shared asset (unit_assets.subtype_id and package_assets.subtype_id; unspecified never counts).",
            "component_relation": "same, different or unknown: the sentence's and the package's top-level component numbers, unknown when either is missing.",
            "sentence_component": "The sentence's top-level component, normalised (2.1 and Component 2 read as 2).",
            "package_component": "The package's top-level component in its plan, normalised the same way.",
            "package_cancelled": "True when the package's plan status is Canceled or Terminated (the one rule, decide.CANCELLED, that package_status also reads).",
            "package_generic": "True when the package description is about running the project (project management, audit, vehicles, office furniture, workshops, per diem, accounting, salaries; src/wbg_portfolio/decide.py GENERIC). A flag to filter on, never a cut.",
        }),
    "commitments": dict(
        key=("commitment_id",),
        foreign=[("project_id", "projects", "project_id")],
        comment="The commitments: the kept sentences of a project and measure grouped by what they commit to, "
                "with no model call (src/wbg_portfolio/decide.py commitment_rows). An investment sentence "
                "belongs to one commitment per asset type it has (asset_id and subtype_id; a sentence with "
                "two assets is in two); with no asset, to one per measure type; a policy sentence to one per "
                "measure type. What is left (no asset and no type, only an unspecified asset type, a policy "
                "sentence with no type) is grouped by paragraph. commitment_key says which key made it: "
                "asset_type, type or paragraph (a paragraph group is a bucket, not a finding). Every "
                "commitment is listed here, whatever the dashboards' filters; commitment_summary is what the "
                "dashboards read. A commitment in an appraisal document is design intent, not delivery.",
        columns={
            "commitment_id": "MD5 of project_id|label_set|instrument|commitment_key|asset_id|subtype_id|type_id|paragraph_id: the same on every publish.",
            "instrument": "investment or policy (the chat model's reading of the sentences; empty while not asked yet, and then grouped as investment).",
            "commitment_key": "asset_type, type or paragraph: which key grouped the sentences.",
            "asset_id": "The asset, for a commitment keyed on asset_type.",
            "subtype_id": "The asset's type, for a commitment keyed on asset_type.",
            "type_id": "The measure type, for a commitment keyed on type.",
            "paragraph_id": "The paragraph, for a commitment keyed on paragraph.",
            "commitment_label": "A plain name: the asset and its type, the measure type, or 'Sentences of one paragraph'.",
            "type_ids": "Tags: every measure type of its sentences.",
            "sentences": "Its kept sentences (distinct units).",
            "paragraphs": "The paragraphs they sit in.",
            "documents": "The documents they come from.",
            "components": "The component numbers its sentences sit under.",
            "linked_packages": "Packages linked to any of its sentences (commitment_package_links).",
            "strong_packages": "Of those, packages whose best level is strong.",
            "best_link_level": "The best level among its links (strong, type_and_component, type, component, asset); empty when none.",
            "best_link_rank": "That level as 1 (strong) to 5 (asset).",
        }),
    "commitment_members": dict(
        key=("commitment_id", "unit_id"),
        foreign=[("unit_id", "search_units", "unit_id"), ("project_id", "projects", "project_id")],
        comment="The sentences of each commitment: one row per commitment and kept sentence.",
        columns={"unit_id": "A kept sentence of the commitment."}),
    "sentence_vectors": dict(
        key=("unit_id",), foreign=[("unit_id", "search_units", "unit_id")],
        comment="Pipeline bookkeeping: one embedding per search unit (3,072 dimensions), used "
                "by wbg.similar_units() and wbg.similar_projects().",
        columns={}),
    "data_release": dict(
        key=("release_id",), foreign=[],
        comment="Pipeline bookkeeping: the single release this database holds, and when each "
                "kind of record was last fetched. wbg.data_as_of shows it.",
        columns={}),
    "loads": dict(
        key=None, foreign=[],
        comment="Pipeline bookkeeping: one row per table load, with the source file's SHA-256 "
                "(an unchanged source is not reloaded).",
        columns={}),
}


def qualified(table):
    """schema.table for a table name, as created."""
    from wbg_portfolio import db
    return f"{db.SCHEMA}.{table}"
