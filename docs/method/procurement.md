# Procurement

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 3. Procurement

The word lists the readers match against (status map, method names and method text, category map, language markers, placeholders, and the column catalogue of the Bank's procurement system, STEP) live in `src/wbg_portfolio/procurement_vocab.json`, one file for the fetch parser and the cleaner.

### 3.1 Fetch (`src/wbg_portfolio/fetch_procurement.py`)

1. List each project's plans (paged fetch, section 1) and download each text rendition.
2. **Bundles.** A rendition with more than one plan preamble concatenates the plans of several operations; it is cut to the segment naming this project. When none does, it is kept on disk, counted, and not parsed.
3. **Finding the tables.** A STEP table starts at a line holding only a section name (WORKS, GOODS, NON CONSULTING SERVICES, CONSULTING FIRMS, INDIVIDUAL CONSULTANTS) followed within three lines by the "Activity Reference No." column heading. Records are read only inside these tables; other text is counted as free text, never parsed.
4. **Layout.** Fixed-width and collapsed tables each have their own reader.
5. **Records, fixed-width.** A record opens on a line whose first column holds a borrower reference and runs to the next. A split reference is joined first. Column zero holds reference and description; its fragments are glued with nothing between, since the cell was clipped mid-word. A trailing loan number is cut. The rest is `cells_raw`, dates and figures removed; a reprinted column-heading line (including the "Planned / Actual" band) is dropped.
6. **Records, collapsed.** The record is read as a stream of text, figure and date pieces. The description runs from the reference to the first cell that is a review type, method, market approach or "Single Stage". A loan number between wrapped description lines is skipped when the text after it opens in lower case, and otherwise ends the description. The remainder is `cells_raw`. When the column headings are reprinted inside a record, or the columns are scattered, the record keeps its reference and description and gives up its figures, status and dates.
7. **Reference chain.** "BJ-UCP / PADA-43641-GO-RFQ / Fourniture ..." gives the last reference-shaped piece that carries a number as the borrower reference, and the rest as description.
8. **Cells.** Method, market approach and status are found among the cells by matching STEP's vocabularies in English, French, Portuguese and Spanish, ignoring accents, case and curly apostrophes, and ignoring spaces for a word wrapped mid-cell. A status wrapped and interleaved with other cells is matched from its head when the head opens exactly one status (at least 10 characters in the parser, 6 in the cleaner). Words are kept as printed.
9. **Dates.** The first date in the record is the planned date; the last is `revised_date`, the last milestone column as printed, which in a STEP row is usually the latest actual date, not a revised plan. Every date column is ISO (`YYYY-MM-DD`) or blank (`src/wbg_portfolio/dates.py`); a date the reader cannot place is blank, never stored as printed.
10. **Figures.** Any whitespace separates figures. With an Estimated Amount column, the first figure is the estimate and the second the actual. Without one (older plans), the only figure is the actual and the estimate is blank.
11. **Notices and awards** keep the interface's fields (reference, description, dates, procurement group, method, country; for awards also amount, review type, supplier, region, sector). An individual-consultant contract's supplier is a person's name; it is kept as published, and showing it more widely is a judgement for a person.
12. **Output.** `data/procurement/{packages_raw,notices_raw,awards_raw}.csv`, one package row per plan version per package, as printed; nothing is derived. A fetch of selected projects (`--projects`) rewrites only the rows it fully re-read; every other project, and a project whose plan listing, plan download, notices or awards request failed (each table separately), keeps its earlier rows.

### 3.2 Clean (`src/wbg_portfolio/clean_procurement.py`)

**For every description (packages, notices, awards), in order:**

1. Fold characters (2.4), remove the replacement character "�", collapse whitespace.
2. **Other cells cut out.** A loan number ("IDA / 12345") between wrapped lines is removed when the text after it opens in lower case; otherwise it and everything after it are cut. Then the review type, method, market approach and "Single Stage" tail is cut. A review type ("Post", "Prior") is cut only when a method or market approach follows (alone it may be a word of the description, "Mbeya Post"); a market approach or "Single Stage" is never prose and is cut wherever it ends the text.
3. **Words repaired** (`src/wbg_portfolio/glue.py`, 3.3).
4. **Markers read.** A borrower reference opening the description is removed (it has its own column). "Lot 2", "Lots 1-3", "Lote 3", "Phase II", "Tranche 2" go to `lot_or_phase`, taken out of the text only where they open or close it. "(Rebid)", "Re-tender", "Relance" set `is_rebid` and are removed.
5. **Stitched records.** If another package's borrower reference appears inside the description (a missed record boundary), the text from it onwards is cut.
6. **Component name at the end.** A component name spilled from the plan's component column onto the end is cut off and used as the package's component.

**For each package version:**

7. **Component.** Only the plan's Component column is read, never the description's words. The cell, or the spilled tail, is matched against the project's components (`components.csv`) by the longest stretch of a component's name it carries, ignoring spaces and punctuation (at least 12 characters, 15 for a partial name), and used only when it identifies one component. A name takes its number from the most recent document that lists it, since restructurings renumber. Failing that, a numbered cell ("Component 3: ...") is read: if the words after the number open the name the appraisal side gives that number, it is that component; otherwise only the plan's number is kept. `component_source` is `name_match`, `number_only`, or blank.
8. **Category.** The STEP section (or the reference's group code GO, CW, CS, NC) maps to `goods`, `works`, `consultant_services` or `non_consulting_services`.
9. **Method** is the Bank's code (RFB, RFQ, RFP, DIR, CDS, QCBS, QBS, FBS, LCS, CQS, INDV, UN, FA) with `method_name`, taken from the first that gives one: the Method cell's words in four languages (`method_cell`); the cell clipped at its column edge, found among the other cells, such as "Request for Propo" (`clipped_method_cell`); the code STEP wrote into the borrower reference ("...-GO-RFB"), including older and French spellings (CI/IC/IND to INDV, QCB/SFQC to QCBS, SQC to CQS, ED to DIR) and suffixed codes ("RFBREBID", "RFQ2") (`reference`). Direct selection is DIR for goods, works and non-consulting services and CDS for consulting services, as STEP numbers them. There is no "other": a method none of these gives is `unknown`. Notices use the interface's method code; awards its method name alone.
10. **Market approach** is the cell as printed, a wrapped "Internationa l" mended.
11. **Status** is the label STEP prints: Pending, Pending Implementation, Under Implementation, Under Review, Signed, Completed, Canceled, Terminated, Planned. French, Portuguese and Spanish words map to the same labels (Achevé to Completed, Résilié to Terminated). A wrapped word is matched with spaces removed; a clipped word ("En attente d'") when it opens exactly one status. Nothing is merged: Pending and Pending Implementation stay different. `status_raw` keeps the printed word.
12. **Amounts and dates in the text.** An amount ("US$ 99.00", "USD 1.5 million", "2,5 millions FCFA": a scale word multiplies; a comma followed by one or two digits and no point is a decimal comma) or date inside the description fills the estimate or planned date only when the plan's own column is empty (`amount_source` = `description`). The date is ISO or not stored; a numeric date that reads both ways ("03/06/2024") is left blank.
13. **Flags and keys.** `is_placeholder` is true for a description that cannot be matched to anything: empty, "TBD", "N/A", a bare category name, a reference only, cell words only, or under two words. `description_lang` is English, French, Spanish or Portuguese by marker words; a tie gives English. `borrower_ref_norm` is the reference upper-cased with each run of separators one hyphen. `description_match` is the cleaned description lower-cased with spaces removed, so a lost space never stops a match. `description_sha256` is the SHA-256 of the cleaned description, the key a label is stored under.

**Across plan versions (one row per package):**

14. Versions are grouped by project and normalised reference; a generic reference one plan uses for several packages ("CS-INDV") is also grouped by description. `package_id` is "project:reference" (plus a short description hash for those). A plan that prints the same reference and description twice with different figures, status or dates lists two packages, the second told apart by order (`package_id` ending ":2"). A row repeated identically in one version is one package, kept once.
15. The newest version (by disclosure date; on the same date the larger document id) gives the row. `in_latest_plan` says whether the package is in the project's most recently disclosed plan (every version on the latest disclosure date). A package that is not was dropped or the latest plan was published in parts; its status is the older plan's. The summary counts these separately (`upcoming_not_in_latest_plan`) without excluding them; whether they count as upcoming is a judgement for a person.
16. **Nothing is carried from an older version.** A status, amount or date the newest row leaves blank stays blank; a zero stays zero. `amount_source` is `plan` (the Estimated Amount cell) or `description`. The actual amount is the newest version's.
17. Older versions go to `superseded_packages.csv` (package, project, plan version, reference, description, status, estimated amount), each pointing at its replacement.
18. **Status with its evidence.** The more recent dated evidence wins, labelled in `status_source` and `status_as_of`: `plan` (the newest version's status, as of its disclosure date); `award` (a signed contract linked to the package by step 19, as of signing; a contract with no signing date is not evidence; it never turns Completed or Terminated back into Signed); `none` (status stays `unknown`). On the same date a signed contract wins over the plan.

**Links (`src/wbg_portfolio/links.py`):**

19. Every notice and award is linked to its plan package by project and normalised borrower reference; a reference chain is first reduced as in 3.1 step 7. `package_link` says how: `reference` (one package has it); `reference_and_description` (several share a generic reference and the description's match key picks one); `ambiguous` (several share it and the description does not pick; left unlinked); `not_in_plan` (no package has it: a plan not disclosed, or a reference printed differently); `no_reference`. Nothing is linked on a guess.
20. Every award also lists in `notice_ids` the notices of the same project and normalised reference, so a contract meets its tender even when no disclosed plan holds the package. Where both are linked to a package it must be the same one; an unlinked award does not take a notice whose reference was ambiguous.

### 3.4 The plan PDF as an enrichment (`src/wbg_portfolio/plan_pdf.py`, `clean_procurement.merge_plan_pdf`)

The text rendition loses the table's columns; the plan's PDF keeps them. The PDF is read **only to fill what the text reading left blank**, never to replace anything.

1. **Read.** pdfplumber's table extraction (default line detection, no model) on every page of `data/raw/plan_pdf/<plan document id>.pdf`, for the plan documents the cleaned packages draw on (`clean_procurement.main` passes their ids to `plan_pdf.read_all`, which reads no other PDF); the PDFs are fetched under the same rule (`fetch.md` 1). A table's heading row is found by the words of its cells with whitespace and case ignored (a column edge breaks "Estimated Am / ount"); the reference, Component and Estimated Amount column names, in English, French, Spanish and Portuguese, are in `procurement_vocab.json` (`plan_pdf_headers`). A table that continues on the next page repeats no heading: the last heading of the same width is used. The borrower reference is the first cell's text up to its "/", read with every space removed (a clipped cell breaks it anywhere; text the row above left at the top is skipped); a cell with no reference is an unread row. The amount must be a plain number (stored as `21828.00`, US$ as in the text reading). Rows are cached in `data/procurement/plan_pdf_rows.csv` keyed on the PDF's checksum, so a PDF is read once; the cache is rewritten as each plan is read, so a run that stops part way through keeps the plans already read and the next run reads only the rest.
2. **Match.** A PDF row is matched to the package of the same plan document with the same reference, compared as upper-case letters and digits only. A reference printed twice in the PDF, or by two packages of the plan, is not matched.
3. **Guard.** A plan's PDF is used only when at least `plan_pdf.min_match_share` (control panel, 0.5) of its data rows carry a reference that is one of the plan's packages; unread rows count against it. A plan whose cell text is scrambled in the PDF reads no reference and is rejected; it keeps the text reading alone.
4. **Fill.** `component_number` / `component` only where the text reading found none: the PDF's cell goes through the same matcher as the text reading's (`Components.in_cells`: the longest stretch of a component name, else the number the plan prints) and `component_matched_by` is `plan_pdf`. The estimated amount only where the text reading has none (neither the Estimated Amount cell nor a figure in the description): `amount_source` is `plan_pdf`.
5. **Never overwrite.** Where the text reading has a component and the PDF names another, the text reading stays and the disagreement is counted (`packages_plan_pdf_component_disagrees`; likewise a different amount, `packages_plan_pdf_amount_differs`). The description is not read from the PDF: its words are broken at the cell edge as in the text.
6. **Seen.** `./wbg_portfolio status` prints how many packages gained a component and an amount from the plan PDF; the clean stage prints the same, with the plans read, accepted and rejected by the guard, and `data/reports/clean_procurement.txt` lists the counters. A plan without a PDF, or whose PDF failed, is read from the text alone.

**Measured 2026-11-21** on 12 plans of 12 projects, run on a throwaway data folder with the real fetch and clean (no model). Counted per plan document, over the packages of that plan version; "disagreements" are packages where the text reading had a component and the PDF's cell names another (the text reading was kept). No value the text reading had was changed (checked by comparing the two packages tables).

| Plan | Packages | PDF rows read (matched) | Components filled | Amounts filled | Disagreements | Note |
|---|---|---|---|---|---|---|
| 40107975 | 283 | 378 (0) | 0 | 0 | | rejected by the guard: cell text scrambled |
| 40110606 | 243 | 243 (243) | 209 | 243 | 1 | |
| 40115160 | 264 | 264 (236) | 194 | 234 | 7 | |
| 40134071 | 140 | 140 (140) | 0 | 5 | 0 | Component column empty in the PDF |
| 40137886 | 122 | 124 (124) | 0 | 4 | 0 | Component column empty in the PDF |
| 40136923 | 191 | 192 (187) | 0 | 8 | 0 | components already read |
| 40032248 | 63 | 63 (63) | 51 | 0 | 0 | |
| 40121625 | 89 | 90 (90) | 0 | 2 | 0 | Component column empty in the PDF |
| 40134888 | 61 | 62 (61) | 0 | 0 | 0 | no Estimated Amount column; no component printed |
| 40112011 | 67 | 67 (67) | 43 | 0 | 0 | |
| 40130555 | 60 | 15 (15) | 0 | 0 | 0 | the extraction found 15 rows of 60 |
| 40108771 | 165 | 168 (168) | 90 | 127 | 3 | |

The first six are the plans of the trial that chose the method; the last six (different regions, approval years 2020 to 2024, chosen for many packages without a component) were not seen before. Across the 12 plans, 575 packages gained a component and 599 an estimated amount. Reading takes about one second a page, once per PDF (the rows are cached by checksum); a re-run with nothing new gave a byte-identical `packages.csv`.


### 3.3 Word repair (`src/wbg_portfolio/glue.py`)

Plan cells are clipped at the column edge, so words come apart ("S upply") or run together ("ofDigital"). No dictionary is used; every decision comes from counts in this corpus. Only package, notice and award descriptions are repaired.

- **Vocabulary.** Every distinct description (counted once however many plan versions repeat it) plus the cleaned appraisal text. The appraisal text is English and free of these defects, so it decides for English descriptions; French, Portuguese and Spanish ones are judged against the descriptions alone.
- **Joining.** Two or three neighbouring fragments join when the joined word occurs at least 3 times (`MIN_SEEN`); the fragments are not all words in their own right ("in formation" stays); no later fragment opens with a capital; and they are not an acronym followed by a word ("I T equipment"). When a two- and a three-fragment join both pass, the more common word wins. A function word at either end stays separate when the rest is already a word ("Pr ovision of" gives "Provision of"). A three-letter join is allowed for a function word ("a nd") or one- and two-letter shards ("E-G ov"). The first piece of a hyphenated word and letters ending a code ("ID4D P roject") stay separate.
- **Moving a space.** "forth e" becomes "for the" when the second piece is not a word, the first new word is a function word and the second a word the prose uses.
- **Splitting.** A token is split when it is not itself a word the corpus uses; both halves are, or the left half is a function word ("andPemba"); the right half is not a common ending ("Auditeur" is not "Audit eur"); it is not an acronym of six capitals or fewer; and it is not a mixed-case name the appraisal prose uses whole ("GovNet"). A token the descriptions repeat is split only when both halves are at least 5 times (`RATIO`) more common than it, and not into a half of three letters or fewer unless the prose uses that half all the time ("contable" stays).
- **Fused pairs, last.** A repeated clipping can make a fused form look like a word. A token is split into a function word and a word when both halves are at least 5 times more common than the token, one half is a function word (the left, or a right one of four letters or more such as "dans"), and the appraisal prose does not use the token whole ("another" stays). "in" is excluded, since English uses it as a prefix.
- **Punctuation.** A bracket or comma run into the next word gets a space.
- **Order.** Join, split, join once more, then split fused pairs.

Four short hand-written lists block or permit a decision but never supply a word: about 35 function words in the four languages, about 30 two-letter words, about 50 word endings, and Roman numerals.
