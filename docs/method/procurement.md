# Procurement

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 3. Procurement

### 3.1 Fetch (`src/fetch_procurement.py`)

1. For each project, list its procurement plans and download each one's text rendition.
2. **Bundles.** Some renditions concatenate the plans of many operations. A rendition with more than one plan preamble is cut to the segment that names this project. When no segment names it, the rendition is kept on disk, counted, and not parsed. (22 bundles cut to their project's plan, 7 not parsed on the current run.)
3. **Finding the tables.** A STEP table starts at a line holding only a section name (WORKS, GOODS, NON CONSULTING SERVICES, CONSULTING FIRMS, INDIVIDUAL CONSULTANTS) followed within three lines by the "Activity Reference No." column heading. Records are read only inside those tables; text outside them is counted as free text, never parsed.
4. **Layout.** A rendition prints a table either at fixed character positions (columns separated by runs of spaces) or collapsed (no column positions). Each is read by its own reader.
5. **Records (fixed-width).**
   - A record opens on a line whose first column holds a borrower reference and runs to the next.
   - A reference split across two lines, or with the row's other cells printed between its halves, is joined back first.
   - Column zero holds the reference and the description; its fragments are glued with nothing between them, because the cell was clipped mid-word.
   - A trailing loan number is cut from the fragment.
   - The rest of the line is the record's other cells, kept as `cells_raw` with dates and figures removed. A reprinted column-heading line inside a record, including the "Planned / Actual" band under the milestone headings, is dropped rather than read as a cell.
6. **Records (collapsed).**
   - The record is read as a stream of text, figure and date pieces.
   - The description runs from the reference to the first cell that is a review type, a method, a market approach or "Single Stage".
   - A loan number between the description's wrapped lines is skipped when the text after it opens in lower case (the rest of a word); otherwise it ends the description.
   - The remaining text is `cells_raw`.
   - When the table's column headings are reprinted inside a record (the page turned there), or the rendition scattered its columns, the record keeps its reference and description and gives up its figures, status and dates.
7. **The reference chain.** "BJ-UCP / PADA-43641-GO-RFQ / Fourniture ..." gives the last reference-shaped piece that carries a number as the borrower reference, and the rest as the description.
8. **Cells.**
   - The method, market approach and status are found among the record's cells by matching STEP's own vocabularies in English, French, Portuguese and Spanish. These are the same word lists the cleaning maps from (section 3.2).
   - Accents, case and curly apostrophes are ignored when matching.
   - A word wrapped mid-cell is matched with spaces ignored.
   - A status wrapped and interleaved with other cells ("Pending Impl … ementat … ion", "Under Imple … mentation") is matched from its head when the head opens exactly one status (at least ten characters of it in the parser; at least six in the cleaner, where the cell has already been isolated).
   - The words are kept as printed.
9. **Dates.** The first date in the record is the planned date; the last is kept as `revised_date`. It is the last milestone column's date as printed, which in a STEP row is usually the latest actual date, not a revised plan.
10. **Figures.** Any whitespace separates figures. Where the table has an Estimated Amount column, the first figure is the estimate and the second the actual. Where it has none (older plans), the only figure is the actual, and the estimate is left blank.
11. **Notices.** Notice id, project, notice type and status, publication and deadline dates, the borrower reference the notice was issued under, description, procurement group, the Bank's method code and name, country, notice language.
12. **Awards.** Contract id, project, borrower reference, description, signing and no-objection dates, contract amount, procurement group, method name, review type, supplier name, country and amount, region and sector. The supplier name is a person's name for an individual-consultant contract; it is kept, as the interface publishes it, and is a judgement for a person before the tables are shown more widely.

Every date column in every table is ISO (`YYYY-MM-DD`) or blank (`src/dates.py`): a date the reader cannot place is left blank, never stored as printed, because the summaries and audits compare dates as strings.
13. **Output.** Written to `data/procurement/{packages_raw,notices_raw,awards_raw}.csv`: one package row per plan version per package, as printed. Nothing is derived at this stage. Fetching a selection of projects (`--projects`) rewrites only the rows it fully re-read: every other project keeps the rows an earlier run recorded, and so does a project whose plan listing, a plan download, its notices or its awards request failed (each table separately).

### 3.2 Clean (`src/clean_procurement.py`)

**For every description (packages, notices and awards), in this order:**

1. Fold characters (section 2.4), remove the replacement character "�" the interfaces emit for a lost byte, and collapse whitespace.
2. **Other cells cut out.** A loan number ("IDA / 12345") between two wrapped lines is removed when the text after it opens in lower case; otherwise it and everything after it are cut. Then the review type, method, market approach and "Single Stage" tail is cut ("... Post Request for Bids Open - National"). A review type ("Post", "Prior") is cut only when a method or a market approach follows it, since alone it is as likely a word of the description ("Mbeya Post"); a market approach or "Single Stage" is never prose and is cut wherever it ends the text.
3. **Words repaired** (`src/glue.py`, section 3.3).
4. **Markers read.**
   - A borrower reference opening the description is removed; it has its own column.
   - "Lot 2", "Lots 1-3", "Lote 3", "Phase II" or "Tranche 2" go to `lot_or_phase`. They are taken out of the text only where they open or close it ("... - Lot 2", "(Phase II)"), so the words left still read.
   - "(Rebid)", "Re-tender" and "Relance" set `is_rebid` and are removed.
5. **Stitched records.** If another package's borrower reference appears inside the description (the parser missed a record boundary), the text from it onwards is cut.
6. **Component name at the end.** A component name spilled from the plan's component column onto the end of the description is cut off and used as the package's component.

**For each package version:**

7. **Component.** Only the plan's Component column is read, never the description's own words. The cell, or the spilled tail from step 6, is matched against the project's components from `components.csv` by the longest stretch of a component's name it carries, ignoring spaces and punctuation (at least 12 characters, or 15 for a partial name). The match is used only when it identifies one component. Each name takes its number from the most recent document that lists it, since restructurings renumber. Failing that, a numbered cell ("Component 3: ...") is read: when the words after the number open the name the appraisal side gives that number, it is that component; otherwise only the plan's number is kept. `component_source` is `name_match`, `number_only`, or blank when the plan prints no component.
8. **Category.** The STEP section (or the reference's group code GO, CW, CS, NC) maps to `goods`, `works`, `consultant_services` or `non_consulting_services`.
9. **Method** is the Bank's code (RFB, RFQ, RFP, DIR, CDS, QCBS, QBS, FBS, LCS, CQS, INDV, UN, FA), with `method_name` beside it. It is taken from the first of these that gives one:
   - the Method cell's words, in four languages (`method_source` = `method_cell`);
   - the Method cell clipped at its column edge, found among the row's other cells, such as "Request for Propo" (`clipped_method_cell`);
   - the code STEP wrote into the borrower reference ("...-GO-RFB"), read from the normalised reference, including older and French spellings (CI/IC/IND → INDV, QCB/SFQC → QCBS, SQC → CQS, ED → DIR) and codes with a suffix ("RFBREBID", "RFQ2") (`reference`).

   Direct selection is DIR for goods, works and non-consulting services and CDS for consulting services, as STEP numbers them. There is no "other": a method none of these gives is `unknown`. For notices, the interface's own method code is used; for awards, the interface's method name alone.
10. **Market approach** is the cell as printed, with a wrapped "Internationa l" mended ("Open - National", "Limited - International", "Direct").
11. **Status** is the label STEP prints: Pending, Pending Implementation, Under Implementation, Under Review, Signed, Completed, Canceled, Terminated, Planned. French, Portuguese and Spanish words map to the same labels (for example Achevé → Completed, Résilié → Terminated, En attente d'exécution → Pending Implementation). A word split by a wrap is matched with its spaces removed; a clipped word ("En attente d'") is matched when it opens exactly one status. Nothing is merged: Pending and Pending Implementation stay different. `status_raw` keeps the word as printed.
12. **Amounts and dates in the text.** An amount ("US$ 99.00", "USD 1.5 million", "2,5 millions FCFA": a following scale word multiplies, a comma followed by one or two digits and no point is a decimal comma) or date written inside the description fills the estimate or planned date only when the plan's own column is empty (`amount_source` = `description`). The date is stored as ISO or not at all; a numeric date that reads both ways ("03/06/2024") is left blank.
13. **Flags and keys.**
    - `is_placeholder` is true for a description that cannot be matched to anything: empty, "TBD", "N/A", a bare category name, a reference only, cell words only ("Post Open - National"), or under two words.
    - `description_lang` is English, French, Spanish or Portuguese, by marker words; a tie gives English.
    - `borrower_ref_norm` is the reference upper-cased with every run of separators made one hyphen.
    - `description_match` is the cleaned description lower-cased with every space removed. It is used for matching, so a lost space never stops a match.
    - `description_sha256` is the SHA-256 of the cleaned description: the key a label is stored under.

**Across plan versions (one row per package):**

14. Package versions are grouped by project and normalised reference. A generic reference that one plan uses for several packages ("CS-INDV") is also grouped by description. `package_id` is "project:reference" (plus a short hash of the description for those). A plan that prints the same reference and description twice with different figures, status or dates lists two packages: the second in a version is told apart by its order (`package_id` ending ":2"), so neither is lost behind the other. A row repeated identically in one version is one package printed twice and is kept once.
15. The newest version (by disclosure date; on the same date, the larger document id) gives the row. `in_latest_plan` says whether the package is in the project's most recently disclosed plan (every plan version on the latest disclosure date). A package that is not was dropped from the plan, or the latest plan was published in parts; its status is the one the older plan gave. The summary counts such packages separately (`upcoming_not_in_latest_plan`) and does not exclude them; whether they should count as upcoming is a judgement left to a person.
16. **Nothing is carried from an older version.** The newest version's row is kept whole: a status, amount or date it leaves blank stays blank, and a zero stays zero. `amount_source` says where the estimated amount came from: `plan` (the newest version's Estimated Amount cell) or `description` (a figure printed inside the description, when the cell was empty). The actual amount is also the newest version's.
17. Older versions are written to `superseded_packages.csv` (package, project, plan version, reference, description, status, estimated amount), each pointing at the version that replaced it.
18. **Status with its evidence.** The more recent dated evidence wins, labelled in `status_source` and `status_as_of`:
    - `plan`: the newest version's own status, as of its disclosure date;
    - `award`: a signed contract linked to the package (step 19), as of signing; a contract with no signing date is not evidence. It never turns Completed or Terminated back into Signed;
    - `none`: no evidence; the status stays `unknown`.

**Links (`src/links.py`):**

19. Every notice and every award is linked to the plan package it was procured under, by project and normalised borrower reference. A notice or award that prints a reference chain ("BJ-UCP / PADA-43641-GO-RFQ") is reduced to its package reference by the same rule as the plans (step 7) before normalising. `package_id` names the package; `package_link` says how:
    - `reference`: one package in the project has this reference;
    - `reference_and_description`: several packages share the reference (a generic "CS-INDV"), and the description's match key picks one;
    - `ambiguous`: several share it and the description does not pick one; left unlinked;
    - `not_in_plan`: no package in the project has this reference (a plan not disclosed, or a reference printed differently);
    - `no_reference`: the record carries none.

    Nothing is linked on a guess. The award status in step 18 uses this link.
20. Every award also lists in `notice_ids` the notices of the same project and normalised reference, so a contract meets its tender even when no disclosed plan holds the package. Where both are linked to a package it must be the same one; an unlinked award does not take a notice whose reference was ambiguous.

On the current run:
- 2,113 plan documents from 55 projects (9 projects published no plan; 6 published only plans with no package row); 131,610 package rows, one per plan version per package. 404 renditions parse to no package: 403 carry no reference anywhere (an empty table), 1 is a parse failure.
- 6,886 packages after grouping (124,665 older versions in `superseded_packages.csv`).
- Method: INDV 2,048, RFQ 1,191, RFB 1,024, CQS 933, QCBS 792, CDS 305, DIR 247, RFP 151, LCS 68, QBS 32, FBS 4, UN 3, FA 1, unknown 87. Read from the Method cell 4,990, the clipped cell 948, the reference 861.
- Status: Canceled 1,953, Signed 1,783, Under Implementation 974, Pending Implementation 875, Completed 678, Pending 74, Terminated 58, Under Review 52, Planned 2, unknown 437. Source: plan 6,212, award 237, none 437. On the same date a signed contract wins over the plan's status.
- Component: matched by name 5,173, plan number only 188, none 1,525.
- Amount: from the newest plan 5,341, none 1,545. Actual amounts recorded on 6,337 packages. 36 packages exist because a plan printed the same reference and description twice with different figures, dates, method or component; 59 rows printed twice identically were kept once.
- Notices: 5,909. Linked to a package 4,530 (by reference 4,514, by reference and description 16); not linked: no package with that reference 1,295 (363 in projects with no disclosed plan), ambiguous 46, no reference 38.
- Awards: 3,485. Linked 2,495; not linked: no package with that reference 963 (258 in projects with no disclosed plan), ambiguous 27. 3,005 awards name a notice of the same reference. Most unlinked references either carry no STEP activity number ("110/NCS/INAGE-MDAP/24") or carry one that no disclosed plan holds; a few are lots or re-bids of a plan package ("-A", "-2"), which are not linked to it.

### 3.3 Word repair (`src/glue.py`)

Plan cells are clipped at the column edge, so words come apart ("S upply", "Commissi on") or run together ("ofDigital", "DataCenter"). No dictionary file is used. Every decision is made from counts in this corpus:

- **The vocabulary.** Every distinct description (each counted once, however many plan versions repeat it), plus the cleaned appraisal text. The appraisal text is English and free of these defects, so for English descriptions it decides whenever it has a view. French, Portuguese and Spanish descriptions are judged against the descriptions alone.
- **Joining.** Two or three neighbouring fragments are joined when:
  - the joined word occurs at least 3 times;
  - the fragments are not all words in their own right ("in formation" stays);
  - no later fragment opens with a capital;
  - the fragments are not an acronym followed by a word ("I T equipment").

  When both a two- and a three-fragment join pass, the one making the more common word wins ("prior itaires ayant" becomes "prioritaires ayant", 54 uses, not "prioritairesayant", 3).

  A function word at either end stays separate when the rest is already a word ("Pr ovision of" becomes "Provision of", but "Commissi on" becomes "Commission"). A three-letter join is allowed for a function word ("a nd") or for one- and two-letter shards ("E-G ov"). The first piece of a hyphenated word stays separate ("for e-commerce"), and so do letters that end a code ("ID4D P roject" becomes "ID4D Project").
- **Moving a space.** "forth e" becomes "for the" when the second piece is not a word, and the first new word is a function word and the second a word the prose uses.
- **Splitting.** A token is split when:
  - it is not itself a word the corpus uses;
  - both halves are, or the left half is a function word ("andPemba");
  - the right half is not a common ending ("Auditeur" is not "Audit eur");
  - it is not an acronym of six capitals or fewer;
  - it is not a mixed-case name the appraisal prose uses whole ("GovNet").

  A token the descriptions repeat is split only when both halves are at least five times more common than it, and not into a half of three letters or fewer unless that half is a word English prose uses all the time ("contable" stays, not "con table").
- **Fused pairs, last.** A clipped cell often runs a function word onto the next word, and repeats it across many descriptions, so the fused form can look like a word ("unconsultant" occurs in 149 descriptions). A token is split into a function word and a word when both halves are at least five times more common than the token, one half is a function word (the left one, or a right one of four letters or more such as "dans"), and the appraisal prose does not use the token whole ("another" stays). "in" is not counted, since English uses it as a prefix ("ineligibility").
- **Punctuation.** A bracket or comma run into the next word gets a space.
- **Order.** Join, split, join once more (a split can leave a fragment that only then has a neighbour), then split fused pairs.
- **Accuracy, checked by hand.** On 120 randomly sampled edits, about 5% were wrong (for example "prop ice", "seg un do"); before the fused-pair pass and the join and short-half rules it was about 8%, and those rules changed 77 of 1,500 sampled descriptions, all for the better on inspection. The threshold of 3 uses was varied to 2, 5 and 10: about 3.5% of descriptions come out differently at 2 or 5. Appraisal text never goes through word repair; only package, notice and award descriptions do.

The rules use four short hand-written lists, which block or permit a decision but never supply a word:
- about 35 function words in English, French, Portuguese and Spanish;
- about 30 two-letter words;
- about 50 word endings;
- Roman numerals.
