# Packages to assets and commitments

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 5e. Packages to assets and to commitments (`./wbg_portfolio run --only link`)

**Two strengths replace one question (2026-11-20, `links.strengths` true).** A link between a committed, digital sentence and a package of the same project now says how it was made, in `commitment_package_links.strength`:

- **`asset`**: they share a top-level asset (connectivity, hosting, platforms, monitoring and control). No model is asked: it is read from the sentence's asset (first pass or second look, at the levels `dashboard.commitment_link_levels` counts) and the package's. It says only that both concern the same kind of asset, not that the package delivers the commitment. Label it "same asset".
- **`strong`** (kind `package_measure`): the package is asked, on its own, the measure's fit question: does the measure apply to the procurement package, that is, is what the package procures the measure, named or clearly implied in the package's own description (`questions.package_measure_question`). It is asked for each package that has an asset, against each measure its project has at least one committed, digital sentence for, and is strong at P(yes) of `link.strong_yes` or more. Every committed, digital sentence of that measure in the project is then linked to the package.

A pair that is both is one row, `strong`, with its `shared_asset_ids` filled. A package has an asset at `link.asset_yes`, or from `link.asset_band_low` up to it when the chat model's second look says yes (`second.package_asset_second`, kind `package_asset_second`, yes or no on the package description, category, component and the asset's definition); a missing or unreadable second look is never a yes (`decide.package_asset_sets`).

**The old link question is retired while `links.strengths` is true.** `questions.LINK_QUESTION` read as "would carrying out the package deliver the commitment", and was read as "delivers": in a trial, the links it gave by a shared asset were right about 15 per cent of the time, so a label that says "delivers" would mislead; the strength that says "same asset" does not. The link stage asks nothing for the kind `link`; its code and the stored scores stay, and `commitment_packages` still publishes from whatever `link` scores exist (empty in a schema that never asked it). With `links.strengths` false the steps below run as before.

**Trial (2026-11-20, graded by models, not checked by people).** A package asked directly about the measure: precision 0.87 and recall 0.88, against 0.33 and 0.19 for the old question read as "names the measure". Package to asset: precision 0.96 and recall 0.71 for the first pass alone, recall 0.93 with the second look on the band. The strengths are readings by models; documented is not done.

The steps before the strengths, unchanged:

The plan's component column joins a package to the appraisal document only where the plan printed one and the parser matched it, and a package description does not sit near the committed sentences by cosine. So the link is made through what both texts name, the asset, and each step is a stored decision-model answer (`checks.links`):

1. **Package to asset.** Every package description in scope (placeholders left out) is embedded once and ranked against the asset phrases; its best `link.asset_top` assets go to the model, one fit question each: does the package buy, build, install, connect, operate or design the asset, with the package text and its plan component as the state. A package has an asset when its P(yes) (through the package-asset layer once fitted) reaches `link.asset_yes`. Most of a plan (works, vehicles, audits) has none. Yes answers at the last rank asked mean the depth is too small.
2. **Commitment to package by asset.** Each committed unit is paired with the packages of its project that share an asset of its own at the levels `dashboard.commitment_link_levels` counts. Each pair is asked once, with the sentence, the package and both components: would carrying out this package be where the commitment is delivered or applied (`questions.LINK_QUESTION`, a person's judgement kept in code until it moves to a file)? Many to many.
3. **Commitment to package by measure** (route `measure`). A package that buys no digital asset (a risk-assessment consultancy, works to raise tower sites) never meets a commitment by asset, so each committed unit is also paired with its project's `link.measure_top` packages nearest the measure's phrases and definition, not already paired; `route`, `measure_rank` and `measure_cosine` are kept.

A link counts at `dashboard.package_link_min_probability` (read at publish and in the views). On the seed-7 grades the link question ranked barely above chance (issue #76): read the links as low confidence.

**Checking.** The combined draw takes package assets and links by band of their probability; `./wbg_portfolio check fill` stores them (kinds `package_asset` and `link`), and the evaluation reports each kind's precision and recall among the pairs asked.

**Output.** `wbg.scores` kinds `package_asset`, `package_asset_second`, `package_measure` and (while the old question is on) `link`; at publish `wbg.package_assets`, `wbg.commitment_packages` and `wbg.commitment_package_links` (`project_id`, `label_set`, `unit_id`, `package_id`, `strength`, `shared_asset_ids`, `strong_p`, `package_category`), read through `package_assets_in_context`, `commitment_packages_in_context`, `commitment_package_links_in_context` and `packages_with_commitments`. The dashboards read `commitment_packages` as before; they do not read the strengths yet.
