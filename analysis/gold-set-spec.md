# The gold set: what to record and why each column exists

Dev hand-builds this. One day of his time, once. Every column below has to
justify itself against that, so each one says what it buys.

## The unit is one activity, not one paragraph and not one sentence

**A measure is an activity applied to an asset.** "Climate risk assessment of
the landing stations" is a measure: the activity is the assessment, the asset is
the landing station. That is the same shape an extractor produces — a verb and
its object — which is why this unit works for every method rather than just one.

A paragraph stating four things gives **four rows**. That separation is the
whole point; the existing portfolio tracker does not have it.

## Four kinds of row, not one

Recording only measures makes precision unmeasurable: when a method returns
something that is not in the set, there is no way to tell junk from a near-miss.
So every extracted span carries a `kind`.

| `kind` | what it is | test |
|---|---|---|
| `measure` | the project commits to an activity that makes an asset withstand or recover from a hazard, or that uses a digital asset for resilience | commitment **and** hazard/resilience content |
| `asset` | the project commits to acquire, build, deploy or upgrade a digital asset | commitment, no hazard content, object is an asset |
| `activity` | the project commits to something else — training, TA, regulation, studies, M&E | commitment, no hazard content, object is not an asset |
| `context` | a statement of fact rather than a commitment — hazard exposure, past damage, a screening already done, policy background | no forward commitment by this project |

**`activity` is the hardest negative class and the reason the clause run failed.**
"The Project will finance training of 200 civil servants" has the same shape as a
measure — agent, modal, verb, object — and no resilience content. Discriminating
those two is the task. Without them in the set there is nothing to fail against.

**`asset` rows are not a throwaway class.** "Under Component 1 the Project will
finance the purchase of 500 km of fiber" is the financed-asset statement, and
the project objective is climate measures *for the assets the Bank finances*. The
A1 probe over-fired to 97-100% of projects precisely because nothing
distinguished "the project will buy fiber" from "fiber is mentioned".

### The pair that defines the `context` boundary

- "The Bank conducted a Climate and Disaster Risk Screening for this operation."
  → `context`. Past tense, the Bank not the project, no forward commitment.
- "The Project will conduct a climate and disaster risk screening of all
  proposed sites." → `measure`. Forward commitment, hazard content.

Same words, different rows. Recording both is what lets a method be tested on
whether it can tell them apart.

## Columns

| column | fill | buys |
|---|---|---|
| `kind` | dropdown | the class boundary above |
| `quote` | paste from the sheet, verbatim | character-offset scoring for every span-producing method |
| `label` | your own short name | the measure **type**, which is the actual output of the project |
| `direction` | dropdown: `resilience_of_asset` / `digital_for_resilience` | your existing axis from the 89-measure list |
| `asset` | free text | measure-to-asset attribution |
| `asset_in_para` | Y / N | whether the asset is recoverable from this paragraph at all |
| `hazard` | free text, blank if unstated | tests whether hazard proximity is a usable signal |
| `stem` | paste, only when the commitment verb sits outside `quote` | how often commitment is severed from content |

### `quote` is the smallest self-contained span, and it must come from the sheet

**Highlight the smallest span that, read on its own, states both that the project
will do this and what it is.** Usually that is one sentence:
`The Project will bury fiber optic cable along flood-prone segments`.

When that is impossible because the commitment sits in a lead-in shared across a
bulleted list, quote the item alone —
`burial of fiber optic cable along 120 km of flood-prone segments` — and put the
lead-in in `stem`. Enumerators and list punctuation stay out either way.

**Paste from the workbook, never from the PDF.** Our extraction differs from
Acrobat on ligatures, hyphenation and glued table cells. A quote that is not an
exact substring of our paragraph text scores as unfound for every method. A
validation pass checks this before scoring.

### `label` must repeat across rows

`label` is a **type**, not a restatement of the quote. Four projects writing
"burial of cable", "underground routing", "cables shall be laid below grade" and
"trenched rather than aerial" all get the same label.

**If 400 rows yield 380 distinct labels, the set describes instances and cannot
test whether a method finds the same measure twice.** If they yield 60-90, it
describes types. That count is itself the first real answer to "how many
measures are there".

### `asset_in_para` is one keystroke and may be the most valuable column

Write the asset if you can work out what it is, even from a different paragraph —
then mark `N`. If a large share of measures have their asset named elsewhere in
the document, **no paragraph-level method can ever get attribution right**, and
that bounds three of the five candidate methods before any of them is built.

### `stem` measures the failure that killed the clause run

**The rule: read the quote on its own. If it tells you the project promised this,
leave `stem` blank. If it does not, because the "will" is in a shared lead-in,
the lead-in goes in `stem`.**

"To enhance the climate resilience of the backbone network, the Project will
finance: (i) burial of fiber optic cable...; (ii) elevation of tower
foundations..." — four measures, and not one of the four items contains "will"
or "the Project". The commitment lives once, in the lead-in.

That is why the clause run failed. "burial of fiber optic cable along flood-prone
segments" scores zero on any agent-plus-modal test, while "this subcomponent will
finance activities" scores high and says nothing — which is exactly what got
reported as a discovered measure in `analysis/findings.md`.

Paste the lead-in once per list and fill it down. **The share of measures with a
non-empty stem is a fork in the road: low, and span-level commitment scoring is
viable; high, and every method that scores spans independently is structurally
broken and is not worth building.**

## Exhaustiveness, tiered

**In every paragraph opened: every `measure` and every `asset`.** These are the
target classes and recall is measured on them.

**In the 60 random paragraphs only: every `activity` and `context` too.** Those
60 are the precision test — a span a method returns from them that matches no row
is a genuine false positive. In the climate-flagged paragraphs, `activity` and
`context` may be skipped, and precision is simply not scored there.

**A paragraph with nothing in it gets zero rows and a tick in its header.** Read
and empty must be distinguishable from not yet read.

Perfect exhaustiveness is not achievable and is not required. Misses cap measured
precision, but they cap it identically for all methods, so the ranking between
methods survives. The CRS work (arXiv:2211.16947) found expert adaptation labels
disagreeing on identical text about half the time; see `analysis/prior-art.md`.

## Sample

| slice | paragraphs | expected rows |
|---|---|---|
| matched to the portfolio tracker's climate-flagged fragments | 80 | ~280 |
| projects chosen for cybersecurity, DPI, data centres | 40 | ~120 |
| random, unfiltered, from Project Components and annexes | 60 | ~10 |

## What is deliberately left out

**Commitment firmness.** It is a different task from discovery, and a column
where blanks are expected gets filled inconsistently. It can be added later by
re-reading only the `measure` rows rather than all 180 paragraphs.

**A controlled vocabulary for `label`.** A dropdown would pre-decide the taxonomy
and destroy the experiment. The words reached for are the finding.

## How each method is scored against it

Every row is (location, verbatim span, my label, my kind). **Anything that
outputs something localisable in text is scored by span overlap; anything that
outputs a category is scored against the label.** That is what makes the set
outlive the current five candidates.

- **recall** — gold `measure` rows whose quote overlaps a returned span
- **precision** — returned spans in the 60 random paragraphs matching no row
- **granularity ratio** — for a paragraph given 4 rows, how many distinct things
  did the method return? 4 is right, 1 lumped, 12 shredded. **This number would
  have stopped the clause run on its first day.**
- **kind confusion** — how often `activity` and `context` spans are returned as
  measures. The single most diagnostic figure in the set.
- **span delimitation** — right paragraph, wrong span. Distinguishes "cannot
  locate" from "cannot delimit", which are different bugs.
