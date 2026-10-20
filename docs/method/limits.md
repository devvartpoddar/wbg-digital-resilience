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
