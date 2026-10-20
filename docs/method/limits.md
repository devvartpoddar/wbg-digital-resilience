# Known limits

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 7. Known limits

- **Results-framework indicators are left out on purpose** (section 5), though some state measured commitments (for example a share of infrastructure built to withstand climate shocks); they are listed in `search_units` with `left_out_reason` indicator, so a question can still find them.
- **Component tags inside annexes.** Annexes have no lettered sections, so a later heading in the same annex that is not a component does not close the last one.
- **Tables without ruling lines read as text**: their cells are read in page order and the unit is usually labelled `table` by its figures.
- **Clipped words stay clipped.** Where a plan cell was cut and the rest of the word is gone ("Project Management Suppor"), nothing can restore it.
- **French, Portuguese and Spanish word repair is weaker** than English: it has only the descriptions to judge by. Some broken words remain ("con sultant").
- **Some plans print no component column** (1,525 of 6,886 packages have none).
- **Scattered renditions give up their figures.** Where the text extractor scattered a table's columns, the rendition's references and descriptions are kept and its amounts, status and dates are refused rather than guessed.
- **Projects with no package rows**: 9 projects publish no procurement plan (P169945, P170910, P171791, P180987, P181416, P506791, P508317, P508363, P511767), and others have plans that parse to no package (among them P171099, P174620, P175218, P175987, P177158, P179204, P180693, P180807, P502532; section 3.2 counts 6, this list was 9: the next run's `clean_procurement.txt` is the count to trust).
- **Award descriptions** occasionally carry "?" where the interface lost an accented letter; that is in the source.
- **One plan's references are slash-coded** ("…/PHN-20/CQS-002"), so its packages can carry a second reference in the description (6 packages).
- **Two projects' plans could not be fetched** (P171099, P171976: the interface answered 403), so they have no package rows either; the next run tries again.
- **Actual amounts are mostly zero.** 5,938 packages print an actual amount of 0 and only 342 a figure above it; a count of "packages with an actual amount" must exclude zeros.
- **Planned dates before approval.** 584 packages in 22 projects carry a planned date earlier than the project's approval date, most from plans disclosed before approval or carried over from a parent operation. Age relative to approval is not a measure of delay.
- **The no-objection date on awards** follows the signing date in 2,970 of 3,024 cases (median 64 days) and is present mostly on post-review contracts: it reads as the Bank's post-review date, not a pre-signature clearance.
- **Seven projects have no approval date and seven a commitment of 0** in the projects interface, among them active ones (P502532, P505095); a filter on either drops them.
- **Status is a fact at a date.** `status_as_of` is the date of the plan or contract that stated it, not today. A package whose newest plan prints no status stays `unknown` unless a signed contract matches it; an older plan's status is not used.
- **Only Investment Project Financing.** Policy loans (appraised in a Program Document) and results programmes (a Program Appraisal Document) are not fetched: 21 Development Policy Lending and 9 Program-for-Results operations in the cohort have no text, by choice (Dev, 2026-10-20), and the commitments views count only IPF operations.
- **Results-framework rows are left out**, even those saying "will" (1,877 of 6,679 such rows on 2026-10-20), by choice.
- **Section headings in title case are not read.** A paper whose sections read "II. Project Description" rather than "II. PROJECT DESCRIPTION" is filed wholly as front matter and yields no unit (documents 34125635 and 34295176 on 2026-10-20). Changing the rule re-reads every PDF (the reader's cache is keyed on its code), so it waits for the next re-parse.
- **Short activities are dropped**: a unit under five words ("Sites will be flood-proofed.") and a sentence whose words, counted without digits and with hyphens splitting them, fall under four ("Elevate 25 substations.").
- **Copies ignore digits**: two sentences differing only in figures or component numbers are one text, searched through the first.

