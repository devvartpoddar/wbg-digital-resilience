# Appraisal documents

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 2. Appraisal documents

### 2.1 Fetch (`src/fetch.py`)

1. For each included project, list its Project Appraisal Documents (PADs) and Project Papers.
2. Label `kind` from type and title: `pad`; `additional_financing` (a Project Paper whose title says so); `restructuring`; otherwise `project_paper_other`.
3. Download the PDF (section 1 gives the checks). A document with no PDF, or no whole PDF, is noted in the report and left out.
4. `data/raw/documents.csv` has one row per document: id, all projects it serves (joined with "|"), type, kind, title, disclosure date, language, `fetched_at`, PDF address, size, SHA-256. A project not listed on this run keeps its earlier rows, so a partial run never shrinks the corpus.

### 2.2 Read the PDF (`src/pdf_layout.py`)

Each PDF is read by layout with pdfplumber. Every threshold is relative to the document's own body type size and line spacing, so no rule depends on one template; the thresholds are named constants in `pdf_layout.py` and `text_rules.py`.

The result is cached (`data/appraisal/cache/`) against the PDF's SHA-256 and the **parser version** (`clean.parser_version`): `clean.PARSER`, a hash of those named constants, and the pdfplumber and pdfminer versions. A changed constant or library re-reads every PDF once; other code edits do not. The version is recorded on every paragraph row.

**Page by page**

1. Drop characters set at an angle (the margin disclosure stamp) and characters drawn wholly off the page.
2. Read lines with position, size, bold and italic; drop a line made only of characters under 6 points.
3. Keep every line as given: the raw text, `data/raw/pdf_text/{doc_id}.txt` (form feed between pages).
4. In each line: remove footnote markers (a digit, comma or asterisk under 80 per cent of the line's size and raised above the baseline; CO2 stays, as does the number a footnote opens with); turn symbol-font bullets into "•"; fold characters (2.4); remove hidden template codes (capitals joined by underscores); collapse spaces.
5. Find ruled tables, ignoring a frame covering 80 per cent or more of the page.

**Across the document**

6. Body size is the size most characters use; full-line width is measured per page.
7. A running header or footer recurs at the same height (4-point bins) with the same letters (spaces and digits ignored) on at least 30 per cent of pages and at least 3; it is removed. Also removed: page numbers ("Page 3 of 40"), classification banners, dated footers, template-code lines, and contents lines (dot leaders and a page number, or a heading followed by a bare page number).
8. **Footnotes** are the unbroken run of lines at a page's foot set under 93 per cent of body size and no larger than the run's smallest numbered line plus 0.5 point, so an annex set a point smaller than body keeps its text. Where the run reaches the upper half of the page, footnotes start after the last gap wider than twice the run's line step that a numbered line follows. A run with no numbered line is a footnote carried from the page before only if it is at most 8 lines with body text above; otherwise it is body. Each page's footnote numbers are recorded.
9. A number run onto a word ("hoped44") is removed when this or the next page's footnotes define it. A number after a capital (IDA19, FY01) or after a word with a capital past its first letter (IPv6, GovNet2) is kept.

**Labelling a line**

10. *Table row*: its middle is within a ruled table's height and at least 90 per cent of its width within the table's; a bullet hung left of the ruling does not exclude it.
11. *Footnote*: in the footnote run.
12. *Heading*: bold, italic or 0.5 point above body size; at most 14 words; not ending in . ; , or :; under 80 per cent of a full line. A unit opening "Component", "Sub-component", "Figure", "Table", "Box", "Map" or "Chart" plus a number or capital, at most 30 words and not ending in . or ;, is also a heading. Headings serve as the caption of a table that follows within a page.
13. *Body*: the rest.

**Grouping lines into paragraphs**

14. A body line continues the paragraph above when it follows at normal spacing on the same page (up to 1.45 times the median, `LINE_GAP`); or the paragraph had not finished its sentence and the page turned; or it had not and the line opens in lower case and either a table, heading or footnote came between or it sits within three line spacings (`LOOSE_GAP`).
15. A bullet glyph always opens a block. A paragraph number ("23.") or enumerator ("(ii)", "a)", "3)") does so only when the text before ended at a stopping point (. : ; ! ? or closing bracket or quote, or "and"/"or"); a wrapped "sub-part (ii) above" stays whole, and so does the next enumerator of a list begun inline.
    A list item joins the paragraph above on a line of its own (its lead-in gives it meaning), across a page turn and footnotes but not past a heading, caption or table; its bullet is removed. A paragraph is its lead-in and all items.
16. A hyphen at a line end is dropped when the document uses the whole word elsewhere, otherwise kept ("climate-resilient"). A footnote line not opening with a number continues the footnote above; the opening number is removed.

**Finishing a unit**

17. A unit opening with a list item and having no paragraph to join is marked `list_item`.
18. Every web address (including pieces a line break left after a space) becomes "[link]". A unit with no letter or digit is dropped.
19. Sections: "II. PROJECT DESCRIPTION" opens a top-level section; "ANNEX 2: ..." an annex (only after the first top-level section, so the contents page cannot); "B. Project Components" a sub-section. Each unit records its section path ("II.B") and title.
20. Block: `heading`; `frontmatter` (before the first top-level section); `narrative`; `annex` (body in an annex); `footnote`; `table` (a table row, or a body unit that reads as a flattened table: at least 6 tokens, and 35 per cent or more figures or 20 per cent or more digit characters, and under 45 per cent words over three letters; before the first top-level section it is front matter).
21. Each unit records its pages and its stretch of raw text (`raw_start`, `raw_end`).

### 2.3 Tables (`pdf_layout._clean_table`)

1. Wrapped cells are joined (hyphen-broken words mended as in prose); template text and all-empty columns are removed.
2. The header is the first row of two or more labels that a later row answers with a figure. Single-cell rows above are titles; the last is the caption. A table without a title takes the last heading on its page or the one before, unless body prose of more than six words followed that heading (then it opened a section, not the table).
3. A row of small numbers under the header is a sub-header ("Intermediate Targets" over "1 2" gives "Intermediate Targets 1", "... 2"). A header repeated mid-table starts a new segment captioned by the title before it.
4. A table ruled only around its edge gives one row per printed line, label fragments and figures regrouped.
5. A table continuing without its header borrows the previous page's when the column count matches and it starts within the first three kept lines.
6. Each row is one unit: "Caption — Header: value · Header: value", or cells joined with " | " when no header.

Tables are kept for reading; they are not given to the model or quality-reviewed.

### 2.4 Character folding (`src/text_rules.py`), both corpora

- Non-breaking, thin and zero-width spaces become a space or nothing; soft hyphens and other invisible format characters are removed.
- Curly quotes become straight; en dashes, minus signs and Unicode hyphens become "-".
- Ligatures (ﬁ, ﬂ, ﬀ, ﬃ, ﬄ) are spelt out.
- Bullet glyphs, including Symbol and Wingdings private-use bullets, become "•"; Symbol's private-use brackets, space and full stop become "[", "]", " ", ".".
- Check marks stay (in a safeguards table they are data). Full Unicode compatibility folding (NFKC) is not applied: it changes the meaning of some symbols.

### 2.5 Paragraphs and sentences (`src/clean.py`)

1. A document is cleaned only if its PDF read and holds at least 2,000 characters (`PDF_MIN_CHARS`); a scan without a text layer or an unreadable PDF is listed and not cleaned. A failed read (unreadable file, memory exhausted) is not cached and is retried.
2. **Project of each part.** The documents interface files a combined document under every operation it appraises. Such a document (a regional programme, then one annex per country each opening with that country's data sheet, then programme-wide annexes) is split into parts (`data/appraisal/document_parts.csv`): before the first country annex, the operation the first data sheet names; each annex holding a later data sheet, the operation that sheet names (by Operation ID), up to the next annex heading with a different number; after the last such annex, and between country annexes with no data sheet, every listed project (`programme_wide`). A paragraph takes the project(s) of the part it starts in, and components are read part by part. A document under one project, or whose data sheets name none of its listed projects (a parent and its additional financing), is one part.
3. A unit under 5 words (`clean.MIN_TOKENS`) is dropped, unless a heading or section or annex title, and recorded with its reason in `data/appraisal/rejected.csv`.
4. Each kept unit is a row of `data/appraisal/paragraphs.csv`: positional id (`{doc_id}:p00042`), projects, section path and title, block, character offsets into the cleaned text file (the text is not in the table), word count, text SHA-256, pages, parser version, `for_model`, `list_item`, `table_id`, `raw_start`/`raw_end`, component tags (2.6).
5. `for_model` is true for `narrative`, `annex` and `footnote` paragraphs, except a footnote that is only a reference and a link (under 15 words besides "[link]"); false for front matter, tables and headings. Embedding and the decision model read only these.
6. **Sentences** (`data/appraisal/sentences.csv`, offsets within the paragraph) are made for narrative and annex paragraphs. A boundary is . ? or ! followed by a space and a capital, digit or opening bracket, except after an abbreviation (e.g., i.e., etc., No., para., U.S., Mr., Fig., months, about forty others) or single-letter initial, inside the leading paragraph number ("38."), or where the sentence so far has under three words.
   - Each list item is its own sentence: each line of a paragraph is split alone, and an inline list is cut after the colon or semicolon before each enumerator. An enumerator (`sentences.MARKER`, shared with the search units' lead-ins) is a lowercase letter or roman numeral i to xx in brackets, then ")" or ".", or a one- or two-digit number in brackets or followed by ")", then a space; "38.", a capital ("J. Smith"), "U.S." and "3.2" never are. Bare forms count only at the start of a line or sentence or after a colon or semicolon. In a comma-only list the cut falls before each enumerator next in sequence after one the line began with (a, i or 1); a marker in brackets ("(see (a) above)") or out of sequence is not a boundary.
   - A form answer alone ("N/A", "None", "Not applicable") is not a sentence.
   - Footnotes are not split; each is one search unit. Two footnotes printed on one line are cut where the next number in sequence plus a capital follows a sentence end, closing bracket or quote, year or web address (`pdf_layout._footnote_pieces`). Footnotes set in body type and read as body are cut the same way and stay body paragraphs.
   - Known limit: figure or box text beside a narrow column of prose at the same height is read into the prose line; cutting at wide gaps would also cut unruled tables.
7. **Re-parse check.** Each paragraph is compared with the previous run's. Changed text under an unchanged parser version means the read is not reproducible: the run stops and writes nothing (cleaned text, raw text and tables are written only after every check passes, so they always belong to one read). A change under a new parser version is expected and counted in the report. A paragraph id repeated across documents also stops the run.

### 2.6 Components (`src/components.py`)

**Collected per project into `data/appraisal/components.csv`**

1. Pages holding a component table are found in the cleaned text by the headings "Component Name" or "Current Component".
2. Tables on that page and the next are read from the PDF as one grid. A restructuring paper's table of changes comes first (current and proposed names, proposed cost, current cost as `cost_before_usd_m`, action); otherwise the data sheet's table. Costs are US$ millions; a figure over 10,000 is read as dollars and converted. A row with no cost continues the name above, unless it opens with "Component N" or the next number. Rows whose name has no word are dropped; the list ends at a financing line ("IDA", "Front-end Fee", "Total").
   A row printing no number takes the one the project's headings and numbered tables give the same name (this document first, then others; equal names, or one the start of the other when the shorter has 12 or more characters), and falls back to row order only when no name matches (`components.number_by_name`).
3. Body headings "Component N: ..." (or "Component N Title" with no punctuation) and "Sub-component N.M: ..." add components and sub-components with any printed cost; so does the first line of a paragraph holding a list under such a title. A bare "N.M Title" counts only once "Component N" has been seen in the same part ("1.2 Sectoral and Institutional Context" is a section).
4. Names are cleaned: number and cost removed, a following sentence cut, neighbouring-column figures removed, a doubled name collapsed, a heading opening in lower case discarded. "Unallocated" and contingency lines are kept unnumbered; the Contingent Emergency Response Component (CERC) keeps its number.
5. A row per component or sub-component per document that states it: projects, document, kind, disclosure date, source (`datasheet`, `restructuring`, `heading`), level, number, name, `name_key`, cost, cost before, action, page. Reads are cached against the PDF, the pages searched, the module's code and the pdfplumber version.

**Tagging paragraphs by position**

6. A paragraph takes the component of the last "Component N" heading above it and the sub-component of the last "Sub-component N.M"; a long paragraph opening "Sub-component 2.2: ..." (or "31. Sub-component 2.2: ...", "Component 3: ...") opens that one. A sub-component heading N.M under another component opens component N (its heading was missed). A bare "N.M Title" opens a sub-component only under component N.
   Each tag carries the heading's printed title (`component_name`, `subcomponent_name`), cut at the first full stop and cleaned as in step 4. Run-in titles are often absent from the data sheet, so search units and the classifier's state take the name from `name_key` in `components.csv`, failing that from this title.
7. The tag closes at the first section other than the one its heading was found in ("II.C Project Beneficiaries" carries none after the last component in "II.B"). A short footnote or table row naming a component ("Component 2: see Annex 3") refers to it and does not open it; only a heading or short narrative or annex line does.
8. `component_mentions` lists every component a paragraph names ("Components 1 and 3"), whole numbers only: "Component 3, 911 Central..." names 3; a glued footnote marker ("Component 324") names none.
