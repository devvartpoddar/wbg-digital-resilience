# Digital for Resilience: method

## What this is for

For a lending project, the pipeline answers four questions:

1. Which digital assets does it finance?
2. Which resilience measures does the appraisal document commit to for those assets?
3. How firm is each commitment?
4. What procurement is coming up against those same assets?

**Terms.** Project Appraisal Document (PAD): the document the Board approves, describing what a lending operation finances. Task Team Leader (TTL): the World Bank staff member leading that operation, and the person this work is for. Systematic Tracking of Exchanges in Procurement (STEP): the system through which borrowers publish procurement plans.

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
| Fetch appraisal documents and procurement | Built |
| Clean appraisal documents into paragraphs and sentences, read from the PDF | Built; **quality under review now** |
| Clean procurement tables: glued words repaired, status resolved with its source | Built; **quality under review now** |
| Load into Postgres, write review sheets | Built |
| Embed paragraphs into pgvector | Next, once cleaning is accepted |
| Judge paragraphs for assets and measures | Next: a test of a pinned decision model (Jev) returning a yes probability |
| Labels for measuring it | To be drawn fresh. Labels will change often: they are keyed by the SHA-256 of the paragraph text, carry the name and date of the set they belong to, and nothing but evaluation reads them |

## Cleaning and chunking

Two corpora, cleaned separately. Appraisal documents are long prose recovered from page layout; procurement text is short metadata typed into a form. They fail in opposite ways, so a rule that helps one damages the other.

| | Appraisal documents | Procurement text |
|---|---|---|
| Unit | Paragraph, split again into sentences | One field, five to fifteen words |
| Noise comes from | Layout recovery: what a line is (heading, footnote, header) is lost in plain text | Human entry, and cells clipped at the column edge |
| Typical defect | Word split across a line break; running header inside a paragraph | `FIBER OPTIC CABLE SUPPLY - LOT 2 (REBID)` |
| Case folding | Not applied. Case is informative, and the classifier handles it | Applied for matching. Entry is inconsistently capitalised |
| Short unit | Almost always a heading or a stray fragment — drop | The entire record — keep |
| Digits and codes | Usually footnote markers or page numbers — strip | Lot and phase numbers distinguish packages — keep |
| Boilerplate | Standard annexes, disclaimers, acronym lists — drop | Placeholder descriptions (`TBD`, `Goods`) — flag, do not drop |

### Appraisal documents

**Read from the PDF, by layout.** The World Bank's text rendition of a document throws away everything that says what a line is: its font size, its position on the page, whether it is bold, whether a character is raised. The old cleaner had to guess all of that back from line patterns, and its leftover defects were exactly where the guess failed: a footnote number fused to a word ("202024"), a running header inside a paragraph, a footnote read as body text. `src/pdf_layout.py` reads the PDF instead and measures:

| What | How it is recognised |
|---|---|
| Running header and footer | A line repeating at the same height on many pages; page-number lines; the rotated "Disclosure Authorized" stamp |
| Footnote marker | A digit set smaller than its line and raised above the line's baseline. Small alone is not enough: the 2 in CO2 is small but lowered, and stays |
| Footnote | The run of small-type lines at the foot of a page |
| Heading | A short line, set in bold, italic or larger type, not ending in punctuation. A full-width bold line is emphasised prose, not a heading |
| Table | A region ruled as a table; each row becomes one unit, cells joined by " \| " |
| Paragraph | A vertical gap larger than the line pitch, or a numbered paragraph or bullet. An enumerator such as "(ii)" only starts a new unit when the text before it had reached a stopping point, because a wrapped cross-reference looks the same |
| Across a page | A paragraph that had not finished its sentence continues on the next page, past any footnotes and header in between |
| Word broken at a line end | Joined without the hyphen when the document uses the whole word elsewhere; otherwise the hyphen is real and kept (`climate-resilient`) |

Every threshold is relative to the document's own body type size and line pitch. Each document's result is cached against the PDF's checksum and a checksum of the reader's code, so a re-run costs nothing and any change to the rules re-reads everything it affects. The text rendition is kept as the fallback for a PDF that is missing, unreadable or image-only, and every row's `source` says which was used.

**Paragraphs and sentences, both.** The paragraph is the unit for what a passage is about: an asset is often named in one sentence and qualified in the next, and the paragraph keeps them together. The sentence is the unit for what a passage commits to: "will finance fiber" and "sites are expected to avoid flood zones" are two claims of different firmness. `src/sentences.py` splits by rule, with no model: a boundary is a full stop, question or exclamation mark followed by a capital, digit or opening bracket, except after an abbreviation ("e.g.", "No.", "U.S."), an initial, or a paragraph number. Sentences are stored as offsets inside their paragraph, never as copies.

Remaining steps, unchanged:

1. Normalise characters: ligatures, smart quotes, non-breaking spaces, soft hyphens, bullet glyphs.
2. Assign ordinals in document order, before any filtering. Units under five words that are not headings are dropped and counted.
3. Hash each paragraph and sentence. On re-parse, verify and fail loudly on mismatch.

**Cleaning is replace-in-place, never delete-in-place.** Removing characters shifts every offset after them, and localisation spans are offsets. Where text must be removed, record the offset map or treat the cleaned text as the reference and keep the raw alongside.

**Footnotes are kept, not dropped.** They carry substantive conditions often enough to matter, and they are cheap to keep as a separate block type.

**Table cells are kept and marked.** They are not prose and no clause parser will read them properly, but dropping them loses quantified targets.

**Acronyms are not expanded.** Appraisal documents define them once then use them throughout; expanding introduces errors where the same acronym means different things across countries. The classifier sees the surface form.

### Procurement

1. Normalise characters and collapse whitespace.
2. Case-fold a matching copy. Keep the original for display.
3. Strip the borrower reference code where it is embedded in the description field; it already has its own column.
3a. Repair words run together where a clipped cell was rejoined (`src/glue.py`): "ofDigital" becomes "of Digital". A token is split only when it is not itself a word the corpus uses, both halves are, a two-letter half is a function word, the right half is not a common ending ("Auditeur" is not "Audit eur"), and it is not a short all-capitals acronym. The vocabulary is the descriptions plus the cleaned appraisal text, so the same rule covers French, Portuguese and Spanish. The repair only ever inserts a space; `description` keeps the original and `glue_repairs` counts the insertions.
4. Separate lot, phase and rebid markers into their own fields rather than leaving them in the matching text.
5. Normalise the borrower reference: case, whitespace, separators. The plan-to-award join depends on it.
6. Detect the language of the description.
7. Flag non-descriptive placeholders — `TBD`, a bare category name, a reference with no words. Flag and count; do not drop.
8. Deduplicate packages across plan versions. The row is assembled field by field, not taken wholesale from the newest version — see **Carry-forward** below. Record the supersession.
9. Normalise status and method values to closed sets (see `src/clean_procurement.py`). The source vocabularies are multilingual — see **The plan is a printed table** below. Unmapped values go to `unknown` and are counted.
10. Hash the raw and cleaned descriptions separately.

**The plan is a printed STEP table, and the parser depends on that.** A plan
document is two things bolted together: narrative the borrower writes, and five
tables STEP generates — WORKS, GOODS, NON CONSULTING SERVICES, CONSULTING FIRMS,
INDIVIDUAL CONSULTANTS. All 496 renditions of the eight-country corpus carry all
five, each under the same English column headings whatever language the rest of
the document is in. Records are built from inside those tables and nowhere else,
each table located by its section heading plus the `Activity Reference No.`
column heading within three lines. The heading alone is not enough: the same
words occur in prose, and one rendition reports 3,822 sections without the
second test.

The section supplies `category_raw`. Taking it from a heading matched anywhere
in the document mislabelled 562 rows, because a bare "CONSULTING FIRMS" in a
contents page relabels every record after it.

Each table is asked, of its own heading band, whether it prints an Estimated
Amount column. 25% of rows come from tables that do not — those renditions are
old enough that STEP printed only the actual — and on them the single figure on
the row is the actual amount. It is recorded as such and never as an estimate;
filing an actual of 0.00 as an estimate of zero is worse than reading nothing,
because nothing about the result looks wrong.

Two layouts, and the layout is the text extractor's rather than the borrower's.
474 renditions print fixed-width columns. 22 have no whitespace left at all,
one cell per line or a few narrow cells sharing one — and those are the newest
rendition for six of the eight projects, so they decide what the current-state
table says. Read as a stream of typed tokens rather than as lines, the two are
one shape, because the only order STEP prints is its columns, left to right.

**A rendition whose columns came apart gives up its figures.** Some collapsed
renditions emit each column of a page as its own run, so the reference column
and the money column end up in different orders — one Niger plan prints the
third row's description before the first row's amounts. Those keep their
references and descriptions and contribute no amounts, status or dates: per
record where a reprinted heading splits it, and wholesale where most records
lost their money line. A package with no amount is a gap a later plan version
can fill; a package with another package's amount is wrong and looks right.

**A bundled rendition is cut to this project, or skipped.** Some plan documents
are published with a text rendition concatenating many operations — one 8.9 MB
rendition returned for Tanzania carries preambles for 59 projects. A rendition
with more than one preamble is split at the preambles and only this project's
segment is used; where it cannot be cut it is counted and not parsed. 29
renditions in the 70-project run.

**Carry-forward: two fields, and the restraint is the point.** Where the newest
plan version does not state a value, `estimated_amount` and `method` — only
those two — fall back to the most recent version that did. `currency` is taken
from the same version as the amount, so the pair is always one that was actually
published. `carried_from` names the source version per field, so nothing is
taken on trust.

**Status comes with its source and its date.** A status is a fact at a point in
time, and silently inheriting an old one manufactures a present-tense claim:
Nigeria read 97.9% populated on statuses that were four years old. So `status`
is resolved from evidence and labelled, in `status_source` and `status_as_of`:

| Source | Meaning |
|---|---|
| `plan` | The newest plan version's own status, as of its disclosure date |
| `award` | A signed contract whose normalised borrower reference matches the package, as of signing. Wins over an older plan status |
| `earlier_plan` | Used only when neither of the above says anything: the last status an older plan version printed, as of that plan's date |
| `none` | No evidence at all; status stays `unknown` |

`terminated` is its own value: a contract signed and then ended early is neither
signed nor cancelled. The status vocabulary covers English, French, Portuguese and
Spanish, and status is re-mapped from the borrower's own word at clean time, so a
mapping change needs a re-clean rather than a re-fetch.

**Amounts and dates sometimes appear inside the description string.** Extract them to their own columns rather than leaving them to be matched as text.

**Cleaning quality is verified, not assumed.** Cleaning runs before embedding, so an undetected defect propagates into every vector and every downstream score. The review step below is how it is checked.


## Reviewing the cleaning

Automated checks count known defects across every paragraph (`src/audit.py`, `src/audit_procurement.py`). They only find what someone already thought of. The rest is found by reading.

`src/review_sheets.py` writes two plain spreadsheets to `data/review/`: 100 paragraphs with the cleaned text, the same text split into sentences, the page it came from, and the raw text-rendition lines beside it; and 100 procurement packages with the raw description next to the cleaned fields. Each has empty `ok` and `note` columns. A sheet that already exists is never overwritten, since it may hold notes.

The raw column shows whole raw lines, including anything cleaning removed from the middle of the paragraph, such as a running header. A paragraph whose raw text cannot be found says so; that is usually a table, and worth a look.

## Reports

Reports do not go in the repository. `src/run_report.py` drafts a run report as a Markdown note in `data/reports/`, and the agent that ran the pipeline copies it into Dev's notes vault at `Projects/WBG Digital Resilience/Reports/`, adding a line to that folder's README index. The note follows the folder's `_template.md`.

## Storage

- **Files**: `data/` in the main checkout, `/ygg/projects/wbg-digital-resilience/data/` on the box. Raw renditions, cleaned text, CSV tables, reports and review sheets. Gitignored, never deleted.
- **Postgres**: schema `wbg` in database `work`, loaded from the CSV files by `src/load_pg.py`. Paragraph and sentence text are columns there. `wbg.loads` records what was loaded from which file version.
- **Embeddings, when they come**: `halfvec(3072)` with an HNSW index on cosine distance. Plain `vector` cannot be indexed past 2,000 dimensions; `halfvec` can up to 4,000, at half the storage and no meaningful loss for similarity search. The full-precision vectors the API returns stay in the on-disk cache as the record.

## Known limits

**Tables without ruling lines read as text.** A table the PDF draws without lines is not found as a table, and its cells are read in page order. One annex table (document 33835349) interleaves row numbers with cell text this way. Narrative is unaffected.

**One appraisal document is six pages long.** 34295176 is a PAD as published, not a parse failure; it carries no body sections.

**Some Spanish fragments survive.** The word-repair rules lean on the appraisal prose to judge English; French, Portuguese and Spanish are judged from the descriptions alone, which is weaker. The Honduras and Peru plans keep a handful of broken words ("present ación"), counted by the audit.

**Clipped words stay clipped.** Where the plan rendition cut a cell and the rest of the word is gone ("Project Management Suppor"), there is nothing to restore it from.

**Parse quality on converted documents.** Sentence segmentation is imperfect on clean text and worse on documents converted from PDF.

**Some plan renditions contribute no figures.** Where the extractor scattered a
table's columns across the page, the rendition's references and descriptions are
kept and its amounts, status and dates are refused rather than guessed. Niger's
newest rendition is one of these, so that project's current-state status reads
zero while its earlier renditions parse at 84%. Whether to fall back to the
newest *readable* rendition is an open decision, not a defect.

**Some plans carry no borrower reference at all.** One project's plan renditions
print a component number where the reference belongs — spot-checked on three of
its twenty renditions, none carries a reference-shaped token anywhere — so its
134 rows have no legitimate key and are not emitted. Keying them would mean
minting a synthetic identifier for packages the Bank never referenced, which is
a data-model decision rather than a parser fix.

**Nine projects parse to zero package rows.** P171099, P174620, P175218,
P175987, P177158, P179204, P180693, P180807, P502532 — one of them because its
single plan rendition returns a hard 404 from the documents interface. The rest
are undiagnosed.

**Cleaning is the first place quality is lost and the hardest to notice.** A defect that survives cleaning is embedded, scored and joined without ever raising an error. The review step is the only check standing between the corpus and every number the pipeline produces.
