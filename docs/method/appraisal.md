# Appraisal documents

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

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

1. Drop every character set at an angle, read from its text matrix. This removes the "Public Disclosure Authorized" stamp down the page margin. Drop every character drawn wholly outside the page: some documents set a "For Official Use Only" stamp off the left edge, printed five times over, which would otherwise be read into the body line level with it.
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
7. A running header or footer is a line that recurs at the same height (heights are binned to 4 points, so two lines within a bin count as the same height), with the same letters ignoring spaces and digits, on at least 30 per cent of pages (and at least 3). Those lines are removed.
8. Also removed: page-number lines ("12", "Page 3 of 40"); classification banners ("Official Use Only", with or without a page number); dated footers ("Oct 12, 2023 Page 27 of 29"); lines of hidden template code; contents-page lines (those ending in dot leaders and a page number, or a section or annex heading followed by a bare page number).
9. The footnotes on a page are the unbroken run of lines at its foot set smaller than 93 per cent of body size and no larger than the smallest of the run's numbered lines (plus 0.5 point; `pdf_layout._footnote_start`). An annex set a point smaller than the body, with footnotes smaller still, so keeps its text as annex text rather than being read as one long footnote. Where that run reaches into the upper half of the page (an annex table set in the footnotes' own type), the footnotes start after the last gap wider than twice the run's line step that a numbered line follows. A run with no numbered line is a footnote carried from the page before only when it is at most eight lines and has body text above it; a page, or the end of one, set small with no footnote number is body text. The footnote numbers each page defines are recorded.
10. A number run onto the end of a word ("hoped44") is removed when the footnotes of this page or the next define that number. A number after a capital letter (IDA19, FY01) is never touched, nor one after a word that carries a capital past its first letter (IPv6, Wi-Fi6, GovNet2): that is a version or a name.

**Each remaining line is labelled:**

11. *Table row*: the line falls inside a ruled table (section 2.3): its middle is within the table's height and at least 90 per cent of its width within the table's (`pdf_layout._in_box`). A bullet glyph hung in the margin left of the table's ruling leaves the line in the table; requiring the whole line inside had split every bulleted cell, its first line read as body text and the rest as table (IDEA's climate annex, for one).
12. *Footnote*: the line is in the footnote run.
13. *Heading*: the line is bold, italic or larger than body type, has at most 14 words, does not end in . ; , or :, and is narrower than 80 per cent of a full line. A unit opening "Component", "Sub-component", "Figure", "Table", "Box", "Map" or "Chart" followed by a number or capital, with at most 30 words and not ending in . or ;, is also made a heading. Headings and these titles also serve as the caption for a table that follows within a page.
14. *Body*: everything else.

**Lines are grouped into units (paragraphs):**

15. A body line continues the paragraph above when it follows at the normal line spacing on the same page (up to 1.45 times the median spacing, `pdf_layout.LINE_GAP`); or the paragraph had not finished its sentence and the page turned; or the paragraph had not finished its sentence, the line opens in lower case, and a table, heading or footnote came in between; or the paragraph had not finished its sentence, the line opens in lower case, and it sits within three line-spacings (`pdf_layout.LOOSE_GAP`; a paragraph set with looser spacing).
16. A line does not continue the paragraph when it opens a new block: a bullet glyph always does; a paragraph number ("23.") or an enumerator ("(ii)", "a)", "3)") does only when the text before it ended at a stopping point (. : ; ! ? or a closing bracket or quote, or "and"/"or"). A wrapped cross-reference such as "as described in sub-part (ii) above" therefore stays one paragraph. An enumerator that is the next in a list the paragraph began inline ("... best practices on: (a) civil registration;" then a line "(b) national identification ...") does not open a block either.

    A list item - a line opening with a bullet glyph or an enumerator that opens a block - joins the paragraph above it on a line of its own, rather than starting a paragraph: "(b) Active infrastructure procurement" means nothing without "Sub-component 1.1 will finance:". It joins across a page turn and the footnotes in between, but not past a heading, a caption or a table, which start a new context; a list there starts its own paragraph. Its bullet glyph is removed. So a paragraph is its lead-in and all its items, one per line.

17. A word broken by a hyphen at a line end is joined without the hyphen when the document uses the whole word unbroken elsewhere; otherwise the hyphen is real and kept ("climate-resilient").
18. A footnote line that does not open with a number continues the footnote above; the number a footnote opens with is removed from its text (a year such as 2016 at the start is not mistaken for one).

**Each unit is then finished:**

19. A unit that opens with a list item (a list with no paragraph above to join, step 16) has its bullet glyph removed and is marked `list_item`.
20. Every web address becomes "[link]", including the pieces a line break left after a space. (1,881 on the current run.)
21. A unit with no letter or digit at all (a lone "$" from a logo) is dropped. (111 on the current run.)
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
7. A table that continues on the next page without reprinting its header borrows the previous page's header when it has the same number of columns and sits within the first three kept lines of the page.
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

   A stretch between two country annexes that holds no data sheet is `programme_wide` too. A paragraph takes the project(s) of the part it starts in, and components are read part by part, so each country keeps its own list and costs. A document listed under one project, or one whose data sheets name none of its listed projects, is one part with the listing (a parent project and its additional financing share a paper that names no single operation). On the current run one document is split: IDEA, into the regional part, DRC, Angola, Malawi and a programme-wide part.
3. A unit with fewer than 5 words (`clean.MIN_TOKENS`, a constant, not a flag) is dropped, unless it is a heading or a section or annex title, and recorded in `data/appraisal/rejected.csv` with its reason. (5,002 on the current run.)
4. Each kept unit becomes a row of `data/appraisal/paragraphs.csv` with: a positional identifier (`{doc_id}:p00042`), the project(s), section path and title, block, character offsets into the cleaned text file (the text itself is not stored in the table), word count, SHA-256 of the text, pages, the parser's identity, `for_model`, `list_item`, `table_id`, `raw_start`/`raw_end`, and the component tags (section 2.6).
5. `for_model` is true for `narrative`, `annex` and `footnote` paragraphs, except a footnote that is only a reference and a link (fewer than 15 words besides "[link]"). It is false for front matter, tables and headings. Embedding and the decision model read only these paragraphs. (14,723 of 52,025 paragraphs: 7,718 narrative, 3,117 annex and 3,888 footnotes on the current run.)
6. Narrative and annex paragraphs are split into sentences, stored in `data/appraisal/sentences.csv` as offsets into the document's cleaned text, each inside its paragraph. A boundary is a full stop, question or exclamation mark followed by a space and a capital, digit or opening bracket, except:
   - after an abbreviation (e.g., i.e., etc., No., para., U.S., Mr., Fig., months and about forty others), or a single-letter initial;
   - inside the leading paragraph number ("38.");
   - where the sentence so far has fewer than three words ("See above." stays with what follows).
   Each list item is its own sentence, however short: each line of a paragraph (a list set out line by line, section 2.2 step 16) is split on its own, and an inline list ("... on: (a) civil registration; b) national ID; and c. ...") is cut after the colon or semicolon before each enumerator. An enumerator (`sentences.MARKER`, one definition shared with the search units' lead-ins) is a lowercase letter or a roman numeral from i to xx in brackets, followed by ")" or followed by ".", or a number of one or two digits in brackets or followed by ")", then a space; "38." stays a paragraph number, and a capital ("J. Smith"), "U.S." or "3.2" is never one. The bare forms count only at the start of a line or sentence or after a colon or semicolon. A list separated by commas only ("(a) setting tariffs, (b) running stations, and (c) ...") is cut before each enumerator that is the next in sequence after one the line has already begun its list with (a, i or 1); a marker inside brackets ("(see (a) above)") or out of sequence is not a boundary. A search then finds the item that names an asset or a measure.
   A form answer standing alone ("N/A", "None", "Not applicable") is not a sentence and is left out.
   Footnotes are not split: they are mostly references, and each is one search unit. Two footnotes printed on one line ("... February 2023 14 A joint study ...") are two footnotes: the next number in sequence, followed by a capital, after a sentence end, a closing bracket or quote, a year or a web address, opens a new one (`pdf_layout._footnote_pieces`). Where a page's footnotes are set in the text's own type and read as body text, a line opening with the next footnote number after a paragraph that opens like a footnote ("9 Ministry of ...", then "10 Average price ...") starts a new paragraph, and such footnotes joined on one line are cut apart the same way; they stay body paragraphs, split into sentences. Text of a figure or box printed beside a narrow column of prose at the same height is still read into the prose line (a known limit: pdfplumber gives it as one line, and cutting lines at wide gaps would also cut unruled tables).
7. The re-parse check: before writing, each paragraph is compared with the previous run's. A paragraph whose text changed while the parser did not means the read is not reproducible; the run stops and writes nothing: the cleaned text files, the raw text files and the tables are all written only after every check has passed, so they always belong to the same read. A change under a new parser is expected and is counted in the report. A PDF whose read failed (an unreadable file, or the box running out of memory) is not cached and is read again on the next run.
8. A paragraph identifier repeated across documents stops the run (it would mean a regional document was listed twice).

### 2.6 Components (`src/components.py`)

**Collected per project, into `data/appraisal/components.csv`:**

1. Pages that hold a component table are found in the cleaned text, by the column headings "Component Name" or "Current Component".
2. All tables on that page and the next are read from the PDF, top to bottom, as one grid. A table row that prints no component number takes the number the project's headings and numbered tables give the same name (this document's first, then the project's others; equal names, or one name the start of the other when the shorter has at least 12 characters), and falls back to its row order only when no name matches (`components.number_by_name`): P171305's April 2026 restructuring paper lists component 2 first and prints no numbers, which by row order put every cost on the wrong component. A restructuring paper's table of changes is read first (current and proposed names, the proposed cost, the current cost kept as `cost_before_usd_m`, and the action); otherwise the data sheet's component table (name and cost). Costs are in millions of US dollars; a figure over 10,000 is read as dollars and converted. A row with no cost continues the name above it, unless it opens with "Component N" or the next component's number ("3. ...", whose cost is printed on the next page). Rows whose name holds no word are dropped, and the list ends at a financing line ("IDA", "Front-end Fee", "Total").
3. Body headings "Component N: ..." (or "Component N Title", with no punctuation after the number) and "Sub-component N.M: ..." add components and sub-components, with any cost printed in the heading; so does the first line of a paragraph that holds a list under such a title. A bare "N.M Title" heading counts only once "Component N" has been seen in the same part: elsewhere it is a numbered section ("1.2 Sectoral and Institutional Context").
4. Each name is cleaned: its number and printed cost removed, a following sentence cut off ("... This component will ..."), figures from neighbouring columns removed, a name read twice collapsed, and a heading that opens in lower case discarded. "Unallocated" and contingency budget lines are kept but not numbered; the Contingent Emergency Response Component (CERC) keeps its number.
5. One row per component or sub-component per document that states it: projects, document, kind, disclosure date, where it was read (`datasheet`, `restructuring`, `heading`), level, number, name, `name_key`, cost, cost before a restructuring, action, page. Several restructurings record large cuts (Haiti's components go from US$56.5m to US$8.1m; one project's from US$200m to US$100m), which the projects interface's commitment does not yet show. The reads are cached against the PDF, the pages searched, the module's code and the pdfplumber version. (970 rows covering all 70 projects on the current run.)

**Paragraphs are tagged by position:**

6. A paragraph takes the component of the last "Component N" heading above it, and the sub-component of the last "Sub-component N.M" heading. A long paragraph that opens with "Sub-component 2.2: ..." (or "31. Sub-component 2.2: ...", or "Component 3: ...") opens that sub-component or component too. A sub-component heading N.M under another component opens component N: the heading that would have opened N was missed. A bare "N.M Title" opens a sub-component only under component N, and nothing when no component is open.
6a. Each tag carries the title its heading printed (`component_name`, `subcomponent_name`), cut at the title's first full stop and cleaned as in step 4. A run-in title is often one the data sheet does not list, so the search units take a component's `name_key` from `components.csv` and, failing that, from this title; the classifier's state takes the name the same way.
7. The tag closes at the first section that is not the one the component heading was found in, or inside it: after the last component in "II.B Project Components", "II.C Project Beneficiaries" carries no component.
7a. A short footnote or table row that names a component ("Component 2: see Annex 3", a row of a cost table) refers to it and does not open it; only a heading or a short narrative or annex line does (corrected 2026-10-20: a footnote had re-tagged every paragraph after it).
8. `component_mentions` separately lists every component a paragraph names in its text ("Components 1 and 3"), whole numbers only: "Component 3, 911 Central..." names 3, and a footnote marker glued on ("Component 324") names none.

6,115 paragraphs carry a component tag, 3,349 of them among the paragraphs given to the model on the current run.
