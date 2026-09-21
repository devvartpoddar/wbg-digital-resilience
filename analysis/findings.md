# Measured constraints from the phase 3 probes

Three facts worth keeping. The probe scripts and their output tables are deleted —
they are re-derivable and the methods they used are superseded — but these numbers
cost a four-hour run and constrain designs downstream. Full working is in the
history of pull request 9.

## The asset classifiers detect topic, not financed asset

Project-level coverage at each class's current threshold, over the 70 included
projects: access_connectivity 100%, dpi 100%, drm_ews 100%, cybersecurity 97.1%,
data_hosting 97.1%, mobile_towers 94.3%, submarine_cable 71.4%, fiber 70.0%.

Seventy of seventy projects do not finance early-warning systems. The high figures
are over-firing, not recall, and fiber and submarine cable are capped by the model
rather than by the cut — even at p ≥ 0.30 they reach 78.6% and 80.0%. **Paragraph-level
classification over 219 positives is not a usable asset signal.** The asset is to be
read out of clause communities instead.

## The procurement join has a hard ceiling

Of 5,601 packages, **11.4% resolve to a single asset class, 21.8% only to a coarse
group, 78.1% to nothing.** Most of the residue is consultancy and administration —
3,336 of the packages are consultant services against 173 works.

Status is `unknown` for 38.4%, but 1,053 of those 2,149 are a mapping gap rather
than a missing fact: the value is present and clipped by the extractor
("Under Implement ation", "En attente d'exéc ution"). Only 1,096 packages genuinely
record no status, so fixing the mapping is worth more than any further parsing.

A de-glue pass over the 30% of descriptions with words run together is worth
**+0.3 percentage points** of resolution — 17 packages. `match_key` in
`src/clean_procurement.py` already strips whitespace from both sides and reaches
9.1pp more, so the de-glue pass is not worth shipping as a stage.

## The splitter over-splits

`src/segment.py` produces 7.48 clauses per prose paragraph, and **14.8% of clauses
are four tokens or fewer.** `verb_subtree` alone accounts for 30.2% of all
boundaries. A four-token fragment has an embedding but no proposition, which makes
it noise in any clustering over clause vectors.

`analysis/a2_segmentation/check_set.csv` holds the 20 hand-counted paragraphs the
splitter is scored against, and is kept for exactly that reason.

## The clause is too small a unit to hold a measure

The clustering run over 119,591 clauses is deleted, but what it established is
not. Communities scoring highest on an agent-plus-modal commitment test turned
out to be **sentence stems**, not commitments:

| community | clauses | projects | what its medoids say |
|---|---|---|---|
| c0358 | 110 | 50 | "The Project is expected to offer multiple social and economic benefits" |
| c0323 | 61 | 31 | "This subcomponent will finance activities" |
| c0324 | 39 | 26 | "This subcomponent will also finance TA activities" |
| c0523 | 30 | 16 | "The project will not finance civil works" |

"This subcomponent will finance activities" is a stem: whatever it finances sits
in the next clause, which has no agent and no modal and therefore scores zero on
the same test. That is why 66% of communities scored zero, and why the measure
layer looked thin — **the splitter severs the commitment from its content**, and
both halves then look like noise. split-2 narrowed `verb_subtree` and still cuts
the object away from the verb.

**A nearest-reference label below about 0.5 cosine is noise wearing a label.**
The four communities above were reported as "climate risk analytics in public
financial management" because REF085 was the closest of 89 entries at cosine
0.26–0.36. A text search for "climate risk analytics", "public financial
management" and "disaster loss database" across all 611 shortlisted communities
returns two hits, both about PFM performance and neither about climate.

**The one thing that has demonstrably worked on this corpus is query-by-example
retrieval at paragraph level.** `data/neighbours_check.txt` queried the
paragraph embeddings with measure-shaped sentences and returned genuine measure
paragraphs at 0.50–0.82 cosine: weather-proofing of ducts and poles, tower
elevation, underground versus aerial choice, non-flood-prone siting. No
clustering, no training, no derived scores.

## The portfolio tracker's shape, measured

Derived statistics only; the worksheet itself stays off this repository. Read
from its `2. Activities Review` and `0. Definitions` tabs.

**573 rows, 331 carrying an excerpt of PAD text, across 55 projects.**

**The excerpt is not a paragraph.** Median 946 characters / 131 words, mean
1,322, maximum 6,981 characters / 931 words. 67% hold three or more
sentence-length units, median four. One excerpt maps to between one and four of
our paragraphs, so a fuzzy match to `paragraphs.csv` is one-to-many.

**Only 19% of excerpts contain two or more enumerators.** Measures live mostly in
running prose, not in bullet lists. Any plan that leans on splitting enumerations
addresses a fifth of the corpus.

**64% of enumerated items carry no modal of their own.** Of 270 items inside the
62 enumerated excerpts, 98 contain `will / shall / is expected to`; the rest
inherit it from a lead-in. Measured by regex, so approximate, but it answers the
question the gold set's `stem` column was added to settle: **scoring spans
independently for commitment language is structurally broken on enumerated
text.** That is what the deleted discovery stage did, and why its highest-scoring
communities were sentence stems.

**15% of sentence-length units open with a nominalisation** ("Provision of...",
"Establishment of...", "Digitization of..."). A verb-frame or OpenIE extractor
needs a fallback for roughly that share, not for the majority.

**The hand labels are instance-level, not type-level.** The short-label column is
filled on 268 of the 331 excerpt rows and holds **198 distinct values** - 1.36
rows per label. The most frequent repeats eight times; most repeat once. A label
set that barely repeats cannot test whether a method finds the same measure in
two different projects, which is why `analysis/gold-set-spec.md` insists the
gold `label` must recur.

**A coarse taxonomy already exists and is documented.** The definitions tab
defines 11 adaptation and 12 mitigation categories, each with a definition,
worked examples, and a dropdown of two to five sub-values - a three-tier
structure already built by hand, whose worked examples sit at technique
granularity. What is missing is the technique tier itself.

**That taxonomy leaks.** Its own header says the categories "will have to be
mutually exclusive", and 58 of 331 excerpt rows carry two or more. Mapping cells
mix a plain `Yes` with free text, so the category set was still moving while the
mapping was being done. 96 excerpt rows carry no adaptation category at all; 62
of those carry a mitigation category and **34 carry neither** - candidate climate
content that fitted no bucket, and therefore the likeliest place for a measure
type the taxonomy has no name for.
