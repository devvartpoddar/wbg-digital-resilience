# The asset map

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 5b. The asset map (`src/classify.py`, `./wbg classify assets`)

Every sentence the search reads (narrative and annex sentences, footnotes, prose table rows; copies once) is asked, for each active asset in `inputs/taxonomy/assets.csv`, the one graded question every stage asks (`classify.graded_question`), with its section and component as context. The grades are the asset scale (`inputs/taxonomy/assets_scale.csv`): 0 not about it, 1 background or another party's work, 2 supported without paying for the asset (technical assistance, policy, studies), 3 financed (built, bought, installed, upgraded, connected or operated). Grades 2 and 3 count together (`read.grades`).

**What is asked.** An asset's question goes to a sentence only when the sentence's cosine to the asset's phrases reaches the asset's gate: the skip rule's fitted gate (`data/labels/assets_gate.json`, section 5g), else `asset.gate_cosine`. A row not asked is kept with `asked` false. A gate of 0 asks every asset of every sentence.

**Which asset a sentence has** (`measure.sentence_asset`, applied where the map is read: the measure stage, and `wbg.unit_assets` at every publish). Each score goes through the asset's probability layer when one is fitted (section 5g). The sentence's own best asset counts when its posterior reaches `asset.floor` (level `unit`). Otherwise, for each asset, its own score and every earlier sentence's in the paragraph are added up, the k-th sentence back weighted by `asset.decay` to the power k; the asset with the highest sum takes the sentence when that sum reaches `asset.floor` (level `paragraph`). Else the sentence has none.

**The model** is Jev through OpenRouter's decisions endpoint, pinned to the dated snapshot `typesafe/jev-1.13-20260917` (the alias moves when the vendor ships a release). It returns a probability per grade. Answers are stored per model, state and question and re-read on every run (`src/answers.py`), so a changed definition re-asks that asset's question only. Items are batched many to a request (`answers.BATCH`, up to `answers.BATCH_MAX_CHARS`); every answer is still stored under its own item and question. An answer missing a grade is not stored; a run with failed requests writes nothing and resumes on the next run; a refusal (bad key, no credit) stops at once.

**Output.** `data/classify/assets-<version>/scores.csv` (sentence by asset: whether asked, the best cosine, each grade's probability, the model's confidence, the snapshot served, the prior inputs) and a manifest with the exact questions. The version is a SHA-256 over every sentence's request, the gate and the fitted gates, so a changed definition, text or gate is a new version, and a version whose answers are all stored costs nothing. Loaded as `wbg.paragraph_grades` (one row per sentence and asset, with `posterior_probability`) and `wbg.unit_assets` (each sentence's asset and level).

**Checking.** Graded asset sheets come later: a new sheet will grade sentences against the assets directly. Until then the asset layers and gates are fitted from the assets people gave sentences on measure sheets (`check_units.fit_asset_layers`).
