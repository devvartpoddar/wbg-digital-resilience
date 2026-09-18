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
