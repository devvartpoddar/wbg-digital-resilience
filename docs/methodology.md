# Digital for Resilience: method

## What this is for

For a lending project, the pipeline answers four questions:

1. Which digital assets does it finance?
2. Which resilience measures does the appraisal document commit to for those assets?
3. How firm is each commitment?
4. What procurement is coming up against those same assets?

**Terms.** Project Appraisal Document (PAD): the document the Board approves, describing what a lending operation finances. Task Team Leader (TTL): the World Bank staff member leading that operation; the output helps an advisor decide which TTLs to talk to, and when. Systematic Tracking of Exchanges in Procurement (STEP): the system through which borrowers publish procurement plans.

**Three statements go with every output.** A commitment in an appraisal document is design intent, not delivery. Nothing here is causal. The pipeline misses real commitments and the miss rate is not known.

## The principle

Every decision the pipeline makes must be:

1. **Repeatable.** The same text gets the same answer on every run, so a figure shown to a task team does not move next week.
2. **Scored.** Every decision is a probability with a cut-off someone chose, so precision can be traded for recall deliberately. Precision is preferred: a false commitment costs credibility with a task team; a missed one costs an opportunity.
3. **Checkable.** It is measured against examples a person has checked, and the measurement is reported with its sample size.

Any model that meets all three may be used, including an external one. For an external model that means: pin the exact version, store every response on the box, and treat the stored responses as the record. A provider can change or retire a model without notice; a re-run reads the store instead of asking again. Changing the version is a deliberate step followed by re-measurement.

## Where the work stands

| Stage | State |
|---|---|
| Fetch appraisal documents (PDFs), procurement records and project records | Built |
| Projects table: name, status, dates, practice, managing unit | Built |
| Clean appraisal documents into paragraphs and sentences | Built; under review |
| Collect each project's components and costs; tag paragraphs with their component | Built |
| Clean procurement packages, notices and awards | Built; under review |
| Link notices and awards to their plan package | Built |
| Load into Postgres, write review sheets and the run report | Built |
| Embed paragraphs into pgvector | Next, once cleaning is accepted |
| Judge paragraphs for assets and measures | Next: a test of a pinned external decision model (referred to as Jev) that returns a yes probability |
| Labels for measuring it | To be drawn fresh. Keyed by the SHA-256 of the unit's text plus the name and date of the label set; nothing but evaluation reads them |

## 0. Running it

`./run.sh` (the same as `./wbg pipeline`) runs every stage below in this order, then loads Postgres, writes the review sheets and drafts the run note. `./wbg` does the same for a selection of the cohort:

1. **Select.** Filters on the included rows of `inputs/config/cohort.csv`:
   - `--project`;
   - `--country` (country code);
   - `--region` (case-insensitive, part of the name is enough);
   - `--fy` (approval fiscal year);
   - `--practice` (Global Practice name or code, or part of one: "digital", "transport");
   - `--unit` (managing unit or its code, or part of one: "IDD04", "AFR EAST").

   Each takes several values; different filters combine with "and". A project outside the cohort is refused. Region, country and year are the cohort's; where the cohort records none, the projects table's (section 1.1) is used. Practice and unit come from the projects table only, so they need a prepared store. A project with no value for a filter is not selected by it; `--project` always can.
2. **Fetch only what is new.** `data/reports/stages.json` records which projects have been fetched, for appraisal documents, procurement and project records separately. `./wbg prepare` fetches only selected projects not recorded there; `--update` fetches the selected projects again, requesting notices and awards afresh (plan renditions already on disk are kept, since a published plan does not change). A project counts as fetched only when its fetch succeeded: each fetcher writes the projects it fully read to `data/reports/fetch_status/`, and a project whose listing or download failed is tried again on the next run. Fetching one project keeps every other project's rows (sections 2.1 and 3.1). A store made before this file existed counts every project it already holds as fetched.
3. **Rebuild only what changed.** Every later stage runs over the whole store, never one project alone, so the shared tables stay whole. `stages.json` keeps a SHA-256 of each stage's code and input files; a stage runs only when that changes. Within a stage, the per-document caches (keyed on the PDF's checksum and the reading code's version) skip unchanged documents. A second `prepare` with nothing new does nothing.
4. **Read and export.** `./wbg summary` prints, for each selected project, its status and closing date, the latest procurement plan's date, its packages by status, the packages still to come (status Pending, Pending Implementation, Under Review or Planned) with their estimated value, how many of those have a planned date already past, the next planned date, open notices (deadline not yet passed) and signed contracts. `./wbg export` writes the same to `data/runs/<selection>-<date>/summary.xlsx` (sheets: projects, with name, practice and managing unit; upcoming packages, each with the notices linked to it; open notices, each with its package's status and estimate; components, the components and costs taken from each project's latest appraisal document), and with `--tables` every filtered table as CSV. A planned date already past is what the plan records, not evidence of delay: the plan may simply not have been updated.

Each stage reads only the files the earlier stages wrote, skips work whose inputs have not changed, and writes its own report to `data/reports/`. Every number marked "on the current run" comes from those reports and will move when the documents do.

## 1. What is pulled

| Source | What | Interface | Kept in |
|---|---|---|---|
| The cohort list | The 70 projects to cover. Kept by people, never written by the code | `inputs/config/cohort.csv` | — |
| Appraisal documents | Every Project Appraisal Document (PAD) and every Project Paper (additional financing or restructuring) disclosed for each project, as PDF | World Bank Documents and Reports interface: document id, type, title, disclosure date, language, PDF address | `data/raw/pdf/` |
| Procurement plans | Every procurement plan disclosed for each project, as the text rendition the Bank publishes (plans are not read from PDF) | Documents and Reports interface, type "Procurement Plan" | `data/raw/plans/` |
| Procurement notices | Every notice published for each project | Procurement notices interface | `data/raw/notices/` |
| Contract awards | Every signed contract recorded for each project | Contract awards interface | `data/raw/awards/` |
| Project records | Each project's name, status, approval and closing dates, region, country, lending instrument, commitment, Global Practice and top three sectors | Projects interface (`search.worldbank.org/api/v2/projects`), only the fields listed in `src/fetch_projects.py` | `data/raw/projects/` |

Nothing else is pulled. The Bank's text renditions of appraisal documents are no longer fetched; the ones fetched earlier stay on disk and are not used. Every request is logged in `data/raw/fetch_log.csv`. A file already on disk is not downloaded again; when a forced re-download (`--refresh`) returns different bytes, the old file is kept beside the new one, named by its checksum. Nothing fetched is ever deleted or overwritten.

Personal contact details in notices (names, e-mail addresses, telephone numbers) and the staff names in award records are not pulled. The projects interface also publishes the project team's names and e-mail addresses; they are not requested. The appraisal documents' `owner` field (the unit that owns the document) is kept in `documents.csv` as `owner_unit`.

### 1.1 The projects table (`src/fetch_projects.py`, `src/projects.py`)

1. For each project, request its record from the projects interface, asking only for the fields above. The record is stored as `data/raw/projects/<project id>.json`; a record that changes between fetches keeps its old copy beside the new one. A project the interface has no record of is stored as an empty record and reported (`in_projects_interface` false); a project whose request failed has no file yet and is left blank, not reported as missing, and is requested again on the next run.
2. One row per cohort project in `data/projects/projects.csv`:
   - from the projects interface: `project_name`, `status` (Active, Closed, Pipeline), `approval_date`, `approval_fy`, `closing_date`, `region`, `country_code`, `country_name`, `lending_instrument`, `commitment_usd_m` (current total commitment, US$ millions), `practice` and `practice_code` (the Global Practice(s) leading the project), `sectors` (the top three, with their shares);
   - from the appraisal documents: `managing_unit`, the owner recorded on the project's most recent appraisal document that records one, printed as recorded, with `unit_doc_id` and `unit_as_of` naming that document and its date; `unit_code` is the code inside it ("Digital Dev - AFR EAST/SOUTH (IDD04)" gives IDD04).
   - `in_projects_interface` says whether the interface had a record.
3. **On the current run:** 67 of 70 projects are in the projects interface (P508317, P508948 and P511767 are not); 62 have a practice (Digital Development 46, Governance 6, Education 6, Finance, Competitiveness and Innovation 3, Social Protection and Jobs 1); 66 have a managing unit, 65 with a code (IDD04 14, IDD02 13, IDD01 9, others 29); 55 have a closing date. Status: Active 56, Pipeline 7, Closed 2, none 5.
4. **What is not there.** Neither interface publishes the vice presidency as a field, so there is no such column. The document owner is uneven: usually a practice unit, sometimes a country office or a regional vice presidency's office, and blank on most recent papers, which is why the most recent document that records one is used.

## 2. Appraisal documents

### 2.1 Fetch (`src/fetch.py`)

1. For each included project, list its documents of type Project Appraisal Document and Project Paper.
2. Label each document's kind from its type and title: `pad`; `additional_financing` (a Project Paper whose title says additional financing); `restructuring` (title says restructuring); otherwise `project_paper_other`.
3. Download the PDF. A document with no PDF, or whose address returns something that is not a PDF, is noted in the report and left out.
4. Write one row per document to `data/raw/documents.csv`: document id, all the projects it serves (a regional document serves several, joined with "|"), type, kind, title, disclosure date, language, when it was fetched, the PDF's address, size and SHA-256.
5. A project not listed on this run (outside the selection being fetched, or because its listing failed) keeps the rows an earlier run recorded for it, so a partial run never shrinks the corpus.

On the current run: 147 documents for the 70 projects: 65 PADs, 13 additional financings and 69 restructuring papers.

### 2.2 Read the PDF (`src/pdf_layout.py`)

Each PDF is read by layout with pdfplumber 0.11.10 (the version is pinned in requirements.txt). Every threshold is measured relative to the document's own body type size and line spacing, so no rule depends on one template. The result for each document is cached (`data/appraisal/cache/`) against the PDF's SHA-256 and a checksum of the reader's code (`pdf_layout.py` and `text_rules.py`), so an unchanged document is never read twice and any change to the rules re-reads every document.

**Page by page:**

1. Drop every character set at an angle, read from its text matrix. This removes the "Public Disclosure Authorized" stamp down the page margin.
2. Read the lines of text with their position, size, and whether they are bold or italic. A line made only of characters under 6 points is dropped.
3. Keep a copy of every line exactly as the PDF gives it. Together these form the document's raw text, written to `data/raw/pdf_text/{doc_id}.txt` (one line per line, a form feed between pages).
4. Within each line:
   - remove footnote markers: a digit, comma or asterisk that is both smaller than the line (under 80 per cent of its size) and raised above its baseline. A small digit set lower, as in CO2, stays. The number a footnote itself opens with stays;
   - turn a bullet drawn from a symbol font (Wingdings, Symbol, a Courier "o") into "•";
   - fold characters (section 2.4);
   - remove hidden template codes the Bank's authoring system leaves in the text layer (for example a name made of capitals joined by underscores);
   - collapse runs of spaces.
5. Find the ruled tables on the page, ignoring a frame that covers the whole page.

**Across the document:**

6. The body type size is the size most characters are set in. The width of a full line of body text is measured per page.
7. A running header or footer is a line that recurs at the same height (to within 4 points), with the same letters ignoring spaces and digits, on at least 30 per cent of pages (and at least 3). Those lines are removed.
8. Also removed: page-number lines ("12", "Page 3 of 40"); classification banners ("Official Use Only", with or without a page number); dated footers ("Oct 12, 2023 Page 27 of 29"); lines of hidden template code; contents-page lines (those ending in dot leaders and a page number, or a section or annex heading followed by a bare page number).
9. The footnotes on a page are the unbroken run of lines at its foot set smaller than 93 per cent of body size. The footnote numbers each page defines are recorded.
10. A number run onto the end of a word ("hoped44") is removed when the footnotes of this page or the next define that number. A number after a capital letter (IDA19, FY01) is never touched.

**Each remaining line is labelled:**

11. *Table row*: the line falls inside a ruled table (section 2.3).
12. *Footnote*: the line is in the footnote run.
13. *Heading*: the line is bold, italic or larger than body type, has at most 14 words, does not end in . ; , or :, and is narrower than 80 per cent of a full line. A unit opening "Component", "Sub-component", "Figure", "Table", "Box", "Map" or "Chart" followed by a number or capital, with at most 30 words and not ending in . or ;, is also made a heading. Headings and these titles also serve as the caption for a table that follows within a page.
14. *Body*: everything else.

**Lines are grouped into units (paragraphs):**

15. A body line continues the paragraph above when it follows at the normal line spacing on the same page (up to 1.45 times the median spacing, `pdf_layout.LINE_GAP`); or the paragraph had not finished its sentence and the page turned; or the paragraph had not finished its sentence, the line opens in lower case, and a table, heading or footnote came in between; or the paragraph had not finished its sentence, the line opens in lower case, and it sits within three line-spacings (`pdf_layout.LOOSE_GAP`; a paragraph set with looser spacing).
16. A line does not continue the paragraph when it opens a new block: a bullet glyph always does; a paragraph number ("23.") or an enumerator ("(ii)", "a)", "3)") does only when the text before it ended at a stopping point (. : ; ! ? or a closing bracket or quote, or "and"/"or"). A wrapped cross-reference such as "as described in sub-part (ii) above" therefore stays one paragraph.
17. A word broken by a hyphen at a line end is joined without the hyphen when the document uses the whole word unbroken elsewhere; otherwise the hyphen is real and kept ("climate-resilient").
18. A footnote line that does not open with a number continues the footnote above; the number a footnote opens with is removed from its text (a year such as 2016 at the start is not mistaken for one).

**Each unit is then finished:**

19. A leading bullet glyph is removed and the unit is marked `list_item`. A unit opening with an enumerator is also marked `list_item`. The last body paragraph ending in ":" before a list is recorded as the list's lead-in (`lead_in_id`).
20. Every web address becomes "[link]", including the pieces a line break left after a space. (1,881 on the current run.)
21. A unit with no letter or digit at all (a lone "$" from a logo) is dropped. (113 on the current run.)
22. Sections: a line such as "II. PROJECT DESCRIPTION" opens a top-level section; "ANNEX 2: ..." opens an annex (only after the first top-level section, so the contents page cannot); a lettered heading "B. Project Components" opens a sub-section. Each unit records its section path ("II.B") and title.
23. Each unit gets a block:
    - `heading`;
    - `frontmatter`: anything before the first top-level section (cover, data sheet, abbreviations);
    - `narrative`: body text in the main sections;
    - `annex`: body text in an annex;
    - `footnote`;
    - `table`: a table row, or a body unit that reads as a flattened table (at least six tokens, 35 per cent or more of them figures or 20 per cent of its characters digits, and under 45 per cent words of more than three letters). A table before the first top-level section is filed as front matter.
24. Each unit records the pages it spans and the stretch of the raw PDF text it was built from (`raw_start`, `raw_end`).

### 2.3 Tables (`src/pdf_layout.py`, `_clean_table`)

1. Cells that wrap keep pdfplumber's line breaks; the pieces are joined, with a word broken at a hyphen mended as in prose.
2. Hidden template text in a cell is removed. Columns empty in every row are removed.
3. The header row is the first row of two or more labels that a later row answers with a figure. Single-cell rows above it are titles; the last title is the table's caption. A table with no title of its own takes the last heading or title line on its page or the one before, unless a line of body prose (more than six words) came after that heading: then the heading opened a section, not the table, and the table has no caption (a data sheet that follows "G. Key Risks" and its text is not captioned "G. Key Risks").
4. A row of small numbers under the header is a sub-header: "Intermediate Targets" over "1 2" becomes "Intermediate Targets 1", "Intermediate Targets 2".
5. A header repeated mid-table starts a new segment, with the title before it as the new caption.
6. A table ruled only around its edge gives one row per printed line; label fragments and their figures are regrouped into one row.
7. A table that continues on the next page without reprinting its header borrows the previous page's header when it has the same number of columns and starts the page.
8. Each row becomes one unit: "Caption — Header: value · Header: value". When no header was found, the cells are joined with " | ".

Tables are kept in the tables for reading. They are not given to the model and are not quality-reviewed.

### 2.4 Character folding (`src/text_rules.py`), used on both corpora

- Non-breaking, thin and zero-width spaces become a space or nothing.
- Curly quotes become straight ones; en dashes, minus signs and the two Unicode hyphens become "-"; soft hyphens are removed.
- Ligatures (ﬁ, ﬂ, ﬀ, ﬃ, ﬄ) are spelt out.
- Bullet glyphs (●, ➢, ▪ and the Symbol and Wingdings bullets left as private-use code points) become "•". Symbol's private-use brackets, space and full stop become "[", "]", " " and ".".
- Other invisible format characters are removed.
- Check marks are left alone: in a safeguards table they are data.
- Not applied: full Unicode compatibility folding (NFKC), which would change the meaning of some symbols.

### 2.5 Paragraphs and sentences (`src/clean.py`)

1. A document is cleaned only when its PDF was read and holds at least 2,000 characters of text; a scan with no text layer, or an unreadable PDF, is listed in the report and not cleaned. (none on the current run.)
2. **Which project each part of a document belongs to.** The documents interface files a combined appraisal document under every operation it appraises. The Inclusive Digitalization in Eastern and Southern Africa (IDEA) programme document is listed under the regional IDEA project (P502532), DRC, Angola and Malawi. It is the regional operation's document (its first data sheet names P502532), followed by one annex per country, each opening with that country's own data sheet, then annexes for the whole programme. The document is therefore split into parts, recorded in `data/appraisal/document_parts.csv`:
   - before the first country annex: the operation the first data sheet names;
   - each annex holding a later data sheet: the operation that data sheet names (by its Operation ID), up to the next annex heading with a different number (a table can repeat its annex's title on every row);
   - after the last such annex: every listed project (`programme_wide`).

   A paragraph takes the project(s) of the part it starts in, and components are read part by part, so each country keeps its own list and costs. A document listed under one project, or one whose data sheets name none of its listed projects, is one part with the listing (a parent project and its additional financing share a paper that names no single operation). On the current run one document is split: IDEA, into the regional part, DRC, Angola, Malawi and a programme-wide part.
3. A unit with fewer than 5 words is dropped, unless it is a heading or a section or annex title, and recorded in `data/appraisal/rejected.csv` with its reason. (4,345 on the current run.)
4. Each kept unit becomes a row of `data/appraisal/paragraphs.csv` with: a positional identifier (`{doc_id}:p00042`), the project(s), section path and title, block, character offsets into the cleaned text file (the text itself is not stored in the table), word count, SHA-256 of the text, pages, the parser's identity, `for_model`, `list_item`, `lead_in_id` (blank when the lead-in was itself dropped), `table_id`, `raw_start`/`raw_end`, and the component tags (section 2.6).
5. `for_model` is true for `narrative`, `annex` and `footnote` paragraphs, except a footnote that is only a reference and a link (fewer than 15 words besides "[link]"). It is false for front matter, tables and headings. Embedding and the decision model read only these paragraphs. (18,475 of 56,512 paragraphs: 10,394 narrative, 4,193 annex and 3,888 footnotes on the current run.)
6. Narrative and annex paragraphs are split into sentences, stored in `data/appraisal/sentences.csv` as offsets inside their paragraph. A boundary is a full stop, question or exclamation mark followed by a space and a capital, digit or opening bracket, except:
   - after an abbreviation (e.g., i.e., etc., No., para., U.S., Mr., Fig., months and about forty others), or a single-letter initial;
   - inside the leading paragraph number ("38.");
   - where the sentence so far has fewer than three words ("See above." stays with what follows).
   Footnotes are not split: they are mostly references.
7. The re-parse check: before writing, each paragraph is compared with the previous run's. A paragraph whose text changed while the parser did not means the read is not reproducible; the run stops and writes nothing. A change under a new parser is expected and is counted in the report.
8. A paragraph identifier repeated across documents stops the run (it would mean a regional document was listed twice).

### 2.6 Components (`src/components.py`)

**Collected per project, into `data/appraisal/components.csv`:**

1. Pages that hold a component table are found in the cleaned text, by the column headings "Component Name" or "Current Component".
2. All tables on that page and the next are read from the PDF, top to bottom, as one grid. A restructuring paper's table of changes is read first (current and proposed names, the proposed cost, the current cost kept as `cost_before_usd_m`, and the action); otherwise the data sheet's component table (name and cost). Costs are in millions of US dollars; a figure over 10,000 is read as dollars and converted. A row with no cost continues the name above it, unless it opens with "Component N" or the next component's number ("3. ...", whose cost is printed on the next page). Rows whose name holds no word are dropped, and the list ends at a financing line ("IDA", "Front-end Fee", "Total").
3. Body headings "Component N: ..." and "Sub-component N.M: ..." add components and sub-components, with any cost printed in the heading.
4. Each name is cleaned: its number and printed cost removed, a following sentence cut off ("... This component will ..."), figures from neighbouring columns removed, a name read twice collapsed, and a heading that opens in lower case discarded. "Unallocated" and contingency budget lines are kept but not numbered; the Contingent Emergency Response Component (CERC) keeps its number.
5. One row per component or sub-component per document that states it: projects, document, kind, disclosure date, where it was read (`datasheet`, `restructuring`, `heading`), level, number, name, `name_key`, cost, cost before a restructuring, action, page. Several restructurings record large cuts (Haiti's components go from US$56.5m to US$8.1m; one project's from US$200m to US$100m), which the projects interface's commitment does not yet show. The reads are cached against the PDF, the pages searched and the module's code. (966 rows covering all 70 projects on the current run.)

**Paragraphs are tagged by position:**

6. A paragraph takes the component of the last "Component N" heading above it, and the sub-component of the last "Sub-component N.M" heading. A long paragraph that opens with "Sub-component 2.2: ..." (or "31. Sub-component 2.2: ...") opens that sub-component too.
7. The tag closes at the first section that is not the one the component heading was found in, or inside it: after the last component in "II.B Project Components", "II.C Project Beneficiaries" carries no component.
8. `component_mentions` separately lists every component a paragraph names in its text ("Components 1 and 3").

7,399 paragraphs carry a component tag, 4,824 of them among the paragraphs given to the model on the current run.

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
   - The rest of the line is the record's other cells, kept as `cells_raw` with dates and figures removed.
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
   - A status wrapped and interleaved with other cells ("Pending Impl … ementat … ion", "Under Imple … mentation") is matched from its head when the head opens exactly one status.
   - The words are kept as printed.
9. **Dates.** The first date in the record is the planned date; the last is the revised date.
10. **Figures.** Where the table has an Estimated Amount column, the first figure is the estimate and the second the actual. Where it has none (older plans), the only figure is the actual, and the estimate is left blank.
11. **Notices.** Notice id, project, notice type and status, publication and deadline dates, the borrower reference the notice was issued under, description, procurement group, the Bank's method code and name, country, notice language.
12. **Awards.** Contract id, project, borrower reference, description, signing and no-objection dates, contract amount, procurement group, method name, review type, supplier name, country and amount, region and sector.
13. **Output.** Written to `data/procurement/{packages_raw,notices_raw,awards_raw}.csv`: one package row per plan version per package, as printed. Nothing is derived at this stage. Fetching a selection of projects (`--projects`) rewrites only the rows it fully re-read: every other project keeps the rows an earlier run recorded, and so does a project whose plan listing, a plan download, its notices or its awards request failed (each table separately).

### 3.2 Clean (`src/clean_procurement.py`)

**For every description (packages, notices and awards), in this order:**

1. Fold characters (section 2.4), remove the replacement character "�" the interfaces emit for a lost byte, and collapse whitespace.
2. **Other cells cut out.** A loan number ("IDA / 12345") between two wrapped lines is removed when the text after it opens in lower case; otherwise it and everything after it are cut. Then the review type, method, market approach and "Single Stage" tail is cut ("... Post Request for Bids Open - National"), when those cells appear together.
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

   Direct selection is DIR for goods, works and non-consulting services and CDS for consulting services, as STEP numbers them. There is no "other": a method none of these gives is `unknown`. For notices, the interface's own method code is used.
10. **Market approach** is the cell as printed, with a wrapped "Internationa l" mended ("Open - National", "Limited - International", "Direct").
11. **Status** is the label STEP prints: Pending, Pending Implementation, Under Implementation, Under Review, Signed, Completed, Canceled, Terminated, Planned. French, Portuguese and Spanish words map to the same labels (for example Achevé → Completed, Résilié → Terminated, En attente d'exécution → Pending Implementation). A word split by a wrap is matched with its spaces removed; a clipped word ("En attente d'") is matched when it opens exactly one status. Nothing is merged: Pending and Pending Implementation stay different. `status_raw` keeps the word as printed.
12. **Amounts and dates in the text.** An amount ("US$ 99.00") or date written inside the description fills the estimate or planned date only when the plan's own column is empty (`amount_source` = `description`).
13. **Flags and keys.**
    - `is_placeholder` is true for a description that cannot be matched to anything: empty, "TBD", "N/A", a bare category name, a reference only, cell words only ("Post Open - National"), or under two words.
    - `description_lang` is English, French, Spanish or Portuguese, by marker words; a tie gives English.
    - `borrower_ref_norm` is the reference upper-cased with every run of separators made one hyphen.
    - `description_match` is the cleaned description lower-cased with every space removed. It is used for matching, so a lost space never stops a match.
    - `description_sha256` is the SHA-256 of the cleaned description: the key a label is stored under.

**Across plan versions (one row per package):**

14. Package versions are grouped by project and normalised reference. A generic reference that one plan uses for several packages ("CS-INDV") is also grouped by description. `package_id` is "project:reference" (plus a short hash of the description for those). A plan that prints the same reference and description twice with different figures, status or dates lists two packages: the second in a version is told apart by its order (`package_id` ending ":2"), so neither is lost behind the other. A row repeated identically in one version is one package printed twice and is kept once.
15. The newest version (by disclosure date) gives the row. `in_latest_plan` says whether the package is in the project's most recently disclosed plan (every plan version on the latest disclosure date). A package that is not was dropped from the plan, or the latest plan was published in parts; its status is the one the older plan gave. The summary counts such packages separately (`upcoming_not_in_latest_plan`) and does not exclude them; whether they should count as upcoming is a judgement left to a person.
16. **Nothing is carried from an older version.** The newest version's row is kept whole: a status, amount or date it leaves blank stays blank, and a zero stays zero. `amount_source` says where the estimated amount came from: `plan` (the newest version's Estimated Amount cell) or `description` (a figure printed inside the description, when the cell was empty). The actual amount is also the newest version's.
17. Older versions are written whole to `superseded_packages.csv`, each pointing at the version that replaced it.
18. **Status with its evidence.** The more recent dated evidence wins, labelled in `status_source` and `status_as_of`:
    - `plan`: the newest version's own status, as of its disclosure date;
    - `award`: a signed contract linked to the package (step 19), as of signing. It never turns Completed or Terminated back into Signed;
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
- 1,930 plan documents from 52 projects (9 projects published no plan); 116,919 package rows, one per plan version per package. 582 renditions parse to no package: 566 carry no reference anywhere (an empty table), 16 are parse failures.
- 6,171 packages after grouping (110,690 older versions in `superseded_packages.csv`).
- Method: INDV 1,857, RFQ 1,128, RFB 894, CQS 857, QCBS 685, CDS 269, DIR 221, RFP 139, LCS 47, QBS 27, UN 3, FBS 2, FA 1, unknown 50. Read from the Method cell 4,684, the clipped cell 702, the reference 744.
- Status: Canceled 1,683, Signed 1,579, Under Implementation 840, Pending Implementation 794, Completed 614, Pending 74, Terminated 49, Under Review 42, Planned 1, unknown 504. Source: plan 5,452, award 224, none 504.
- Component: matched by name 4,591, plan number only 174, none 1,415.
- Amount: from the newest plan 4,719, none 1,461. Actual amounts recorded on 5,607 packages. 28 packages exist because a plan printed the same reference and description twice with different figures; 58 rows printed twice identically were kept once.
- Notices: 5,909. Linked to a package 3,966 (by reference 3,954, by reference and description 12); not linked: no package with that reference 1,859 (536 in projects with no disclosed plan), ambiguous 46, no reference 38.
- Awards: 3,485. Linked 2,227; not linked: no package with that reference 1,234 (423 in projects with no disclosed plan), ambiguous 24. 3,005 awards name a notice of the same reference. Most unlinked references either carry no STEP activity number ("110/NCS/INAGE-MDAP/24") or carry one that no disclosed plan holds; a few are lots or re-bids of a plan package ("-A", "-2"), which are not linked to it.

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

## 4. Checks and review

**Automated checks** (`src/audit.py`, `src/audit_procurement.py`) count known defects:
- *Appraisal prose* (narrative and annex): footnote markers glued to words, paragraphs opening as footnote bodies, inline page numbers, column gutters, tabular rows in prose, paragraphs starting mid-sentence or ending without punctuation, unfolded characters, private-use and control characters.
- *Procurement*: private-use, replacement and control characters; glued and broken words; second references; mid-word endings; missing or unnormalised references; placeholders; unmapped category, method and status; markers left at the edges; other cells left in a description; component not found; date and amount inconsistencies in the source.

Each check has a gate in the tests, set just above the measured rate, so a regression fails.

**Reading by eye** (`src/review_sheets.py`): two spreadsheets in `data/review/` with empty `ok` and `note` columns.
- *Paragraphs*: 100 paragraphs drawn from those given to the model. Each shows the cleaned text, its sentences, and the stretch of raw PDF text it was built from (including anything cleaning removed from between its lines).
- *Packages*: 100 packages with the raw description beside every cleaned field.

An existing sheet is never overwritten; a new draw takes a new `--seed`.

**Sensitivity** (`src/sensitivity.py`, `./wbg sensitivity`): how much the cleaned text depends on three settings, written to `data/reports/sensitivity.md`. Nothing the pipeline writes is changed.
- *Paragraph joining*: a sample of 10 documents (PADs and additional financings first, chosen by a hash of their id) is read again from the PDF with the line gap at 1.30 and 1.60 and the loose gap at 2 and 4; the report gives the number of model paragraphs and the share of the default's model text in paragraphs that no longer exist word for word. This re-reads PDFs and is slow on the box.
- *Component tags*: every document is tagged with a tag closing at the section change (the default) and with it running on to the next component heading; the report gives how many model paragraphs are tagged each way and how many differ.
- *Sentences*: every narrative and annex model paragraph is split with the word minimum at 3 (default), 2 and 4, and without the abbreviation list; the report gives the sentence count and how many paragraphs split differently.

**Postgres** (`src/load_pg.py`): every table above is loaded into schema `wbg` in database `work`, with paragraph and sentence text resolved from the offsets. A table whose file has not changed is not reloaded.

**Reports** (`src/run_report.py`): drafts a run note in `data/reports/`, which is copied into the notes vault at `Projects/WBG Digital Resilience/Reports/`. Reports never go into the repository.

## 5. Storage

- **Files**: `data/` in the main checkout (`/ygg/projects/wbg-digital-resilience/data/` on the box). Gitignored, never deleted. Every file has one home, defined once in `src/paths.py`:
  - `raw/`: what was fetched, as published (`pdf/`, `pdf_text/`, `plans/`, `notices/`, `awards/`, `projects/`, `documents.csv`, `fetch_log.csv`, and the old `text/` renditions);
  - `appraisal/`: cleaned text (`text/`), per-document caches (`cache/`), `paragraphs.csv`, `sentences.csv`, `rejected.csv`, `components.csv`, `document_parts.csv`;
  - `procurement/`: packages, notices and awards, raw and cleaned, and superseded package versions;
  - `projects/`: `projects.csv`;
  - `embeddings/`: vectors and their index, when they come;
  - `reports/`: every stage's report and the run notes;
  - `review/`: the review spreadsheets;
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
  | awards | notices | `notice_ids`: the notices issued under the same reference (and, where both are linked, the same package) |
  | notices, awards | projects | `project_id` |

  So a paragraph about a component, the packages that buy it, the notices that tendered them and the contracts that were signed can be read together.
- **Postgres**: schema `wbg` in database `work`, a queryable copy of the files.
- **Embeddings, when they come**: `halfvec(3072)` with an HNSW index on cosine distance; plain `vector` cannot be indexed past 2,000 dimensions. Only paragraphs with `for_model` true are embedded. The full-precision vectors stay in the on-disk cache as the record.

## 6. Known limits

- **Results-framework indicators are tables**, so the model does not see them, though some state measured commitments (for example a share of infrastructure built to withstand climate shocks).
- **Component tags inside annexes.** Annexes have no lettered sections, so a later heading in the same annex that is not a component does not close the last one.
- **Tables without ruling lines read as text**: their cells are read in page order and the unit is usually labelled `table` by its figures.
- **Clipped words stay clipped.** Where a plan cell was cut and the rest of the word is gone ("Project Management Suppor"), nothing can restore it.
- **French, Portuguese and Spanish word repair is weaker** than English: it has only the descriptions to judge by. Some broken words remain ("con sultant").
- **Some plans print no component column** (1,415 of 6,180 packages have none).
- **Scattered renditions give up their figures.** Where the text extractor scattered a table's columns, the rendition's references and descriptions are kept and its amounts, status and dates are refused rather than guessed.
- **Projects with no package rows**: 9 projects publish no procurement plan (P169945, P170910, P171791, P180987, P181416, P506791, P508317, P508363, P511767), and 9 more have plans that parse to no package (P171099, P174620, P175218, P175987, P177158, P179204, P180693, P180807, P502532).
- **Award descriptions** occasionally carry "?" where the interface lost an accented letter; that is in the source.
- **One plan's references are slash-coded** ("…/PHN-20/CQS-002"), so its packages can carry a second reference in the description (6 packages).
- **Status is a fact at a date.** `status_as_of` is the date of the plan or contract that stated it, not today. A package whose newest plan prints no status stays `unknown` unless a signed contract matches it; an older plan's status is not used.
