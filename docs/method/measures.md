# Measures and their types

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

**Versions.** Every file in `analysis/labels/` carries a `version` column for people (v2.0 since the taxonomy rewrite of 2026-10-22). Runs and stored answers are keyed on the exact questions, never on it.

## 5c. A measure through the funnel

Each measure is one label set in `analysis/labels/`: a definition that names the intervention and never the asset (the model reads literally: a tower-siting sentence scored 0.17 for "a climate risk assessment of its digital infrastructure" and 0.97 for "a climate risk assessment"), an optional `boundary` line the pair checks read, search phrases, optional word patterns and a type list. The scale is shared (`analysis/labels/scale.csv`). Every number is a control-panel setting.

1. **The pool and the rounds** are the funnel of section 5g (`src/wbg_portfolio/funnel.py`): word patterns, the gate on the best cosine over the measure's phrases and definition, every sentence already answered, then the feedback rounds counted at `floor.ask`.
2. **The decision model** reads each sentence once, with its list lead-in, section and component (`ask.context` adds the paragraph). Grades 2 and 3 count together.
3. **The decision** is read at publish (`src/wbg_portfolio/decide.py`): the score through the measure's layer when fitted, at `floor.<set>` else `floor.default`, as `is_committed`; the asset from the asset map (section 5b); the types at `floor.types`. Every row carries the wording's firmness (`activity`, `outcome`, `none`, from fixed word lists) and its template wording (`template_projects`, `template_share`): columns to filter on, never cuts.

**Cross-measure pair checks.** A unit two measures both count at `floor.ask` is asked, for each side, whether the measure applies with the other measure's boundary beside it; the views keep it in a measure when that side's P(keep) reaches `floor.pair_check` (`dashboard.apply_measure_pair_checks`).

**Switching a measure off.** A set whose labels are all inactive is not run and its rows leave Postgres at the next publish; its stored answers stay, so switching it back on asks nothing.

## 5d. A measure's types

1. **Propose**, only for a measure with no list a person wrote: the committed sentences go to a generative model (`checks.GEN_MODEL`) once, and the list is written to `data/types/<set>_types.csv` for a person to approve into `analysis/labels/<set>_types.csv`.
2. **Assign**: every unit the measure counts at `floor.ask` is asked one fit question per approved type (yes, unsure or no), told the sentence is already a commitment to the measure, so it asks only which kind. At publish every type at `floor.types` counts, best first (`types.multiple`; the best only when off); a sentence no approved type reaches takes the OTH types it was asked (those proposed by the gap step, `types.gaps`); a sentence no type reaches has none, shown as `(no type)`.
3. **Merge and rename** are file edits, never a model call: `merge_into` folds a type into another of the same measure, `display_name` changes what is shown while `type_name` stays as asked; `./wbg_portfolio publish` applies them.
