# Known limits

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 7. Known limits

- **Results-framework indicators are left out on purpose** (section 5), though some state measured commitments; they are listed in `search_units` with `left_out_reason` indicator, so a question can still find them.
- **Component tags inside annexes.** Annexes have no lettered sections, so a later heading in the same annex that is not a component does not close the last one.
- **Tables without ruling lines read as text**: their cells are read in page order and the unit is usually labelled `table` by its figures.
- **Clipped words stay clipped.** Where a plan cell was cut and the rest of the word is gone, nothing can restore it.
- **French, Portuguese and Spanish word repair is weaker** than English: it has only the descriptions to judge by.
- **Some plans print no component column**, and scattered renditions give up their figures: references and descriptions are kept, amounts, status and dates refused rather than guessed.
- **Some projects publish no procurement plan**, and some plans parse to no package; `data/reports/clean_procurement.txt` counts them on each run.
- **Actual amounts are mostly zero**; a count of "packages with an actual amount" must exclude zeros.
- **Planned dates can predate approval** (plans disclosed before approval or carried over from a parent operation). Age relative to approval is not a measure of delay.
- **The no-objection date on awards** mostly follows the signing date: it reads as the Bank's post-review date, not a pre-signature clearance.
- **Status is a fact at a date.** `status_as_of` is the date of the plan or contract that stated it, not today. A package whose newest plan prints no status stays `unknown` unless a signed contract matches it.
- **Only Investment Project Financing.** Policy loans and results programmes are not fetched, by choice, and the commitments views count only IPF operations.
- **Section headings in title case are not read.** A paper whose sections read "II. Project Description" rather than "II. PROJECT DESCRIPTION" is filed wholly as front matter and yields no unit.
- **Short activities are dropped**: a unit under five words and a sentence whose words, counted without digits, fall under four.
- **Copies ignore digits**: two sentences differing only in figures or component numbers are one text, searched through the first.
- **The skip rule sees only graded yes some earlier step found.** A fitted gate can rise above sentences no person has seen; the elusion draw is what looks below it.
- **Spanish and French sentences are searched and asked against English definitions**; how well they are read is unmeasured.
- **A narrow column of prose beside a figure or box** can be read as one line with the figure's text (section 2.5).
- **A fresh answer depends a little on its batch.** Items are asked many to a request; about one fresh answer in ten at 0.5 differs with its neighbours (issue #80). Stored answers make re-runs repeatable, but a rebuilt store would not reproduce every answer.
- **The model reads the sentence, the person read the paragraph.** The asset question misses most assets a person gives for that reason (issue #75); `ask.context` is the test, at the cost of re-asking every sentence.
- **Grades by an agent are not grades by a person.** The grader is recorded on every grade; an evaluation draw graded by a model alone does not meet the method's third principle without a person checking a sample.
