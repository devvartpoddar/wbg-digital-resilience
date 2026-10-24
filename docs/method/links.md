# Packages to assets and commitments

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 5e. Packages to assets and to commitments (`./wbg run --only link`)

The plan's component column joins a package to the appraisal document only where the plan printed one and the parser matched it, and a package description does not sit near the committed sentences by cosine. So the link is made through what both texts name, the asset, and each step is a stored decision-model answer (`checks.links`):

1. **Package to asset.** Every package description in scope (placeholders left out) is embedded once and ranked against the asset phrases; its best `link.asset_top` assets go to the model, one fit question each: does the package buy, build, install, connect, operate or design the asset, with the package text and its plan component as the state. A package has an asset when its P(yes) (through the package-asset layer once fitted) reaches `link.asset_yes`. Most of a plan (works, vehicles, audits) has none. Yes answers at the last rank asked mean the depth is too small.
2. **Commitment to package by asset.** Each committed unit is paired with the packages of its project that share an asset of its own at the levels `dashboard.commitment_link_levels` counts. Each pair is asked once, with the sentence, the package and both components: would carrying out this package be where the commitment is delivered or applied (`questions.LINK_QUESTION`, a person's judgement kept in code until it moves to a file)? Many to many.
3. **Commitment to package by measure** (route `measure`). A package that buys no digital asset (a risk-assessment consultancy, works to raise tower sites) never meets a commitment by asset, so each committed unit is also paired with its project's `link.measure_top` packages nearest the measure's phrases and definition, not already paired; `route`, `measure_rank` and `measure_cosine` are kept.

A link counts at `dashboard.package_link_min_probability` (read at publish and in the views). On the seed-7 grades the link question ranked barely above chance (issue #76): read the links as low confidence.

**Checking.** The combined draw takes package assets and links by band of their probability; `./wbg check fill` stores them (kinds `package_asset` and `link`), and the evaluation reports each kind's precision and recall among the pairs asked.

**Output.** `wbg.scores` kinds `package_asset` and `link`; at publish `wbg.package_assets` and `wbg.commitment_packages`, read through `package_assets_in_context`, `commitment_packages_in_context` and `packages_with_commitments`.
