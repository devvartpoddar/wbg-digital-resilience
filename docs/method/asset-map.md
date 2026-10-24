# The asset map

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 5b. The asset map (`./wbg run --only classify`)

The asset set is `inputs/taxonomy/assets.csv` with its phrases (`assets_queries.csv`) and word patterns (`assets_patterns.csv`); it changes rarely and deliberately, because a changed definition is a new question and every grade given under the old wording is a grade of another question. Every sentence the search reads is asked, for each active asset, the graded question of section 5g on the shared scale, through the same funnel as a measure: the gate (the fitted gate, else `asset.gate_cosine`), the patterns, every sentence already answered, then the rounds counted at `asset.ask_floor` with `asset_feedback.max_rounds` (0 turns them off; the other round settings are the measures'). One read of the embedding matrix serves every asset in a round. The scores are rows of `wbg.scores` with kind `asset`.

**Which assets a sentence has** is read at publish (`decide.sentence_assets`; in no run key). The sentence's own assets count when their posterior reaches `asset.floor` (level `unit`). Otherwise, with `asset.decay` above 0, each asset's own score and every earlier sentence's in the paragraph are added up, the k-th sentence back weighted by `asset.decay` to the power k, and the assets whose sum reaches `asset.paragraph_floor` take the sentence (level `paragraph`). With `asset.multiple` every such asset counts, best first; without it, the best only. Else the sentence has none, and a commitment on it shows `(no asset)` on the dashboards (`dashboard.commitment_link_levels`).

Loaded as `wbg.paragraph_grades` (one row per sentence and asset asked, with `posterior_probability`) and `wbg.unit_assets` (each sentence's assets and level, one row per asset).

**Checking.** The combined draw grades asset lines (0-3) on the sentences it shows, and the assets of a shown commitment come with its measure line; the evaluation fits the asset layers and gates from them (section 5g). Issue #75 records that the model, reading the sentence alone, misses most assets a person reading the paragraph gives; `ask.context` = `paragraph` is the test for that.
