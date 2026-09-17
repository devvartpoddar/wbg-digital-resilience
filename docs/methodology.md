# Digital for Resilience: methodology

---

## 1. Purpose

For a given lending project, the pipeline answers four questions:

1. Which digital assets does it finance?
2. Which resilience measures does the appraisal document commit to for those assets?
3. How firm is each commitment?
4. What procurement activity is coming up against those same assets?

**Terms.** Project Appraisal Document (PAD) — the document approved by the Board describing what a lending operation will finance. Task Team Leader (TTL) — the World Bank staff member leading that operation. Systematic Tracking of Exchanges in Procurement (STEP) — the system through which borrowers publish procurement plans. Terms of Reference (TOR) — the specification annexed to a tender. Modality — whether a sentence commits to something, aspires to it, or reports it as done. Dependency parse — an automatic analysis linking each word to the word it grammatically depends on.

---

## 2. What the output is

A candidate set. A commitment matches every package of that asset in that project, so the match is at project-and-asset level, not package level. It is a short list of plausible entry points for an advisory conversation, not an audit.

The pipeline is deterministic: same input, same output, no sampling, no generative model in the decision path.

Precision is preferred to recall. A missed commitment costs an opportunity; a false one costs credibility with a task team. Every threshold is exposed so the trade-off is set deliberately.

**Three statements accompany every output.**

1. An appraisal commitment evidences design intent, not delivery.
2. Nothing here is causal. The pipeline observes what documents say, and has no counterfactual.
3. Recall is unmeasured. The pipeline misses real commitments and the miss rate is not known.

**Open questions are numbered V1 to V10 and listed in Section 8.** Several steps below have more than one plausible method; each names its candidates, a default, and the verification step that decides. The default is what gets built first so the alternatives have something to be measured against. It is not a decision.

---

## 3. Precedent

Climate Policy Radar built a comparable system for finding quantified climate targets in national laws and policies. Three method points transfer:

1. **Annotation can be bootstrapped from existing hand-written summaries** by matching them back to the paragraphs they came from.
2. **The operating threshold is chosen after training**, from a calibrated score. A rule-based system has no dial to turn.
3. **Domain-specific pretraining bought almost nothing.** A climate-pretrained model and a general-purpose model of similar design scored within half a point of each other on F1; the smaller was chosen for size, not accuracy. There is no reason to hunt for a domain-pretrained model here.

Their published classifier is not reusable. It detects net zero targets, emissions reduction targets and other quantified national targets, with no label corresponding to resilience conditions on infrastructure.

---

## 4. Sources

| Source | Role | Cannot evidence |
|---|---|---|
| Project Appraisal Documents | Primary. The Section II components narrative is the commitment evidence | Whether anything was delivered |
| Procurement plans | Package-level demand pipeline, planned dates, process status | Design requirements. Actual amount field is unreliable |
| Projects metadata | Cohort definition, approval dates, country, sector | — |
| Solicitation notices | Longer description than a plan title; real bid deadlines | The specification, which sits in the bidding documents |
| Contract awards | Signed amount, signature date, supplier identity | Design. Arrives after the specification is fixed, so it closes the timeline rather than opening a window |

The gap between documented and delivered is not closed by any of these. The link between a stated commitment and the specification that would deliver it exists only in bidding documents and terms of reference, which are not published as structured data. **How much design detail notice and award text actually carries has not been checked against a sample — V7.**

Endpoints, versions and field lists are in the data model.

---

## 5. Stages

Stages run in the order given. Schemas are in the data model; value constraints and the detection rule sets are in the business rules.

### Stage 1 — Acquisition

1. Fetch appraisal documents and procurement plans from the documents interface.
2. Fetch project metadata for cohort definition.
3. Fetch notices and awards filtered by project identifier.
4. Hash every fetched artefact; write a manifest row with address, checksum and timestamp.
5. Deduplicate by content hash. Link shared regional documents to every project that uses them.

Fetch wide; filter at output, not at input.

**Output:** `document_manifest`, `api_fetch_log`, raw artefacts on disk.

### Stage 2 — Preparation

Two corpora, cleaned separately. Appraisal documents are long prose recovered from page layout; procurement text is short metadata typed into a form. They fail in opposite ways, so a rule that helps one damages the other.

| | Appraisal documents | Procurement text |
|---|---|---|
| Unit | Paragraph, hundreds of words | One field, five to fifteen words |
| Noise comes from | Layout recovery — the extractor guessing at reading order | Human entry — abbreviation, codes, placeholders |
| Typical defect | Word split across a line break; running header inside a paragraph | `FIBER OPTIC CABLE SUPPLY - LOT 2 (REBID)` |
| Case folding | Not applied. Case is informative, and the classifier handles it | Applied for matching. Entry is inconsistently capitalised |
| Short unit | Almost always a heading or a stray fragment — drop | The entire record — keep |
| Digits and codes | Usually footnote markers or page numbers — strip | Lot and phase numbers distinguish packages — keep |
| Boilerplate | Standard annexes, disclaimers, acronym lists — drop | Placeholder descriptions (`TBD`, `Goods`) — flag, do not drop |

#### 2a. Document preparation

1. Parse to text with a pinned tool and version. Record tool, version and settings on the document row.
2. Normalise characters: ligatures, smart quotes, non-breaking spaces, soft hyphens, bullet glyphs.
3. Rejoin words split across line breaks. Distinguish a line-break hyphen from a real one — `climate-resilient` must survive.
4. Remove running headers, footers and page numbers by detecting text repeating at the same position across pages.
5. Rejoin paragraphs split across a page boundary.
6. Split to paragraphs; assign ordinals in document order, before any filtering.
7. Classify block type: prose, bullet, heading, table cell, footnote.
8. Drop boilerplate: standard annexes, disclaimers, acronym glossaries, signature blocks. Record every drop with a reason and count them.
9. Locate the Section II components narrative; mark paragraphs in scope. Where it cannot be located, record the document as excluded and count it.
10. Hash the raw text and the cleaned text separately. On re-parse, verify both and fail loudly on mismatch.
11. Embed paragraphs. Checkpoint and make resumable.

**Cleaning is replace-in-place, never delete-in-place.** Removing characters shifts every offset after them, and localisation spans are offsets. Where text must be removed, record the offset map or treat the cleaned text as the reference and keep the raw alongside.

**Footnotes are kept, not dropped.** They carry substantive conditions often enough to matter, and they are cheap to keep as a separate block type.

**Table cells are kept and marked.** They are not prose and no clause parser will read them properly, but dropping them loses quantified targets — see V9.

**Acronyms are not expanded.** Appraisal documents define them once then use them throughout; expanding introduces errors where the same acronym means different things across countries. The classifier sees the surface form.

#### 2b. Procurement preparation

1. Normalise characters and collapse whitespace.
2. Case-fold a matching copy. Keep the original for display.
3. Strip the borrower reference code where it is embedded in the description field; it already has its own column.
4. Separate lot, phase and rebid markers into their own fields rather than leaving them in the matching text.
5. Normalise the borrower reference: case, whitespace, separators. This is what V6 depends on.
6. Detect the language of the description.
7. Flag non-descriptive placeholders — `TBD`, a bare category name, a reference with no words. Flag and count; do not drop.
8. Deduplicate packages across plan versions. The row is assembled field by field, not taken wholesale from the newest version — see **Carry-forward** below. Record the supersession.
9. Normalise status and method values to the closed sets in the business rules. The source vocabularies are multilingual — see **The plan is a printed table** below. Unmapped values go to `unknown` and are counted.
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

**Status is never carried forward.** A status is a fact at a point in time, and
inheriting it manufactures a present-tense claim out of a stale one: Nigeria read
97.9% populated on statuses that were four years old. With carry-forward removed
the honest figure is 23.2%. An unknown status stays unknown and is confirmed with
the project team.

**Amounts and dates sometimes appear inside the description string.** Extract them to their own columns rather than leaving them to be matched as text.

**Cleaning quality is verified, not assumed — V10.** Cleaning runs before embedding, so an undetected defect propagates into every vector and every downstream score.

**Output:** `documents`, `span_project`, `paragraphs`, `rejected_units`, `packages`, `notices`, `awards`, `paragraph_emb`, `paragraph_emb_index`, `embedding_manifest`.

### Stage 3 — Asset detection

Runs on in-scope paragraphs.

1. Score each paragraph with eight binary classifiers, one per asset class: fiber, mobile and towers, submarine cable, data hosting, cybersecurity, digital public infrastructure, disaster risk management and early warning, access and connectivity.
2. Write all eight probabilities per paragraph, including those below threshold, so a threshold can move without re-running the model.
3. Mark `fired` where the probability clears the class threshold.
4. Locate asset mentions and record character offsets. **Method open — V3.**
5. Record disagreements: classifier fired with nothing located, or located with the classifier silent.

Eight independent binary models rather than one multi-class model, because a paragraph routinely finances several assets at once. The classifier input is the paragraph embedding and the label is a 0 or a 1; no seed string enters the decision.

Do not pre-filter on climate language. Much of the strongest evidence never uses the word — Tanzania's requirement was non-flood-prone sites, the Philippines' rationale was typhoon vulnerability.

**Step 4 — locating the asset.** Detection and localisation are separate jobs. The classifier decides whether a paragraph is about an asset; something else must say where the asset is named, as a character offset, because attribution resolves two tokens in a parse and a paragraph-level probability has no position.

| Candidate | Trade-off |
|---|---|
| Embedding-derived term list | Extract noun and verb phrases from the corpus, embed, rank by similarity to the class, human accepts or rejects. Cheapest and reuses existing machinery. Still a list, so still has to be exhaustive at match time |
| Span classification | Token-classification model marking mentions in place. Handles paraphrase, no list to maintain. Needs labelled spans, which cost more per unit than labelled paragraphs |
| Head extraction and classify | Pull noun phrases from the parse, embed and classify each. No enumeration, positions come from the parse, reuses the classifier. Inherits parser error, currently unmeasured |

Default: embedding-derived term list.

**Output:** `paragraph_asset`, `paragraph_asset_spans`, `detector_disagreement`.

### Stage 4 — Segmentation

Runs only on paragraphs where at least one asset fired.

1. Sentence-split with a pinned parser.
2. Split sentences into smaller units. **Method open — V2.**
3. Record which boundary condition produced each split.
4. Record offsets into the paragraph so a unit can always be shown in context.
5. Embed clauses.

Paragraph remains the unit for detection; clause is the unit for attribution and modality. The two units answer different questions and are not reconciled.

**Step 2 — unit size.** The required output is units small enough that one measure and one asset can be linked without crossing into a different proposition.

| Candidate | Trade-off |
|---|---|
| Clause splitting | Smallest unit, best fit for attribution and modality. Custom code, no standard implementation, error rate unknown |
| Sentence only | Off-the-shelf and measurable. A sentence can hold several propositions, so attribution and modality both degrade |
| Parse-derived predicate spans | Principled boundaries. Fully dependent on parser quality |

Default: clause splitting, at coordinating conjunctions, subordinating conjunctions, relative pronouns, semicolons and verb-headed subtrees.

**Output:** `clauses`, `clause_emb`.

### Stage 5 — Measure detection

1. Score each clause with family classifiers over the clause embedding.
2. Resolve the specific measure within a fired family by locating measure terms. **Method open — V4.**
3. Record whether a resilience qualifier is present — flood, cyclone, typhoon, landslide, seismic, hazard, exposure, vulnerability, climate risk.
4. Where a family fires and no measure resolves, write the clause to `term_candidates`, grouped by head phrase and sorted by corpus frequency.

Measures are grouped into families by shared vocabulary, not shared intervention type: two measures belong together if a reader could plausibly confuse their text. Families are uneven; cybersecurity is its own class. Per-asset variants of the same measure collapse to one measure — which asset it attaches to is Stage 6's job.

Inside a confirmed family the qualifier is a confidence signal, not a gate. The family classifier has already established resilience relevance.

**Step 2 — locating the measure.** Same three candidates as Stage 3 step 4. Measure vocabulary is more open-ended than asset vocabulary, so the cost profiles differ and the chosen method may differ between them.

Default: embedding-derived term list.

**Step 4 — the discovery loop.** `term_candidates` is how the measure list gets filled out without reading documents by hand. High-frequency misses surface first, so the review is short and gets shorter. Every accepted term is a ruleset change and triggers re-validation: adding terms raises recall and will sometimes drop precision.

**Output:** `clause_measure`, `term_candidates`.

### Stage 6 — Attribution

1. Where the paragraph names exactly one asset, attribute to it.
2. Otherwise resolve against competing assets. **Method open — V1.**
3. Where more than one asset competes and nothing resolves, write `UNATTRIBUTED`.
4. Record which rule fired, the tier, the competing asset count and the path length on every row.

Nothing is guessed. `rule_fired` is what allows each rule to be scored separately and removed on evidence.

**Step 2 — resolving multi-asset paragraphs.**

| Candidate | Trade-off |
|---|---|
| Shortcut only | Everything beyond a single-asset paragraph goes to `UNATTRIBUTED`. Trivial to build, costs recall in proportion to the multi-asset share |
| Dependency patterns | Six named patterns plus a bounded path rule, specified in the business rules. Resolves multi-asset paragraphs, carries per-rule diagnostics. Only worth building if the residue is large |
| Trained pair classifier over the two spans | No pattern maintenance. Needs labelled pairs and loses the per-rule diagnostics |

Default: shortcut only. V1 measures the multi-asset share and decides whether the patterns are worth building.

**Output:** `attribution`.

### Stage 7 — Modality

1. Determine sentence shape: the measure has its own verb (predicate), or is a modifier inside a noun phrase (inherits the governing verb).
2. Select the governing token accordingly.
3. Apply rules 1 to 8 in order, as specified in the business rules.
4. Route rule 8 cases to the three-class classifier.
5. Record the rule number, the governing verb, and whether a rule or the classifier decided.

Getting step 1 wrong is the largest false-positive risk in the pipeline. Applying predicate logic to a modifier sentence drops a real commitment. Inheriting the matrix verb when the measure has its own predicate reads "will finance fiber, which is expected to be climate-resilient" as firm. It is not.

**Step 3 — rules or classifier.**

| Candidate | Trade-off |
|---|---|
| Rules with classifier fallback | Explainable on the cases rules decide, with hard cases routed rather than guessed. Two components to maintain |
| Classifier alone | One component. Loses the per-rule diagnostic and needs more labelled clauses |
| Rules alone | No labelling cost. Modal expressions are varied and ambiguous enough that recall suffers |

Default: rules with classifier fallback.

Modality language is domain-general, so training clauses need not come from the cohort or concern resilience. "Already done" will be rare in forward-looking documents and will be the weakest class; top it up deliberately or report it as weak.

**Output:** `modality`.

### Stage 8 — Procurement classification

Runs on the cleaned procurement tables from Stage 2b. Asset attribution only — a package description makes no commitment, so no modality.

1. Match cleaned package titles, bid descriptions and award descriptions against asset terms, in English, French, Spanish and Portuguese.
2. Where terms do not resolve, fall back to a classifier.
3. Flag packages whose description is itself a resilience measure — the only procurement rows that evidence delivery rather than opportunity.
4. Link plan packages to signed awards. **Method open — V6.**
5. Reclassify only rows whose description hash changed.

Term matching runs before the classifier here, the reverse of Stage 3. Package titles are short, and short text is where lexical matching is strongest and dense embeddings weakest.

**Step 4 — plan to award.**

| Candidate | Trade-off |
|---|---|
| Borrower reference, exact | Unambiguous where it works. Fails on any formatting difference |
| Borrower reference, normalised | Case folding, whitespace and separator normalisation. Recovers formatting variants, small risk of collision |
| Fuzzy on description, amount and date | Works with no shared reference. Produces multiple candidates per package and needs a confidence threshold |

Default: borrower reference, normalised. V6 checks whether references match at all for this cohort.

**Output:** `package_asset`, `package_award_link`.

### Stage 9 — Join and outputs

1. Join commitments to packages. **Method open — V8.**
2. Assemble the commitment table.
3. Compute both gap views raw, with no bucketing: same-span (asset and measure in one paragraph) and whole-project (both present anywhere).
4. Build the menu: measures present, measures that would complete the package, distance to complete, cost band per package.
5. Build the intervention window, the learning cases, and the unattributed count.

Cut-offs come from expert calibration against a hand-labelled sample, never from a flat threshold inside the pipeline.

**Step 1 — join granularity.**

| Candidate | Trade-off |
|---|---|
| Asset-type match within project | Works today. Deliberately coarse — every package of that asset matches |
| Text similarity, commitment evidence to package description | Could narrow to package level. Untested, and package descriptions are short |
| Component-level match | Narrowest, if appraisal component structure and procurement component tagging align. Alignment unverified |

Default: asset-type match within project.

Climate-language-to-climate-language does not work: procurement text rarely carries climate language. A cross-tabulation across 1,864 packages found none in the most physically exposed classes — zero of 47 fiber packages, zero of 11 tower packages.

**Output:** `commitments`, `unattributed`, `project_asset_coverage`, `gap_same_span`, `gap_whole_project`, `menu`, `intervention_window`, `learning_cases`.

---

## 6. The labelled sample

One draw, labelled at two levels. Nested, because dependent labels only exist where the parent fired.

**Pass one — paragraph.** Eight yes/no asset labels per paragraph.

**Pass two — clause.** Only on paragraphs where an asset fired. Per clause: measure family (multi-label), specific measure, attributed asset, modality.

Every "no" is a negative example, so negatives cost nothing extra.

**Drawing rules.**

1. Stratify across projects and regions. Oversample rare asset classes.
2. Training draws follow the true corpus distribution. A score-stratified draw over-represents borderline cases, which helps training and breaks calibration.
3. Set thresholds on a separate representative slice, never on the training draw.
4. Training, calibration and gold rows are disjoint. A unit appears in exactly one draw.
5. Every draw is recorded with its identifier, purpose, stratification and seed.
6. The draw file carries the unit text. It is the only committed place text appears, and it is what makes a sample self-contained for hand-labelling.

Sample sizes are set once the taxonomy is complete.

---

## 7. Validation

Freeze a gold set and recompute on every ruleset change. Stamp `ruleset_version` on every output row.

| Metric | Scope |
|---|---|
| Precision and recall per asset classifier | Stage 3, at the chosen threshold |
| Precision and recall per measure family | Stage 5 |
| Attribution accuracy per rule | Stage 6, each rule scored separately |
| Modality confusion matrix | Stage 7, three classes |
| Unattributed rate | Stage 6, reported not hidden |
| Locator coverage | Proportion of classifier hits with no span located |

Sample size is reported with every precision or recall figure.

Keep a gate that flags spans firing for an implausible number of classes, and report how often it triggers.

---

## 8. Verification steps

Each is a check that has not been run. Results go in `analysis/`, script and output table together.

| ID | Check | Runs against | What would change the design | Blocks |
|---|---|---|---|---|
| V1 | Proportion of asset-bearing paragraphs naming exactly one asset | Existing corpus, one query | High → ship the shortcut and skip the dependency patterns. Low → build them | Stage 6 step 2 |
| V2 | Segmentation accuracy against a hand-checked sample of results framework tables and bulleted component lists | Messiest text in the corpus | Poor → fall back to sentence units | Stage 4 step 2, and everything downstream |
| V3 | Asset localisation: term list vs span classification vs head extraction | Same labelled slice, precision and recall on span boundaries | Determines the method | Stage 3 step 4 |
| V4 | Measure localisation, same three candidates | Same slice | Determines the method; may differ from V3 | Stage 5 step 2 |
| V5 | Build and hand-verify the gold set | New draw, two labellers where feasible | Establishes the first measured precision and recall | Quoting any accuracy figure |
| V6 | Whether borrower package references match between plans and awards for this cohort | Plan and award tables | Exact works → use it. Partial → normalise. Neither → fuzzy | Stage 8 step 4 |
| V7 | Read a sample of notice and award descriptions for specification detail | 50–100 descriptions across asset classes | Descriptions carry design language → the join can do more than asset-type matching | The claim in Section 4 |
| V8 | Whether appraisal component structure aligns with procurement component tagging | Cohort metadata and plans | Alignment → component-level join becomes available | Stage 9 step 1 |
| V9 | Whether quantified resilience targets in results framework tables justify a separate extraction path | Sample of results frameworks | Dense enough → build a table extraction path | Recall on quantified targets |
| V10 | Read cleaned text against raw for a sample of both corpora | 100 paragraphs, 100 procurement descriptions | Defects found → fix the cleaning rules before embedding | Embedding, and everything after it |

---

## 9. Known limits

**Cross-sentence reference is not resolved.** The main recall loss. Lands visibly in the unattributed bucket rather than silently.

**Results framework tables are not prose.** A row reading "kilometres of fiber added, of which climate resilient" is a quantified resilience target no clause parser will reach. See V9.

**Parse quality on converted documents.** Sentence segmentation is imperfect on clean text and worse on documents converted from PDF. Errors compound across attribution and modality because both consume the same parse.

**The measure list is the binding constraint.** A run against an incomplete measure list is reproducibly incomplete.

**Procurement evidences opportunity, not delivery.**

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

**Cleaning is the first place quality is lost and the hardest to notice.** A defect that survives Stage 2 is embedded, scored and joined without ever raising an error. V10 is the only check standing between the corpus and every number the pipeline produces.

**Committed outputs are not self-describing.** Evidence text is resolved from the corpus on disk at read time, not stored in the output table.

---

## 10. Worked example

Traced from Stage 3. Stages 1 and 2 have nothing to show on a single paragraph.

**Source paragraph (Tanzania, mobile network component):**

> "…an increase in the number of operators required to build towers by choosing sites which are not subject to flooding…"

**Stage 3 — asset detection.** Mobile and towers classifier fires. Asset span located at `towers`.

**Stage 4 — segmentation.**

| Clause | Text |
|---|---|
| 1 | `an increase in the number of operators required to build towers` |
| 2 | `by choosing sites which are not subject to flooding` |

**Stage 5 — measure detection.** Runs on clause 2. Family `siting_and_routing` fires. Measure span located at `choosing sites`, resolving to site selection. Qualifier `flooding` present.

**Stage 6 — attribution.** The measure is in clause 2; the asset is in clause 1. The paragraph names one asset, so the shortcut resolves it: mobile/towers, tier high, `rule_fired = single_asset_paragraph`.

Under the dependency-pattern alternative the same row resolves through the weakest available rule — patterns 1 to 6 all fail, the path rule exceeds its arc limit, and the adjacent-clause fallback fires at tier low. Stamping the rule is what makes that difference visible when precision is scored per rule.

**Stage 7 — modality.** Governing token `required`; shape is predicate, since the measure has its own verb `choosing` subordinate to it. Rules 1 to 3 do not fire. Rule 4 fires — *require* with the measure in its complement. Modality: firm.

**Stages 8 and 9 — procurement.** Four mobile packages in this project, all signed:

| Reference | Description | Signed |
|---|---|---|
| TZ-MCIT-285632-NC-RFB | Upgrade of existing cell towers from 2G to 3G, 4G and above | 2023-05-13 |
| TZ-MCIT-306158-NC-RFB | New cell sites in underserved areas — Phase I | 2023-05-13 |
| TZ-MCIT-360210-NC-DIR | Upgrade of existing cell towers — Phase 2 | 2024-03-21 |
| TZ-MCIT-363989-NC-DIR | New cell sites in underserved areas — Phase 2 | 2024-03-21 |

No description mentions flooding, siting or climate. Four signed contracts for cell sites, a firm commitment to avoid flood-prone siting, and no way from public data to tell whether the two met.
